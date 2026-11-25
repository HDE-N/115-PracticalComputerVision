"""內參、外參檔案的讀寫（cv2.FileStorage yaml 格式，OpenCV 可直接讀回）。"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .geometry import camera_center, projection_matrix, R_to_rvec
from .io_utils import fs_read, fs_write

EXTRINSIC_COMMENTS = [
    "外參定義: P_cam_i = R_i * P_world + t_i （Day04 p3）",
    "世界座標 = cam0 的相機座標（R_0 = I, t_0 = 0）",
    "P_i = K_i [R_i | t_i] 作用在「去畸變後」的像素座標；center = 相機光心的世界座標 = -R_i^T t_i",
    "長度單位: mm（與標定板量測值一致）",
]


def write_intrinsics(path: Path, K, dist, image_size, rms, n_images, extra: dict | None = None):
    items = {
        "K": np.asarray(K, np.float64),
        "dist": np.asarray(dist, np.float64).reshape(1, -1),
        "image_width": int(image_size[0]),
        "image_height": int(image_size[1]),
        "rms_px": float(rms),
        "n_images": int(n_images),
        "unit": "mm",
    }
    if extra:
        items.update(extra)
    fs_write(path, items, comments=[
        "相機內參（cv2.calibrateCamera）",
        "K: 內參矩陣 [fx s cx; 0 fy cy; 0 0 1]（px）；dist: 畸變係數 (k1 k2 p1 p2 k3 ...)",
        "rms_px: 重投影誤差 RMS（px）；長度單位 mm",
    ])


def load_intrinsics(path: Path) -> dict:
    d = fs_read(path)
    return {
        "K": d["K"].astype(np.float64),
        "dist": d["dist"].astype(np.float64).reshape(-1),
        "image_size": (int(d["image_width"]), int(d["image_height"])),
        "rms_px": float(d.get("rms_px", np.nan)),
    }


def write_extrinsics(path: Path, cams: dict[int, dict], extra: dict | None = None):
    """cams: {cam_id: {"K", "dist", "R", "t", "image_size"}}。"""
    ids = sorted(cams)
    items: dict = {"num_cameras": len(ids), "camera_ids": [int(i) for i in ids], "unit": "mm"}
    for i in ids:
        c = cams[i]
        R, t = np.asarray(c["R"], np.float64), np.asarray(c["t"], np.float64).reshape(3, 1)
        items[f"cam{i}_image_width"] = int(c["image_size"][0])
        items[f"cam{i}_image_height"] = int(c["image_size"][1])
        items[f"cam{i}_K"] = np.asarray(c["K"], np.float64)
        items[f"cam{i}_dist"] = np.asarray(c["dist"], np.float64).reshape(1, -1)
        items[f"cam{i}_R"] = R
        items[f"cam{i}_t"] = t
        items[f"cam{i}_rvec"] = R_to_rvec(R).reshape(3, 1)
        items[f"cam{i}_P"] = projection_matrix(c["K"], R, t)
        items[f"cam{i}_center"] = camera_center(R, t).reshape(3, 1)
    if extra:
        items.update(extra)
    fs_write(path, items, comments=EXTRINSIC_COMMENTS)


def load_extrinsics(path: Path) -> dict[int, dict]:
    d = fs_read(path)
    ids = [int(round(v)) for v in np.asarray(d["camera_ids"]).reshape(-1)]
    cams = {}
    for i in ids:
        R = d[f"cam{i}_R"].astype(np.float64)
        t = d[f"cam{i}_t"].astype(np.float64).reshape(3)
        K = d[f"cam{i}_K"].astype(np.float64)
        cams[i] = {
            "K": K,
            "dist": d[f"cam{i}_dist"].astype(np.float64).reshape(-1),
            "R": R,
            "t": t,
            "P": projection_matrix(K, R, t),
            "center": camera_center(R, t),
            "image_size": (int(d[f"cam{i}_image_width"]), int(d[f"cam{i}_image_height"])),
        }
    return cams
