"""相機參數調整：讓自動對焦 / 自動曝光 / 自動白平衡收斂後，鎖定成手動值並匯出。

操作（在 OpenCV 預覽視窗中）：
  a      開啟自動（AF / AE / AWB），等畫面清晰、亮度穩定
  l      鎖定：讀回目前的對焦、曝光、增益、色溫，關閉自動並設成這些手動值
  滑桿   微調（拉動某個滑桿時，該項的自動功能會關閉）
  s      匯出：寫入 config/cameras.yaml 中該相機的個別設定，並保存紀錄與一張照片
  q      結束這台（--cam all 時換下一台）     ESC  全部結束
畫面中央方框內的清晰度（sharp，Laplacian 變異數）越大越清楚：對焦時把標定板或物品放在中央、
放在之後實際拍攝的距離。

保存：
  data/camera_setup/cam{i}_YYYYMMDD_HHMMSS.png (+ .json)   匯出時的畫面與實際讀回的設定
  output/00_camera_setup/run_*/camera_settings.yaml          各相機匯出的設定（要求值與讀回值）
  output/00_camera_setup/run_*/config_used.yaml、run.log
⚠ 對焦改變後內參就失效，要重新拍內參照片並標定（Task.md 0-3）。

範例：
  python src/camera_setup.py --cam 0
  python src/camera_setup.py --cam all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.camera import Camera, auto_values, camera_settings  # noqa: E402
from common.io_utils import (add_root_arg, create_run_dir, imwrite, load_configs, now_iso,  # noqa: E402
                             parse_cam_list, rel, save_json, save_yaml, set_latest, setup_logger,
                             timestamp)
from common.yaml_edit import update_yaml_file  # noqa: E402

# 滑桿：(設定名稱, params.camera_setup 中的範圍 key, 滑桿顯示名稱, 對應的自動功能)
SLIDERS = [
    ("focus", "focus_range", "focus", "autofocus"),
    ("exposure", "exposure_range", "exposure", "auto_exposure"),
    ("gain", "gain_range", "gain", "auto_exposure"),
    ("wb_temperature", "wb_range", "WB (K)", "auto_wb"),
]
VALUE_KEYS = [s[0] for s in SLIDERS]
AUTO_KEYS = ["autofocus", "auto_exposure", "auto_wb"]


def put_text(img, text, org, color=(0, 255, 255), scale=0.65):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2, cv2.LINE_AA)


def roi_box(shape, ratio: float) -> tuple[int, int, int, int]:
    h, w = shape[:2]
    rw, rh = int(w * ratio), int(h * ratio)
    x0, y0 = (w - rw) // 2, (h - rh) // 2
    return x0, y0, x0 + rw, y0 + rh


def roi_metrics(frame: np.ndarray, box) -> tuple[float, float]:
    """回傳 (中央區域清晰度, 平均亮度)。"""
    x0, y0, x1, y1 = box
    gray = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var()), float(gray.mean())


def clean_number(v: float):
    """整數值存成 int，其餘保留小數（寫進 yaml 比較好讀）。"""
    v = float(v)
    return int(round(v)) if abs(v - round(v)) < 1e-6 else round(v, 4)


class Tuner:
    """單台相機的預覽、滑桿、自動 / 鎖定狀態。"""

    def __init__(self, cam: Camera, p: dict, win: str, log):
        self.cam, self.p, self.win, self.log = cam, p, win, log
        self.auto = False
        self.locked = False
        self.dirty = False          # 有改過設定但還沒匯出
        self.suppress = False
        self.ranges = {k: [float(x) for x in p[rk]] for k, rk, _, _ in SLIDERS}
        cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)
        for key, _, name, _ in SLIDERS:
            lo, hi = self.ranges[key]
            cv2.createTrackbar(name, win, 0, int(hi - lo), self._on_slider(key))
        self.readback = self.read_values()
        self.sync_sliders(self.readback)

    def read_values(self) -> dict:
        out = {k: self.cam.get(k) for k in VALUE_KEYS + AUTO_KEYS}
        return out

    def sync_sliders(self, values: dict) -> None:
        self.suppress = True
        for key, _, name, _ in SLIDERS:
            lo, hi = self.ranges[key]
            pos = int(np.clip(round(values[key] - lo), 0, hi - lo))
            cv2.setTrackbarPos(name, self.win, pos)
        self.suppress = False

    def _on_slider(self, key: str):
        def cb(pos: int) -> None:
            if self.suppress:
                return
            auto_key = next(a for k, _, _, a in SLIDERS if k == key)
            self.cam.set_auto(False, keys=(auto_key,))
            self.cam.set(key, self.ranges[key][0] + pos)
            self.auto = False
            self.dirty = True
        return cb

    def enable_auto(self) -> None:
        self.cam.set_auto(True)
        self.auto, self.locked, self.dirty = True, False, True
        self.log.info(f"cam{self.cam.cam_id}：已開啟自動對焦 / 自動曝光 / 自動白平衡，畫面穩定後按 l 鎖定")

    def lock(self) -> dict:
        """讀回目前的值 -> 關閉自動 -> 設成手動值 -> 讀回確認。"""
        target = {k: self.cam.get(k) for k in VALUE_KEYS}
        self.cam.set_auto(False)
        for k, v in target.items():
            self.cam.set(k, v)
        for _ in range(int(self.p["settle_frames"])):
            self.cam.read()
        rb = self.read_values()
        self.auto, self.locked, self.dirty = False, True, True
        self.readback = rb
        self.sync_sliders(rb)
        self.log.info(f"cam{self.cam.cam_id} 已鎖定：" + "，".join(
            f"{k} {target[k]:g} -> 讀回 {rb[k]:g}" for k in VALUE_KEYS))
        for k in VALUE_KEYS:
            if abs(rb[k] - target[k]) > max(1.0, 0.02 * abs(target[k])):
                self.log.warning(f"cam{self.cam.cam_id} {k} 鎖定後讀回值與目標不同：此相機可能不支援在自動模式下"
                                 f"讀回實際值，或不接受此數值。請看畫面確認，必要時用滑桿微調後再匯出")
        return rb


def export(root: Path, cfg: dict, tuner: Tuner, frame: np.ndarray, sharp: float, bright: float,
           write_config: bool, log) -> dict:
    """把目前（手動）設定寫入 cameras.yaml，並保存照片與 metadata。"""
    cam = tuner.cam
    if tuner.auto:
        log.info(f"cam{cam.cam_id} 仍在自動模式，先鎖定再匯出")
        tuner.lock()
        ok, f2 = cam.read()
        if ok and f2 is not None:
            frame = f2
    off = {k: v[1] for k, v in auto_values(cam.backend_name).items()}
    rb = tuner.read_values()
    settings = {
        "autofocus": off["autofocus"], "focus": clean_number(rb["focus"]),
        "auto_exposure": off["auto_exposure"], "exposure": clean_number(rb["exposure"]),
        "gain": clean_number(rb["gain"]),
        "auto_wb": off["auto_wb"], "wb_temperature": clean_number(rb["wb_temperature"]),
    }
    full = cam.read_settings()
    out_dir = root / "data" / "camera_setup"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"cam{cam.cam_id}_{timestamp()}"
    imwrite(out_dir / f"{name}.png", frame)
    record = {
        "file": f"{name}.png", "mode": "camera_setup", "camera_id": cam.cam_id, "timestamp": now_iso(),
        "image_size": [frame.shape[1], frame.shape[0]], "roi_sharpness": sharp, "roi_brightness": bright,
        "exported_settings": settings, "settings_readback": full,
    }
    save_json(out_dir / f"{name}.json", record)
    log.info(f"cam{cam.cam_id} 匯出設定：{settings}（照片 {rel(out_dir / f'{name}.png', root)}）")

    record["written_to_config"] = False
    if write_config:
        cams = cfg["cameras"]["cameras"]
        idx = next(i for i, c in enumerate(cams) if int(c["id"]) == cam.cam_id)
        updates = {("cameras", idx, k): v for k, v in settings.items()}
        missing = update_yaml_file(root / "config" / "cameras.yaml", updates, insert_missing=True)
        if missing:
            log.error(f"cameras.yaml 無法寫入 {missing}，請手動修改")
        else:
            record["written_to_config"] = True
            log.info(f"已寫入 config/cameras.yaml 的 cameras[{idx}]（id {cam.cam_id}）")
    tuner.dirty = False
    return record


def run_camera(root: Path, cfg: dict, cam_id: int, write_config: bool, log) -> tuple[list[dict], bool]:
    """回傳 (此相機的匯出紀錄, 使用者是否按 ESC 要全部結束)。"""
    p = cfg["params"]["camera_setup"]
    cam = Camera(cam_id, cfg["cameras"])
    win = f"camera setup cam{cam_id}"
    tuner = Tuner(cam, p, win, log)
    log.info(f"cam{cam_id} 開啟時的設定：{ {k: round(v, 3) for k, v in tuner.readback.items()} }")
    records: list[dict] = []
    n, quit_all = 0, False
    sharp = bright = 0.0
    try:
        while True:
            ok, frame = cam.read()
            if not ok or frame is None:
                log.error(f"cam{cam_id} 讀取畫面失敗")
                break
            n += 1
            box = roi_box(frame.shape, float(p["roi_ratio"]))
            sharp, bright = roi_metrics(frame, box)
            if n % int(p["readback_interval"]) == 0:
                tuner.readback = tuner.read_values()
                if tuner.auto:
                    tuner.sync_sliders(tuner.readback)      # 自動模式下滑桿跟著相機的值移動
            rb = tuner.readback
            vis = frame.copy()
            cv2.rectangle(vis, box[:2], box[2:], (255, 255, 0), 2)
            mode = "AUTO" if tuner.auto else ("LOCKED" if tuner.locked else "MANUAL")
            put_text(vis, f"cam{cam_id}  {mode}  sharp {sharp:.0f}  bright {bright:.0f}"
                     f"{'  *not exported' if tuner.dirty else ''}", (15, 32),
                     (0, 200, 255) if tuner.auto else (0, 255, 0))
            put_text(vis, f"focus {rb['focus']:g}  exposure {rb['exposure']:g}  gain {rb['gain']:g}  "
                     f"WB {rb['wb_temperature']:g}", (15, 64))
            put_text(vis, "a: auto   l: lock   s: export   q: next   ESC: quit",
                     (15, vis.shape[0] - 18), (255, 255, 255), 0.6)
            scale = float(cfg["params"]["capture"]["preview_width"]) / vis.shape[1]
            cv2.imshow(win, cv2.resize(vis, None, fx=scale, fy=scale))

            key = cv2.waitKey(1) & 0xFF
            if key == ord("a"):
                tuner.enable_auto()
            elif key == ord("l"):
                tuner.lock()
            elif key == ord("s"):
                records.append(export(root, cfg, tuner, frame, sharp, bright, write_config, log))
            elif key in (ord("q"), 27):
                quit_all = key == 27
                if tuner.dirty:
                    log.warning(f"cam{cam_id} 有修改但未匯出（按 s 匯出），這些設定不會保存")
                break
    finally:
        cam.release()
        cv2.destroyAllWindows()
    return records, quit_all


def main() -> None:
    ap = argparse.ArgumentParser(description="相機參數調整：自動對焦 / 曝光 / 白平衡後鎖定並匯出")
    ap.add_argument("--cam", default="all", help="相機 id，例如 0、0,2 或 all（依序調整）")
    ap.add_argument("--no-write-config", action="store_true",
                    help="匯出時不寫入 config/cameras.yaml（只保存紀錄）")
    add_root_arg(ap)
    args = ap.parse_args()
    root = args.root
    cfg = load_configs(root, args.set)
    cam_ids = parse_cam_list(args.cam, cfg["cameras"])

    step_dir = root / "output" / "00_camera_setup"
    run_dir = create_run_dir(step_dir)
    log = setup_logger(run_dir)
    log.info(f"===== 相機參數調整，輸出：{rel(run_dir, root)} =====")
    print(__doc__.split("保存：")[0])
    save_yaml(run_dir / "config_used.yaml", {
        "step": "00_camera_setup", "cameras": cam_ids, "write_config": not args.no_write_config,
        "camera_setup": cfg["params"]["camera_setup"], "cameras_cfg_before": cfg["cameras"],
    })

    results: dict[str, dict] = {}
    for i in cam_ids:
        before = camera_settings(cfg["cameras"], i)
        try:
            records, quit_all = run_camera(root, cfg, i, not args.no_write_config, log)
        except RuntimeError as e:
            log.error(str(e))
            continue
        if records:
            last = records[-1]
            results[f"cam{i}"] = {"settings_before": before, "exported": last["exported_settings"],
                                  "readback": last["settings_readback"], "photo": f"data/camera_setup/{last['file']}",
                                  "written_to_config": last["written_to_config"]}
            if before.get("focus") is not None and last["exported_settings"]["focus"] != before.get("focus") \
                    and (root / "output" / "01_intrinsic" / f"cam{i}" / "latest.txt").exists():
                log.warning(f"cam{i} 對焦由 {before.get('focus')} 改為 {last['exported_settings']['focus']}，"
                            "既有的內參已失效，請重新拍內參照片並標定")
        if quit_all:
            break

    save_yaml(run_dir / "camera_settings.yaml", results)
    if results:
        set_latest(step_dir, run_dir)
        log.info(f"完成，已匯出 {len(results)} 台：{', '.join(results)}（{rel(run_dir / 'camera_settings.yaml', root)}）")
    else:
        log.info("沒有匯出任何相機設定")


def launch_gui() -> None:
    """不加參數執行時開啟的 UI。"""
    from common.gui import Arg, ScriptGUI, cam_choices
    ScriptGUI(
        __file__, "相機參數調整（對焦 / 曝光 / 白平衡）",
        "在 OpenCV 視窗中：a 開啟自動、等畫面清晰穩定 → l 鎖定成手動值 → 滑桿微調 → s 匯出到 cameras.yaml。"
        "q 換下一台，ESC 結束。對焦改變後要重新做內參標定。",
        args=[
            Arg("--cam", "相機", "combo", "all", cam_choices(with_all=True), help="all = 依序調整每一台"),
            Arg("--no-write-config", "不寫入 cameras.yaml（只保存紀錄）", "check", False),
        ],
        sections=[("相機", "cameras", ()), ("調整範圍", "params", ("camera_setup",))],
    ).run()


if __name__ == "__main__":
    if len(sys.argv) == 1:
        launch_gui()
    else:
        main()
