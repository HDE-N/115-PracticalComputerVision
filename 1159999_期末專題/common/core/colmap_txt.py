"""COLMAP 文字格式模型（cameras.txt / images.txt / points3D.txt）的讀寫。

只處理這三個檔案（3DGS 訓練只需要它們）；COLMAP 3.12 以後的 rigs.txt / frames.txt 不讀取。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .geometry import qvec_to_R


@dataclass
class Camera:
    id: int
    model: str
    width: int
    height: int
    params: np.ndarray

    def K(self) -> np.ndarray:
        p = self.params
        if self.model in ("SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL", "SIMPLE_RADIAL_FISHEYE", "RADIAL_FISHEYE"):
            f, cx, cy = p[0], p[1], p[2]
            return np.array([[f, 0, cx], [0, f, cy], [0, 0, 1.0]])
        return np.array([[p[0], 0, p[2]], [0, p[1], p[3]], [0, 0, 1.0]])


@dataclass
class Image:
    id: int
    qvec: np.ndarray        # cam_from_world (w, x, y, z)
    tvec: np.ndarray
    camera_id: int
    name: str
    xys: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    point3D_ids: np.ndarray = field(default_factory=lambda: np.zeros((0,), np.int64))

    def R(self) -> np.ndarray:
        return qvec_to_R(self.qvec)

    def center(self) -> np.ndarray:
        return -self.R().T @ self.tvec

    def P(self, K: np.ndarray) -> np.ndarray:
        return K @ np.hstack([self.R(), self.tvec.reshape(3, 1)])


@dataclass
class Point3D:
    id: int
    xyz: np.ndarray
    rgb: np.ndarray
    error: float
    track: list            # [(image_id, point2D_idx), ...]


@dataclass
class Model:
    cameras: dict[int, Camera]
    images: dict[int, Image]
    points: dict[int, Point3D]


def _lines(path: Path):
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            yield line


def read_model(folder: Path) -> Model:
    folder = Path(folder)
    cameras = {}
    for line in _lines(folder / "cameras.txt"):
        e = line.split()
        cameras[int(e[0])] = Camera(int(e[0]), e[1], int(e[2]), int(e[3]), np.array(e[4:], np.float64))
    images = {}
    lines = list(_lines_keep_empty(folder / "images.txt"))
    for i in range(0, len(lines) - 1, 2):
        e = lines[i].split()
        if not e:
            continue
        pts = lines[i + 1].split()
        arr = np.array(pts, np.float64).reshape(-1, 3) if pts else np.zeros((0, 3))
        images[int(e[0])] = Image(int(e[0]), np.array(e[1:5], np.float64), np.array(e[5:8], np.float64),
                                  int(e[8]), " ".join(e[9:]), arr[:, :2], arr[:, 2].astype(np.int64))
    points = {}
    if (folder / "points3D.txt").exists():
        for line in _lines(folder / "points3D.txt"):
            e = line.split()
            track = np.array(e[8:], np.int64).reshape(-1, 2)
            points[int(e[0])] = Point3D(int(e[0]), np.array(e[1:4], np.float64), np.array(e[4:7], np.int64),
                                        float(e[7]), [tuple(t) for t in track])
    return Model(cameras, images, points)


def _lines_keep_empty(path: Path):
    """images.txt 每張影像佔兩行，第二行（2D 點）可能是空行，因此不能跳過空行。"""
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            continue
        yield line.rstrip("\n")


def write_model(model: Model, folder: Path) -> None:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "cameras.txt", "w", encoding="utf-8") as f:
        f.write("# CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n")
        for c in model.cameras.values():
            f.write(f"{c.id} {c.model} {c.width} {c.height} " + " ".join(f"{v:.10g}" for v in c.params) + "\n")
    with open(folder / "images.txt", "w", encoding="utf-8") as f:
        f.write("# IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME\n# POINTS2D[] as (X, Y, POINT3D_ID)\n")
        for im in model.images.values():
            f.write(f"{im.id} " + " ".join(f"{v:.12g}" for v in im.qvec) + " "
                    + " ".join(f"{v:.12g}" for v in im.tvec) + f" {im.camera_id} {im.name}\n")
            f.write(" ".join(f"{x:.4f} {y:.4f} {int(p)}" for (x, y), p in zip(im.xys, im.point3D_ids)) + "\n")
    with open(folder / "points3D.txt", "w", encoding="utf-8") as f:
        f.write("# POINT3D_ID, X, Y, Z, R, G, B, ERROR, TRACK[] as (IMAGE_ID, POINT2D_IDX)\n")
        for p in model.points.values():
            f.write(f"{p.id} " + " ".join(f"{v:.10g}" for v in p.xyz) + " "
                    + " ".join(str(int(v)) for v in p.rgb) + f" {p.error:.6g} "
                    + " ".join(f"{a} {b}" for a, b in p.track) + "\n")


def model_stats(model: Model) -> dict:
    errs = np.array([p.error for p in model.points.values()]) if model.points else np.zeros(0)
    tracks = np.array([len(p.track) for p in model.points.values()]) if model.points else np.zeros(0)
    return {
        "n_cameras": len(model.cameras), "n_images": len(model.images), "n_points": len(model.points),
        "mean_reproj_px": float(errs.mean()) if errs.size else None,
        "mean_track_length": float(tracks.mean()) if tracks.size else None,
    }
