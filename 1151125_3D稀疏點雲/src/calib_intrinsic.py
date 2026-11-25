"""步驟 1-1：每台相機各自做內參標定（張正友標定法，cv2.calibrateCamera）。

輸入：data/intrinsic/cam{i}/*.png
輸出：output/01_intrinsic/cam{i}/run_YYYYMMDD_HHMMSS/
  intrinsics.yaml        K、dist、影像尺寸、RMS
  detections.npz         每張照片的 ChArUco 角點與 id
  per_image_errors.csv   每張照片的角點數、重投影誤差、是否採用、剔除原因
  rvecs_tvecs.npz        每張採用照片的標定板位姿（mm）
  calib_history.json     逐張剔除照片的過程（每一輪的 RMS）
  summary.json           結果摘要與門檻判定
  undistort_preview/     去畸變前後對照圖
  config_used.yaml、run.log

通過門檻（RMS、最少照片數）才會更新 latest.txt；未通過時請檢查原因，或用 --force-latest 強制採用。

範例：
  python src/calib_intrinsic.py --cam 0
  python src/calib_intrinsic.py --cam all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.board import CharucoFinder, is_degenerate  # noqa: E402
from common.calib_io import write_intrinsics  # noqa: E402
from common.io_utils import (add_root_arg, create_run_dir, imread, imwrite, list_images,  # noqa: E402
                             load_configs, parse_cam_list, rel, save_csv, save_json, save_yaml,
                             set_latest, setup_logger)


def coverage_ratio(corners_list: list[np.ndarray], image_size, grid) -> float:
    """角點涵蓋率：把畫面切成 grid 格，計算有角點落入的格子比例。"""
    gw, gh = int(grid[0]), int(grid[1])
    hit = np.zeros((gh, gw), bool)
    w, h = image_size
    for c in corners_list:
        if len(c) == 0:
            continue
        gx = np.clip((c[:, 0] / w * gw).astype(int), 0, gw - 1)
        gy = np.clip((c[:, 1] / h * gh).astype(int), 0, gh - 1)
        hit[gy, gx] = True
    return float(hit.mean())


def calibrate(obj_list, img_list, image_size, flags):
    """回傳 (rms, K, dist, rvecs, tvecs, std_intrinsics, per_view_errors)。"""
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_COUNT, 100, 1e-9)
    rms, K, dist, rvecs, tvecs, std_in, _std_ex, per_view = cv2.calibrateCameraExtended(
        [o.reshape(-1, 1, 3).astype(np.float32) for o in obj_list],
        [p.reshape(-1, 1, 2).astype(np.float32) for p in img_list],
        tuple(image_size), None, None, flags=flags, criteria=criteria)
    return rms, K, dist, rvecs, tvecs, std_in.reshape(-1), per_view.reshape(-1)


def side_by_side(a: np.ndarray, b: np.ndarray, max_width: int = 1600) -> np.ndarray:
    out = np.hstack([a, b])
    for img, x, label in ((out, 15, "original"), (out, a.shape[1] + 15, "undistorted")):
        cv2.putText(img, label, (x, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 5, cv2.LINE_AA)
        cv2.putText(img, label, (x, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 2, cv2.LINE_AA)
    if out.shape[1] > max_width:
        s = max_width / out.shape[1]
        out = cv2.resize(out, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    return out


def run_camera(root: Path, cfg: dict, cam_id: int, force_latest: bool) -> dict:
    p = cfg["params"]["intrinsic"]
    img_dir = root / "data" / "intrinsic" / f"cam{cam_id}"
    step_dir = root / "output" / "01_intrinsic" / f"cam{cam_id}"
    run_dir = create_run_dir(step_dir)
    log = setup_logger(run_dir)
    log.info(f"===== cam{cam_id} 內參標定，輸出：{rel(run_dir, root)} =====")

    save_yaml(run_dir / "config_used.yaml", {
        "step": "01_intrinsic", "camera_id": cam_id, "input_dir": rel(img_dir, root),
        "board": cfg["board"], "intrinsic": p,
    })

    images = list_images(img_dir)
    if not images:
        log.error(f"{rel(img_dir, root)} 沒有照片，請先執行 capture.py intrinsic --cam {cam_id}")
        return {"camera_id": cam_id, "passed": False, "error": "no images"}

    finder = CharucoFinder(cfg["board"])
    flags = 0
    for name in p.get("flags") or []:
        flags |= int(getattr(cv2, name))

    # ---- 1. 偵測角點
    records = []      # 每張照片一筆
    det_img, det_ids, det_corners = [], [], []
    image_size = None
    for k, path in enumerate(images):
        img = imread(path)
        rec = {"image": path.name, "n_corners": 0, "used": False, "reproj_error_px": None, "reject_reason": ""}
        records.append(rec)
        if img is None:
            rec["reject_reason"] = "無法讀取"
            continue
        size = (img.shape[1], img.shape[0])
        if image_size is None:
            image_size = size
        corners, ids = finder.detect(img)
        rec["n_corners"] = int(len(ids))
        det_img += [k] * len(ids)
        det_ids += ids.tolist()
        det_corners += corners.tolist()
        if size != image_size:
            rec["reject_reason"] = f"解析度不符 {size}，應為 {image_size}"
        elif len(ids) < int(p["min_corners"]):
            rec["reject_reason"] = f"角點不足（< {p['min_corners']}）"
        elif is_degenerate(finder.object_points(ids)):
            rec["reject_reason"] = "角點近乎共線"
        else:
            rec["_corners"], rec["_ids"] = corners, ids

    np.savez(run_dir / "detections.npz",
             image_names=np.array([r["image"] for r in records]),
             image_index=np.array(det_img, np.int32),
             ids=np.array(det_ids, np.int32),
             corners=np.array(det_corners, np.float64).reshape(-1, 2),
             image_size=np.array(image_size or (0, 0)))

    valid = [r for r in records if "_ids" in r]
    log.info(f"照片 {len(images)} 張，可用於標定 {len(valid)} 張")
    if len(valid) < 3:
        log.error("可用照片少於 3 張，無法標定。請補拍（標定板要清楚、角點數足夠）。")
        save_csv(run_dir / "per_image_errors.csv", [{k: v for k, v in r.items() if not k.startswith("_")} for r in records])
        return {"camera_id": cam_id, "passed": False, "error": "too few images", "run": run_dir.name}

    # ---- 2. 標定；RMS 超過門檻時逐張剔除誤差最大的照片後重算
    used = list(valid)
    history = []
    max_reject = int(np.floor(len(valid) * float(p["max_reject_ratio"])))
    while True:
        obj = [finder.object_points(r["_ids"]) for r in used]
        imgp = [r["_corners"] for r in used]
        rms, K, dist, rvecs, tvecs, std_in, per_view = calibrate(obj, imgp, image_size, flags)
        history.append({"n_images": len(used), "rms_px": rms,
                        "worst_image": used[int(np.argmax(per_view))]["image"],
                        "worst_error_px": float(per_view.max())})
        log.info(f"  使用 {len(used)} 張：RMS = {rms:.4f} px（最差 {history[-1]['worst_image']} "
                 f"{per_view.max():.3f} px）")
        n_rejected = len(valid) - len(used)
        if (rms <= float(p["max_rms_px"]) or not p.get("reject_worst", True)
                or n_rejected >= max_reject or len(used) - 1 < int(p["min_images"])):
            break
        worst = used.pop(int(np.argmax(per_view)))
        worst["reject_reason"] = f"誤差最大而剔除（第 {len(history)} 輪，{per_view.max():.3f} px）"

    for r, e in zip(used, per_view):
        r["used"], r["reproj_error_px"] = True, float(e)

    # 被剔除的照片也計算在最終參數下的重投影誤差，方便檢查
    for r in valid:
        if not r["used"]:
            ok, rv, tv = cv2.solvePnP(finder.object_points(r["_ids"]), r["_corners"], K, dist)
            if ok:
                proj, _ = cv2.projectPoints(finder.object_points(r["_ids"]), rv, tv, K, dist)
                r["reproj_error_px"] = float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - r["_corners"]) ** 2, axis=1))))

    coverage = coverage_ratio([r["_corners"] for r in used], image_size, p["coverage_grid"])

    # ---- 3. 存檔
    write_intrinsics(run_dir / "intrinsics.yaml", K, dist, image_size, rms, len(used), extra={
        "std_dev_intrinsics": std_in.reshape(-1, 1),
        "coverage": coverage,
        "camera_id": cam_id,
    })
    save_csv(run_dir / "per_image_errors.csv",
             [{k: v for k, v in r.items() if not k.startswith("_")} for r in records],
             ["image", "n_corners", "used", "reproj_error_px", "reject_reason"])
    np.savez(run_dir / "rvecs_tvecs.npz",
             image_names=np.array([r["image"] for r in used]),
             rvecs=np.array([v.reshape(3) for v in rvecs]),
             tvecs_mm=np.array([v.reshape(3) for v in tvecs]))
    save_json(run_dir / "calib_history.json", history)

    prev_dir = run_dir / "undistort_preview"
    prev_dir.mkdir()
    n_prev = min(int(p["preview_count"]), len(used))
    for k in np.linspace(0, len(used) - 1, n_prev).round().astype(int) if n_prev else []:
        img = imread(img_dir / used[k]["image"])
        und = cv2.undistort(img, K, dist)
        imwrite(prev_dir / f"{Path(used[k]['image']).stem}_compare.png", side_by_side(img, und))

    # ---- 4. 門檻判定
    problems = []
    if rms > float(p["max_rms_px"]):
        problems.append(f"RMS {rms:.3f} px 超過門檻 {p['max_rms_px']} px")
    if len(used) < int(p["min_images"]):
        problems.append(f"採用照片 {len(used)} 張少於 {p['min_images']} 張")
    warnings = []
    if coverage < float(p["min_coverage"]):
        warnings.append(f"角點涵蓋率 {coverage:.0%} 偏低，建議補拍標定板位於畫面四角、邊緣的照片")
    passed = not problems

    summary = {
        "camera_id": cam_id, "run": run_dir.name, "passed": passed,
        "rms_px": rms, "n_images_total": len(images), "n_images_valid": len(valid), "n_images_used": len(used),
        "image_size": image_size, "coverage": coverage,
        "fx": K[0, 0], "fy": K[1, 1], "cx": K[0, 2], "cy": K[1, 2], "dist": dist.reshape(-1),
        "problems": problems, "warnings": warnings,
    }
    save_json(run_dir / "summary.json", summary)

    log.info(f"K =\n{np.array2string(K, precision=3, suppress_small=True)}")
    log.info(f"dist = {np.array2string(dist.reshape(-1), precision=5)}")
    log.info(f"RMS = {rms:.4f} px，採用 {len(used)}/{len(images)} 張，角點涵蓋率 {coverage:.0%}")
    for w in warnings:
        log.warning(w)
    if passed or force_latest:
        set_latest(step_dir, run_dir)
        log.info(f"已更新 {rel(step_dir / 'latest.txt', root)} -> {run_dir.name}"
                 + ("（--force-latest 強制採用）" if not passed else ""))
    else:
        for pr in problems:
            log.warning(f"未通過門檻：{pr}")
        log.warning("latest.txt 未更新。常見原因：照片模糊、角度不夠多樣、標定板不平或 board.yaml 尺寸錯誤。")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="步驟 1-1：內參標定")
    ap.add_argument("--cam", default="all", help="相機 id，例如 0、0,2 或 all")
    ap.add_argument("--force-latest", action="store_true", help="未通過門檻也更新 latest.txt")
    add_root_arg(ap)
    args = ap.parse_args()
    cfg = load_configs(args.root)

    results = [run_camera(args.root, cfg, i, args.force_latest)
               for i in parse_cam_list(args.cam, cfg["cameras"])]
    print("\n===== 內參標定結果 =====")
    for r in results:
        if "rms_px" in r:
            print(f"cam{r['camera_id']}: RMS {r['rms_px']:.4f} px, 採用 {r['n_images_used']}/{r['n_images_total']} 張, "
                  f"{'通過' if r['passed'] else '未通過'}  ({r['run']})")
        else:
            print(f"cam{r['camera_id']}: 失敗（{r.get('error')}）")


if __name__ == "__main__":
    main()
