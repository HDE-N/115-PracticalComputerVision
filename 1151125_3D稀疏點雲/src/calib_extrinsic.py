"""步驟 1-2：外參標定，把所有相機放到同一個世界座標（世界座標 = cam0 相機座標）。

流程：
  1. 每組照片、每台相機用 solvePnP 求「標定板 -> 相機」位姿
  2. 以共同看到標定板的組數建立相機關係圖，從 cam0 出發（最大生成樹）串出各相機外參初值
  3. Bundle Adjustment（Day06 p10–14）：scipy least_squares（預設 Levenberg-Marquardt）同時優化
     所有相機外參與每組標定板位姿，最小化全部角點的重投影誤差（內參固定）

輸入：data/extrinsic/frame_xxx/cam{i}.png、output/01_intrinsic/cam{i}/latest.txt 指向的內參
輸出：output/02_extrinsic/run_YYYYMMDD_HHMMSS/
  board_poses.npz、observations.csv   每組每台相機的偵測與 solvePnP 結果
  extrinsics_init.yaml                BA 前的外參
  extrinsics.yaml                     BA 後的外參（R、t、P = K[R|t]、光心）
  board_poses_world.npz               BA 後每組標定板在世界座標的位姿
  ba_report.json                      BA 前後各相機重投影誤差、優化資訊
  baselines.csv                       相機間距離：計算值 vs 實測值
  camera_layout.png                   相機位置與朝向示意圖
  sources.json、config_used.yaml、run.log

範例：
  python src/calib_extrinsic.py
  python src/calib_extrinsic.py --intrinsic-runs 0:run_20261125_101500,2:run_20261125_102000
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
from common.board import CharucoFinder, is_degenerate  # noqa: E402
from common.calib_io import load_intrinsics, write_extrinsics  # noqa: E402
from common.geometry import (camera_center, from_T, inv_T, R_to_rvec, rotation_mean,  # noqa: E402
                             rvec_to_R, to_T)
from common.io_utils import (add_root_arg, camera_ids, create_run_dir, imread, load_configs,  # noqa: E402
                             rel, resolve_run, save_csv, save_json, save_yaml, set_latest,
                             setup_logger)
from common.plot3d import save_scene  # noqa: E402


def parse_runs(text: str | None) -> dict[int, str]:
    if not text:
        return {}
    out = {}
    for item in text.split(","):
        cam, run = item.split(":", 1)
        out[int(cam)] = run.strip()
    return out


def average_T(Ts: list[np.ndarray]) -> np.ndarray:
    """多個位姿的平均：旋轉用 chordal mean，平移取中位數（較不受離群值影響）。"""
    R = rotation_mean([T[:3, :3] for T in Ts])
    t = np.median(np.array([T[:3, 3] for T in Ts]), axis=0)
    return to_T(R, t)


def main() -> None:
    ap = argparse.ArgumentParser(description="步驟 1-2：外參標定 + Bundle Adjustment")
    ap.add_argument("--intrinsic-runs", help="指定各相機使用的內參 run，例如 0:run_xxx,1:run_yyy（未指定者讀 latest.txt）")
    ap.add_argument("--force-latest", action="store_true", help="未通過門檻也更新 latest.txt")
    add_root_arg(ap)
    args = ap.parse_args()
    root = args.root
    cfg = load_configs(root)
    p = cfg["params"]["extrinsic"]
    cams_all = camera_ids(cfg["cameras"])
    ref = cams_all[0]

    step_dir = root / "output" / "02_extrinsic"
    run_dir = create_run_dir(step_dir)
    log = setup_logger(run_dir)
    log.info(f"===== 外參標定，輸出：{rel(run_dir, root)} =====")

    # ---- 0. 讀內參
    runs = parse_runs(args.intrinsic_runs)
    intr, sources = {}, {"intrinsics": {}}
    for i in cams_all:
        d = resolve_run(root / "output" / "01_intrinsic" / f"cam{i}", runs.get(i))
        intr[i] = load_intrinsics(d / "intrinsics.yaml")
        sources["intrinsics"][f"cam{i}"] = rel(d / "intrinsics.yaml", root)
        log.info(f"cam{i} 內參：{rel(d, root)}（RMS {intr[i]['rms_px']:.3f} px）")
    save_json(run_dir / "sources.json", sources)
    save_yaml(run_dir / "config_used.yaml", {
        "step": "02_extrinsic", "reference_camera": ref, "board": cfg["board"], "extrinsic": p,
        "intrinsic_runs": sources["intrinsics"],
        "measured_baselines_mm": cfg["cameras"].get("measured_baselines_mm"),
    })

    # ---- 1. 偵測 + solvePnP
    frame_dirs = sorted(d for d in (root / "data" / "extrinsic").glob("frame_*") if d.is_dir())
    if not frame_dirs:
        log.error("data/extrinsic/ 沒有照片，請先執行 capture.py extrinsic")
        return
    finder = CharucoFinder(cfg["board"])
    obs = []   # 每組 x 每台相機一筆
    for f_idx, fd in enumerate(frame_dirs):
        for i in cams_all:
            o = {"frame": fd.name, "frame_idx": f_idx, "cam": i, "n_corners": 0, "pnp_rms_px": np.nan,
                 "valid": False, "reason": "", "rvec": np.zeros(3), "tvec": np.zeros(3),
                 "corners": np.zeros((0, 2)), "ids": np.zeros((0,), int)}
            obs.append(o)
            path = fd / f"cam{i}.png"
            img = imread(path) if path.exists() else None
            if img is None:
                o["reason"] = "沒有照片"
                continue
            if (img.shape[1], img.shape[0]) != intr[i]["image_size"]:
                o["reason"] = f"解析度 {(img.shape[1], img.shape[0])} 與內參 {intr[i]['image_size']} 不符"
                continue
            corners, ids = finder.detect(img)
            o["corners"], o["ids"], o["n_corners"] = corners, ids, int(len(ids))
            if len(ids) < int(p["min_corners"]):
                o["reason"] = f"角點不足（< {p['min_corners']}）"
                continue
            objp = finder.object_points(ids)
            if is_degenerate(objp):
                o["reason"] = "角點近乎共線"
                continue
            K, dist = intr[i]["K"], intr[i]["dist"]
            ok, rvec, tvec = cv2.solvePnP(objp, corners, K, dist, flags=cv2.SOLVEPNP_ITERATIVE)
            if not ok:
                o["reason"] = "solvePnP 失敗"
                continue
            rvec, tvec = cv2.solvePnPRefineLM(objp, corners, K, dist, rvec, tvec)
            proj, _ = cv2.projectPoints(objp, rvec, tvec, K, dist)
            err = float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - corners) ** 2, axis=1))))
            o["pnp_rms_px"], o["rvec"], o["tvec"] = err, rvec.reshape(3), tvec.reshape(3)
            if err > float(p["pnp_max_reproj_px"]):
                o["reason"] = f"solvePnP 誤差 {err:.2f} px 過大"
                continue
            o["valid"] = True

    n_valid = sum(o["valid"] for o in obs)
    log.info(f"共 {len(frame_dirs)} 組照片，有效觀測 {n_valid} 筆")
    save_csv(run_dir / "observations.csv",
             [{k: o[k] for k in ("frame", "cam", "n_corners", "pnp_rms_px", "valid", "reason")} for o in obs])
    det_obs = np.concatenate([[k] * o["n_corners"] for k, o in enumerate(obs)]).astype(np.int32) \
        if any(o["n_corners"] for o in obs) else np.zeros(0, np.int32)
    np.savez(run_dir / "board_poses.npz",
             frame_names=np.array([fd.name for fd in frame_dirs]),
             obs_frame_idx=np.array([o["frame_idx"] for o in obs], np.int32),
             obs_cam=np.array([o["cam"] for o in obs], np.int32),
             obs_valid=np.array([o["valid"] for o in obs]),
             obs_n_corners=np.array([o["n_corners"] for o in obs], np.int32),
             obs_pnp_rms_px=np.array([o["pnp_rms_px"] for o in obs]),
             obs_rvec=np.array([o["rvec"] for o in obs]),
             obs_tvec_mm=np.array([o["tvec"] for o in obs]),
             det_obs_index=det_obs,
             det_ids=np.concatenate([o["ids"] for o in obs]).astype(np.int32),
             det_corners=np.concatenate([o["corners"] for o in obs]).reshape(-1, 2))

    # ---- 2. 外參初值：相機關係圖 + 最大生成樹
    T_board = {(o["frame_idx"], o["cam"]): to_T(rvec_to_R(o["rvec"]), o["tvec"]) for o in obs if o["valid"]}
    frames_by_cam = {i: {f for (f, c) in T_board if c == i} for i in cams_all}
    shared = {(a, b): sorted(frames_by_cam[a] & frames_by_cam[b]) for a in cams_all for b in cams_all if a != b}
    for a in cams_all:
        log.info("  共同觀測組數 cam%d: %s" % (a, ", ".join(f"cam{b}={len(shared[(a, b)])}" for b in cams_all if b != a)))

    T_cam = {ref: np.eye(4)}          # 世界(cam0) -> cam i
    parent = {}
    while len(T_cam) < len(cams_all):
        best = max(((len(shared[(a, b)]), a, b) for a in T_cam for b in cams_all if b not in T_cam),
                   default=(0, None, None))
        n, a, b = best
        if n == 0:
            missing = [i for i in cams_all if i not in T_cam]
            log.error(f"相機 {missing} 沒有和已定位的相機同時看到標定板，無法求外參。"
                      f"請補拍讓這些相機與相鄰相機同時看到標定板的照片。")
            return
        # cam a -> cam b：T_ab = T_board->b · (T_board->a)^-1
        T_ab = average_T([T_board[(f, b)] @ inv_T(T_board[(f, a)]) for f in shared[(a, b)]])
        T_cam[b] = T_ab @ T_cam[a]
        parent[b] = a
        log.info(f"  cam{b} 由 cam{a} 串接（{n} 組共同觀測）")

    # 每組標定板在世界座標的位姿初值（只取至少 min_views_per_frame 台相機看到的組）
    min_views = int(p["min_views_per_frame"])
    ba_frames = sorted(f for f in range(len(frame_dirs))
                       if sum((f, i) in T_board for i in cams_all) >= min_views)
    if not ba_frames:
        log.error(f"沒有任何一組照片有 {min_views} 台以上相機同時看到標定板")
        return
    T_wb = {f: average_T([inv_T(T_cam[i]) @ T_board[(f, i)] for i in cams_all if (f, i) in T_board])
            for f in ba_frames}

    # ---- 3. Bundle Adjustment
    others = [i for i in cams_all if i != ref]
    cam_col = {i: 6 * k for k, i in enumerate(others)}
    frm_col = {f: 6 * len(others) + 6 * k for k, f in enumerate(ba_frames)}
    groups = []
    for (f, i), _ in T_board.items():
        if f not in T_wb:
            continue
        o = next(o for o in obs if o["frame_idx"] == f and o["cam"] == i)
        groups.append({"f": f, "i": i, "obj": finder.object_points(o["ids"]), "img": o["corners"]})
    n_res = 2 * sum(len(g["img"]) for g in groups)

    def pack(Tc, Tw) -> np.ndarray:
        x = np.zeros(6 * (len(others) + len(ba_frames)))
        for i in others:
            R, t = from_T(Tc[i])
            x[cam_col[i]:cam_col[i] + 6] = np.r_[R_to_rvec(R), t]
        for f in ba_frames:
            R, t = from_T(Tw[f])
            x[frm_col[f]:frm_col[f] + 6] = np.r_[R_to_rvec(R), t]
        return x

    def unpack(x):
        Tc = {ref: np.eye(4)}
        for i in others:
            v = x[cam_col[i]:cam_col[i] + 6]
            Tc[i] = to_T(rvec_to_R(v[:3]), v[3:])
        Tw = {}
        for f in ba_frames:
            v = x[frm_col[f]:frm_col[f] + 6]
            Tw[f] = to_T(rvec_to_R(v[:3]), v[3:])
        return Tc, Tw

    def residuals(x) -> np.ndarray:
        Tc, Tw = unpack(x)
        out = np.empty(n_res)
        k = 0
        for g in groups:
            T = Tc[g["i"]] @ Tw[g["f"]]            # 標定板 -> 相機 i
            R, t = from_T(T)
            proj, _ = cv2.projectPoints(g["obj"], R_to_rvec(R), t, intr[g["i"]]["K"], intr[g["i"]]["dist"])
            r = (proj.reshape(-1, 2) - g["img"]).ravel()
            out[k:k + len(r)] = r
            k += len(r)
        return out

    sparsity = lil_matrix((n_res, len(pack(T_cam, T_wb))), dtype=int)
    k = 0
    for g in groups:
        rows = slice(k, k + 2 * len(g["img"]))
        if g["i"] != ref:
            sparsity[rows, cam_col[g["i"]]:cam_col[g["i"]] + 6] = 1
        sparsity[rows, frm_col[g["f"]]:frm_col[g["f"]] + 6] = 1
        k += 2 * len(g["img"])

    def per_cam_rms(res: np.ndarray) -> dict[int, float]:
        e2 = res.reshape(-1, 2)
        e2 = np.sum(e2 ** 2, axis=1)
        out, k = {}, 0
        acc = {i: [] for i in cams_all}
        for g in groups:
            acc[g["i"]].append(e2[k:k + len(g["img"])])
            k += len(g["img"])
        for i in cams_all:
            out[i] = float(np.sqrt(np.mean(np.concatenate(acc[i])))) if acc[i] else float("nan")
        return out

    x0 = pack(T_cam, T_wb)
    res0 = residuals(x0)
    rms0 = per_cam_rms(res0)
    log.info("BA 前各相機 RMS：" + ", ".join(f"cam{i}={v:.3f}" for i, v in rms0.items()) + " px")

    cams_out_init = {i: {"K": intr[i]["K"], "dist": intr[i]["dist"], "R": T_cam[i][:3, :3],
                         "t": T_cam[i][:3, 3], "image_size": intr[i]["image_size"]} for i in cams_all}
    write_extrinsics(run_dir / "extrinsics_init.yaml", cams_out_init, extra={"stage": "init (before BA)"})

    method = str(p.get("ba_method", "lm")).lower()
    max_nfev = None if p.get("ba_max_nfev") is None else int(p["ba_max_nfev"])
    log.info(f"Bundle Adjustment（{method}）：{len(others)} 台相機 + {len(ba_frames)} 組標定板位姿，"
             f"{n_res // 2} 個角點觀測")
    if method == "lm":
        # Levenberg-Marquardt（Day06 p10）：不支援魯棒損失函數與稀疏 Jacobian
        sol = least_squares(residuals, x0, method="lm", x_scale="jac", max_nfev=max_nfev)
    else:
        sol = least_squares(residuals, x0, jac_sparsity=sparsity, method="trf", x_scale="jac",
                            loss=str(p["ba_loss"]), f_scale=float(p["ba_f_scale_px"]), max_nfev=max_nfev)
    if sol.status == 0:
        log.warning("BA 達到最大評估次數而停止（未滿足收斂條件），請檢查結果，必要時調高 ba_max_nfev")
    T_cam_ba, T_wb_ba = unpack(sol.x)
    res1 = residuals(sol.x)
    rms1 = per_cam_rms(res1)
    total0 = float(np.sqrt(np.mean(np.sum(res0.reshape(-1, 2) ** 2, axis=1))))
    total1 = float(np.sqrt(np.mean(np.sum(res1.reshape(-1, 2) ** 2, axis=1))))
    log.info("BA 後各相機 RMS：" + ", ".join(f"cam{i}={v:.3f}" for i, v in rms1.items()) + " px")
    log.info(f"整體 RMS：{total0:.3f} -> {total1:.3f} px（{sol.message}）")

    # ---- 4. 存檔
    cams_out = {i: {"K": intr[i]["K"], "dist": intr[i]["dist"], "R": T_cam_ba[i][:3, :3],
                    "t": T_cam_ba[i][:3, 3], "image_size": intr[i]["image_size"]} for i in cams_all}
    write_extrinsics(run_dir / "extrinsics.yaml", cams_out, extra={
        "stage": "after bundle adjustment", "reprojection_rms_px": total1})

    np.savez(run_dir / "board_poses_world.npz",
             frame_names=np.array([frame_dirs[f].name for f in ba_frames]),
             rvec=np.array([R_to_rvec(T_wb_ba[f][:3, :3]) for f in ba_frames]),
             tvec_mm=np.array([T_wb_ba[f][:3, 3] for f in ba_frames]),
             note="board -> world(cam0): P_world = R(rvec) * P_board + tvec")

    centers = {i: camera_center(T_cam_ba[i][:3, :3], T_cam_ba[i][:3, 3]) for i in cams_all}
    measured = cfg["cameras"].get("measured_baselines_mm") or {}
    baseline_rows = []
    for a_i, a in enumerate(cams_all):
        for b in cams_all[a_i + 1:]:
            d = float(np.linalg.norm(centers[a] - centers[b]))
            m = measured.get(f"{a}-{b}", measured.get(f"{b}-{a}"))
            baseline_rows.append({"pair": f"{a}-{b}", "computed_mm": round(d, 2),
                                  "measured_mm": m, "diff_mm": None if m is None else round(d - float(m), 2)})
    save_csv(run_dir / "baselines.csv", baseline_rows)
    for r in baseline_rows:
        log.info(f"  基線 cam{r['pair']}：{r['computed_mm']:.1f} mm"
                 + ("" if r["measured_mm"] is None else f"（實測 {r['measured_mm']} mm，差 {r['diff_mm']} mm）"))

    board_pts = np.vstack([(T_wb_ba[f][:3, :3] @ finder.all_object_points.T).T + T_wb_ba[f][:3, 3]
                           for f in ba_frames])
    save_scene(run_dir / "camera_layout.png", cams_out_with_centers(cams_out, centers),
               board_points=board_pts, title="camera layout (world = cam0, mm)")

    max_px = float(p["max_reproj_px"])
    problems = [f"cam{i} BA 後 RMS {v:.3f} px 超過門檻 {max_px} px" for i, v in rms1.items() if not v <= max_px]
    passed = not problems
    save_json(run_dir / "ba_report.json", {
        "run": run_dir.name, "passed": passed, "problems": problems,
        "reference_camera": ref, "spanning_tree_parent": {f"cam{b}": f"cam{a}" for b, a in parent.items()},
        "n_frames_total": len(frame_dirs), "n_frames_used": len(ba_frames),
        "frames_used": [frame_dirs[f].name for f in ba_frames],
        "n_observations": len(groups), "n_corner_observations": n_res // 2,
        "rms_before_px": {f"cam{i}": v for i, v in rms0.items()},
        "rms_after_px": {f"cam{i}": v for i, v in rms1.items()},
        "total_rms_before_px": total0, "total_rms_after_px": total1,
        "optimizer": {"method": method, "status": int(sol.status), "message": sol.message,
                      "nfev": int(sol.nfev), "cost": float(sol.cost),
                      "loss": "linear" if method == "lm" else p["ba_loss"],
                      "f_scale_px": None if method == "lm" else p["ba_f_scale_px"]},
        "camera_centers_mm": {f"cam{i}": c for i, c in centers.items()},
    })

    if passed or args.force_latest:
        set_latest(step_dir, run_dir)
        log.info(f"已更新 {rel(step_dir / 'latest.txt', root)} -> {run_dir.name}"
                 + ("（--force-latest 強制採用）" if not passed else ""))
    else:
        for pr in problems:
            log.warning(f"未通過門檻：{pr}")
        log.warning("latest.txt 未更新。常見原因：內參不準、相機在拍攝期間被移動、標定板不平、多相機擷取不同步。")


def cams_out_with_centers(cams_out: dict, centers: dict) -> dict:
    return {i: {**c, "center": centers[i]} for i, c in cams_out.items()}


if __name__ == "__main__":
    main()
