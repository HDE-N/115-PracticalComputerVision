"""步驟 2-2：SIFT 特徵擷取與配對，並用已知外參的對極約束篩除錯誤配對。

流程（每一對相機）：
  1. SIFT 特徵擷取（Day05）
  2. knnMatch + Lowe ratio test（+ 雙向檢查）
  3. 由標定結果算出 F_ij = K_j^-T [t_ij]x R_ij K_i^-1，丟掉離對極線太遠的配對（Day04）
  4. （比較用）以 RANSAC / LMedS 從配對估計 F（Day06 p22），記錄與標定 F 的差異

輸入：data/object/shot_xxx/cam{i}.png（可選 mask_cam{i}.png：白色 = 物品區域）
      output/02_extrinsic/latest.txt 指向的外參
輸出：output/03_features/shot_xxx/run_YYYYMMDD_HHMMSS/
  keypoints_cam{i}.npz   關鍵點座標（原始 / 去畸變）、尺度、方向、描述子
  matches_{i}{j}.npz     原始配對與各階段的篩選結果
  F_{i}{j}.yaml          標定 F、RANSAC F、LMedS F
  match_stats.json       各階段配對數量
  match_vis/             最終配對視覺化
  sources.json、config_used.yaml、run.log

範例：
  python src/match_features.py                # 處理最新一組 shot
  python src/match_features.py --shot shot_002
  python src/match_features.py --shot all
"""
from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.calib_io import load_extrinsics  # noqa: E402
from common.geometry import epipolar_distance, fundamental_from_calib, undistort_pixels  # noqa: E402
from common.io_utils import (add_root_arg, create_run_dir, fs_write, imread, imwrite,  # noqa: E402
                             load_configs, rel, resolve_run, save_json, save_yaml, set_latest,
                             setup_logger)


def camera_pairs(spec, cam_ids: list[int]) -> list[tuple[int, int]]:
    if spec == "all":
        return list(itertools.combinations(cam_ids, 2))
    if spec == "adjacent":
        n = len(cam_ids)
        pairs = [(cam_ids[k], cam_ids[(k + 1) % n]) for k in range(n)] if n > 2 else [(cam_ids[0], cam_ids[1])]
        return sorted({tuple(sorted(pr)) for pr in pairs})
    return [tuple(int(v) for v in pr) for pr in spec]


def list_shots(root: Path, shot: str | None) -> list[Path]:
    shots = sorted(d for d in (root / "data" / "object").glob("shot_*") if d.is_dir())
    if not shots:
        raise FileNotFoundError("data/object/ 沒有照片，請先執行 capture.py object")
    if shot is None:
        return [shots[-1]]
    if shot == "all":
        return shots
    d = root / "data" / "object" / shot
    if not d.is_dir():
        raise FileNotFoundError(f"找不到 {rel(d, root)}")
    return [d]


def match_pair(des_i, des_j, ratio: float, mutual: bool):
    """回傳 raw（每個 i 特徵的最佳配對）與 ratio / mutual 的布林遮罩。"""
    bf = cv2.BFMatcher(cv2.NORM_L2)
    knn = bf.knnMatch(des_i, des_j, k=2)
    q, t, d1, d2 = [], [], [], []
    for m in knn:
        if len(m) == 0:
            continue
        q.append(m[0].queryIdx)
        t.append(m[0].trainIdx)
        d1.append(m[0].distance)
        d2.append(m[1].distance if len(m) > 1 else np.inf)
    q, t = np.array(q, np.int32), np.array(t, np.int32)
    d1, d2 = np.array(d1, np.float32), np.array(d2, np.float32)
    ratio_ok = d1 < ratio * d2
    if mutual and len(q):
        back = bf.match(des_j, des_i)
        best_back = np.full(len(des_j), -1, np.int64)
        for m in back:
            best_back[m.queryIdx] = m.trainIdx
        mutual_ok = best_back[t] == q
    else:
        mutual_ok = np.ones(len(q), bool)
    return q, t, d1, d2, ratio_ok, mutual_ok


def estimate_F(pts_i, pts_j, method, thr, conf):
    if len(pts_i) < 8:
        return None, np.zeros(len(pts_i), bool)
    F, mask = cv2.findFundamentalMat(pts_i, pts_j, method, thr, conf)
    if F is None or F.shape != (3, 3) or mask is None:
        return None, np.zeros(len(pts_i), bool)
    return F / np.linalg.norm(F), mask.reshape(-1).astype(bool)


