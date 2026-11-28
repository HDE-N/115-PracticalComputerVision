"""Pi 端共用工具：設定檔、V4L2 控制、相機擷取（保存 MJPEG 原始資料）、供電與儲存檢查。

只依賴 numpy、opencv-python、PyYAML 與系統指令 v4l2-ctl / vcgencmd，
不 import 運算端的程式，方便單獨部署到 Raspberry Pi。
"""
from __future__ import annotations

import collections
import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml

try:
    import cv2
except ImportError:  # 讓 --help 等不需要相機的功能仍可執行
    cv2 = None

PROJECT_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_DIR / "config"


# ---------------------------------------------------------------- 設定檔
def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8-sig") as f:
        return yaml.safe_load(f) or {}


def load_pi_config(path: Path | None = None) -> dict:
    return load_yaml(path or CONFIG_DIR / "pi.yaml")


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


# ---------------------------------------------------------------- 檔案（每次寫入都 fsync，斷電時最多遺失正在寫的檔案）
def write_bytes_fsync(path: Path, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def write_json_fsync(path: Path, obj) -> None:
    write_bytes_fsync(path, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))


def fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def check_storage(cfg: dict, allow_non_ssd: bool = False) -> tuple[bool, str, float]:
    """確認 capture_root 位於已掛載的 SSD 上，且剩餘空間足夠。回傳 (ok, 訊息, 剩餘 GB)。"""
    mount = Path(cfg.get("ssd_mount", "/mnt/ssd"))
    root = Path(cfg["capture_root"])
    if not allow_non_ssd and not os.path.ismount(mount):
        return False, f"{mount} 沒有掛載 SSD（請檢查 lsblk -f 與 /etc/fstab）", 0.0
    if not allow_non_ssd and mount not in root.resolve().parents and root.resolve() != mount:
        return False, f"capture_root {root} 不在 SSD {mount} 底下", 0.0
    root.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(root).free / 1e9
    need = float(cfg.get("min_free_gb", 5))
    if free_gb < need:
        return False, f"SSD 剩餘 {free_gb:.1f} GB，低於 {need} GB", free_gb
    return True, f"SSD 剩餘 {free_gb:.1f} GB", free_gb


# ---------------------------------------------------------------- 系統指令
def run_cmd(args: list[str], timeout: float = 5.0) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, f"找不到指令 {args[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"{args[0]} 逾時"


# ---------------------------------------------------------------- V4L2 控制項
def v4l2_set(device: str, controls: dict) -> list[str]:
    """依序設定控制項（順序很重要：先關自動，再設數值），回傳錯誤訊息列表。"""
    errors = []
    for name, value in controls.items():
        if value is None:
            continue
        code, out = run_cmd(["v4l2-ctl", "-d", device, "-c", f"{name}={int(value)}"])
        if code != 0:
            errors.append(f"{name}={value}: {out}")
    return errors


def v4l2_get(device: str, names: list[str]) -> dict:
    """讀回控制項目前的值，回傳 {name: int}；讀不到的不列入。"""
    out_vals = {}
    if not names:
        return out_vals
    code, out = run_cmd(["v4l2-ctl", "-d", device, "-C", ",".join(names)])
    for line in out.splitlines():
        m = re.match(r"\s*(\w+):\s*(-?\d+)", line)
        if m:
            out_vals[m.group(1)] = int(m.group(2))
    return out_vals


def wanted_controls(cfg: dict, profile: str | None) -> dict:
    ctrls = dict(cfg.get("controls") or {})
    if profile:
        profiles = cfg.get("profiles") or {}
        if profile not in profiles:
            raise ValueError(f"config/pi.yaml 沒有對焦設定檔 {profile}（可用：{list(profiles)}）")
        ctrls.update(profiles[profile] or {})
    return ctrls


def apply_controls(device: str, cfg: dict, profile: str | None) -> dict:
    """套用共同控制項 + 設定檔，讀回比對。回傳 {"wanted", "readback", "errors", "mismatch"}。"""
    wanted = wanted_controls(cfg, profile)
    errors = v4l2_set(device, wanted)
    time.sleep(0.2)
    readback = v4l2_get(device, list(wanted))
    mismatch = {k: {"wanted": int(v), "readback": readback.get(k)}
                for k, v in wanted.items() if v is not None and readback.get(k) != int(v)}
    return {"wanted": wanted, "readback": readback, "errors": errors, "mismatch": mismatch}


# ---------------------------------------------------------------- 供電、溫度
THROTTLE_BITS = {
    0: "目前電壓不足", 1: "目前 CPU 頻率受限", 2: "目前降頻", 3: "目前達溫度軟限制",
    16: "曾經電壓不足", 17: "曾經 CPU 頻率受限", 18: "曾經降頻", 19: "曾經達溫度軟限制",
}


def get_throttled() -> int | None:
    code, out = run_cmd(["vcgencmd", "get_throttled"])
    m = re.search(r"0x([0-9a-fA-F]+)", out)
    return int(m.group(1), 16) if code == 0 and m else None


