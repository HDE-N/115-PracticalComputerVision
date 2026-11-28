"""幾何工具：四元數、相似轉換（Umeyama）、多視角三角化。

COLMAP 位姿慣例：X_cam = R · X_world + t（cam_from_world），四元數順序 (w, x, y, z)。
"""
from __future__ import annotations

import numpy as np


def qvec_to_R(q) -> np.ndarray:
    w, x, y, z = np.asarray(q, np.float64) / np.linalg.norm(q)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def R_to_qvec(R) -> np.ndarray:
    """旋轉矩陣轉四元數 (w, x, y, z)，w >= 0。"""
    R = np.asarray(R, np.float64)
    K = np.array([
        [R[0, 0] - R[1, 1] - R[2, 2], 0, 0, 0],
        [R[1, 0] + R[0, 1], R[1, 1] - R[0, 0] - R[2, 2], 0, 0],
        [R[2, 0] + R[0, 2], R[2, 1] + R[1, 2], R[2, 2] - R[0, 0] - R[1, 1], 0],
        [R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1], R[0, 0] + R[1, 1] + R[2, 2]],
    ]) / 3.0
    vals, vecs = np.linalg.eigh(K)
    q = vecs[[3, 0, 1, 2], np.argmax(vals)]
    return -q if q[0] < 0 else q


def quat_multiply(a, b) -> np.ndarray:
    """四元數乘法 a ⊗ b，支援 (4,) 與 (N, 4)，順序 (w, x, y, z)。"""
    a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
    aw, ax, ay, az = np.moveaxis(a, -1, 0)
    bw, bx, by, bz = np.moveaxis(b, -1, 0)
    return np.stack([
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ], axis=-1)


def umeyama(src: np.ndarray, dst: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """求相似轉換 dst ≈ s · R · src + t（Umeyama 1991）。回傳 (s, R, t)。"""
    src, dst = np.asarray(src, np.float64), np.asarray(dst, np.float64)
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    cov = xd.T @ xs / len(src)
    U, D, Vt = np.linalg.svd(cov)
    S = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[2, 2] = -1
    R = U @ S @ Vt
    var_s = (xs ** 2).sum() / len(src)
    s = float(np.trace(np.diag(D) @ S) / var_s)
    t = mu_d - s * R @ mu_s
    return s, R, t


def triangulate_dlt(Ps: list[np.ndarray], pts: list[np.ndarray]) -> np.ndarray:
    """多視角 DLT 三角化：Ps 為 3x4 投影矩陣，pts 為對應的像素座標 (2,)。"""
    A = []
    for P, (x, y) in zip(Ps, pts):
        A.append(x * P[2] - P[0])
        A.append(y * P[2] - P[1])
    _, _, Vt = np.linalg.svd(np.asarray(A))
    X = Vt[-1]
    return X[:3] / X[3]


def project(P: np.ndarray, X: np.ndarray) -> np.ndarray:
    x = P @ np.append(X, 1.0)
    return x[:2] / x[2]


def transform_pose(R_cw: np.ndarray, t_cw: np.ndarray, s: float, R: np.ndarray, t: np.ndarray):
    """世界座標套用相似轉換 X' = s·R·X + t 後，相機位姿 (cam_from_world) 的新值。

    相機座標同時乘上 s（保持單位一致）：R' = R_cw · Rᵀ，t' = s·t_cw − R'·t
    """
    R_new = R_cw @ R.T
    t_new = s * np.asarray(t_cw) - R_new @ t
    return R_new, t_new


# 板子座標（OpenCV：x 右、y 下、z 指向板子背面 = 桌面下方）→ 世界座標（x 同板子、y 朝上、z = 板子 y）
BOARD_TO_YUP = np.array([[1.0, 0, 0], [0, 0, -1.0], [0, 1.0, 0]])
