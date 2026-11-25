"""ChArUco 標定板：建立板子、偵測角點、取得角點的 3D 座標（單位 mm）。"""
from __future__ import annotations

import cv2
import numpy as np


def make_board(board_cfg: dict) -> cv2.aruco.CharucoBoard:
    name = board_cfg["dictionary"]
    if not hasattr(cv2.aruco, name):
        raise ValueError(f"cv2.aruco 中沒有字典 {name}，請檢查 config/board.yaml")
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))
    sx, sy = int(board_cfg["squares_x"]), int(board_cfg["squares_y"])
    square = float(board_cfg["square_length_mm"])
    marker = float(board_cfg["marker_length_mm"])
    if marker >= square:
        raise ValueError("marker_length_mm 必須小於 square_length_mm")
    board = cv2.aruco.CharucoBoard((sx, sy), square, marker, dictionary)
    board.setLegacyPattern(bool(board_cfg.get("legacy_pattern", False)))
    return board


class CharucoFinder:
    """包裝 cv2.aruco.CharucoDetector（OpenCV >= 4.7 新 API）。

    detectBoard 內部會以 cornerSubPix 把角點精修到次像素（Day03 p36）。
    """

    def __init__(self, board_cfg: dict):
        self.board = make_board(board_cfg)
        charuco_params = cv2.aruco.CharucoParameters()
        charuco_params.tryRefineMarkers = True
        detector_params = cv2.aruco.DetectorParameters()
        self.detector = cv2.aruco.CharucoDetector(self.board, charuco_params, detector_params)
        # 所有內角點在標定板座標系的 3D 座標 (N, 3)，z = 0，單位 mm
        self.all_object_points = np.asarray(self.board.getChessboardCorners(), dtype=np.float64)

    @property
    def max_corners(self) -> int:
        return len(self.all_object_points)

    def detect(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """回傳 (corners (N,2) float64, ids (N,) int)，沒偵測到時回傳空陣列。"""
        gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        ch_corners, ch_ids, _, _ = self.detector.detectBoard(gray)
        if ch_ids is None or len(ch_ids) == 0:
            return np.zeros((0, 2), np.float64), np.zeros((0,), np.int32)
        return ch_corners.reshape(-1, 2).astype(np.float64), ch_ids.reshape(-1).astype(np.int32)

    def object_points(self, ids: np.ndarray) -> np.ndarray:
        return self.all_object_points[np.asarray(ids, dtype=int)]

    @staticmethod
    def draw(image: np.ndarray, corners: np.ndarray, ids: np.ndarray) -> np.ndarray:
        out = image.copy()
        if len(ids) > 0:
            cv2.aruco.drawDetectedCornersCharuco(
                out, corners.reshape(-1, 1, 2).astype(np.float32),
                ids.reshape(-1, 1).astype(np.int32), (0, 255, 0))
        return out


def is_degenerate(object_points: np.ndarray, ratio: float = 0.05) -> bool:
    """角點少於 4 個，或幾乎排成一直線時，無法可靠求解位姿（回傳 True）。"""
    if len(object_points) < 4:
        return True
    xy = object_points[:, :2] - object_points[:, :2].mean(axis=0)
    s = np.linalg.svd(xy, compute_uv=False)
    return bool(s[0] == 0 or s[1] / s[0] < ratio)