def decode_throttled(value: int | None) -> list[str]:
    if value is None:
        return ["無法讀取（vcgencmd 不可用）"]
    return [text for bit, text in THROTTLE_BITS.items() if value & (1 << bit)]


def undervoltage_now(value: int | None) -> bool:
    return value is not None and bool(value & 1)


def read_temp_c() -> float | None:
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()) / 1000.0
    except (OSError, ValueError):
        code, out = run_cmd(["vcgencmd", "measure_temp"])
        m = re.search(r"([\d.]+)", out)
        return float(m.group(1)) if m else None


def read_pmic() -> dict:
    """vcgencmd pmic_read_adc：輸入電壓 EXT5V 與 PMIC 各電源軌的功耗總和。

    注意：USB 周邊（相機、SSD）的耗電不經過這些電源軌，不包含在 rails_w 內；整機功耗請用 USB 電流表量測。
    """
    code, out = run_cmd(["vcgencmd", "pmic_read_adc"])
    if code != 0:
        return {}
    amps, volts = {}, {}
    for line in out.splitlines():
        m = re.match(r"\s*(\S+)_([AV])\s+\w+\(\d+\)=([\d.]+)", line)
        if m:
            (amps if m.group(2) == "A" else volts)[m.group(1)] = float(m.group(3))
    rails_w = sum(amps[k] * volts[k] for k in amps if k in volts)
    return {"ext5v_v": volts.get("EXT5V"), "rails_w": round(rails_w, 3)}


def read_cpu_times() -> tuple[int, int]:
    """回傳 (idle, total)，用兩次取樣的差計算 CPU 使用率。"""
    with open("/proc/stat") as f:
        vals = [int(v) for v in f.readline().split()[1:]]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    return idle, sum(vals)


def read_mem_used_mb() -> float | None:
    try:
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            info[k] = int(v.split()[0])
        return (info["MemTotal"] - info["MemAvailable"]) / 1024.0
    except (OSError, KeyError, ValueError):
        return None


# ---------------------------------------------------------------- JPEG
def is_valid_jpeg(data: bytes) -> bool:
    """MJPEG 影格完整性：開頭 SOI（FFD8），後半段有 EOI（FFD9；部分相機會在 EOI 後補 0）。"""
    if len(data) < 1024 or data[:2] != b"\xff\xd8":
        return False
    return data.rfind(b"\xff\xd9") > len(data) // 2


def decode_reduced(data: bytes, factor: int = 4):
    """快速解碼成 1/factor 尺寸（2、4、8），預覽與清晰度檢查用。"""
    flag = {1: cv2.IMREAD_COLOR, 2: cv2.IMREAD_REDUCED_COLOR_2,
            4: cv2.IMREAD_REDUCED_COLOR_4, 8: cv2.IMREAD_REDUCED_COLOR_8}[factor]
    return cv2.imdecode(np.frombuffer(data, np.uint8), flag)


