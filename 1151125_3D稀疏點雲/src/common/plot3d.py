"""3D 繪圖：相機位置與朝向、標定板角點、點雲（matplotlib，不依賴 open3d）。

顯示時把相機座標 (X 右, Y 下, Z 前) 轉成 (X, Z, -Y)，讓「上」朝上、地面水平。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from matplotlib.figure import Figure

CAM_COLORS = ["tab:red", "tab:green", "tab:blue", "tab:orange", "tab:purple", "tab:brown"]


def _disp(P: np.ndarray) -> np.ndarray:
    """世界座標 (N,3) -> 顯示座標 (N,3) = (X, Z, -Y)。"""
    P = np.asarray(P).reshape(-1, 3)
    return np.stack([P[:, 0], P[:, 2], -P[:, 1]], axis=1)


def frustum_world(K, R, t, image_size, depth: float) -> np.ndarray:
    """相機視錐的 5 個頂點（光心 + 影像四角在 depth 深度處），世界座標。"""
    w, h = image_size
    corners = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float64)
    rays = np.linalg.inv(K) @ np.hstack([corners, np.ones((4, 1))]).T   # 3x4
    pts_cam = np.vstack([np.zeros((1, 3)), (rays * depth).T])
    return (R.T @ (pts_cam.T - np.asarray(t).reshape(3, 1))).T


def draw_scene(ax, cams: dict[int, dict], points=None, colors=None,
               board_points=None, frustum_depth: float | None = None) -> None:
    """在 3D 座標軸上畫出相機、標定板角點與點雲。"""
    all_pts = []
    centers = np.array([cams[i]["center"] for i in sorted(cams)])
    if frustum_depth is None:
        span = np.ptp(centers, axis=0).max() if len(centers) > 1 else 100.0
        frustum_depth = max(span * 0.25, 30.0)

    for k, i in enumerate(sorted(cams)):
        c = cams[i]
        f = _disp(frustum_world(c["K"], c["R"], c["t"], c["image_size"], frustum_depth))
        col = CAM_COLORS[k % len(CAM_COLORS)]
        for a, b in [(0, 1), (0, 2), (0, 3), (0, 4), (1, 2), (2, 3), (3, 4), (4, 1)]:
            ax.plot(*zip(f[a], f[b]), color=col, linewidth=1.2)
        ax.text(*f[0], f"cam{i}", color=col, fontsize=9)
        all_pts.append(f)

    if board_points is not None and len(board_points):
        bp = _disp(board_points)
        ax.scatter(bp[:, 0], bp[:, 1], bp[:, 2], s=1, c="gray", alpha=0.4)
        all_pts.append(bp)

    if points is not None and len(points):
        pp = _disp(points)
        c = None if colors is None else np.asarray(colors, dtype=np.float64) / 255.0
        ax.scatter(pp[:, 0], pp[:, 1], pp[:, 2], s=2, c=c if c is not None else "k", depthshade=False)
        all_pts.append(pp)

    allp = np.vstack(all_pts)
    lo, hi = allp.min(axis=0), allp.max(axis=0)
    mid, half = (lo + hi) / 2, max((hi - lo).max() / 2, 1.0)
    ax.set_xlim(mid[0] - half, mid[0] + half)
    ax.set_ylim(mid[1] - half, mid[1] + half)
    ax.set_zlim(mid[2] - half, mid[2] + half)
    ax.set_box_aspect((1, 1, 1))
    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Z (mm)")
    ax.set_zlabel("-Y (mm)")


def save_scene(path: Path, cams, points=None, colors=None, board_points=None,
               elev: float = 20, azim: float = -60, title: str = "") -> None:
    fig = Figure(figsize=(8, 7), dpi=120)
    ax = fig.add_subplot(111, projection="3d")
    draw_scene(ax, cams, points, colors, board_points)
    ax.view_init(elev=elev, azim=azim)
    if title:
        ax.set_title(title)
    fig.tight_layout()
    fig.savefig(str(path))


def save_cloud_views(path_prefix: Path, cams, points, colors, azimuths, show_cameras=True) -> list[Path]:
    """點雲多角度截圖；以點雲中心為主，另存一張含相機的全景。"""
    paths = []
    path_prefix = Path(path_prefix)
    for az in azimuths:
        fig = Figure(figsize=(8, 7), dpi=120)
        ax = fig.add_subplot(111, projection="3d")
        pp = _disp(points)
        c = None if colors is None else np.asarray(colors, dtype=np.float64) / 255.0
        ax.scatter(pp[:, 0], pp[:, 1], pp[:, 2], s=3, c=c if c is not None else "k", depthshade=False)
        lo, hi = pp.min(axis=0), pp.max(axis=0)
        mid, half = (lo + hi) / 2, max((hi - lo).max() / 2, 1.0)
        ax.set_xlim(mid[0] - half, mid[0] + half)
        ax.set_ylim(mid[1] - half, mid[1] + half)
        ax.set_zlim(mid[2] - half, mid[2] + half)
        ax.set_box_aspect((1, 1, 1))
        ax.set_xlabel("X (mm)")
        ax.set_ylabel("Z (mm)")
        ax.set_zlabel("-Y (mm)")
        # 預設從 cam0 的後方看向前方（-90 度為沿 +Z 方向看）
        ax.view_init(elev=15, azim=-90 + az)
        ax.set_title(f"points: {len(points)}  azim {az:+d}")
        fig.tight_layout()
        p = path_prefix.parent / f"{path_prefix.name}_az{az:+04d}.png"
        fig.savefig(str(p))
        paths.append(p)
    if show_cameras:
        p = path_prefix.parent / f"{path_prefix.name}_with_cameras.png"
        save_scene(p, cams, points, colors, title=f"points: {len(points)}")
        paths.append(p)
    return paths
