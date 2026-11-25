"""各腳本共用的簡易參數 UI（tkinter，Python 內建，不需另外安裝）。

不加任何參數執行腳本時（例如 python src/calib_intrinsic.py）會開啟本 UI：
  - 上方「執行參數」：對應腳本的命令列參數（相機、shot、run 資料夾...）。
  - 中間分頁：config/*.yaml 中與此步驟相關的設定（標定板、相機、門檻值），說明文字取自 yaml 的註解。
    修改後按「執行」只套用於本次執行（以 --set 傳給腳本，會記錄在 run 資料夾的 config_used.yaml），
    按「寫回設定檔」才會存進 config/*.yaml（保留原本的註解）。
  - 「標定板」分頁可切換 / 另存標定板版本（config/board_presets.yaml）。
  - 下方顯示腳本的輸出訊息。腳本在獨立的子行程中執行，OpenCV / Open3D 視窗照常開啟。
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, simpledialog, ttk

import yaml

from .io_utils import (PRESET_FILE, PROJECT_DIR, camera_ids, load_board_presets, load_yaml,
                       save_board_preset)
from .yaml_edit import comments_of, get_path, update_yaml_file, yaml_scalar

CONFIG_FILES = {"board": "board.yaml", "cameras": "cameras.yaml", "params": "params.yaml"}


def aruco_dictionaries() -> list[str]:
    """cv2.aruco 中所有預設字典名稱（依 OpenCV 編號排序）。"""
    try:
        import cv2
        names = [n for n in dir(cv2.aruco) if n.startswith("DICT_")]
        return sorted(names, key=lambda n: getattr(cv2.aruco, n))
    except Exception:
        return []


# 設定檔中有固定選項的 key（以最後一層 key 名稱比對）
CONFIG_CHOICES: dict[str, Callable[[], list[str]] | list[str]] = {
    "dictionary": aruco_dictionaries,
    "backend": ["DSHOW", "MSMF", "V4L2", "ANY"],
    "fourcc": ["MJPG", "YUYV", "null"],
    "ba_method": ["lm", "trf"],
    "ba_loss": ["linear", "huber", "soft_l1", "cauchy"],
    "pairs": ["all", "adjacent"],
}


# ---------------------------------------------------------------- 執行參數的選項來源
def cam_choices(with_all: bool = False) -> Callable[[Path, dict], list]:
    def f(root: Path, cfg: dict) -> list:
        ids = [str(i) for i in camera_ids(cfg["cameras"])]
        return (["all"] if with_all else []) + ids
    return f


def shot_choices(sub: str, need_latest: bool = False, extra: tuple = ()) -> Callable[[Path, dict], list]:
    """列出 root/sub 下的 shot_* 資料夾；extra 是放在最前面的選項。"""
    def f(root: Path, cfg: dict) -> list:
        base = root / sub
        shots = sorted(d.name for d in base.glob("shot_*")
                       if d.is_dir() and (not need_latest or (d / "latest.txt").exists())) \
            if base.exists() else []
        return list(extra) + shots
    return f


def run_choices(sub: str) -> Callable[[Path, dict], list]:
    """列出 root/sub 下的 run_* 資料夾（新的在前），第一個選項是「讀 latest.txt」。"""
    def f(root: Path, cfg: dict) -> list:
        base = root / sub
        runs = sorted((d.name for d in base.glob("run_*") if d.is_dir()), reverse=True) \
            if base.exists() else []
        return [("", "（預設：latest.txt）")] + runs
    return f


@dataclass
class Arg:
    """一個命令列參數在 UI 上的欄位。"""
    name: str                       # "--cam"；不以 - 開頭表示位置參數（例如 "mode"）
    label: str
    kind: str = "entry"             # entry / combo / check / file（檔案路徑 + 瀏覽按鈕）
    default: Any = ""
    choices: list | Callable[[Path, dict], list] | None = None   # 字串，或 (值, 顯示文字)
    help: str = ""
    when: dict[str, list] | None = None     # 只在其他參數為指定值時有效，例：{"mode": ["intrinsic"]}
    editable: bool = True           # combo 是否可自行輸入
    ui_only: bool = False           # 只用來切換其他欄位（when），不傳給程式

    @property
    def positional(self) -> bool:
        return not self.name.startswith("-")


@dataclass
class _CfgField:
    fk: str            # board / cameras / params
    path: tuple
    original: Any
    var: tk.Variable
    kind: str          # entry / combo / check


@dataclass
class _ArgWidget:
    arg: Arg
    var: tk.Variable
    widget: Any = None
    display_to_value: dict = field(default_factory=dict)
    browse: Any = None


def _display(v: Any) -> str:
    if isinstance(v, str):
        return v
    return yaml_scalar(v)


def _setup_style(win: tk.Tk) -> None:
    if sys.platform == "win32":
        families = set(tkfont.families(win))
        for fam in ("Microsoft JhengHei UI", "Microsoft JhengHei"):
            if fam in families:
                for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
                    tkfont.nametofont(name).configure(family=fam, size=10)
                break
    # 避免捲動頁面時滑鼠滾輪改到下拉選單的值
    win.unbind_class("TCombobox", "<MouseWheel>")
    win.unbind_class("TCombobox", "<Button-4>")
    win.unbind_class("TCombobox", "<Button-5>")


def _enable_dpi_awareness() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


class ScrollFrame(ttk.Frame):
    """可上下捲動的容器，內容放在 self.inner。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, padding=8)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(win, width=e.width))
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _wheel(self, event):
        if self.inner.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")


