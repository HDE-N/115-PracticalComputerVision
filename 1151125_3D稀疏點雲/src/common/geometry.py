"""幾何工具：位姿轉換、旋轉平均、基礎矩陣、投影與三角測量。

外參定義（全專案一致，Day04 p3）：P_cam = R · P_world + t，世界座標 = cam0 相機座標。
"""
from __future__ import annotations

import cv2
import numpy as np


def to_T(R: np.ndarray, t: np.ndarray) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = np.asarray(t).reshape(3)
    return T


def from_T(T: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return T[:3, :3].copy(), T[:3, 3].copy()


def rvec_to_R(rvec: np.ndarray) -> np.ndarray:
    return cv2.Rodrigues(np.asarray(rvec, dtype=np.float64).reshape(3, 1))[0]


def R_to_rvec(R: np.ndarray) -> np.ndarray:
    return cv2.Rodrigues(np.asarray(R, dtype=np.float64))[0].reshape(3)


def inv_T(T: np.ndarray) -> np.ndarray:
    R, t = from_T(T)
    return to_T(R.T, -R.T @ t)


def rotation_mean(Rs: list[np.ndarray]) -> np.ndarray:
    """旋轉矩陣的平均（chordal L2 mean：把矩陣總和投影回最接近的旋轉矩陣）。"""
    M = np.sum(Rs, axis=0)
    U, _, Vt = np.linalg.svd(M)
    D = np.diag([1.0, 1.0, np.sign(np.linalg.det(U @ Vt))])
    return U @ D @ Vt


def rotation_angle_deg(R: np.ndarray) -> float:
    c = (np.trace(R) - 1.0) / 2.0
    return float(np.degrees(np.arccos(np.clip(c, -1.0, 1.0))))


def camera_center(R: np.ndarray, t: np.ndarray) -> np.ndarray:
    """相機光心在世界座標中的位置 C = -R^T t。"""
    return -R.T @ np.asarray(t).reshape(3)


def projection_matrix(K: np.ndarray, R: np.ndarray, t: np.ndarray) -> np.ndarray:
    """P = K [R | t]（3x4）。"""
    return K @ np.hstack([R, np.asarray(t).reshape(3, 1)])


def skew(v: np.ndarray) -> np.ndarray:
    x, y, z = np.asarray(v).reshape(3)
    return np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]], dtype=np.float64)


def relative_pose(Ri, ti, Rj, tj) -> tuple[np.ndarray, np.ndarray]:
    """相機 i 座標 -> 相機 j 座標：P_j = R_ij P_i + t_ij。"""
    R_ij = Rj @ Ri.T
    t_ij = np.asarray(tj).reshape(3) - R_ij @ np.asarray(ti).reshape(3)
    return R_ij, t_ij


def fundamental_from_calib(Ki, Ri, ti, Kj, Rj, tj) -> np.ndarray:
    """由標定結果計算基礎矩陣：x_j^T F_ij x_i = 0，F_ij = K_j^-T [t_ij]x R_ij K_i^-1（Day04 p43）。"""
    R_ij, t_ij = relative_pose(Ri, ti, Rj, tj)
    E = skew(t_ij) @ R_ij
    F = np.linalg.inv(Kj).T @ E @ np.linalg.inv(Ki)
    return F / np.linalg.norm(F)


def epipolar_distance(F: np.ndarray, pts_i: np.ndarray, pts_j: np.ndarray) -> np.ndarray:
    """對稱對極線距離（px）：max(點 j 到 F·x_i 的距離, 點 i 到 F^T·x_j 的距離)。"""
    xi = np.hstack([pts_i, np.ones((len(pts_i), 1))])
    xj = np.hstack([pts_j, np.ones((len(pts_j), 1))])
    lj = xi @ F.T          # 影像 j 上的對極線
    li = xj @ F            # 影像 i 上的對極線
    num = np.abs(np.sum(xj * lj, axis=1))
    dj = num / np.maximum(np.linalg.norm(lj[:, :2], axis=1), 1e-12)
    di = num / np.maximum(np.linalg.norm(li[:, :2], axis=1), 1e-12)
    return np.maximum(di, dj)


def undistort_pixels(pts: np.ndarray, K: np.ndarray, dist: np.ndarray) -> np.ndarray:
    """去畸變，回傳理想針孔模型下的像素座標（P = K）。"""
    if len(pts) == 0:
        return np.zeros((0, 2), np.float64)
    out = cv2.undistortPoints(pts.reshape(-1, 1, 2).astype(np.float64), K, dist, P=K)
    return out.reshape(-1, 2)


def project(P: np.ndarray, X: np.ndarray) -> np.ndarray:
    """以 3x4 投影矩陣把 (N,3) 世界點投影到 (N,2) 像素（不含畸變）。"""
    Xh = np.hstack([X, np.ones((len(X), 1))])
    x = Xh @ P.T
    return x[:, :2] / x[:, 2:3]


def triangulate_dlt(Ps: list[np.ndarray], xs: list[np.ndarray]) -> np.ndarray:
    """多視角線性三角測量（DLT）：每個視角提供兩條方程式，以 SVD 求最小平方解。"""
    A = []
    for P, (u, v) in zip(Ps, xs):
        A.append(u * P[2] - P[0])
        A.append(v * P[2] - P[1])
    _, _, Vt = np.linalg.svd(np.asarray(A))
    X = Vt[-1]
    return X[:3] / X[3]
