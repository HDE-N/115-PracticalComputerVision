"""路徑、設定檔、run 資料夾、latest.txt、log 與各種格式的讀寫。

注意：本作業資料夾路徑含中文，Windows 上 cv2.imread / cv2.imwrite / cv2.FileStorage
直接讀寫中文路徑會失敗，因此一律透過本模組的 imread / imwrite / fs_write / fs_read。
"""
from __future__ import annotations

import csv
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np
import yaml

# 本週作業資料夾（src/common/io_utils.py 往上兩層）
PROJECT_DIR = Path(__file__).resolve().parents[2]

# 避免主控台編碼不支援某些字元時程式中斷
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass


# ---------------------------------------------------------------- 路徑與設定
def add_root_arg(parser, config_overrides: bool = True) -> None:
    parser.add_argument(
        "--root", type=Path, default=PROJECT_DIR,
        help="作業資料夾（內含 config/、data/、output/），預設為本週資料夾",
    )
    parser.add_argument(
        "--nogui", action="store_true",
        help="不開 UI，直接以預設值執行（不加任何參數執行時會開啟 UI）",
    )
    if config_overrides:
        parser.add_argument(
            "--set", action="append", default=[], metavar="KEY=VALUE",
            help="只在本次執行覆寫設定檔中的值（不修改 config/*.yaml），可重複使用。"
                 "例：--set board.dictionary=DICT_4X4_50 --set params.intrinsic.max_rms_px=0.5 "
                 "--set cameras.cameras.0.device_index=1",
        )


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8-sig") as f:
        return yaml.safe_load(f) or {}


def apply_overrides(cfg: dict, overrides: Iterable[str] | None) -> list[str]:
    """套用 --set KEY=VALUE（VALUE 以 yaml 語法解析），回傳套用的項目文字。"""
    applied = []
    for item in overrides or []:
        if "=" not in item:
            raise ValueError(f"--set 格式應為 KEY=VALUE：{item}")
        key, text = item.split("=", 1)
        parts = [p for p in key.strip().split(".") if p != ""]
        if len(parts) < 2 or parts[0] not in cfg:
            raise ValueError(f"--set 的 KEY 應以 board. / cameras. / params. 開頭：{key}")
        value = yaml.safe_load(text) if text.strip() else None
        node: Any = cfg
        for depth, p in enumerate(parts):
            last = depth == len(parts) - 1
            if isinstance(node, list):
                try:
                    p = int(p)
                    node[p]
                except (ValueError, IndexError):
                    raise ValueError(f"--set {key}：列表索引 {p} 無效") from None
            elif isinstance(node, dict):
                if not last and p not in node:
                    raise ValueError(f"--set {key}：設定檔中沒有 {p}")
            else:
                raise ValueError(f"--set {key}：{p} 的上一層不是 mapping 或列表")
            if last:
                node[p] = value
            else:
                node = node[p]
        applied.append(f"{key.strip()} = {value!r}")
    return applied


def load_configs(root: Path, overrides: Iterable[str] | None = None) -> dict:
    """讀取 config/ 下的三個設定檔，再套用命令列 / UI 傳入的覆寫值。"""
    cfg_dir = Path(root) / "config"
    cfg = {
        "board": load_yaml(cfg_dir / "board.yaml"),
        "cameras": load_yaml(cfg_dir / "cameras.yaml"),
        "params": load_yaml(cfg_dir / "params.yaml"),
    }
    applied = apply_overrides(cfg, overrides)
    if applied:
        print("本次執行覆寫的設定（未寫入 config/*.yaml，會記錄在 config_used.yaml）：")
        for a in applied:
            print(f"  {a}")
    return cfg


def camera_ids(cameras_cfg: dict) -> list[int]:
    return [int(c["id"]) for c in cameras_cfg["cameras"]]


def parse_cam_list(text: str | None, cameras_cfg: dict) -> list[int]:
    """把 "0,1,3" 或 "all" 轉成相機 id 列表。"""
    all_ids = camera_ids(cameras_cfg)
    if text is None or str(text).lower() == "all":
        return all_ids
    ids = [int(s) for s in str(text).split(",") if s.strip() != ""]
    unknown = [i for i in ids if i not in all_ids]
    if unknown:
        raise ValueError(f"cameras.yaml 中沒有相機 {unknown}")
    return ids


