"""視覺化：相機位置、點雲顯示、量測點雲中兩點的距離（驗證尺度）。

子指令：
  layout   顯示相機位置與朝向（預設讀 output/02_extrinsic/latest.txt）
  cloud    顯示點雲與相機（預設為最新一組 shot 的 latest 重建結果）
  measure  在點雲上用 Shift + 左鍵選點，關閉視窗後列出相鄰兩點的距離（需要 open3d）

有安裝 open3d 時使用 open3d 視窗，否則使用 matplotlib。加上 --save 檔名.png 可只存圖不開視窗。

範例：
  python src/visualize.py layout
  python src/visualize.py cloud --shot shot_001
  python src/visualize.py cloud --raw
  python src/visualize.py measure
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.calib_io import load_extrinsics  # noqa: E402
from common.io_utils import add_root_arg, load_json, resolve_run  # noqa: E402
from common.plot3d import draw_scene, frustum_world, save_scene  # noqa: E402
from common.pointcloud import try_import_open3d  # noqa: E402


def latest_recon(root: Path, shot: str | None, run: str | None) -> Path:
    base = root / "output" / "04_reconstruction"
    shots = sorted(d.name for d in base.glob("shot_*") if (d / "latest.txt").exists()) if base.exists() else []
    if shot is None:
        if not shots:
            raise FileNotFoundError("找不到重建結果，請先執行 reconstruct.py")
        shot = shots[-1]
    return resolve_run(base / shot, run)


def load_cloud(recon_dir: Path, raw: bool):
    if raw:
        d = np.load(recon_dir / "points_raw.npz")
        return d["X_mm"], None
    d = np.load(recon_dir / "points_filtered.npz")
    return d["X_mm"], d["colors_rgb"]


def o3d_geometries(o3d, cams, points=None, colors=None):
    geoms = []
    centers = np.array([c["center"] for c in cams.values()])
    span = np.ptp(centers, axis=0).max() if len(centers) > 1 else 100.0
    depth = max(span * 0.25, 30.0)
    palette = [(1, 0, 0), (0, 0.7, 0), (0, 0, 1), (1, 0.5, 0), (0.6, 0, 0.8)]
    for k, i in enumerate(sorted(cams)):
        c = cams[i]
        f = frustum_world(c["K"], c["R"], c["t"], c["image_size"], depth)
        ls = o3d.geometry.LineSet(
            points=o3d.utility.Vector3dVector(f),
            lines=o3d.utility.Vector2iVector([[0, 1], [0, 2], [0, 3], [0, 4], [1, 2], [2, 3], [3, 4], [4, 1]]))
        ls.paint_uniform_color(palette[k % len(palette)])
        geoms.append(ls)
    geoms.append(o3d.geometry.TriangleMesh.create_coordinate_frame(size=depth * 0.5))
    if points is not None and len(points):
        pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
        if colors is not None:
            pc.colors = o3d.utility.Vector3dVector(np.asarray(colors, float) / 255.0)
        geoms.append(pc)
    return geoms


def show(cams, points=None, colors=None, save: Path | None = None, title: str = "") -> None:
    if save:
        save_scene(save, cams, points, colors, title=title)
        print(f"已存圖：{save}")
        return
    o3d = try_import_open3d()
    if o3d is not None:
        o3d.visualization.draw_geometries(o3d_geometries(o3d, cams, points, colors), window_name=title or "view")
        return
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(9, 8))
    ax = fig.add_subplot(111, projection="3d")
    draw_scene(ax, cams, points, colors)
    ax.view_init(elev=20, azim=-60)
    ax.set_title(title)
    plt.show()


def main() -> None:
    ap = argparse.ArgumentParser(description="相機位置 / 點雲視覺化")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("layout", help="顯示相機位置")
    p1.add_argument("--run", help="外參 run（預設 latest）")
    p2 = sub.add_parser("cloud", help="顯示點雲")
    p3 = sub.add_parser("measure", help="量測點雲中兩點的距離（需要 open3d）")
    for sp in (p2, p3):
        sp.add_argument("--shot", help="shot 名稱（預設最新一組）")
        sp.add_argument("--run", help="重建 run（預設 latest）")
        sp.add_argument("--raw", action="store_true", help="使用篩選前的點")
    for sp in (p1, p2):
        sp.add_argument("--save", type=Path, help="只存成 png，不開視窗")
    for sp in (p1, p2, p3):
        add_root_arg(sp)
    args = ap.parse_args()
    root = args.root

    if args.cmd == "layout":
        ext_dir = resolve_run(root / "output" / "02_extrinsic", args.run)
        cams = load_extrinsics(ext_dir / "extrinsics.yaml")
        for i, c in cams.items():
            print(f"cam{i} 光心（mm）：{np.array2string(c['center'], precision=1)}")
        show(cams, save=args.save, title=f"camera layout ({ext_dir.name})")
        return

    recon_dir = latest_recon(root, args.shot, args.run)
    src = load_json(recon_dir / "sources.json")
    cams = load_extrinsics(root / src["extrinsics"])
    pts, cols = load_cloud(recon_dir, args.raw)
    print(f"點雲：{recon_dir}（{len(pts)} 點，單位 mm）")

    if args.cmd == "cloud":
        show(cams, pts, cols, save=args.save, title=f"{src['shot']} {recon_dir.name}")
        return

    o3d = try_import_open3d()
    if o3d is None:
        print("measure 需要 open3d：pip install open3d")
        return
    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(pts))
    if cols is not None:
        pc.colors = o3d.utility.Vector3dVector(np.asarray(cols, float) / 255.0)
    print("Shift + 左鍵選點（每兩點量一段距離），Shift + 右鍵取消，選完關閉視窗")
    vis = o3d.visualization.VisualizerWithEditing()
    vis.create_window(window_name="measure (Shift + click)")
    vis.add_geometry(pc)
    vis.run()
    vis.destroy_window()
    picked = vis.get_picked_points()
    if len(picked) < 2:
        print("至少要選 2 個點")
        return
    for a, b in zip(picked[0::2], picked[1::2]):
        d = np.linalg.norm(pts[a] - pts[b])
        print(f"點 {a} -> 點 {b}：{d:.2f} mm")


if __name__ == "__main__":
    main()
