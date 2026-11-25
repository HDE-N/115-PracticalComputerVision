"""UVC 相機：開啟、手動 3A 設定（Day03 p33–34）、讀回實際設定、多相機近同步擷取。"""
from __future__ import annotations

import time

import cv2
import numpy as np

BACKENDS = {
    "DSHOW": cv2.CAP_DSHOW,
    "MSMF": cv2.CAP_MSMF,
    "V4L2": cv2.CAP_V4L2,
    "ANY": cv2.CAP_ANY,
}

# 設定檔欄位 -> OpenCV 屬性；順序即設定順序（先關自動，再設手動值）
PROPS = [
    ("width", cv2.CAP_PROP_FRAME_WIDTH),
    ("height", cv2.CAP_PROP_FRAME_HEIGHT),
    ("fps", cv2.CAP_PROP_FPS),
    ("autofocus", cv2.CAP_PROP_AUTOFOCUS),
    ("focus", cv2.CAP_PROP_FOCUS),
    ("auto_exposure", cv2.CAP_PROP_AUTO_EXPOSURE),
    ("exposure", cv2.CAP_PROP_EXPOSURE),
    ("gain", cv2.CAP_PROP_GAIN),
    ("auto_wb", cv2.CAP_PROP_AUTO_WB),
    ("wb_temperature", cv2.CAP_PROP_WB_TEMPERATURE),
]
PROP_IDS = dict(PROPS)
EXTRA_READBACK = [
    ("brightness", cv2.CAP_PROP_BRIGHTNESS),
    ("contrast", cv2.CAP_PROP_CONTRAST),
    ("saturation", cv2.CAP_PROP_SATURATION),
    ("sharpness", cv2.CAP_PROP_SHARPNESS),
]


def camera_settings(cameras_cfg: dict, cam_id: int) -> dict:
    """合併 defaults 與該相機的個別覆寫設定。"""
    for cam in cameras_cfg["cameras"]:
        if int(cam["id"]) == cam_id:
            settings = dict(cameras_cfg.get("defaults", {}))
            settings.update({k: v for k, v in cam.items() if k != "id"})
            return settings
    raise ValueError(f"cameras.yaml 中沒有相機 {cam_id}")


# 各 backend 開 / 關自動功能時要設定的值 (開, 關)。
# DSHOW 的 auto_exposure：四捨五入為 1 = 自動，其他 = 手動（cameras.yaml 慣用 0.25）；
# V4L2 的 auto_exposure：3 = 自動（光圈優先），1 = 手動。
AUTO_VALUES = {
    "DSHOW": {"autofocus": (1, 0), "auto_exposure": (1, 0.25), "auto_wb": (1, 0)},
    "V4L2": {"autofocus": (1, 0), "auto_exposure": (3, 1), "auto_wb": (1, 0)},
}
AUTO_VALUES_DEFAULT = {"autofocus": (1, 0), "auto_exposure": (0.75, 0.25), "auto_wb": (1, 0)}


def auto_values(backend_name: str) -> dict:
    return AUTO_VALUES.get(str(backend_name).upper(), AUTO_VALUES_DEFAULT)


def _decode_fourcc(value: float) -> str:
    v = int(value)
    return "".join(chr((v >> (8 * i)) & 0xFF) for i in range(4)).strip("\x00")


class Camera:
    def __init__(self, cam_id: int, cameras_cfg: dict):
        self.cam_id = cam_id
        self.settings = camera_settings(cameras_cfg, cam_id)
        self.device_index = int(self.settings["device_index"])
        backend_name = str(cameras_cfg.get("backend", "ANY")).upper()
        self.backend_name = backend_name
        self.cap = cv2.VideoCapture(self.device_index, BACKENDS.get(backend_name, cv2.CAP_ANY))
        if not self.cap.isOpened():
            raise RuntimeError(f"無法開啟 cam{cam_id}（device_index={self.device_index}）")

        fourcc = cameras_cfg.get("fourcc")
        if fourcc:
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*str(fourcc)))
        if cameras_cfg.get("buffer_size") is not None:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, int(cameras_cfg["buffer_size"]))
        for key, prop in PROPS:
            value = self.settings.get(key)
            if value is not None:
                self.cap.set(prop, float(value))
        # 丟掉前幾張畫面，讓曝光等設定生效
        for _ in range(5):
            self.cap.grab()

    def read_settings(self) -> dict:
        """讀回相機「實際」的設定值（相機不一定接受所有設定）。"""
        out = {"device_index": self.device_index, "backend": self.backend_name}
        for key, prop in PROPS + EXTRA_READBACK:
            out[key] = float(self.cap.get(prop))
        out["fourcc"] = _decode_fourcc(self.cap.get(cv2.CAP_PROP_FOURCC))
        out["requested"] = {k: self.settings.get(k) for k, _ in PROPS}
        return out

    def get(self, key: str) -> float:
        return float(self.cap.get(PROP_IDS[key]))

    def set(self, key: str, value: float) -> bool:
        return bool(self.cap.set(PROP_IDS[key], float(value)))

    def set_auto(self, on: bool, keys=("autofocus", "auto_exposure", "auto_wb")) -> None:
        """開啟或關閉自動對焦 / 自動曝光 / 自動白平衡。"""
        vals = auto_values(self.backend_name)
        for k in keys:
            self.set(k, vals[k][0 if on else 1])

    def read(self) -> tuple[bool, np.ndarray | None]:
        return self.cap.read()

    def release(self) -> None:
        self.cap.release()


def grab_all(cams: list[Camera]) -> tuple[list[np.ndarray | None], list[float]]:
    """多相機近同步擷取：先對所有相機 grab()（很快），再逐一 retrieve()（解碼較慢）。

    回傳 (frames, grab_times)；grab_times 為各相機 grab 完成的時間（秒，perf_counter），
    彼此的差可當作擷取時間差的估計值。
    """
    oks, times = [], []
    for c in cams:
        oks.append(c.cap.grab())
        times.append(time.perf_counter())
    frames = []
    for c, ok in zip(cams, oks):
        if ok:
            ok2, frame = c.cap.retrieve()
            frames.append(frame if ok2 else None)
        else:
            frames.append(None)
    return frames, times


def sharpness(image: np.ndarray) -> float:
    """清晰度指標（Laplacian 變異數），數值越小越模糊。"""
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(small, cv2.CV_64F).var())
