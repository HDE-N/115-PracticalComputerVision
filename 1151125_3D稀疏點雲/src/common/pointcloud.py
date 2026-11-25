"""點雲工具：PLY 讀寫、統計式離群點濾除（不依賴 open3d，有安裝 open3d 時另可用於顯示）。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree


def write_ply(path: Path, points: np.ndarray, colors: np.ndarray | None = None,
              comments: list[str] | None = None) -> None:
    """寫 ASCII PLY。colors 為 (N,3) uint8 RGB。"""
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    lines = ["ply", "format ascii 1.0"]
    for c in comments or []:
        lines.append(f"comment {c}")
    lines += [f"element vertex {len(points)}", "property float x", "property float y", "property float z"]
    if colors is not None:
        lines += ["property uchar red", "property uchar green", "property uchar blue"]
    lines.append("end_header")
    body = []
    if colors is not None:
        colors = np.asarray(colors, dtype=np.uint8).reshape(-1, 3)
        for p, c in zip(points, colors):
            body.append(f"{p[0]:.4f} {p[1]:.4f} {p[2]:.4f} {c[0]} {c[1]} {c[2]}")
    else:
        for p in points:
            body.append(f"{p[0]:.4f} {p[1]:.4f} {p[2]:.4f}")
    Path(path).write_text("\n".join(lines + body) + "\n", encoding="utf-8")


def read_ply(path: Path) -> tuple[np.ndarray, np.ndarray | None]:
    """讀取 write_ply 寫出的 ASCII PLY。"""
    text = Path(path).read_text(encoding="utf-8").splitlines()
    end = text.index("end_header")
    has_color = any(l.startswith("property uchar red") for l in text[:end])
    data = np.array([l.split() for l in text[end + 1:] if l.strip()], dtype=np.float64)
    if data.size == 0:
        return np.zeros((0, 3)), None
    pts = data[:, :3]
    cols = data[:, 3:6].astype(np.uint8) if has_color else None
    return pts, cols


def statistical_outlier_mask(points: np.ndarray, nb_neighbors: int, std_ratio: float) -> np.ndarray:
    """與 open3d remove_statistical_outlier 相同的演算法。

    計算每個點到最近 nb_neighbors 個鄰居的平均距離，平均距離大於
    (全體平均 + std_ratio * 標準差) 的點視為離群點。回傳「保留」的布林遮罩。
    """
    n = len(points)
    if n <= nb_neighbors:
        return np.ones(n, dtype=bool)
    tree = cKDTree(points)
    d, _ = tree.query(points, k=nb_neighbors + 1)
    mean_d = d[:, 1:].mean(axis=1)
    thr = mean_d.mean() + std_ratio * mean_d.std()
    return mean_d <= thr


def try_import_open3d():
    try:
        import open3d as o3d  # noqa: F401
        return o3d
    except ImportError:
        return None
