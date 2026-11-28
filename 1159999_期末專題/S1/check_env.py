"""S1（運算端）：環境檢查 —— 確認 PATH.md B 部分的安裝都正確。

檢查項目：Python、NVIDIA 驅動 / GPU、CUDA Toolkit（nvcc）、PyTorch（CUDA 版本與 GPU 運算能力）、
gsplat（實際在 GPU 上算一張小圖）、COLMAP（版本、CUDA、rig 支援）、gsplat 訓練程式路徑、
OpenCV（cv2.aruco、沒有重複安裝）、其他 Python 套件、1151125 標定程式、硬碟空間。

範例：
  python S1/check_env.py
  python S1/check_env.py --skip-gpu-test        # 不執行 gsplat 的 GPU 測試

輸出：output/01_env/<主機名稱>/run_YYYYMMDD_HHMMSS/env_report.json、run.log；全部通過才更新 latest.txt。
結束碼：0 = 全部通過；2 = 有必要項目未通過。
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata as md
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # 專題資料夾
from common.core.io_utils import (PROJECT_DIR, create_run_dir, load_yaml, rel, resolve_path,  # noqa: E402
                                  save_json, set_latest, setup_logger, step_dir)


def run(args: list[str], timeout: float = 30.0) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, errors="replace")
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, f"找不到 {args[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"{args[0]} 逾時"


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(v) for v in re.findall(r"\d+", text)[:3])


class Report:
    def __init__(self, log):
        self.log = log
        self.items: list[dict] = []

    def add(self, name: str, ok: bool, detail: str, required: bool = True, hint: str = "") -> bool:
        self.items.append({"name": name, "ok": bool(ok), "required": required, "detail": detail, "hint": hint})
        mark = "OK" if ok else ("!!" if required else "??")
        self.log.info(f"[{mark}] {name}：{detail}" + (f"\n       → {hint}" if not ok and hint else ""))
        return bool(ok)

    @property
    def passed(self) -> bool:
        return all(i["ok"] for i in self.items if i["required"])


def check_gpu_driver(r: Report) -> None:
    code, out = run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,compute_cap",
                     "--format=csv,noheader"])
    r.add("NVIDIA 驅動 / GPU", code == 0 and bool(out), out.splitlines()[0] if code == 0 and out else out,
          hint="確認驅動已安裝（nvidia-smi）")


def check_nvcc(r: Report) -> tuple[int, ...] | None:
    code, out = run(["nvcc", "--version"])
    m = re.search(r"release (\d+\.\d+)", out)
    ver = version_tuple(m.group(1)) if m else None
    r.add("CUDA Toolkit（nvcc）", ver is not None and ver >= (12, 8), f"release {m.group(1)}" if m else out[:120],
          hint="PATH.md B2：在 gs3d 環境 conda install -c nvidia/label/cuda-12.8.1 cuda-toolkit（Blackwell 需要 >= 12.8）")
    return ver


def arch_supported(cap: tuple[int, int], arch_list: list[str]) -> bool:
    """GPU（compute capability cap）能否執行 PyTorch 內建的程式碼：
    同主版本、次版本不高於 GPU 的 sm_XY（二進位相容），或不高於 GPU 的 compute_XY（PTX 可即時編譯）。"""
    gpu = cap[0] * 10 + cap[1]
    for a in arch_list:
        m = re.match(r"(sm|compute)_(\d+)", a)
        if not m:
            continue
        v = int(m.group(2))
        if m.group(1) == "sm" and v // 10 == cap[0] and v <= gpu:
            return True
        if m.group(1) == "compute" and v <= gpu:
            return True
    return False


def check_torch(r: Report, nvcc_ver) -> bool:
    try:
        import torch
    except Exception as e:  # noqa: BLE001
        r.add("PyTorch", False, f"無法 import：{e}", hint="PATH.md B3：pip install torch --index-url https://download.pytorch.org/whl/cu128")
        return False
    cuda = torch.version.cuda
    avail = torch.cuda.is_available()
    detail = f"{torch.__version__}，CUDA {cuda}，GPU 可用 {avail}"
    ok = avail
    if avail:
        cap = torch.cuda.get_device_capability(0)
        detail += f"，{torch.cuda.get_device_name(0)}，compute capability {cap[0]}.{cap[1]}"
        arch_ok = arch_supported(cap, torch.cuda.get_arch_list())
        r.add("PyTorch 支援此 GPU 架構", arch_ok, ", ".join(torch.cuda.get_arch_list()),
              hint="此版本 PyTorch 未包含 sm_120，請改裝 cu128 以上的版本")
    r.add("PyTorch", ok, detail, hint="確認安裝的是 CUDA 版（cu128），不是 CPU 版")
    if cuda and nvcc_ver:
        same_major = version_tuple(cuda)[:1] == nvcc_ver[:1]
        r.add("PyTorch 與 nvcc 的 CUDA 主版本一致", same_major, f"PyTorch {cuda} / nvcc {'.'.join(map(str, nvcc_ver))}",
              hint="gsplat 以 nvcc 編譯，主版本需與 PyTorch 相同")
    return avail


def check_gsplat(r: Report, gpu_test: bool, torch_ok: bool) -> None:
    try:
        import gsplat
    except Exception as e:  # noqa: BLE001
        r.add("gsplat", False, f"無法 import：{e}", hint="PATH.md B4")
        return
    r.add("gsplat", True, f"版本 {getattr(gsplat, '__version__', '?')}")
    if not gpu_test:
        return
    if not torch_ok:
        r.add("gsplat GPU 測試", False, "PyTorch 無法使用 GPU，略過")
        return
    try:
        import torch
        torch.manual_seed(0)
        n, dev = 2000, "cuda"
        means = torch.randn(n, 3, device=dev) * 0.5
        means[:, 2] += 4.0
        quats = torch.randn(n, 4, device=dev)
        scales = torch.rand(n, 3, device=dev) * 0.05
        opac = torch.rand(n, device=dev)
        colors = torch.rand(n, 3, device=dev)
        K = torch.tensor([[[300.0, 0, 160], [0, 300.0, 120], [0, 0, 1]]], device=dev)
        img, alpha, _ = gsplat.rasterization(means, quats, scales, opac, colors,
                                             torch.eye(4, device=dev)[None], K, 320, 240)
        torch.cuda.synchronize()
        cover = float((alpha > 0.01).float().mean())
        r.add("gsplat GPU 測試", img.shape[-1] == 3 and cover > 0, f"輸出 {tuple(img.shape)}，覆蓋率 {cover:.1%}",
              hint="CUDA 核心沒有正確編譯：設定 TORCH_CUDA_ARCH_LIST=12.0 後重新 pip install -e . --no-build-isolation")
    except Exception as e:  # noqa: BLE001
        r.add("gsplat GPU 測試", False, f"{type(e).__name__}: {e}",
              hint="CUDA 核心沒有正確編譯：設定 TORCH_CUDA_ARCH_LIST=12.0 後重新 pip install -e . --no-build-isolation")


def check_compiler(r: Report, nvcc_ver) -> None:
    """nvcc 使用的主機編譯器（CUDAHOSTCXX > CXX > g++）版本是否被此 CUDA 支援（12.8 約到 GCC 14）。"""
    cxx = os.environ.get("CUDAHOSTCXX") or os.environ.get("CXX") or "g++"
    code, out = run([cxx, "-dumpversion"])
    if code != 0:
        r.add("nvcc 主機編譯器", False, f"{cxx}：{out[:80]}", required=False,
              hint="PATH.md B2：conda install gcc_linux-64=13 gxx_linux-64=13，並設定 CUDAHOSTCXX")
        return
    major = version_tuple(out)[0] if version_tuple(out) else 99
    limit = 14 if (nvcc_ver or (12, 8)) < (13, 0) else 15
    r.add("nvcc 主機編譯器", major <= limit, f"{cxx}（GCC {out.strip()}）", required=False,
          hint=f"CUDA {'.'.join(map(str, nvcc_ver or ()))} 只支援到 GCC {limit}：PATH.md B2 安裝 conda 的 GCC 13 並設定 CUDAHOSTCXX")
    r.add("CUDA_HOME 已設定", bool(os.environ.get("CUDA_HOME")), os.environ.get("CUDA_HOME", "（未設定）"),
          required=False, hint="PATH.md B2：在 gs3d_env.sh 設定 CUDA_HOME")


def colmap_exe(pipeline: dict) -> str:
    return os.path.expanduser(str((pipeline.get("colmap") or {}).get("bin", "colmap")))


def check_colmap_gpu_sift(r: Report, exe: str, work: Path) -> None:
    """用 2 張合成影像實際跑一次 GPU 版 SIFT；失敗時 S8 會改用 CPU 版（不影響結果，只是較慢）。"""
    import numpy as np
    try:
        import cv2
    except Exception:  # noqa: BLE001
        r.add("COLMAP GPU SIFT", False, "沒有 OpenCV，無法產生測試影像", required=False)
        return
    code, help_text = run([exe, "feature_extractor", "-h"])
    m = re.search(r"--(\w+)\.use_gpu", help_text)
    if code != 0 or not m:
        r.add("COLMAP GPU SIFT", False, "feature_extractor 沒有 use_gpu 選項", required=False)
        return
    img_dir = work / "colmap_gpu_test" / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    base = cv2.GaussianBlur((rng.random((480, 640)) * 255).astype(np.uint8), (0, 0), 2)
    for i in range(2):
        cv2.imwrite(str(img_dir / f"{i}.png"), np.roll(base, 20 * i, axis=1))
    db = work / "colmap_gpu_test" / "test.db"
    code, out = run([exe, "feature_extractor", "--database_path", str(db), "--image_path", str(img_dir),
                     f"--{m.group(1)}.use_gpu", "1"], timeout=120)
    n_kp = 0
    if code == 0 and db.exists():
        import sqlite3
        with sqlite3.connect(db) as con:
            n_kp = con.execute("SELECT COALESCE(SUM(rows), 0) FROM keypoints").fetchone()[0]
    ok = code == 0 and n_kp > 0
    last = out.strip().splitlines()[-1] if out.strip() else ""
    r.add("COLMAP GPU SIFT", ok, f"特徵點 {n_kp}" if ok else f"失敗：{last[:120]}", required=False,
          hint="S8 會自動改用 CPU 版 SIFT（書約 200 張影像，CPU 也只需幾分鐘）；需要 GPU 版時依 PATH.md B5 自行編譯")


def check_colmap(r: Report, pipeline: dict, work: Path) -> None:
    exe = colmap_exe(pipeline)
    code, out = run([exe, "-h"])
    if code == 127:
        r.add("COLMAP", False, out, hint="PATH.md B5（conda 環境 colmap）；或在 config/pipeline.yaml 的 colmap.bin 填完整路徑")
        return
    head = out.splitlines()[0] if out else ""
    m = re.search(r"COLMAP\s+(\d+\.\d+(?:\.\d+)?)", out)
    ver = version_tuple(m.group(1)) if m else None
    r.add("COLMAP 版本 >= 3.12", ver is not None and ver >= (3, 12), head or out[:120],
          hint="rig 功能需要 3.12 以上；改裝新版，或在 config/pipeline.yaml 設 colmap.use_rig: false")
    r.add("COLMAP 支援 CUDA", "with CUDA" in out, head, required=False,
          hint="PATH.md B5：conda create -n colmap -c conda-forge \"colmap=*=*cuda*\"（沒有 CUDA 版時 S8 仍可用 CPU 執行，但較慢）")
    code_rig, out_rig = run([exe, "rig_configurator", "-h"])
    r.add("COLMAP rig_configurator", code_rig == 0, "可用" if code_rig == 0 else out_rig[:120], required=False,
          hint="沒有 rig 功能時 S8 會自動改為不使用 rig")
    if "with CUDA" in out:
        check_colmap_gpu_sift(r, exe, work)


def check_trainer(r: Report, pipeline: dict) -> None:
    gdir = resolve_path((pipeline.get("trainer") or {}).get("gsplat_dir", "~/gsplat"))
    script = gdir / "examples" / "simple_trainer.py"
    r.add("gsplat 訓練程式", script.exists(), str(script),
          hint="git clone gsplat 到 config/pipeline.yaml 的 trainer.gsplat_dir 所指的位置")
    if script.exists():
        code, out = run(["git", "-C", str(gdir), "describe", "--tags", "--always"])
        code2, commit = run(["git", "-C", str(gdir), "rev-parse", "HEAD"])
        r.add("gsplat 版本（記錄用）", code2 == 0, f"{out if code == 0 else '?'} / {commit[:12] if code2 == 0 else '?'}",
              required=False)


def check_opencv(r: Report) -> None:
    try:
        import cv2
    except Exception as e:  # noqa: BLE001
        r.add("OpenCV", False, f"無法 import：{e}", hint="pip install -r requirements.txt")
        return
    ver = version_tuple(cv2.__version__)
    ok = (4, 8) <= ver < (5,) and hasattr(cv2, "aruco") and hasattr(cv2.aruco, "CharucoDetector")
    r.add("OpenCV（cv2.aruco.CharucoDetector）", ok, cv2.__version__,
          hint="需要 4.8 以上、5 以下：pip install 'opencv-contrib-python-headless>=4.10.0.84,<5'")
    dists = sorted({d.metadata["Name"].lower() for d in md.distributions()
                    if d.metadata["Name"] and d.metadata["Name"].lower().startswith("opencv")})
    r.add("OpenCV 沒有重複安裝", len(dists) <= 1, ", ".join(dists) or "（無）",
          hint="PATH.md B6：只保留 opencv-contrib-python-headless")


def check_packages(r: Report) -> None:
    for name, required in (("numpy", True), ("scipy", True), ("yaml", True), ("matplotlib", True), ("open3d", False)):
        try:
            mod = importlib.import_module(name)
            r.add(f"Python 套件 {name}", True, getattr(mod, "__version__", "?"), required=required)
        except Exception as e:  # noqa: BLE001
            r.add(f"Python 套件 {name}", False, str(e), required=required, hint="pip install -r requirements.txt")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=PROJECT_DIR)
    ap.add_argument("--skip-gpu-test", action="store_true")
    ap.add_argument("--min-free-gb", type=float, default=20.0, help="專題資料夾所在硬碟的剩餘空間下限")
    args = ap.parse_args()

    root = args.root.resolve()
    host = socket.gethostname()
    sdir = step_dir(root, "env", host)
    run_dir = create_run_dir(sdir)
    log = setup_logger(run_dir)
    log.info(f"===== S1 環境檢查（{host}）輸出：{rel(run_dir, root)} =====")
    pipeline = load_yaml(root / "config" / "pipeline.yaml")
    r = Report(log)

    r.add("Python >= 3.10", sys.version_info >= (3, 10), f"{platform.python_version()}（{sys.executable}）")
    r.add("作業系統", platform.system() == "Linux", f"{platform.platform()}", required=False,
          hint="完整流程需在 Linux 伺服器執行；Windows 只能做部分檢查")
    check_gpu_driver(r)
    nvcc_ver = check_nvcc(r)
    check_compiler(r, nvcc_ver)
    torch_ok = check_torch(r, nvcc_ver)
    check_gsplat(r, not args.skip_gpu_test, torch_ok)
    check_colmap(r, pipeline, run_dir)
    check_trainer(r, pipeline)
    check_opencv(r)
    check_packages(r)

    toolkit = resolve_path(pipeline.get("calib_toolkit", "../1151125_3D稀疏點雲"), root)
    r.add("1151125 標定程式", (toolkit / "src" / "calib_intrinsic.py").exists()
          and (toolkit / "src" / "calib_extrinsic.py").exists(), str(toolkit),
          hint="整個倉庫都要 clone 到伺服器（S3 會呼叫 1151125 的程式）")
    free_gb = shutil.disk_usage(root).free / 1e9
    r.add(f"硬碟剩餘空間 >= {args.min_free_gb:g} GB", free_gb >= args.min_free_gb, f"{free_gb:.1f} GB（{root}）",
          hint="PATH.md B8：請管理者擴增，或把 data/、output/ 連結到可寫入的大容量位置")

    report = {"host": host, "python": sys.executable, "passed": r.passed, "items": r.items}
    save_json(run_dir / "env_report.json", report)
    failed = [i["name"] for i in r.items if i["required"] and not i["ok"]]
    if r.passed:
        set_latest(sdir, run_dir)
        log.info("===== 全部通過 =====")
        return 0
    log.warning(f"===== 未通過：{failed}（詳見 {rel(run_dir / 'env_report.json', root)}）=====")
    return 2


if __name__ == "__main__":
    sys.exit(main())