def draw_matches(img_i, img_j, pts_i, pts_j, max_n: int, seed: int = 0) -> np.ndarray:
    n = len(pts_i)
    sel = np.random.default_rng(seed).permutation(n)[:max_n] if n > max_n else np.arange(n)
    kp_i = [cv2.KeyPoint(float(x), float(y), 5) for x, y in pts_i[sel]]
    kp_j = [cv2.KeyPoint(float(x), float(y), 5) for x, y in pts_j[sel]]
    dm = [cv2.DMatch(k, k, 0) for k in range(len(sel))]
    vis = cv2.drawMatches(img_i, kp_i, img_j, kp_j, dm, None,
                          flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS)
    if vis.shape[1] > 2400:
        s = 2400 / vis.shape[1]
        vis = cv2.resize(vis, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    return vis


def process_shot(root: Path, cfg: dict, shot_dir: Path, ext_dir: Path, force_latest: bool) -> dict:
    p = cfg["params"]["features"]
    step_dir = root / "output" / "03_features" / shot_dir.name
    run_dir = create_run_dir(step_dir)
    log = setup_logger(run_dir)
    log.info(f"===== 特徵配對 {shot_dir.name}，輸出：{rel(run_dir, root)} =====")

    cams = load_extrinsics(ext_dir / "extrinsics.yaml")
    sources = {"shot": rel(shot_dir, root), "extrinsics": rel(ext_dir / "extrinsics.yaml", root)}
    save_json(run_dir / "sources.json", sources)
    save_yaml(run_dir / "config_used.yaml", {"step": "03_features", **sources, "features": p})
    log.info(f"外參：{sources['extrinsics']}")

    # ---- 1. SIFT
    s = p["sift"]
    sift = cv2.SIFT_create(nfeatures=int(s["nfeatures"]), contrastThreshold=float(s["contrast_threshold"]),
                           edgeThreshold=float(s["edge_threshold"]), sigma=float(s["sigma"]))
    images, kps = {}, {}
    for i in sorted(cams):
        path = shot_dir / f"cam{i}.png"
        img = imread(path) if path.exists() else None
        if img is None:
            log.warning(f"缺少 {rel(path, root)}，略過 cam{i}")
            continue
        if (img.shape[1], img.shape[0]) != cams[i]["image_size"]:
            log.warning(f"cam{i} 照片解析度與標定時不同，略過")
            continue
        mask_path = shot_dir / f"mask_cam{i}.png"
        mask = imread(mask_path, cv2.IMREAD_GRAYSCALE) if mask_path.exists() else None
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        kp, des = sift.detectAndCompute(gray, mask)
        pts = np.array([k.pt for k in kp], np.float64).reshape(-1, 2)
        pts_u = undistort_pixels(pts, cams[i]["K"], cams[i]["dist"])
        des = des if des is not None else np.zeros((0, 128), np.float32)
        np.savez(run_dir / f"keypoints_cam{i}.npz",
                 pts=pts, pts_undist=pts_u,
                 size=np.array([k.size for k in kp], np.float32),
                 angle=np.array([k.angle for k in kp], np.float32),
                 response=np.array([k.response for k in kp], np.float32),
                 octave=np.array([k.octave for k in kp], np.int32),
                 descriptors=des,
                 used_mask=mask is not None)
        images[i], kps[i] = img, {"pts": pts, "pts_u": pts_u, "des": des}
        log.info(f"cam{i}：{len(kp)} 個 SIFT 特徵" + ("（使用遮罩）" if mask is not None else ""))

    # ---- 2–4. 配對、對極約束、RANSAC 比較
    vis_dir = run_dir / "match_vis"
    vis_dir.mkdir()
    stats = {"shot": shot_dir.name, "run": run_dir.name, "n_keypoints": {f"cam{i}": len(k["pts"]) for i, k in kps.items()},
             "pairs": {}}
    total_final = 0
    for i, j in camera_pairs(p["pairs"], sorted(kps)):
        if i not in kps or j not in kps:
            continue
        ki, kj = kps[i], kps[j]
        if len(ki["des"]) < 2 or len(kj["des"]) < 2:
            log.warning(f"cam{i}-cam{j}：特徵太少，略過")
            continue
        q, t, d1, d2, ratio_ok, mutual_ok = match_pair(ki["des"], kj["des"], float(p["ratio"]),
                                                      bool(p["mutual_check"]))
        stage1 = ratio_ok & mutual_ok
        F_cal = fundamental_from_calib(cams[i]["K"], cams[i]["R"], cams[i]["t"],
                                       cams[j]["K"], cams[j]["R"], cams[j]["t"])
        epi = epipolar_distance(F_cal, ki["pts_u"][q], kj["pts_u"][t])
        epi_ok = epi < float(p["epipolar_px"])
        final = stage1 & epi_ok

        pair_stat = {"raw": int(len(q)), "ratio_test": int(ratio_ok.sum()),
                     "ratio_and_mutual": int(stage1.sum()), "epipolar": int(final.sum())}
        F_items = {"F_calib": F_cal}
        if p.get("compare_ransac", True):
            pi_, pj_ = ki["pts_u"][q[stage1]], kj["pts_u"][t[stage1]]
            for name, method in (("ransac", cv2.FM_RANSAC), ("lmeds", cv2.FM_LMEDS)):
                F_est, inl = estimate_F(pi_, pj_, method, float(p["ransac_threshold_px"]),
                                        float(p["ransac_confidence"]))
                epi_set = epi_ok[stage1]
                inter = int((inl & epi_set).sum())
                union = int((inl | epi_set).sum())
                pair_stat[name] = {
                    "inliers": int(inl.sum()),
                    "agree_with_calib_jaccard": inter / union if union else None,
                    "median_epipolar_px_under_calib_F": float(np.median(epipolar_distance(F_cal, pi_[inl], pj_[inl])))
                    if inl.any() else None,
                }
                if F_est is not None:
                    F_items[f"F_{name}"] = F_est
        fs_write(run_dir / f"F_{i}{j}.yaml", F_items, comments=[
            f"cam{i} -> cam{j} 的基礎矩陣：x_{j}^T F x_{i} = 0（x 為去畸變後的像素座標，已正規化為 ||F|| = 1）",
            "F_calib：由標定的內外參計算；F_ransac / F_lmeds：由配對點估計（比較用）",
        ])
        np.savez(run_dir / f"matches_{i}{j}.npz",
                 cam_i=i, cam_j=j, query_idx=q, train_idx=t, dist1=d1, dist2=d2,
                 ratio_ok=ratio_ok, mutual_ok=mutual_ok, epipolar_px=epi, epipolar_ok=epi_ok, final=final,
                 note=f"query = cam{i} keypoint index, train = cam{j} keypoint index")
        if final.any():
            imwrite(vis_dir / f"matches_{i}{j}.png",
                    draw_matches(images[i], images[j], ki["pts"][q[final]], kj["pts"][t[final]],
                                 int(p["vis_max_matches"])))
        stats["pairs"][f"{i}-{j}"] = pair_stat
        total_final += int(final.sum())
        msg = (f"cam{i}-cam{j}：原始 {pair_stat['raw']} -> ratio {pair_stat['ratio_test']} -> "
               f"雙向 {pair_stat['ratio_and_mutual']} -> 對極約束 {pair_stat['epipolar']}")
        if "ransac" in pair_stat:
            msg += f"（RANSAC inliers {pair_stat['ransac']['inliers']}，LMedS inliers {pair_stat['lmeds']['inliers']}）"
        log.info(msg)

    stats["total_final_matches"] = total_final
    passed = total_final > 0
    stats["passed"] = passed
    save_json(run_dir / "match_stats.json", stats)
    if passed or force_latest:
        set_latest(step_dir, run_dir)
        log.info(f"已更新 {rel(step_dir / 'latest.txt', root)} -> {run_dir.name}")
    else:
        log.warning("沒有任何通過篩選的配對，latest.txt 未更新。請確認物品紋理、相機重疊視野與外參是否正確。")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="步驟 2-2：SIFT 特徵擷取與配對")
    ap.add_argument("--shot", help="shot 資料夾名稱，或 all；預設為最新一組")
    ap.add_argument("--extrinsic-run", help="指定外參 run（預設讀 output/02_extrinsic/latest.txt）")
    ap.add_argument("--force-latest", action="store_true")
    add_root_arg(ap)
    args = ap.parse_args()
    cfg = load_configs(args.root)
    ext_dir = resolve_run(args.root / "output" / "02_extrinsic", args.extrinsic_run)
    for shot_dir in list_shots(args.root, args.shot):
        process_shot(args.root, cfg, shot_dir, ext_dir, args.force_latest)


if __name__ == "__main__":
    main()
