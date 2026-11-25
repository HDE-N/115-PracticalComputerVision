"""步驟 2-3、2-4：三角測量、篩選、後處理，輸出 3D 稀疏點雲。

流程：
  1. 讀取步驟 2-2 的配對；multiview = true 時，把同一特徵在多台相機的配對串成 track（union-find）
  2. 三角測量：2 個視角用 cv2.triangulatePoints，3 個以上用多視角 DLT（座標皆為去畸變後的像素）
  3. 篩選：深度為正、重投影誤差（多視角時先剔除最差的視角再重算）、三角化夾角、範圍框
  4. （可選）非線性最小平方微調 3D 點，再檢查一次重投影誤差
  5. 統計式離群點濾除，取像素顏色，輸出 PLY 與截圖

輸入：output/03_features/shot_xxx/latest.txt 指向的配對；外參沿用該配對 run 使用的外參
輸出：output/04_reconstruction/shot_xxx/run_YYYYMMDD_HHMMSS/
  points_raw.npz、cloud_raw.ply           三角化後、篩選前的所有點
  points_filtered.npz、cloud.ply          最終點雲（顏色、視角、重投影誤差、夾角）
  filter_stats.json                       每個篩選條件剔除的點數
  summary.json、screenshots/、sources.json、config_used.yaml、run.log
座標：世界座標 = cam0 相機座標，單位 mm。

範例：
  python src/reconstruct.py                 # 最新一組 shot
  python src/reconstruct.py --shot shot_002
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.calib_io import load_extrinsics  # noqa: E402
from common.geometry import triangulate_dlt  # noqa: E402
from common.io_utils import (add_root_arg, create_run_dir, imread, load_configs, load_json,  # noqa: E402
                             rel, resolve_run, save_json, save_yaml, set_latest, setup_logger)
from common.plot3d import save_cloud_views  # noqa: E402
from common.pointcloud import statistical_outlier_mask, write_ply  # noqa: E402

PLY_COMMENTS = ["unit: mm", "world coordinate = cam0 camera coordinate (X right, Y down, Z forward)"]


# --------------------------------------------------------------------- tracks
class UnionFind:
    def __init__(self):
        self.parent: dict = {}

    def find(self, a):
        self.parent.setdefault(a, a)
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def build_tracks(matches: dict, multiview: bool) -> tuple[list[list[tuple[int, int]]], int]:
    """回傳 (tracks, 衝突數)。track = [(cam, keypoint_idx), ...]，同一台相機只出現一次。"""
    if not multiview:
        return [[(i, int(a)), (j, int(b))] for (i, j), (qa, tb) in matches.items() for a, b in zip(qa, tb)], 0
    uf = UnionFind()
    for (i, j), (qa, tb) in matches.items():
        for a, b in zip(qa, tb):
            uf.union((i, int(a)), (j, int(b)))
    groups: dict = {}
    for node in uf.parent:
        groups.setdefault(uf.find(node), []).append(node)
    tracks, conflicts = [], 0
    for nodes in groups.values():
        cams = [c for c, _ in nodes]
        if len(set(cams)) != len(cams):     # 同一台相機有兩個不同特徵 -> 配對矛盾，整條捨棄
            conflicts += 1
            continue
        tracks.append(sorted(nodes))
    return tracks, conflicts


# --------------------------------------------------------------------- 三角測量與評估
def triangulate_tracks(tracks, cams, kps) -> np.ndarray:
    X = np.zeros((len(tracks), 3))
    two_view: dict = {}
    for k, tr in enumerate(tracks):
        if len(tr) == 2:
            two_view.setdefault((tr[0][0], tr[1][0]), []).append(k)
        else:
            X[k] = triangulate_dlt([cams[c]["P"] for c, _ in tr], [kps[c][f] for c, f in tr])
    for (i, j), idx in two_view.items():
        xi = np.array([kps[i][tracks[k][0][1]] for k in idx]).T
        xj = np.array([kps[j][tracks[k][1][1]] for k in idx]).T
        Xh = cv2.triangulatePoints(cams[i]["P"], cams[j]["P"], xi, xj)   # Day04：齊次座標 (4, N)
        X[idx] = (Xh[:3] / Xh[3]).T
    return X


def view_errors(X: np.ndarray, track, cams, kps) -> tuple[np.ndarray, np.ndarray]:
    """回傳 (各視角重投影誤差 px, 各視角深度 mm)。"""
    errs, depths = [], []
    for c, f in track:
        x = cams[c]["P"] @ np.r_[X, 1.0]
        depths.append((cams[c]["R"] @ X + cams[c]["t"])[2])
        errs.append(np.linalg.norm(x[:2] / x[2] - kps[c][f]) if x[2] != 0 else np.inf)
    return np.array(errs), np.array(depths)


def max_angle_deg(X: np.ndarray, track, cams) -> float:
    rays = [X - cams[c]["center"] for c, _ in track]
    rays = [r / np.linalg.norm(r) for r in rays]
    best = 0.0
    for a in range(len(rays)):
        for b in range(a + 1, len(rays)):
            best = max(best, float(np.degrees(np.arccos(np.clip(rays[a] @ rays[b], -1, 1)))))
    return best


def refine_points(X: np.ndarray, tracks, cams, kps, max_nfev: int = 50) -> np.ndarray:
    """固定相機，只優化 3D 點，使所有視角的重投影誤差平方和最小。"""
    e_p, e_P, e_uv = [], [], []
    for k, tr in enumerate(tracks):
        for c, f in tr:
            e_p.append(k)
            e_P.append(cams[c]["P"])
            e_uv.append(kps[c][f])
    e_p, e_P, e_uv = np.array(e_p), np.array(e_P), np.array(e_uv)

    def fun(x):
        Xs = x.reshape(-1, 3)[e_p]
        proj = np.einsum("eij,ej->ei", e_P, np.hstack([Xs, np.ones((len(Xs), 1))]))
        return (proj[:, :2] / proj[:, 2:3] - e_uv).ravel()

    A = lil_matrix((2 * len(e_p), 3 * len(X)), dtype=int)
    for e, k in enumerate(e_p):
        A[2 * e:2 * e + 2, 3 * k:3 * k + 3] = 1
    sol = least_squares(fun, X.ravel(), jac_sparsity=A, method="trf", x_scale="jac", max_nfev=max_nfev)
    return sol.x.reshape(-1, 3)


def pack_tracks(tracks) -> dict:
    offsets = np.cumsum([0] + [len(t) for t in tracks]).astype(np.int32)
    return {
        "track_offsets": offsets,
        "view_cam": np.array([c for t in tracks for c, _ in t], np.int32),
        "view_kp": np.array([f for t in tracks for _, f in t], np.int32),
    }


def track_colors(tracks, images, kps_raw) -> np.ndarray:
    """各視角原始影像（含畸變）在特徵點位置的顏色平均，回傳 RGB uint8。"""
    cols = np.zeros((len(tracks), 3))
    for k, tr in enumerate(tracks):
        acc = []
        for c, f in tr:
            img = images[c]
            x, y = np.round(kps_raw[c][f]).astype(int)
            x, y = np.clip(x, 0, img.shape[1] - 1), np.clip(y, 0, img.shape[0] - 1)
            acc.append(img[y, x, ::-1])       # BGR -> RGB
        cols[k] = np.mean(acc, axis=0)
    return cols.round().astype(np.uint8)


# --------------------------------------------------------------------- 主流程
def process_shot(root: Path, cfg: dict, shot: str, feat_run: str | None) -> dict:
    p = cfg["params"]["reconstruction"]
    feat_dir = resolve_run(root / "output" / "03_features" / shot, feat_run)
    feat_src = load_json(feat_dir / "sources.json")
    ext_path = root / feat_src["extrinsics"]
    shot_dir = root / feat_src["shot"]

    step_dir = root / "output" / "04_reconstruction" / shot
    run_dir = create_run_dir(step_dir)
    log = setup_logger(run_dir)
    log.info(f"===== 三角測量 {shot}，輸出：{rel(run_dir, root)} =====")
    sources = {"shot": feat_src["shot"], "features": rel(feat_dir, root), "extrinsics": feat_src["extrinsics"]}
    save_json(run_dir / "sources.json", sources)
    save_yaml(run_dir / "config_used.yaml", {"step": "04_reconstruction", **sources, "reconstruction": p})
    log.info(f"配對：{sources['features']}；外參：{sources['extrinsics']}")

    cams = load_extrinsics(ext_path)
    kps, kps_raw, images = {}, {}, {}
    for i in cams:
        f = feat_dir / f"keypoints_cam{i}.npz"
        if f.exists():
            d = np.load(f)
            kps[i], kps_raw[i] = d["pts_undist"], d["pts"]
            images[i] = imread(shot_dir / f"cam{i}.png")
    matches = {}
    for f in sorted(feat_dir.glob("matches_*.npz")):
        d = np.load(f)
        fin = d["final"]
        matches[(int(d["cam_i"]), int(d["cam_j"]))] = (d["query_idx"][fin], d["train_idx"][fin])

    # ---- 1. tracks
    tracks, conflicts = build_tracks(matches, bool(p["multiview"]))
    tracks = [t for t in tracks if len(t) >= int(p["min_views"])]
    hist = np.bincount([len(t) for t in tracks]) if tracks else np.zeros(1, int)
    log.info(f"track {len(tracks)} 條（衝突捨棄 {conflicts} 條）；視角數分布："
             + ", ".join(f"{n} 視角={c}" for n, c in enumerate(hist) if c))
    stats = {"n_pairs": len(matches), "n_pair_matches": int(sum(len(v[0]) for v in matches.values())),
             "n_tracks": len(tracks), "track_conflicts_discarded": conflicts,
             "tracks_by_n_views": {str(n): int(c) for n, c in enumerate(hist) if c}}
    if not tracks:
        log.error("沒有可三角化的配對")
        save_json(run_dir / "filter_stats.json", stats)
        return stats

    # ---- 2. 三角測量（篩選前）
    X = triangulate_tracks(tracks, cams, kps)
    raw_err, raw_depth_ok = [], []
    for k, tr in enumerate(tracks):
        e, dpt = view_errors(X[k], tr, cams, kps)
        raw_err.append(e.max())
        raw_depth_ok.append(bool((dpt > 0).all()))
    np.savez(run_dir / "points_raw.npz", X_mm=X, reproj_max_px=np.array(raw_err),
             depth_ok=np.array(raw_depth_ok), **pack_tracks(tracks))
    write_ply(run_dir / "cloud_raw.ply", X, track_colors(tracks, images, kps_raw), PLY_COMMENTS + ["stage: raw"])

    # ---- 3. 篩選：深度、重投影誤差（多視角時逐一剔除最差視角）、夾角
    thr = float(p["max_reproj_px"])
    keep_tracks, keep_X = [], []
    n_cheir = n_reproj = n_views_dropped = 0
    for tr, x in zip(tracks, X):
        tr = list(tr)
        while True:
            e, dpt = view_errors(x, tr, cams, kps)
            e = np.where(dpt > 0, e, np.inf)        # 在相機後方的視角視為誤差無限大
            if e.max() <= thr:
                keep_tracks.append(tr)
                keep_X.append(x)
                break
            if len(tr) > 2:
                tr.pop(int(np.argmax(e)))
                n_views_dropped += 1
                x = triangulate_dlt([cams[c]["P"] for c, _ in tr], [kps[c][f] for c, f in tr])
                continue
            if (dpt <= 0).any():
                n_cheir += 1
            else:
                n_reproj += 1
            break
    stats.update({"removed_cheirality": n_cheir, "removed_reprojection": n_reproj,
                  "views_dropped_in_multiview_tracks": n_views_dropped})
    tracks, X = keep_tracks, np.array(keep_X).reshape(-1, 3)

    angles = np.array([max_angle_deg(x, tr, cams) for x, tr in zip(X, tracks)])
    ok = angles >= float(p["min_angle_deg"])
    stats["removed_small_angle"] = int((~ok).sum())
    tracks, X, angles = [t for t, o in zip(tracks, ok) if o], X[ok], angles[ok]

    if p.get("bbox_mm"):
        b = np.asarray(p["bbox_mm"], float)
        ok = ((X[:, 0] >= b[0]) & (X[:, 0] <= b[1]) & (X[:, 1] >= b[2]) & (X[:, 1] <= b[3])
              & (X[:, 2] >= b[4]) & (X[:, 2] <= b[5]))
        stats["removed_bbox"] = int((~ok).sum())
        tracks, X, angles = [t for t, o in zip(tracks, ok) if o], X[ok], angles[ok]

    # ---- 4. 微調 3D 點
    if p.get("refine_points", True) and len(X):
        X = refine_points(X, tracks, cams, kps)
        errs = [view_errors(x, tr, cams, kps) for x, tr in zip(X, tracks)]
        ok = np.array([(e.max() <= thr) and (d > 0).all() for e, d in errs])
        stats["removed_after_refine"] = int((~ok).sum())
        tracks, X, angles = [t for t, o in zip(tracks, ok) if o], X[ok], angles[ok]

    # ---- 5. 統計式離群點濾除
    if p.get("outlier_removal", True) and len(X):
        ok = statistical_outlier_mask(X, int(p["outlier_nb_neighbors"]), float(p["outlier_std_ratio"]))
        stats["removed_statistical_outlier"] = int((~ok).sum())
        tracks, X, angles = [t for t, o in zip(tracks, ok) if o], X[ok], angles[ok]

    stats["n_points_final"] = len(X)
    log.info("篩選：" + "，".join(f"{k}={v}" for k, v in stats.items() if k.startswith(("removed", "views"))))
    save_json(run_dir / "filter_stats.json", stats)
    if not len(X):
        log.error("篩選後沒有剩下任何點。請檢查外參誤差、配對品質，或物品是否有足夠紋理。")
        return stats

    # ---- 6. 輸出
    errs = [view_errors(x, tr, cams, kps)[0] for x, tr in zip(X, tracks)]
    colors = track_colors(tracks, images, kps_raw)
    np.savez(run_dir / "points_filtered.npz", X_mm=X, colors_rgb=colors,
             n_views=np.array([len(t) for t in tracks], np.int32),
             reproj_mean_px=np.array([e.mean() for e in errs]), reproj_max_px=np.array([e.max() for e in errs]),
             angle_deg=angles, **pack_tracks(tracks))
    write_ply(run_dir / "cloud.ply", X, colors, PLY_COMMENTS + ["stage: filtered"])

    shots_dir = run_dir / "screenshots"
    shots_dir.mkdir()
    save_cloud_views(shots_dir / "cloud", cams, X, colors, [int(a) for a in p["screenshot_azimuths"]])

    all_e = np.concatenate(errs)
    summary = {
        "shot": shot, "run": run_dir.name, "n_points": len(X),
        "n_views_hist": {str(n): int(c) for n, c in enumerate(np.bincount([len(t) for t in tracks])) if c},
        "reproj_mean_px": float(all_e.mean()), "reproj_median_px": float(np.median(all_e)),
        "angle_median_deg": float(np.median(angles)),
        "bbox_min_mm": X.min(axis=0), "bbox_max_mm": X.max(axis=0), "extent_mm": np.ptp(X, axis=0),
        "centroid_mm": X.mean(axis=0),
    }
    save_json(run_dir / "summary.json", summary)
    log.info(f"最終點雲 {len(X)} 點，平均重投影誤差 {summary['reproj_mean_px']:.3f} px，"
             f"範圍 {np.array2string(summary['extent_mm'], precision=1)} mm")
    set_latest(step_dir, run_dir)
    log.info(f"已更新 {rel(step_dir / 'latest.txt', root)} -> {run_dir.name}")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="步驟 2-3、2-4：三角測量與點雲輸出")
    ap.add_argument("--shot", help="shot 名稱或 all；預設為有配對結果的最新一組")
    ap.add_argument("--features-run", help="指定配對 run（預設讀該 shot 的 latest.txt）")
    add_root_arg(ap)
    args = ap.parse_args()
    cfg = load_configs(args.root, args.set)

    feat_root = args.root / "output" / "03_features"
    shots = sorted(d.name for d in feat_root.glob("shot_*") if (d / "latest.txt").exists()) \
        if feat_root.exists() else []
    if not shots:
        print("找不到任何配對結果，請先執行 match_features.py")
        return
    if args.shot is None:
        targets = [shots[-1]]
    elif args.shot == "all":
        targets = shots
    else:
        targets = [args.shot]
    for s in targets:
        process_shot(args.root, cfg, s, args.features_run)


def launch_gui() -> None:
    """不加參數執行時開啟的 UI。"""
    from common.gui import Arg, ScriptGUI, shot_choices
    ScriptGUI(
        __file__, "步驟 2-3、2-4：三角測量與點雲", "讀取配對結果做三角測量、篩選與離群點濾除，"
        "輸出 PLY 點雲到 output/04_reconstruction/shot_xxx/run_*/。",
        args=[
            Arg("--shot", "shot", "combo", "",
                shot_choices("output/03_features", need_latest=True,
                             extra=(("", "（預設：最新一組）"), "all"))),
            Arg("--features-run", "配對 run", "entry", "", help="空白 = 讀該 shot 的 latest.txt"),
        ],
        sections=[("重建參數", "params", ("reconstruction",))],
    ).run()


if __name__ == "__main__":
    if len(sys.argv) == 1:
        launch_gui()
    else:
        main()
