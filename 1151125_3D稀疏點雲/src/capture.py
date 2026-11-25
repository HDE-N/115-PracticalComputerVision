"""拍照程式（步驟 0、1-1、1-2、2-1）。

模式：
  intrinsic  單台相機拍內參標定照片  -> data/intrinsic/cam{i}/img_YYYYMMDD_HHMMSS_xxx.png (+ .json)
  extrinsic  多台同時拍外參標定照片  -> data/extrinsic/frame_xxx/cam{i}.png (+ frame.json)
  object     多台同時拍物品照片      -> data/object/shot_xxx/cam{i}.png (+ shot.json)

操作：空白鍵拍照，q 或 ESC 結束。
每次按下空白鍵拍到的照片都會保存（即使偵測不到角點），之後的步驟會自動判斷是否採用。

範例：
  python src/capture.py intrinsic --cam 0
  python src/capture.py extrinsic
  python src/capture.py object --cams 0,1,2,3
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.board import CharucoFinder  # noqa: E402
from common.camera import Camera, grab_all, sharpness  # noqa: E402
from common.io_utils import (add_root_arg, imread, imwrite, list_images, load_configs,  # noqa: E402
                             now_iso, parse_cam_list, save_json, timestamp)

GUIDE = {
    "intrinsic": """
[內參標定拍攝說明] 每台相機拍 20–30 張
  - 標定板要出現在畫面的四角、邊緣與中央（畫面上的綠點是已拍到的角點位置，盡量讓綠點佈滿畫面）
  - 變換距離與傾斜角度（約 ±45 度），不要每張都正對鏡頭
  - 拍照時手要穩，避免模糊；畫面上 corners 數量越多越好（至少 6 個）
""",
    "extrinsic": """
[外參標定拍攝說明] 4 台相機同時拍 10–20 組
  - 相機固定好之後不可再移動
  - 把標定板放在多台相機都看得到的位置，每組換位置與角度
  - 至少要讓相鄰相機（0-1、1-2、2-3、3-0）同時看到標定板；畫面上會顯示每台相機的 corners 數
""",
    "object": """
[物品拍攝說明]
  - 相機位置與設定不可改變（與外參標定時相同）
  - 物品要有豐富紋理，光線均勻、避免反光
  - 物品與相機都靜止時再按空白鍵