def to_builtin(obj: Any) -> Any:
    """把 numpy / Path 等型別轉成 yaml、json 可以存的內建型別。"""
    if isinstance(obj, dict):
        return {str(k): to_builtin(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_builtin(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return to_builtin(obj.tolist())
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        v = float(obj)
        return None if np.isnan(v) else v
    if isinstance(obj, float) and np.isnan(obj):
        return None
    if isinstance(obj, Path):
        return obj.as_posix()
    return obj


def rel(path: Path, root: Path) -> str:
    """回傳相對於作業資料夾的路徑字串（存進紀錄檔用，避免寫入絕對路徑）。"""
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


# ---------------------------------------------------------------- 一般檔案
def save_yaml(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(to_builtin(data), f, allow_unicode=True, sort_keys=False)


def save_json(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(to_builtin(data), f, ensure_ascii=False, indent=2)


def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    """存 CSV（utf-8-sig，Excel 開啟中文不會亂碼）。"""
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else []
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: to_builtin(v) for k, v in r.items()})


# ---------------------------------------------------------------- 影像（支援中文路徑）
def imread(path: Path, flags: int = cv2.IMREAD_COLOR) -> np.ndarray | None:
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)


def imwrite(path: Path, image: np.ndarray) -> None:
    path = Path(path)
    ok, buf = cv2.imencode(path.suffix or ".png", image)
    if not ok:
        raise IOError(f"影像編碼失敗：{path}")
    buf.tofile(str(path))


def list_images(folder: Path) -> list[Path]:
    return sorted(p for p in Path(folder).glob("*.png") if p.is_file())


# ---------------------------------------------------------------- cv2.FileStorage（支援中文路徑）
def fs_write(path: Path, items: dict[str, Any], comments: Iterable[str] = ()) -> None:
    """用 cv2.FileStorage 格式寫 yaml，OpenCV 可直接讀回。

    items 的值可以是 numpy 陣列、數字、字串，或數字/字串列表。
    """
    fs = cv2.FileStorage("out.yaml", cv2.FILE_STORAGE_WRITE | cv2.FILE_STORAGE_MEMORY)
    for c in comments:
        fs.writeComment(str(c))
    for key, val in items.items():
        if isinstance(val, np.ndarray):
            fs.write(key, val.astype(np.float64) if val.dtype != np.float64 else val)
        elif isinstance(val, (bool, np.bool_)):
            fs.write(key, int(val))
        elif isinstance(val, (int, np.integer)):
            fs.write(key, int(val))
        elif isinstance(val, (float, np.floating)):
            fs.write(key, float(val))
        elif isinstance(val, str):
            fs.write(key, val)
        elif isinstance(val, (list, tuple)):
            if all(isinstance(v, (int, float, np.integer, np.floating)) for v in val):
                fs.write(key, np.asarray(val, dtype=np.float64).reshape(1, -1))
            else:
                fs.startWriteStruct(key, cv2.FileNode_SEQ)
                for v in val:
                    fs.write("", str(v))
                fs.endWriteStruct()
        elif val is None:
            continue
        else:
            raise TypeError(f"fs_write 不支援 {key} 的型別 {type(val)}")
    text = fs.releaseAndGetString()
    Path(path).write_text(text, encoding="utf-8")


def fs_read(path: Path) -> dict[str, Any]:
    """讀取 fs_write 寫出的 yaml，回傳 {key: 值}（矩陣為 numpy 陣列）。"""
    text = Path(path).read_text(encoding="utf-8")
    fs = cv2.FileStorage(text, cv2.FILE_STORAGE_READ | cv2.FILE_STORAGE_MEMORY)
    root = fs.root()
    out: dict[str, Any] = {}
    for key in root.keys():
        node = root.getNode(key)
        if node.isMap() and not node.empty():
            mat = node.mat()
            out[key] = mat if mat is not None else None
        elif node.isSeq():
            out[key] = [node.at(i).string() for i in range(node.size())]
        elif node.isString():
            out[key] = node.string()
        elif node.isInt():
            out[key] = int(node.real())
        elif node.isReal():
            out[key] = float(node.real())
    fs.release()
    return out


# ---------------------------------------------------------------- run 資料夾與 latest.txt
def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def create_run_dir(step_dir: Path) -> Path:
    """建立新的 run_YYYYMMDD_HHMMSS 資料夾（不覆寫舊的）。"""
    step_dir = Path(step_dir)
    step_dir.mkdir(parents=True, exist_ok=True)
    base = f"run_{timestamp()}"
    run_dir = step_dir / base
    n = 2
    while run_dir.exists():
        run_dir = step_dir / f"{base}_{n}"
        n += 1
    run_dir.mkdir()
    return run_dir


def set_latest(step_dir: Path, run_dir: Path) -> None:
    (Path(step_dir) / "latest.txt").write_text(Path(run_dir).name + "\n", encoding="utf-8")


def resolve_run(step_dir: Path, run: str | None = None) -> Path:
    """取得某步驟要使用的 run 資料夾：run 為 None 時讀 latest.txt，否則用指定的名稱或路徑。"""
    step_dir = Path(step_dir)
    if run:
        p = Path(run)
        if not p.is_absolute() and (step_dir / run).exists():
            p = step_dir / run
        if not p.exists():
            raise FileNotFoundError(f"找不到指定的 run 資料夾：{run}")
        return p
    latest = step_dir / "latest.txt"
    if not latest.exists():
        raise FileNotFoundError(
            f"找不到 {latest}。請先執行前一步驟並通過門檻，或用參數指定要使用的 run 資料夾。"
        )
    name = latest.read_text(encoding="utf-8").strip()
    p = step_dir / name
    if not p.exists():
        raise FileNotFoundError(f"latest.txt 指向的資料夾不存在：{p}")
    return p


# ---------------------------------------------------------------- log
def setup_logger(run_dir: Path | None = None, name: str = "cv") -> logging.Logger:
    """同時輸出到主控台與 run_dir/run.log。"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    if run_dir is not None:
        fh = logging.FileHandler(Path(run_dir) / "run.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(fh)
    logger.propagate = False
    return logger
