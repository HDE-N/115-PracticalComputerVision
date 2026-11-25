"""主選單：依步驟順序列出所有程式，按下按鈕開啟該程式的參數 UI。

執行：python src/app.py
每個程式也可以單獨執行（不加參數就會開 UI），例如 python src/calib_intrinsic.py。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import tkinter as tk
from tkinter import ttk

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))
from common.gui import _enable_dpi_awareness, _setup_style  # noqa: E402

STEPS = [
    ("0", "相機參數調整", "camera_setup.py", "自動對焦 / 曝光 / 白平衡後鎖定，匯出到 cameras.yaml"),
    ("0", "標定板偵測", "detect_board.py", "自動判斷 ChArUco 字典與方格數，寫回 board.yaml"),
    ("0 / 2-1", "拍照", "capture.py", "內參（單台）、外參（多台同時）、物品照片"),
    ("1-1", "內參標定", "calib_intrinsic.py", "calibrateCamera，求每台相機的 K、dist"),
    ("1-2", "外參標定 + BA", "calib_extrinsic.py", "solvePnP 初值 + Bundle Adjustment"),
    ("2-2", "特徵配對", "match_features.py", "SIFT + ratio test + 對極約束"),
    ("2-3、2-4", "三角測量與點雲", "reconstruct.py", "三角化、篩選、離群點濾除、輸出 PLY"),
    ("驗證", "視覺化", "visualize.py", "相機位置、點雲、量測兩點距離"),
]


def open_script(name: str) -> None:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    subprocess.Popen([sys.executable, str(SRC / name)], cwd=SRC.parent, creationflags=flags)


def main() -> None:
    _enable_dpi_awareness()
    win = tk.Tk()
    win.title("3D 稀疏點雲 — 主選單")
    _setup_style(win)
    frame = ttk.Frame(win, padding=14)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="4 鏡頭標定與 3D 稀疏點雲", font=("", 14, "bold")).grid(
        row=0, column=0, columnspan=4, sticky="w")
    ttk.Label(frame, text="依序執行各步驟；每個按鈕會開啟該程式的參數視窗（可設定標定板版本、門檻值等）。",
              foreground="#555").grid(row=1, column=0, columnspan=4, sticky="w", pady=(2, 10))
    for r, (step, title, script, desc) in enumerate(STEPS, start=2):
        ttk.Label(frame, text=step, foreground="#666").grid(row=r, column=0, sticky="w", padx=(0, 10), pady=3)
        ttk.Button(frame, text=title, width=16, command=lambda s=script: open_script(s)).grid(
            row=r, column=1, sticky="w", pady=3)
        ttk.Label(frame, text=desc).grid(row=r, column=2, sticky="w", padx=(10, 0))
        ttk.Label(frame, text=script, foreground="#888").grid(row=r, column=3, sticky="w", padx=(10, 0))
    win.mainloop()


if __name__ == "__main__":
    main()