""",
}


def next_index(names: list[str], pattern: str) -> int:
    idx = [int(m.group(1)) for n in names if (m := re.search(pattern, n))]
    return max(idx) + 1 if idx else 1


def put_text(img, text, org, color=(0, 255, 255), scale=0.7):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2, cv2.LINE_AA)


# --------------------------------------------------------------------- 單相機（內參）
def capture_intrinsic(root: Path, cfg: dict, cam_id: int) -> None:
    params = cfg["params"]
    finder = CharucoFinder(cfg["board"])
    min_corners = int(params["intrinsic"]["min_corners"])
    out_dir = root / "data" / "intrinsic" / f"cam{cam_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    existing = list_images(out_dir)
    seq = next_index([p.name for p in existing], r"_(\d+)\.png$")

    print(GUIDE["intrinsic"])
    cam = Camera(cam_id, cfg["cameras"])
    settings = cam.read_settings()
    print(f"cam{cam_id} 實際設定：{settings}")

    # 已拍照片的角點位置（顯示涵蓋範圍）
    coverage = []
    for p in existing:
        img = imread(p)
        if img is not None:
            c, _ = finder.detect(img)
            coverage.append(c)
    print(f"資料夾內已有 {len(existing)} 張照片，新照片從編號 {seq:03d} 開始")

    win = f"intrinsic cam{cam_id}"
    frame_count, corners, ids = 0, np.zeros((0, 2)), np.zeros((0,), int)
    saved_now = 0
    try:
        while True:
            ok, frame = cam.read()
            if not ok or frame is None:
                print("讀取畫面失敗")
                break
            frame_count += 1
            if frame_count % int(params["capture"]["detect_interval"]) == 0:
                corners, ids = finder.detect(frame)

            vis = finder.draw(frame, corners, ids)
            for c in coverage:
                for x, y in c.astype(int):
                    cv2.circle(vis, (x, y), 3, (0, 200, 0), -1)
            color = (0, 255, 0) if len(ids) >= min_corners else (0, 0, 255)
            put_text(vis, f"cam{cam_id} corners {len(ids)}/{finder.max_corners}", (15, 35), color)
            put_text(vis, f"saved {len(existing) + saved_now}  sharp {sharpness(frame):.0f}", (15, 70))
            put_text(vis, "SPACE: capture   q/ESC: quit", (15, vis.shape[0] - 20), (255, 255, 255), 0.6)
            scale = float(params["capture"]["preview_width"]) / vis.shape[1]
            cv2.imshow(win, cv2.resize(vis, None, fx=scale, fy=scale))

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord(" "):
                c_full, id_full = finder.detect(frame)   # 用完整解析度、同一張畫面重新偵測
                name = f"img_{timestamp()}_{seq:03d}"
                imwrite(out_dir / f"{name}.png", frame)
                meta = {
                    "file": f"{name}.png",
                    "mode": "intrinsic",
                    "camera_id": cam_id,
                    "timestamp": now_iso(),
                    "image_size": [frame.shape[1], frame.shape[0]],
                    "n_corners": int(len(id_full)),
                    "sharpness": sharpness(frame),
                    "settings_readback": cam.read_settings(),
                }
                save_json(out_dir / f"{name}.json", meta)
                coverage.append(c_full)
                seq += 1
                saved_now += 1
                msg = f"已保存 {name}.png（corners {len(id_full)}）"
                if len(id_full) < min_corners:
                    msg += f"  [警告] 角點少於 {min_corners} 個，照片仍保存，標定時會自動剔除"
                print(msg)
    finally:
        cam.release()
        cv2.destroyAllWindows()
    print(f"本次共拍 {saved_now} 張，cam{cam_id} 資料夾內共 {len(existing) + saved_now} 張")


# --------------------------------------------------------------------- 多相機（外參、物品）
def mosaic(images: list[np.ndarray], width: int) -> np.ndarray:
    n = len(images)
    cols = 2 if n > 1 else 1
    rows = int(np.ceil(n / cols))
    h0, w0 = images[0].shape[:2]
    cell_w = width // cols
    cell_h = int(h0 * cell_w / w0)
    canvas = np.zeros((rows * cell_h, cols * cell_w, 3), np.uint8)
    for k, img in enumerate(images):
        r, c = divmod(k, cols)
        canvas[r * cell_h:(r + 1) * cell_h, c * cell_w:(c + 1) * cell_w] = cv2.resize(img, (cell_w, cell_h))
    return canvas


def capture_multi(root: Path, cfg: dict, cam_ids: list[int], mode: str) -> None:
    params = cfg["params"]
    detect = mode == "extrinsic"
    finder = CharucoFinder(cfg["board"]) if detect else None
    min_corners = int(params["extrinsic"]["min_corners"])
    prefix = "frame" if mode == "extrinsic" else "shot"
    base_dir = root / "data" / mode
    base_dir.mkdir(parents=True, exist_ok=True)
    seq = next_index([p.name for p in base_dir.iterdir() if p.is_dir()], rf"^{prefix}_(\d+)$")
    sync_warn = float(params["capture"]["sync_warn_ms"])

    print(GUIDE[mode])
    cams = []
    try:
        for i in cam_ids:
            cams.append(Camera(i, cfg["cameras"]))
            print(f"cam{i} 實際設定：{cams[-1].read_settings()}")
    except RuntimeError:
        for c in cams:
            c.release()
        raise

    win = f"{mode} cams {cam_ids}"
    loop = 0
    det = {i: (np.zeros((0, 2)), np.zeros((0,), int)) for i in cam_ids}
    saved_now = 0
    try:
        while True:
            frames, times = grab_all(cams)
            if any(f is None for f in frames):
                bad = [i for i, f in zip(cam_ids, frames) if f is None]
                print(f"相機 {bad} 讀取畫面失敗")
                break
            loop += 1
            spread = (max(times) - min(times)) * 1000.0
            if detect and loop % int(params["capture"]["detect_interval"]) == 0:
                for i, f in zip(cam_ids, frames):
                    det[i] = finder.detect(f)

            vis_list = []
            for i, f in zip(cam_ids, frames):
                v = finder.draw(f, *det[i]) if detect else f.copy()
                if detect:
                    n = len(det[i][1])
                    color = (0, 255, 0) if n >= min_corners else (0, 0, 255)
                    put_text(v, f"cam{i} corners {n}", (15, 45), color, 1.2)
                else:
                    put_text(v, f"cam{i} sharp {sharpness(f):.0f}", (15, 45), (0, 255, 255), 1.2)
                vis_list.append(v)
            vis = mosaic(vis_list, int(params["capture"]["preview_width"]))
            put_text(vis, f"{prefix}_{seq:03d}  saved {saved_now}  sync {spread:.0f} ms   SPACE: capture  q/ESC: quit",
                     (10, vis.shape[0] - 12), (255, 255, 255), 0.55)
            cv2.imshow(win, vis)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord(" "):
                folder = base_dir / f"{prefix}_{seq:03d}"
                folder.mkdir(parents=True, exist_ok=False)
                t0 = min(times)
                per_cam = []
                for i, f, t, c in zip(cam_ids, frames, times, cams):
                    imwrite(folder / f"cam{i}.png", f)
                    entry = {
                        "camera_id": i,
                        "file": f"cam{i}.png",
                        "grab_offset_ms": (t - t0) * 1000.0,
                        "image_size": [f.shape[1], f.shape[0]],
                        "sharpness": sharpness(f),
                        "settings_readback": c.read_settings(),
                    }
                    if detect:
                        _, ids_full = finder.detect(f)
                        entry["n_corners"] = int(len(ids_full))
                    per_cam.append(entry)
                meta = {
                    "mode": mode,
                    "name": folder.name,
                    "timestamp": now_iso(),
                    "sync_spread_ms": spread,
                    "cameras": per_cam,
                }
                save_json(folder / f"{prefix}.json", meta)
                seq += 1
                saved_now += 1
                msg = f"已保存 {folder.name}（擷取時間差 {spread:.1f} ms"
                if detect:
                    msg += "，corners " + ", ".join(f"cam{e['camera_id']}={e['n_corners']}" for e in per_cam)
                msg += "）"
                if spread > sync_warn:
                    msg += f"  [警告] 擷取時間差超過 {sync_warn:.0f} ms，拍攝時請保持標定板/物品靜止"
                print(msg)
    finally:
        for c in cams:
            c.release()
        cv2.destroyAllWindows()
    print(f"本次共拍 {saved_now} 組，存於 {base_dir}")


def main() -> None:
    ap = argparse.ArgumentParser(description="拍照程式：內參 / 外參 / 物品")
    ap.add_argument("mode", choices=["intrinsic", "extrinsic", "object"])
    ap.add_argument("--cam", type=int, help="intrinsic 模式：要拍的相機 id")
    ap.add_argument("--cams", default="all", help="extrinsic / object 模式：相機 id 列表，例如 0,1,2,3")
    add_root_arg(ap)
    args = ap.parse_args()
    cfg = load_configs(args.root, args.set)

    if args.mode == "intrinsic":
        if args.cam is None:
            ap.error("intrinsic 模式需要 --cam")
        capture_intrinsic(args.root, cfg, args.cam)
    else:
        capture_multi(args.root, cfg, parse_cam_list(args.cams, cfg["cameras"]), args.mode)


def launch_gui() -> None:
    """不加參數執行時開啟的 UI。"""
    from common.gui import Arg, ScriptGUI, cam_choices
    ScriptGUI(
        __file__, "拍照", "空白鍵拍照，q / ESC 結束（在 OpenCV 預覽視窗中按）。每按一次都會存檔，照片只增不刪。"
        "標定板參數錯誤時預覽畫面會顯示 corners 0。",
        args=[
            Arg("mode", "模式", "combo", "intrinsic", editable=False, choices=[
                ("intrinsic", "intrinsic — 內參（單台，每台 20–30 張）"),
                ("extrinsic", "extrinsic — 外參（多台同時，10–20 組）"),
                ("object", "object — 物品（多台同時）")]),
            Arg("--cam", "相機 id", "combo", "0", cam_choices(), editable=False,
                help="內參模式：要拍的相機", when={"mode": ["intrinsic"]}),
            Arg("--cams", "相機列表", "entry", "all",
                help="外參 / 物品模式：例如 0,1,2,3 或 all", when={"mode": ["extrinsic", "object"]}),
        ],
        sections=[("標定板", "board", ()), ("相機", "cameras", ()), ("拍照參數", "params", ("capture",))],
    ).run()


if __name__ == "__main__":
    if len(sys.argv) == 1:
        launch_gui()
    else:
        main()