class ScriptGUI:
    """單一腳本的參數 UI。

    script:   腳本路徑（通常傳 __file__）
    args:     命令列參數欄位（Arg 列表）
    sections: 要顯示的設定分頁 [(分頁名稱, "board"/"cameras"/"params", 路徑 tuple), ...]
    """

    def __init__(self, script: str | Path, title: str, description: str,
                 args: list[Arg], sections: list[tuple[str, str, tuple]] = (),
                 root: Path = PROJECT_DIR):
        _enable_dpi_awareness()
        self.script = Path(script).resolve()
        self.root = Path(root)
        self.args_spec = list(args)
        self.sections = list(sections)
        self.proc: subprocess.Popen | None = None
        self.q: queue.Queue = queue.Queue()
        self.cfg_fields: list[_CfgField] = []
        self.arg_widgets: dict[str, _ArgWidget] = {}
        self.preset_box: ttk.Combobox | None = None

        self.win = tk.Tk()
        self.win.title(f"{title} — {self.script.name}")
        self.win.geometry("1000x880")
        self.win.minsize(720, 600)
        _setup_style(self.win)
        self.win.report_callback_exception = self._on_error
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

        self._load_configs()
        self._build(title, description)
        self._refresh_choices()
        self._refresh_when()
        self._update_status()
        self._after_id = self.win.after(100, self._poll)

    # ------------------------------------------------------------ 設定檔
    def _load_configs(self) -> None:
        cfg_dir = self.root / "config"
        self.data = {k: load_yaml(cfg_dir / f) for k, f in CONFIG_FILES.items()}
        self.comments = {k: comments_of(cfg_dir / f) for k, f in CONFIG_FILES.items()}

    def _presets(self) -> dict:
        return load_board_presets(self.root)

    # ------------------------------------------------------------ 版面
    def _build(self, title: str, description: str) -> None:
        top = ttk.Frame(self.win, padding=(10, 8, 10, 0))
        top.pack(fill="x")
        ttk.Label(top, text=title, font=(tkfont.nametofont("TkDefaultFont").actual("family"), 14, "bold")
                  ).pack(anchor="w")
        if description:
            ttk.Label(top, text=description, foreground="#555", wraplength=960, justify="left"
                      ).pack(anchor="w", pady=(2, 0))

        paned = ttk.PanedWindow(self.win, orient="vertical")
        paned.pack(fill="both", expand=True, padx=10, pady=8)

        upper = ttk.Frame(paned)
        paned.add(upper, weight=3)

        # 執行參數
        if self.args_spec:
            af = ttk.LabelFrame(upper, text="執行參數", padding=8)
            af.pack(fill="x")
            af.columnconfigure(2, weight=1)
            for r, a in enumerate(self.args_spec):
                self._build_arg(af, r, a)

        # 設定分頁
        self.upper = upper
        self.nb: ttk.Notebook | None = None
        self._build_tabs()

        # 按鈕列
        bar = ttk.Frame(upper)
        bar.pack(fill="x", pady=(8, 0))
        self.bar = bar
        self.run_btn = ttk.Button(bar, text="▶ 執行", command=self._run)
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(bar, text="■ 停止", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(6, 0))
        if self.sections:
            ttk.Button(bar, text="寫回設定檔", command=self._save_configs).pack(side="left", padx=(18, 0))
            ttk.Button(bar, text="還原修改", command=self._revert).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="重新讀取", command=self._reload).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="開啟 output 資料夾", command=self._open_output).pack(side="right")
        self.status = ttk.Label(upper, text="", foreground="#a05a00", wraplength=960, justify="left")
        self.status.pack(fill="x", pady=(4, 0))

        # 輸出訊息
        lower = ttk.LabelFrame(paned, text="輸出訊息", padding=4)
        paned.add(lower, weight=2)
        self.log = tk.Text(lower, height=12, wrap="word", font=("Consolas", 10),
                           background="#1e1e1e", foreground="#dddddd", insertbackground="#dddddd")
        lsb = ttk.Scrollbar(lower, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set, state="disabled")
        self.log.tag_configure("info", foreground="#6cb6ff")
        self.log.tag_configure("warn", foreground="#f0b400")
        self.log.tag_configure("err", foreground="#ff6b6b")
        self.log.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

    def _build_tabs(self) -> None:
        """建立（或重建）設定分頁；重建時保留目前選取的分頁。"""
        if not self.sections:
            return
        selected = 0
        if self.nb is not None:
            try:
                selected = self.nb.index(self.nb.select())
            except tk.TclError:
                pass
            self.nb.destroy()
        self.cfg_fields = []
        self.preset_box = None
        nb = ttk.Notebook(self.upper)
        if hasattr(self, "bar"):
            nb.pack(fill="both", expand=True, pady=(8, 0), before=self.bar)
        else:
            nb.pack(fill="both", expand=True, pady=(8, 0))
        for tab_title, fk, path in self.sections:
            sf = ScrollFrame(nb)
            nb.add(sf, text=tab_title)
            inner = sf.inner
            inner.columnconfigure(2, weight=1)
            row = 0
            if fk == "board" and path == ():
                row = self._build_preset_bar(inner)
            try:
                node = get_path(self.data[fk], path)
            except (KeyError, IndexError, TypeError):
                ttk.Label(inner, text=f"設定檔中沒有 {fk}.{'.'.join(map(str, path))}", foreground="#c00"
                          ).grid(row=row, column=0, sticky="w")
                continue
            self._build_node(inner, fk, path, node, row)
        self.nb = nb
        if selected < len(self.sections):
            nb.select(selected)

    def _build_arg(self, parent, r: int, a: Arg) -> None:
        ttk.Label(parent, text=a.label).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=2)
        aw = _ArgWidget(a, tk.BooleanVar(value=bool(a.default)) if a.kind == "check"
                        else tk.StringVar(value=str(a.default)))
        if a.kind == "check":
            aw.widget = ttk.Checkbutton(parent, variable=aw.var)
        elif a.kind == "combo":
            aw.widget = ttk.Combobox(parent, textvariable=aw.var, width=36,
                                     state="normal" if a.editable else "readonly")
        elif a.kind == "file":
            box = ttk.Frame(parent)
            aw.widget = ttk.Entry(box, textvariable=aw.var, width=38)
            aw.widget.pack(side="left")
            aw.browse = ttk.Button(box, text="瀏覽…", width=6, command=lambda: self._browse(aw))
            aw.browse.pack(side="left", padx=(4, 0))
            box.grid(row=r, column=1, sticky="w", pady=2)
        else:
            aw.widget = ttk.Entry(parent, textvariable=aw.var, width=38)
        if a.kind != "file":
            aw.widget.grid(row=r, column=1, sticky="w", pady=2)
        if a.help:
            ttk.Label(parent, text=a.help, foreground="#666", wraplength=520, justify="left"
                      ).grid(row=r, column=2, sticky="w", padx=(10, 0))
        aw.var.trace_add("write", lambda *_: self._refresh_when())
        self.arg_widgets[a.name] = aw

    def _build_preset_bar(self, parent) -> int:
        bar = ttk.LabelFrame(parent, text="標定板版本", padding=6)
        bar.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        ttk.Label(bar, text="版本：").pack(side="left")
        self.preset_box = ttk.Combobox(bar, state="readonly", width=28)
        self.preset_box.pack(side="left")
        ttk.Button(bar, text="套用", command=self._apply_preset).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="將目前參數另存為版本…", command=self._save_preset).pack(side="left", padx=(6, 0))
        ttk.Label(bar, text="（套用後只用於本次執行；要成為預設請按「寫回設定檔」）",
                  foreground="#666").pack(side="left", padx=(8, 0))
        self._refresh_presets()
        return 1

    def _build_node(self, parent, fk: str, path: tuple, node: Any, row: int) -> int:
        items = node.items() if isinstance(node, dict) else enumerate(node)
        for k, v in items:
            p = path + (k,)
            comment = self.comments[fk].get(p, "")
            if isinstance(v, dict):
                lf = ttk.LabelFrame(parent, text=f"{k}    {comment}".rstrip(), padding=6)
                lf.grid(row=row, column=0, columnspan=3, sticky="ew", pady=4)
                lf.columnconfigure(2, weight=1)
                self._build_node(lf, fk, p, v, 0)
            elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                lf = ttk.LabelFrame(parent, text=f"{k}    {comment}".rstrip(), padding=6)
                lf.grid(row=row, column=0, columnspan=3, sticky="ew", pady=4)
                lf.columnconfigure(0, weight=1)
                for i, item in enumerate(v):
                    title = f"{k}[{i}]" + (f"  id = {item['id']}" if "id" in item else "")
                    sub = ttk.LabelFrame(lf, text=title, padding=6)
                    sub.grid(row=i, column=0, sticky="ew", pady=2)
                    sub.columnconfigure(2, weight=1)
                    self._build_node(sub, fk, p + (i,), item, 0)
            else:
                self._build_leaf(parent, row, fk, p, k, v, comment)
            row += 1
        return row

    def _build_leaf(self, parent, row: int, fk: str, p: tuple, key, value, comment: str) -> None:
        ttk.Label(parent, text=str(key)).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
        choices = CONFIG_CHOICES.get(str(key))
        if isinstance(value, bool):
            var: tk.Variable = tk.BooleanVar(value=value)
            w = ttk.Checkbutton(parent, variable=var)
            kind = "check"
        elif choices is not None:
            var = tk.StringVar(value=_display(value))
            vals = choices() if callable(choices) else list(choices)
            w = ttk.Combobox(parent, textvariable=var, values=vals, width=28)
            kind = "combo"
        else:
            var = tk.StringVar(value=_display(value))
            w = ttk.Entry(parent, textvariable=var, width=30)
            kind = "entry"
        w.grid(row=row, column=1, sticky="w", pady=2)
        if comment:
            ttk.Label(parent, text=comment, foreground="#666", wraplength=520, justify="left"
                      ).grid(row=row, column=2, sticky="w", padx=(10, 0))
        f = _CfgField(fk, p, value, var, kind)
        var.trace_add("write", lambda *_: self._update_status())
        self.cfg_fields.append(f)

    # ------------------------------------------------------------ 欄位值
    @staticmethod
    def _field_value(f: _CfgField) -> Any:
        if f.kind == "check":
            return bool(f.var.get())
        text = str(f.var.get()).strip()
        if text == "":
            return None
        try:
            v = yaml.safe_load(text)
        except yaml.YAMLError:
            v = text
        if isinstance(f.original, float) and isinstance(v, int) and not isinstance(v, bool):
            v = float(v)
        return v

    def _changed(self) -> list[tuple[_CfgField, Any]]:
        out = []
        for f in self.cfg_fields:
            v = self._field_value(f)
            if v != f.original:
                out.append((f, v))
        return out

    @staticmethod
    def _key_text(f: _CfgField) -> str:
        return f"{f.fk}." + ".".join(str(p) for p in f.path)

    def _update_status(self) -> None:
        if not hasattr(self, "status"):
            return
        ch = self._changed()
        if not ch:
            self.status.configure(text="")
            return
        names = "、".join(f"{self._key_text(f)}={yaml_scalar(v)}" for f, v in ch[:6])
        more = f" 等 {len(ch)} 項" if len(ch) > 6 else ""
        self.status.configure(text=f"已修改：{names}{more}（按「執行」只套用於本次；按「寫回設定檔」才會存成預設）")

    def _arg_value(self, name: str) -> Any:
        aw = self.arg_widgets[name]
        v = aw.var.get()
        if aw.arg.kind == "check":
            return bool(v)
        v = str(v).strip()
        return aw.display_to_value.get(v, v)

    def _active(self, a: Arg) -> bool:
        if not a.when:
            return True
        return all(name in self.arg_widgets and self._arg_value(name) in vals
                   for name, vals in a.when.items())

    def _browse(self, aw: "_ArgWidget") -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=self.win, initialdir=str(self.root / "data"),
            filetypes=[("影像", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff"), ("所有檔案", "*.*")])
        if path:
            aw.var.set(path)

    def _refresh_when(self) -> None:
        for aw in self.arg_widgets.values():
            if aw.widget is None:
                continue
            on = self._active(aw.arg)
            if aw.browse is not None:
                aw.browse.configure(state="normal" if on else "disabled")
            if aw.arg.kind == "combo":
                aw.widget.configure(state=("normal" if aw.arg.editable else "readonly") if on else "disabled")
            else:
                aw.widget.configure(state="normal" if on else "disabled")

    def _refresh_choices(self) -> None:
        cfg = self.data
        for aw in self.arg_widgets.values():
            a = aw.arg
            if a.kind != "combo" or a.choices is None:
                continue
            try:
                raw = a.choices(self.root, cfg) if callable(a.choices) else a.choices
            except Exception as e:  # 例如設定檔錯誤
                raw = []
                self._append(f"[警告] 無法列出 {a.label} 的選項：{e}\n", "warn")
            aw.display_to_value = {}
            shown = []
            for c in raw:
                val, text = (c if isinstance(c, tuple) else (c, c))
                aw.display_to_value[text] = val
                shown.append(text)
            aw.widget.configure(values=shown)
            cur = str(aw.var.get())
            if cur == "" or (not a.editable and cur not in aw.display_to_value):
                default_text = next((t for t, v in aw.display_to_value.items() if v == a.default), None)
                if default_text is None and shown:
                    default_text = shown[0]
                if default_text is not None:
                    aw.var.set(default_text)

    def _refresh_presets(self) -> None:
        if self.preset_box is None:
            return
        try:
            names = list(self._presets().keys())
        except Exception as e:
            names = []
            self._append(f"[警告] 無法讀取 config/{PRESET_FILE}：{e}\n", "warn")
        self.preset_box.configure(values=names)
        cur = self.data["board"].get("name")
        if cur in names:
            self.preset_box.set(cur)
        elif names and not self.preset_box.get():
            self.preset_box.set(names[0])

    # ------------------------------------------------------------ 標定板版本
    def _board_fields(self) -> dict[str, _CfgField]:
        return {f.path[0]: f for f in self.cfg_fields if f.fk == "board" and len(f.path) == 1}

    def _apply_preset(self) -> None:
        name = self.preset_box.get() if self.preset_box else ""
        preset = self._presets().get(name)
        if not preset:
            messagebox.showwarning("標定板版本", "請先選擇一個版本")
            return
        fields = self._board_fields()
        for k, v in preset.items():
            if k in fields:
                f = fields[k]
                f.var.set(bool(v) if f.kind == "check" else _display(v))
        if "name" in fields and "name" not in preset:
            fields["name"].var.set(name)
        self._append(f"已套用標定板版本「{name}」\n", "info")

    def _save_preset(self) -> None:
        fields = self._board_fields()
        cur = self._field_value(fields["name"]) if "name" in fields else ""
        name = simpledialog.askstring("另存為標定板版本", "版本名稱（例如 A4_7x5_v2）：",
                                      initialvalue=str(cur or ""), parent=self.win)
        if not name:
            return
        name = name.strip()
        if name in self._presets() and                 not messagebox.askyesno("另存為標定板版本", f"版本「{name}」已存在，要覆寫嗎？"):
            return
        save_board_preset(self.root, name, {k: self._field_value(f) for k, f in fields.items()})
        if "name" in fields:
            fields["name"].var.set(name)
        self._refresh_presets()
        self.preset_box.set(name)
        self._append(f"已將目前的標定板參數存為版本「{name}」（config/{PRESET_FILE}）\n", "info")

    # ------------------------------------------------------------ 按鈕動作
    def _build_cmd(self) -> list[str] | None:
        cmd = [sys.executable, "-u", str(self.script)]
        opts: list[str] = []
        for a in self.args_spec:
            if a.ui_only or not self._active(a):
                continue
            v = self._arg_value(a.name)
            if a.positional:
                if v == "":
                    messagebox.showwarning("缺少參數", f"請填寫「{a.label}」")
                    return None
                cmd.append(str(v))
            elif a.kind == "check":
                if v:
                    opts.append(a.name)
            elif v != "":
                opts += [a.name, str(v)]
        cmd += opts + ["--root", str(self.root)]
        if self.sections:
            for f, v in self._changed():
                cmd += ["--set", f"{self._key_text(f)}={json.dumps(v, ensure_ascii=False)}"]
        return cmd

    def _run(self) -> None:
        if self.proc is not None:
            return
        cmd = self._build_cmd()
        if cmd is None:
            return
        shown = ["python", self.script.relative_to(self.root).as_posix()
                 if self.script.is_relative_to(self.root) else str(self.script)] + \
                [("." if c == str(self.root) else c) for c in cmd[3:]]
        self._append("\n$ " + subprocess.list2cmdline(shown) + "\n", "info")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            self.proc = subprocess.Popen(cmd, cwd=self.root, env=env, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         creationflags=flags)
        except OSError as e:
            self._append(f"無法啟動：{e}\n", "err")
            self.proc = None
            return
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def _reader(self, proc: subprocess.Popen) -> None:
        for raw in iter(proc.stdout.readline, b""):
            self.q.put(("line", raw.decode("utf-8", errors="replace")))
        proc.wait()
        self.q.put(("exit", proc.returncode))

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "line":
                    tag = None
                    if "[ERROR]" in payload or "Traceback" in payload or "Error" in payload:
                        tag = "err"
                    elif "[WARNING]" in payload or "警告" in payload or "未通過" in payload:
                        tag = "warn"
                    self._append(payload.replace("\r\n", "\n"), tag)
                else:
                    tag = "info" if payload == 0 else "err"
                    self._append(f"=== 執行結束（代碼 {payload}）===\n", tag)
                    self.proc = None
                    self.run_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    if self.sections and not self._changed():
                        self._reload()          # 程式可能改了設定檔（例如 detect_board.py）
                    else:
                        self._refresh_choices()
        except queue.Empty:
            pass
        self._after_id = self.win.after(100, self._poll)

    def _stop(self) -> None:
        if self.proc is not None:
            self._append("強制停止中…（拍照時建議在 OpenCV 視窗按 q / ESC 正常結束）\n", "warn")
            self.proc.terminate()

    def _save_configs(self) -> None:
        ch = self._changed()
        if not ch:
            messagebox.showinfo("寫回設定檔", "沒有修改任何設定")
            return
        lines = "\n".join(f"  {self._key_text(f)}: {yaml_scalar(f.original)} → {yaml_scalar(v)}"
                          for f, v in ch)
        if not messagebox.askyesno("寫回設定檔", f"以下修改會寫入 config/*.yaml，成為之後的預設值：\n\n{lines}"):
            return
        by_file: dict[str, dict] = {}
        for f, v in ch:
            by_file.setdefault(f.fk, {})[f.path] = v
        failed = []
        for fk, updates in by_file.items():
            path = self.root / "config" / CONFIG_FILES[fk]
            try:
                missing = update_yaml_file(path, updates)
            except Exception as e:
                messagebox.showerror("寫回設定檔", f"寫入 {path.name} 失敗，檔案已還原：\n{e}")
                return
            failed += [f"{fk}." + ".".join(map(str, m)) for m in missing]
        self._append(f"已寫回設定檔：{len(ch) - len(failed)} 項\n", "info")
        if failed:
            messagebox.showwarning("寫回設定檔", "以下項目無法自動寫回，請手動修改 yaml：\n" + "\n".join(failed))
        self._reload()

    def _revert(self) -> None:
        for f in self.cfg_fields:
            f.var.set(bool(f.original) if f.kind == "check" else _display(f.original))

    def _reload(self) -> None:
        try:
            self._load_configs()
        except Exception as e:
            messagebox.showerror("重新讀取", f"讀取設定檔失敗：{e}")
            return
        self._build_tabs()      # 重建分頁：程式可能在設定檔中新增了欄位（例如 camera_setup.py）
        self._refresh_choices()
        self._refresh_presets()
        self._update_status()

    def _open_output(self) -> None:
        target = self.root / "output"
        if not target.exists():
            target = self.root
        if sys.platform == "win32":
            os.startfile(target)  # noqa: S606
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(target)])

    # ------------------------------------------------------------ 其他
    def _append(self, text: str, tag: str | None = None) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text, (tag,) if tag else ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_error(self, exc, val, tb) -> None:
        import traceback
        self._append("".join(traceback.format_exception(exc, val, tb)), "err")
        messagebox.showerror("錯誤", str(val))

    def _on_close(self) -> None:
        if self.proc is not None:
            if not messagebox.askyesno("關閉", "程式仍在執行，要強制停止並關閉嗎？"):
                return
            self.proc.terminate()
        self.win.after_cancel(self._after_id)
        self.win.destroy()

    def run(self) -> None:
        self.win.mainloop()


def want_gui() -> bool:
    """不加任何命令列參數執行時開啟 UI。"""
    return len(sys.argv) == 1
