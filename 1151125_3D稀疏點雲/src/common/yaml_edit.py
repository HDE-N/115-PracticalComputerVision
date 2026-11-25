"""保留註解的 yaml 讀寫：給 UI 讀取每個參數的行內註解（當說明文字），並把修改後的值寫回設定檔。

只處理本作業 config/*.yaml 的寫法：區塊式 mapping、縮排式列表（"  - id: 0"），
值都寫在同一行（純量或 [a, b] 這種 flow 列表）。只改值的部分，註解與排版保持不變。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import yaml

_KEY_RE = re.compile(
    r"^(?P<indent> *)(?P<dash>- +)?"
    r"(?P<key>\"[^\"]*\"|'[^']*'|[^\s#\"'\-][^:#]*?|-[^\s][^:#]*?)\s*:(?=\s|$)(?P<rest>.*)$"
)


def _split_value_comment(rest: str) -> tuple[int, int, str]:
    """把 "key:" 後面的字串拆成值與註解，回傳 (值起點, 值終點, 註解文字)。"""
    quote, depth, cut = None, 0, len(rest)
    for i, ch in enumerate(rest):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "#" and depth <= 0 and (i == 0 or rest[i - 1] in " \t"):
            cut = i
            break
    value = rest[:cut]
    start = len(value) - len(value.lstrip())
    end = len(value.rstrip())
    comment = rest[cut + 1:].strip() if cut < len(rest) else ""
    return start, max(start, end), comment


def index_yaml(text: str) -> dict[tuple, dict]:
    """回傳 {路徑 tuple: {"line", "vstart", "vend", "comment", "leaf"}}。

    路徑中的列表元素以整數表示，例如 ("cameras", 0, "device_index")。
    """
    out: dict[tuple, dict] = {}
    stack: list[tuple[int, tuple]] = []
    counters: dict[tuple, int] = {}
    for n, line in enumerate(text.splitlines()):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _KEY_RE.match(line)
        if not m:
            continue
        indent = len(m["indent"])
        if m["dash"]:
            while stack and stack[-1][0] >= indent:
                stack.pop()
            parent = stack[-1][1] if stack else ()
            idx = counters.get(parent, -1) + 1
            counters[parent] = idx
            stack.append((indent, parent + (idx,)))
            indent += len(m["dash"])
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else ()
        key = m["key"].strip()
        if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
            key = key[1:-1]
        path = parent + (key,)
        vs, ve, comment = _split_value_comment(m["rest"])
        base = m.start("rest")
        out[path] = {"line": n, "vstart": base + vs, "vend": base + ve,
                     "comment": comment, "leaf": ve > vs}
        stack.append((indent, path))
    return out


def comments_of(path: Path) -> dict[tuple, str]:
    """讀取 yaml 檔中每個 key 的行內註解。"""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    return {k: v["comment"] for k, v in index_yaml(text).items() if v["comment"]}


def yaml_scalar(v: Any) -> str:
    """把值轉成一行 yaml 文字（flow 形式）。"""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return json.dumps(str(v))
        s = repr(v)
        if "e" in s and "." not in s:      # PyYAML（YAML 1.1）的科學記號必須有小數點
            mant, exp = s.split("e")
            s = f"{mant}.0e{exp}"
        return s
    if isinstance(v, str):
        try:
            plain = v != "" and v.strip() == v and "#" not in v and yaml.safe_load(v) == v
        except yaml.YAMLError:
            plain = False
        return v if plain else json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(yaml_scalar(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{yaml_scalar(k)}: {yaml_scalar(x)}" for k, x in v.items()) + "}"
    raise TypeError(f"無法轉成 yaml：{type(v)}")


def get_path(data: Any, path: tuple) -> Any:
    for p in path:
        data = data[p]
    return data


def update_yaml_file(path: Path, updates: dict[tuple, Any]) -> list[tuple]:
    """把 {路徑: 新值} 寫回 yaml 檔，保留註解與排版。

    回傳找不到、無法寫回的路徑列表（這些路徑不會被修改）。
    寫入後會重新讀取檢查，結果不符時還原原始檔案並丟出例外。
    """
    path = Path(path)
    raw_bytes = path.read_bytes()
    bom = raw_bytes.startswith(b"\xef\xbb\xbf")
    raw = raw_bytes.decode("utf-8-sig")
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.splitlines()
    idx = index_yaml(raw)

    missing = []
    for key_path, value in updates.items():
        info = idx.get(tuple(key_path))
        if info is None or not info["leaf"]:
            missing.append(key_path)
            continue
        line = lines[info["line"]]
        vs, ve = info["vstart"], info["vend"]
        new = yaml_scalar(value)
        after = line[ve:]
        if after.strip():
            spaces = len(after) - len(after.lstrip())
            spaces = max(1, spaces + (ve - vs) - len(new))
            lines[info["line"]] = line[:vs] + new + " " * spaces + after.lstrip()
        else:
            lines[info["line"]] = line[:vs] + new

    text = nl.join(lines) + (nl if raw.endswith(("\n", "\r")) else "")
    data = text.encode("utf-8")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + data)

    # 檢查：重新讀回的值必須與要寫入的值相同
    try:
        check = yaml.safe_load(text) or {}
        for key_path, value in updates.items():
            if key_path in missing:
                continue
            got = get_path(check, tuple(key_path))
            if got != value and not (isinstance(value, tuple) and list(value) == got):
                raise ValueError(f"{'.'.join(map(str, key_path))} 寫回後讀到 {got!r}，預期 {value!r}")
    except Exception:
        path.write_bytes(raw_bytes)
        raise
    return missing