def sharpness(img) -> float:
    """畫面中央 50% 區域的 Laplacian 變異數（越大越清晰）。"""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = g.shape
    roi = g[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
    return float(cv2.Laplacian(roi, cv2.CV_64F).var())


# ---------------------------------------------------------------- 相機（背景執行緒持續讀取，保留最近幾張）
Frame = collections.namedtuple("Frame", "t_mono t_wall data raw")


class Camera(threading.Thread):
    """以 OpenCV V4L2 讀取 MJPEG 原始資料（CAP_PROP_CONVERT_RGB = 0），不解碼。

    若後端不支援原始資料輸出（拿到已解碼的影像），改以 JPEG 品質 95 壓縮並標記 raw=False。
    """

    def __init__(self, cam_id: int, device: str, stream: dict, ring_size: int = 6,
                 reconnect_after_s: float = 2.0):
        super().__init__(daemon=True)
        self.cam_id = cam_id
        self.device = device
        self.stream = stream
        self.reconnect_after_s = reconnect_after_s
        self.ring: collections.deque[Frame] = collections.deque(maxlen=ring_size)
        self.cond = threading.Condition()
        self.cap = None
        self.running = True
        self.frames = 0
        self.invalid = 0
        self.reconnects = 0
        self.disconnect_events: list[str] = []
        self.last_frame_mono = 0.0
        self.opened_size = None

    # -- 開啟 / 關閉
    def open(self) -> bool:
        if cv2 is None:
            raise RuntimeError("沒有安裝 opencv-python")
        real = os.path.realpath(self.device)
        cap = cv2.VideoCapture(real, cv2.CAP_V4L2)
        if not cap.isOpened():
            return False
        s = self.stream
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*s.get("fourcc", "MJPG")))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(s["width"]))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(s["height"]))
        cap.set(cv2.CAP_PROP_FPS, int(s.get("fps", 30)))
        cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
        self.opened_size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        self.cap = cap
        return True

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    def stop(self) -> None:
        self.running = False

    # -- 讀取迴圈
    def _to_bytes(self, frame) -> tuple[bytes | None, bool]:
        if frame is None:
            return None, False
        if frame.ndim == 1 or (frame.ndim == 2 and frame.shape[0] == 1):
            return frame.reshape(-1).tobytes(), True
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        return (buf.tobytes(), False) if ok else (None, False)

    def run(self) -> None:
        if self.cap is None and not self.open():
            self.disconnect_events.append(f"{now_iso()} 無法開啟 {self.device}")
        while self.running:
            if self.cap is None:
                time.sleep(0.5)
                if self.open():
                    self.reconnects += 1
                continue
            ok, frame = self.cap.read()
            t = time.monotonic()
            if ok:
                data, raw = self._to_bytes(frame)
                if data is not None and is_valid_jpeg(data):
                    with self.cond:
                        self.ring.append(Frame(t, time.time(), data, raw))
                        self.frames += 1
                        self.last_frame_mono = t
                        self.cond.notify_all()
                    continue
                self.invalid += 1
            if self.last_frame_mono and t - self.last_frame_mono > self.reconnect_after_s:
                self.disconnect_events.append(f"{now_iso()} 超過 {self.reconnect_after_s}s 沒有影格，重新開啟")
                self.close()
                self.last_frame_mono = time.monotonic()
            elif not ok:
                time.sleep(0.01)
        self.close()

    # -- 取影格
    def latest(self) -> Frame | None:
        with self.cond:
            return self.ring[-1] if self.ring else None

    def frames_after(self, t_mono: float, n: int, timeout: float) -> list[Frame]:
        """等待時間戳晚於 t_mono 的 n 張影格（逾時則回傳目前已收到的）。"""
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                got = [f for f in self.ring if f.t_mono > t_mono]
                if len(got) >= n:
                    return got[:n]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return got
                self.cond.wait(remaining)

    def wait_first_frame(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.latest() is not None:
                return True
            time.sleep(0.05)
        return False


def pick_synchronized(candidates: dict[int, list[Frame]]) -> tuple[dict[int, Frame], float]:
    """從每台相機的候選影格中，挑出時間差最小的組合。回傳 ({cam: Frame}, 時間差 ms)。"""
    ids = sorted(candidates)
    ref_id = ids[0]
    best, best_skew = None, float("inf")
    for ref in candidates[ref_id]:
        combo = {ref_id: ref}
        for cid in ids[1:]:
            combo[cid] = min(candidates[cid], key=lambda f: abs(f.t_mono - ref.t_mono))
        ts = [f.t_mono for f in combo.values()]
        skew = (max(ts) - min(ts)) * 1000.0
        if skew < best_skew:
            best, best_skew = combo, skew
    return best or {}, best_skew


def open_cameras(cfg: dict, cam_ids: list[int] | None = None, devices: list[str] | None = None) -> list[Camera]:
    """依 config/pi.yaml 開啟相機並啟動讀取執行緒。devices 指定時改用這些裝置（測試 4 台用）。"""
    cap_cfg = cfg.get("capture") or {}
    if devices:
        specs = [{"id": i, "device": d} for i, d in enumerate(devices)]
    else:
        specs = [c for c in cfg["cameras"] if cam_ids is None or int(c["id"]) in cam_ids]
    cams = []
    for c in specs:
        cam = Camera(int(c["id"]), c["device"], cfg["stream"], int(cap_cfg.get("ring_size", 6)),
                     float(cap_cfg.get("reconnect_after_s", 2.0)))
        if not os.path.exists(c["device"]):
            raise FileNotFoundError(f"cam{c['id']} 裝置不存在：{c['device']}（檢查 USB 是否插好、插孔是否正確）")
        if not cam.open():
            raise RuntimeError(f"cam{c['id']} 無法開啟：{c['device']}")
        cam.start()
        cams.append(cam)
    return cams


def stop_cameras(cams: list[Camera]) -> None:
    for c in cams:
        c.stop()
    for c in cams:
        c.join(timeout=2.0)


def list_usb_video_devices() -> list[str]:
    """列出所有 USB 相機的擷取節點（by-path 的 video-index0，依實體路徑去重）。"""
    seen, out = set(), []
    for p in sorted(Path("/dev/v4l/by-path").glob("*usb-*video-index0")):
        real = os.path.realpath(p)
        if real not in seen:
            seen.add(real)
            out.append(str(p))
    return out


# ---------------------------------------------------------------- ChArUco（標定模式顯示角點數）
class BoardCounter:
    """依 config/board.yaml 偵測 ChArUco 角點數（只用於拍攝時的提示）。"""

    def __init__(self, board_yaml: Path):
        b = load_yaml(board_yaml)
        dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, b["dictionary"]))
        board = cv2.aruco.CharucoBoard((int(b["squares_x"]), int(b["squares_y"])),
                                       float(b["square_length_mm"]), float(b["marker_length_mm"]), dictionary)
        board.setLegacyPattern(bool(b.get("legacy_pattern", False)))
        self.detector = cv2.aruco.CharucoDetector(board)
        self.max_corners = (int(b["squares_x"]) - 1) * (int(b["squares_y"]) - 1)

    def count(self, img) -> int:
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        _, ids, _, _ = self.detector.detectBoard(g)
        return 0 if ids is None else int(len(ids))
