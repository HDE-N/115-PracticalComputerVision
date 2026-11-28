"""路徑、設定檔、run 資料夾、latest.txt、log 與檔案讀寫（沿用 1151125 的規則）。"""
from __future__ import annotations

import copy
import csv
import json
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import yaml

PROJECT_DIR = Path(__file__).resolve().parents[2]

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

# 各步驟的輸出資料夾名稱（output/<名稱>/<scene>/run_*/）
STEP_DIRS = {
    "env": "01_env", "calib": "03_calib", "ingest": "06_ingest", "preprocess": "07_preprocess", "sfm": "08_sfm",
    "align": "09_align", "train": "10_train", "evaluate": "11_eval", "cleanup": "12_cleanup",
    "export": "14_export",
}


# ---------------------------------------------------------------- 設定檔
def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8-sig") as f:
        return yaml.safe_load(f) or {}


def deep_merge(base: dict, over: dict | None) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def apply_overrides(cfg: dict, overrides: Iterable[str] | None) -> list[str]:
    """--set KEY=VALUE（VALUE 以 yaml 解析），KEY 以設定檔名稱開頭，例：params.sfm.min_registered_ratio=0.9"""
    applied = []
    for item in overrides or []:
        if "=" not in item:
            raise ValueError(f"--set 格式應為 KEY=VALUE：{item}")
        key, text = item.split("=", 1)
        parts = [p for p in key.strip().split(".") if p]
        if len(parts) < 2 or parts[0] not in cfg:
            raise ValueError(f"--set 的 KEY 應以 {sorted(cfg)} 之一開頭：{key}")
        node = cfg
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = yaml.safe_load(text) if text.strip() else None
        applied.append(f"{key.strip()} = {node[parts[-1]]!r}")
    return applied


def load_configs(root: Path, scene: str | None, overrides: Iterable[str] | None = None) -> dict:
    """讀取 config/ 下的設定，場景的 params_override 併入 params，再套用 --set。"""
    cdir = Path(root) / "config"
    cfg = {
        "pipeline": load_yaml(cdir / "pipeline.yaml"),
        "params": load_yaml(cdir / "params.yaml"),
        "board": load_yaml(cdir / "board.yaml"),
        "rig": load_yaml(cdir / "rig.yaml") if (cdir / "rig.yaml").exists() else {"profiles": {}},
        "pi": load_yaml(cdir / "pi.yaml") if (cdir / "pi.yaml").exists() else {},
        "scene": {},
    }
    if scene:
        p = cdir / "scenes" / f"{scene}.yaml"
        if not p.exists():
            raise FileNotFoundError(f"找不到場景設定 {p}（複製 config/scenes/book_001.yaml 改名後修改）")
        cfg["scene"] = load_yaml(p)
        cfg["params"] = deep_merge(cfg["params"], cfg["scene"].get("params_override"))
    applied = apply_overrides(cfg, overrides)
    if applied:
        print("本次執行覆寫的設定（不寫入 config/，記錄在 config_used.yaml）：")
        for a in applied:
            print(f"  {a}")
    return cfg


def resolve_path(text: str | Path, base: Path = PROJECT_DIR) -> Path:
    p = Path(str(text)).expanduser()
    return p if p.is_absolute() else (base / p).resolve()


def to_builtin(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): to_builtin(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_builtin(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return to_builtin(obj.tolist())
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return None if np.isnan(v) else v
    if isinstance(obj, Path):
        return obj.as_posix()
    return obj


def rel(path: Path, root: Path = PROJECT_DIR) -> str:
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


# ---------------------------------------------------------------- 檔案
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
    if fieldnames is None:
        fieldnames = []
        for r in rows:
            for k in r:
                if k not in fieldnames:
                    fieldnames.append(k)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: to_builtin(v) for k, v in r.items()})


def load_csv(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def link_or_copy(src: Path, dst: Path) -> None:
    """建立硬連結（不佔額外空間），跨檔案系統或不支援時改為複製。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        dst.hardlink_to(src)
    except (OSError, AttributeError, NotImplementedError):
        shutil.copy2(src, dst)


def imread(path: Path, flags: int | None = None):
    """支援中文路徑的 cv2.imread。"""
    import cv2
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR if flags is None else flags)


def imwrite(path: Path, image) -> None:
    import cv2
    ok, buf = cv2.imencode(Path(path).suffix or ".png", image)
    if not ok:
        raise IOError(f"影像編碼失敗：{path}")
    buf.tofile(str(path))


def dir_size_mb(path: Path) -> float:
    return sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file()) / 1e6


# ---------------------------------------------------------------- run 資料夾與 latest.txt
def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def step_dir(root: Path, step: str, key: str) -> Path:
    """output/<步驟>/<scene 或 profile>/"""
    return Path(root) / "output" / STEP_DIRS[step] / key


def create_run_dir(sdir: Path) -> Path:
    sdir.mkdir(parents=True, exist_ok=True)
    base = f"run_{timestamp()}"
    run = sdir / base
    n = 2
    while run.exists():
        run = sdir / f"{base}_{n}"
        n += 1
    run.mkdir()
    return run


def set_latest(sdir: Path, run_dir: Path) -> None:
    (Path(sdir) / "latest.txt").write_text(Path(run_dir).name + "\n", encoding="utf-8")


def resolve_run(sdir: Path, run: str | None = None) -> Path:
    sdir = Path(sdir)
    if run:
        p = Path(run)
        if not p.is_absolute() and (sdir / run).exists():
            p = sdir / run
        if not p.exists():
            raise FileNotFoundError(f"找不到指定的 run：{run}")
        return p
    latest = sdir / "latest.txt"
    if not latest.exists():
        raise FileNotFoundError(f"找不到 {rel(latest)}：請先執行前一步驟並通過門檻，或以 --input-run 指定。")
    p = sdir / latest.read_text(encoding="utf-8").strip()
    if not p.exists():
        raise FileNotFoundError(f"latest.txt 指向的資料夾不存在：{p}")
    return p


def setup_logger(run_dir: Path | None, name: str = "gs3d") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
    logger.addHandler(sh)
    if run_dir is not None:
        fh = logging.FileHandler(Path(run_dir) / "run.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(fh)
    logger.propagate = False
    return logger
