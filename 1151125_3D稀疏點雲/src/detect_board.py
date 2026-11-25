"""步驟 0：標定板自動偵測。從相機畫面或照片判斷 ChArUco 的字典大小、方格數與圖樣版本。

影像無法得知實際長度，方格邊長、ArUco 邊長（實測 mm）沿用 config/board.yaml 的值。

判斷方法：
  1. 用每個字典家族（4X4 / 5X5 / 6X6 / 7X7）最大的字典偵測 ArUco，取偵測到最多 marker 的家族。
     OpenCV 同家族預設字典的前 N 個 marker 完全相同（例如 DICT_5X5_100 = DICT_5X5_1000 的前 100 個），
     所以採用「能涵蓋最大 id 的最小字典」。
  2. 枚舉方格數 (squares_x, squares_y)（偶數列時另試舊版圖樣），依各候選板子的 marker 位置
     與影像中的 marker 角點估計單應性 (homography)，符合平面幾何的 marker 最多者即為答案；
     同分時取 marker 總數最少者，所以**整塊板子都要在畫面內**。
  3. 用 CharucoDetector 以結果實際偵測一次角點，確認可用。

照片與結果都會保存：
  data/board_detect/img_YYYYMMDD_HHMMSS.png (+ .json)    拍到的照片與相機設定
  output/00_board_detect/run_*/                           result.json、markers.npz、detection.png、
                                                          config_used.yaml、run.log
加上 --apply 時，偵測成功會把字典、方格數、圖樣版本寫回 config/board.yaml，
並以 board.yaml 的 name 存成標定板版本（config/board_presets.yaml）。

範例：
  python src/detect_board.py --cam 0 --apply          # 相機預覽，空白鍵偵測，q / ESC 離開
  python src/detect_board.py --image data/intrinsic/cam0/img_xxx.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.io_utils import (add_root_arg, create_run_dir, imread, imwrite, load_configs, now_iso,  # noqa: E402
                             rel, save_board_preset, save_json, save_yaml, set_latest, setup_logger,
                             timestamp)
from common.yaml_edit import update_yaml_file  # noqa: E402

FAMILIES = ["4X4", "5X5", "6X6", "7X7"]
SIZES = [50, 100, 250, 1000]


def get_dict(name: str):
    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))


def detect_markers(gray: np.ndarray, dict_name: str) -> tuple[list[np.ndarray], np.ndarray]:
    """回傳 (marker 角點列表 [(1,4,2)...], ids (N,))。"""
    det = cv2.aruco.ArucoDetector(get_dict(dict_name), cv2.aruco.DetectorParameters())
    corners, ids, _ = det.detectMarkers(gray)
    if ids is None or len(ids) == 0:
        return [], np.zeros((0,), np.int32)
    return list(corners), ids.reshape(-1).astype(np.int32)


def pick_family(gray: np.ndarray, family: str) -> tuple[str | None, list, np.ndarray, dict]:
    """回傳 (家族, marker 角點, ids, 各家族偵測數)。"""
    fams = FAMILIES if family == "auto" else [family]
    counts, best = {}, (None, [], np.zeros((0,), np.int32))
    for fam in fams:
        corners, ids = detect_markers(gray, f"DICT_{fam}_1000")
        counts[fam] = int(len(np.unique(ids)))
        if counts[fam] > len(np.unique(best[2])):
            best = (fam, corners, ids)
    return best[0], best[1], best[2], counts


def score_layouts(corners: list, ids: np.ndarray, dictionary, square: float, marker: float,
                  max_squares: int, dict_size: int) -> list[dict]:
    """枚舉方格數，計算每個候選與影像 marker 的幾何一致性。"""
    img_pts = np.concatenate([c.reshape(4, 2) for c in corners]).astype(np.float64)
    side = np.median([np.linalg.norm(c.reshape(4, 2)[0] - c.reshape(4, 2)[1]) for c in corners])
    thr = max(3.0, 0.15 * float(side))      # 容許的誤差（px），考慮鏡頭畸變
    max_id = int(ids.max())
    results = []
    for sx in range(3, max_squares + 1):
        for sy in range(3, max_squares + 1):
            n_markers = sx * sy // 2
            if n_markers <= max_id or n_markers > dict_size:
                continue
            for legacy in ([False, True] if sy % 2 == 0 else [False]):
                board = cv2.aruco.CharucoBoard((sx, sy), square, marker, dictionary)
                board.setLegacyPattern(legacy)
                obj = np.asarray(board.getObjPoints(), np.float64)       # (n_markers, 4, 3)
                pos = {int(i): k for k, i in enumerate(np.asarray(board.getIds()).reshape(-1))}
                obj_pts = np.concatenate([obj[pos[int(i)], :, :2] for i in ids])
                H, _ = cv2.findHomography(obj_pts, img_pts, cv2.RANSAC, thr)
                if H is None:
                    continue
                proj = cv2.perspectiveTransform(obj_pts.reshape(-1, 1, 2), H).reshape(-1, 2)
                err = np.linalg.norm(proj - img_pts, axis=1).reshape(-1, 4).max(axis=1)
                ok = err < thr
                results.append({
                    "squares_x": sx, "squares_y": sy, "legacy_pattern": legacy, "n_markers": n_markers,
                    "marker_inliers": int(ok.sum()),
                    "median_err_px": float(np.median(err[ok])) if ok.any() else None,
                })
    results.sort(key=lambda r: (-r["marker_inliers"], r["n_markers"], r["legacy_pattern"]))
    return results


def analyze(gray: np.ndarray, board_cfg: dict, family: str, max_squares: int, log) -> dict:
    """偵測標定板參數；回傳結果 dict（ok 為 False 時 reason 說明原因）。"""
    square = float(board_cfg["square_length_mm"])
    marker = float(board_cfg["marker_length_mm"])
    res: dict = {"ok": False, "square_length_mm": square, "marker_length_mm": marker}
    fam, corners, ids, counts = pick_family(gray, family)
    res["markers_per_family"] = counts
    log.info(f"各字典家族偵測到的 marker 數：{counts}")
    uniq = np.unique(ids)
    if fam is None or len(uniq) < 4:
        res["reason"] = "偵測到的 ArUco marker 少於 4 個（板子不在畫面中、太小、反光或模糊）"
        return res
    if len(uniq) < len(ids):
        log.warning(f"有重複的 marker id（{len(ids) - len(uniq)} 個），可能是誤判，只保留第一個")
        keep = [int(np.where(ids == u)[0][0]) for u in uniq]
        corners, ids = [corners[k] for k in keep], ids[keep]
    max_id = int(ids.max())
    size = next(n for n in SIZES if n > max_id)
    dict_name = f"DICT_{fam}_{size}"
    log.info(f"字典家族 {fam}，偵測到 {len(ids)} 個 marker，最大 id {max_id} -> 採用 {dict_name}")

    # 用選定的字典重新偵測（與之後拍照、標定使用的字典一致）
    dictionary = get_dict(dict_name)
    corners, ids = detect_markers(gray, dict_name)
    uniq, first = np.unique(ids, return_index=True)
    corners, ids = [corners[k] for k in first], ids[first]
    res.update({"family": fam, "dictionary": dict_name, "marker_ids": ids, "max_marker_id": int(ids.max())})

    cands = score_layouts(corners, ids, dictionary, square, marker, max_squares, size)
    res["candidates"] = cands[:10]
    if not cands:
        res["reason"] = f"在 {max_squares}x{max_squares} 以內找不到符合的方格數，可加大 --max-squares"
        return res
    best = cands[0]
    log.info("候選方格數（前 5 名）：")
    for c in cands[:5]:
        log.info(f"  {c['squares_x']}x{c['squares_y']}{' 舊版圖樣' if c['legacy_pattern'] else ''}："
                 f"marker 符合 {c['marker_inliers']}/{len(ids)}，marker 總數 {c['n_markers']}")
    if best["marker_inliers"] < max(4, 0.8 * len(ids)):
        res["reason"] = (f"最佳候選只有 {best['marker_inliers']}/{len(ids)} 個 marker 符合，"
                         "可能是 marker 誤判或板子嚴重彎曲；請正對、拉近再試一次")
        return res

    # 用結果實際偵測 ChArUco 角點
    board = cv2.aruco.CharucoBoard((best["squares_x"], best["squares_y"]), square, marker, dictionary)
    board.setLegacyPattern(best["legacy_pattern"])
    ch_c, ch_ids, _, _ = cv2.aruco.CharucoDetector(board).detectBoard(gray)
    n_ch = 0 if ch_ids is None else len(ch_ids)
    n_total = (best["squares_x"] - 1) * (best["squares_y"] - 1)
    res.update({
        "ok": True, "squares_x": best["squares_x"], "squares_y": best["squares_y"],
        "legacy_pattern": best["legacy_pattern"], "n_markers_board": best["n_markers"],
        "n_markers_detected": int(len(ids)), "n_charuco_corners": n_ch, "n_charuco_corners_board": n_total,
        "_draw": (corners, ids, ch_c, ch_ids),
    })
    if len(ids) < best["n_markers"]:
        res["warning"] = (f"板子共有 {best['n_markers']} 個 marker，只偵測到 {len(ids)} 個。"
                          "若板子邊緣沒有完全入鏡，方格數可能被低估，請讓整塊板子入鏡再確認一次")
    tie = [c for c in cands[1:] if c["marker_inliers"] == best["marker_inliers"]
           and c["n_markers"] == best["n_markers"]]
    if tie:
        res["warning"] = (res.get("warning", "") + " 另有同分的候選："
                          + "、".join(f"{c['squares_x']}x{c['squares_y']}" for c in tie)).strip()
    return res


def draw_result(img: np.ndarray, res: dict) -> np.ndarray:
    out = img.copy()
    if "_draw" in res:
        corners, ids, ch_c, ch_ids = res["_draw"]
        cv2.aruco.drawDetectedMarkers(out, corners, ids.reshape(-1, 1))
        if ch_ids is not None and len(ch_ids):
            # OpenCV 5 的 detectBoard 回傳 (N,2)、(N,)，畫圖函式需要 (N,1,2)、(N,1)
            cv2.aruco.drawDetectedCornersCharuco(out, ch_c.reshape(-1, 1, 2).astype(np.float32),
                                                 ch_ids.reshape(-1, 1).astype(np.int32), (0, 255, 0))
        text = (f"{res['dictionary']}  {res['squares_x']}x{res['squares_y']}"
                f"{' legacy' if res['legacy_pattern'] else ''}  corners {res['n_charuco_corners']}"
                f"/{res['n_charuco_corners_board']}")
    else:
        text = "FAILED"
    for color, thick in (((0, 0, 0), 5), ((0, 255, 255), 2)):
        cv2.putText(out, text, (15, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, thick, cv2.LINE_AA)
    return out


def grab_from_camera(root: Path, cfg: dict, cam_id: int, family: str) -> tuple[np.ndarray, dict] | None:
    """相機預覽（即時顯示偵測到的 marker），按空白鍵回傳畫面與相機設定。"""
    from common.camera import Camera
    preview_dict = f"DICT_{'5X5' if family == 'auto' else family}_1000"
    print("[標定板偵測] 讓整塊標定板出現在畫面中（正對、不要反光），按空白鍵偵測，q / ESC 離開")
    cam = Camera(cam_id, cfg["cameras"])
    win = f"detect board cam{cam_id}"
    n = 0
    corners, ids = [], np.zeros((0,), np.int32)
    try:
        while True:
            ok, frame = cam.read()
            if not ok or frame is None:
                print("讀取畫面失敗")
                return None
            n += 1
            if n % int(cfg["params"]["capture"]["detect_interval"]) == 0:
                corners, ids = detect_markers(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), preview_dict)
            vis = frame.copy()
            if len(ids):
                cv2.aruco.drawDetectedMarkers(vis, corners, ids.reshape(-1, 1))
            for color, thick in (((0, 0, 0), 4), ((0, 255, 255), 2)):
                cv2.putText(vis, f"markers {len(ids)} ({preview_dict[5:8]})  SPACE: detect  q/ESC: quit",
                            (15, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, thick, cv2.LINE_AA)
            scale = float(cfg["params"]["capture"]["preview_width"]) / vis.shape[1]
            cv2.imshow(win, cv2.resize(vis, None, fx=scale, fy=scale))
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                return None
            if key == ord(" "):
                return frame, cam.read_settings()
    finally:
        cam.release()
        cv2.destroyAllWindows()


def main() -> None:
    ap = argparse.ArgumentParser(description="步驟 0：標定板自動偵測（字典、方格數、圖樣版本）")
    ap.add_argument("--cam", type=int, help="從這台相機取像（與 --image 二選一）")
    ap.add_argument("--image", type=Path, help="改用既有的照片偵測")
    ap.add_argument("--family", default="auto", choices=["auto"] + FAMILIES, help="ArUco 字典家族，auto = 全部試")
    ap.add_argument("--max-squares", type=int, default=20, help="枚舉方格數的上限")
    ap.add_argument("--apply", action="store_true", help="偵測成功時寫回 config/board.yaml 並存成標定板版本")
    add_root_arg(ap)
    args = ap.parse_args()
    root = args.root
    cfg = load_configs(root, args.set)
    if (args.cam is None) == (args.image is None):
        ap.error("請指定 --cam 或 --image 其中一個")

    # ---- 取得影像（相機拍到的照片一律保存）
    if args.image is not None:
        img_path = args.image if args.image.is_absolute() else (Path.cwd() / args.image)
        img = imread(img_path)
        if img is None:
            print(f"讀不到影像：{args.image}")
            sys.exit(1)
        source = {"type": "image", "path": rel(img_path, root)}
    else:
        got = grab_from_camera(root, cfg, args.cam, args.family)
        if got is None:
            print("未拍照，結束")
            return
        img, settings = got
        out_dir = root / "data" / "board_detect"
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"img_{timestamp()}_cam{args.cam}"
        img_path = out_dir / f"{name}.png"
        imwrite(img_path, img)
        save_json(out_dir / f"{name}.json", {
            "file": img_path.name, "mode": "board_detect", "camera_id": args.cam, "timestamp": now_iso(),
            "image_size": [img.shape[1], img.shape[0]], "settings_readback": settings,
        })
        source = {"type": "camera", "camera_id": args.cam, "path": rel(img_path, root)}

    step_dir = root / "output" / "00_board_detect"
    run_dir = create_run_dir(step_dir)
    log = setup_logger(run_dir)
    log.info(f"===== 標定板偵測，影像：{source['path']}，輸出：{rel(run_dir, root)} =====")
    save_yaml(run_dir / "config_used.yaml", {
        "step": "00_board_detect", "source": source, "family": args.family,
        "max_squares": args.max_squares, "apply": args.apply, "board_before": cfg["board"],
    })

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    res = analyze(gray, cfg["board"], args.family, args.max_squares, log)
    imwrite(run_dir / "detection.png", draw_result(img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR), res))
    draw = res.pop("_draw", None)
    if draw is not None:
        corners, ids, ch_c, ch_ids = draw
        np.savez(run_dir / "markers.npz",
                 marker_corners=np.array([c.reshape(4, 2) for c in corners]), marker_ids=ids,
                 charuco_corners=np.zeros((0, 2)) if ch_c is None else ch_c.reshape(-1, 2),
                 charuco_ids=np.zeros((0,), int) if ch_ids is None else ch_ids.reshape(-1))
    res["source"] = source

    if not res["ok"]:
        log.error(f"偵測失敗：{res['reason']}")
        res["applied"] = False
        save_json(run_dir / "result.json", res)
        sys.exit(1)

    log.info(f"結果：{res['dictionary']}，方格 {res['squares_x']} x {res['squares_y']}，"
             f"{'舊版' if res['legacy_pattern'] else '新版'}圖樣，ChArUco 角點 "
             f"{res['n_charuco_corners']}/{res['n_charuco_corners_board']}")
    if res.get("warning"):
        log.warning(res["warning"])

    res["applied"] = False
    if args.apply:
        updates = {("dictionary",): res["dictionary"], ("squares_x",): res["squares_x"],
                   ("squares_y",): res["squares_y"], ("legacy_pattern",): res["legacy_pattern"]}
        missing = update_yaml_file(root / "config" / "board.yaml", updates)
        if missing:
            log.error(f"board.yaml 中找不到 {missing}，請手動修改")
        else:
            res["applied"] = True
            board = dict(cfg["board"], **{k[0]: v for k, v in updates.items()})
            log.info("已寫回 config/board.yaml")
            if board.get("name"):
                save_board_preset(root, str(board["name"]), board)
                log.info(f"已存成標定板版本「{board['name']}」（config/board_presets.yaml）")
    else:
        log.info("未寫回 board.yaml（要寫回請勾選「寫回 board.yaml」或加 --apply）")
    save_json(run_dir / "result.json", res)
    set_latest(step_dir, run_dir)


def launch_gui() -> None:
    """不加參數執行時開啟的 UI。"""
    from common.gui import Arg, ScriptGUI, cam_choices
    ScriptGUI(
        __file__, "步驟 0：標定板自動偵測",
        "判斷 ChArUco 的字典大小、方格數與圖樣版本。方格 / ArUco 邊長無法從影像得知，請在「標定板」分頁填實測值。"
        "相機模式：讓整塊板子正對鏡頭入鏡，在 OpenCV 視窗按空白鍵偵測。",
        args=[
            Arg("source", "影像來源", "combo", "camera", editable=False,
                choices=[("camera", "camera — 相機即時取像"), ("image", "image — 既有照片")], ui_only=True),
            Arg("--cam", "相機 id", "combo", "0", cam_choices(), editable=False, when={"source": ["camera"]}),
            Arg("--image", "照片", "file", "", when={"source": ["image"]}),
            Arg("--family", "字典家族", "combo", "auto", ["auto"] + FAMILIES, editable=False,
                help="auto = 4X4 / 5X5 / 6X6 / 7X7 全部試"),
            Arg("--max-squares", "方格數上限", "entry", "20"),
            Arg("--apply", "成功時寫回 board.yaml", "check", True, help="並以 name 存成標定板版本"),
        ],
        sections=[("標定板", "board", ()), ("相機", "cameras", ())],
    ).run()


if __name__ == "__main__":
    if len(sys.argv) == 1:
        launch_gui()
    else:
        main()
