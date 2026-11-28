"""步驟框架：每個步驟共用的參數、run 資料夾、計時、外部指令、品質門檻與 latest.txt。

每個步驟腳本的寫法：
    step = Step("sfm", __doc__)
    src = step.upstream("preprocess")        # 上一步驟的 run（預設讀 latest.txt，可用 --input-run 指定）
    with step.stage("mapper"):
        step.run_cmd([...], "mapper")
    step.check("註冊率", ratio >= 0.95, f"{ratio:.1%}")
    sys.exit(step.finish({"registered": n}))

結束碼：0 = 通過門檻（已更新 latest.txt）；2 = 未通過門檻；1 = 執行錯誤。
"""
from __future__ import annotations

import argparse
import contextlib
import shlex
import subprocess
import sys
import time
from pathlib import Path

from .io_utils import (PROJECT_DIR, create_run_dir, load_configs, rel, resolve_run, save_json, save_yaml,
                       set_latest, step_dir, setup_logger)


class Step:
    def __init__(self, name: str, doc: str | None, extra_args=None, key_arg: str = "scene"):
        ap = argparse.ArgumentParser(description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
        if key_arg == "scene":
            ap.add_argument("--scene", required=True, help="場景名稱（config/scenes/<scene>.yaml）")
        ap.add_argument("--root", type=Path, default=PROJECT_DIR, help="專題資料夾（內含 config/、data/、output/）")
        ap.add_argument("--input-run", help="指定上一步驟使用的 run 資料夾名稱（預設讀 latest.txt）")
        ap.add_argument("--force-latest", action="store_true", help="未通過門檻也更新 latest.txt")
        ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="只在本次執行覆寫設定，例：--set params.sfm.min_registered_ratio=0.9")
        if extra_args:
            extra_args(ap)
        self.args = ap.parse_args()
        self.name = name
        self.root = self.args.root.resolve()
        self.cfg = load_configs(self.root, getattr(self.args, "scene", None), self.args.set)
        self.key = getattr(self.args, "scene", None) or self.key_from_args()
        self.sdir = step_dir(self.root, name, self.key)
        self.run_dir = create_run_dir(self.sdir)
        self.log = setup_logger(self.run_dir)
        self.params = self.cfg["params"].get(name, {})
        self.t0 = time.time()
        self.stages: dict[str, float] = {}
        self.checks: list[dict] = []
        save_yaml(self.run_dir / "config_used.yaml", {"step": name, "key": self.key,
                                                      "args": vars(self.args), "config": self.cfg})
        self.log.info(f"===== {name}（{self.key}）輸出：{rel(self.run_dir, self.root)} =====")

    def key_from_args(self) -> str:
        """非場景步驟（例：calib 以 profile 為 key）覆寫此方法。"""
        return getattr(self.args, "profile", "default")

    # ---------------------------------------------------------------- 上游結果
    def upstream(self, step: str, primary: bool = True, key: str | None = None) -> Path:
        run = self.args.input_run if primary else None
        p = resolve_run(step_dir(self.root, step, key or self.key), run)
        self.log.info(f"輸入（{step}）：{rel(p, self.root)}")
        return p

    # ---------------------------------------------------------------- 計時
    @contextlib.contextmanager
    def stage(self, name: str):
        t = time.time()
        self.log.info(f"-- {name}")
        try:
            yield
        finally:
            self.stages[name] = round(self.stages.get(name, 0.0) + time.time() - t, 2)

    # ---------------------------------------------------------------- 外部指令
    def run_cmd(self, cmd: list, log_name: str, check: bool = True, cwd: Path | None = None) -> int:
        """執行外部指令，輸出同時寫到 run_dir/logs/<log_name>.log 與主控台。"""
        cmd = [str(c) for c in cmd]
        logs = self.run_dir / "logs"
        logs.mkdir(exist_ok=True)
        log_path = logs / f"{log_name}.log"
        self.log.info("$ " + " ".join(shlex.quote(c) for c in cmd))
        tail: list[str] = []
        with open(log_path, "a", encoding="utf-8", errors="replace") as lf:
            lf.write("$ " + " ".join(cmd) + "\n")
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd,
                                        text=True, encoding="utf-8", errors="replace", bufsize=1)
            except FileNotFoundError:
                msg = f"找不到指令 {cmd[0]}（檢查 config/pipeline.yaml 與 PATH）"
                lf.write(msg + "\n")
                if check:
                    raise RuntimeError(msg) from None
                self.log.error(msg)
                return 127
            for line in proc.stdout:
                lf.write(line)
                tail = (tail + [line.rstrip()])[-30:]
                if any(k in line for k in ("Error", "ERROR", "error:", "Traceback", "Elapsed time", "PSNR")):
                    print("   " + line.rstrip())
            code = proc.wait()
        if code != 0:
            self.log.error(f"指令失敗（{code}），最後輸出：\n   " + "\n   ".join(tail[-12:]))
            if check:
                raise RuntimeError(f"{log_name} 失敗，詳見 {rel(log_path, self.root)}")
        return code

    # ---------------------------------------------------------------- 門檻與結束
    def check(self, name: str, ok: bool, detail: str = "", warn_only: bool = False) -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail, "warn_only": warn_only})
        mark = "OK" if ok else ("??" if warn_only else "!!")
        self.log.info(f"   [{mark}] {name}：{detail}")
        return bool(ok)

    def finish(self, stats: dict) -> int:
        total = round(time.time() - self.t0, 2)
        save_json(self.run_dir / "stats.json", stats)
        save_json(self.run_dir / "timing.json", {"total_s": total, "stages_s": self.stages})
        passed = all(c["ok"] for c in self.checks if not c["warn_only"])
        save_json(self.run_dir / "checks.json", {"passed": passed, "checks": self.checks})
        if passed or self.args.force_latest:
            set_latest(self.sdir, self.run_dir)
            self.log.info(f"===== 完成（{total:.0f} s），latest.txt -> {self.run_dir.name}"
                          + ("（未通過門檻，--force-latest 強制採用）" if not passed else "") + " =====")
            return 0
        failed = [c["name"] for c in self.checks if not c["ok"] and not c["warn_only"]]
        self.log.warning(f"===== 未通過門檻：{failed}；latest.txt 未更新。"
                         f"確認要採用時加 --force-latest，或用 --input-run {self.run_dir.name} 給下一步 =====")
        return 2

    def fail(self, message: str) -> int:
        self.log.error(message)
        save_json(self.run_dir / "checks.json", {"passed": False, "error": message, "checks": self.checks})
        save_json(self.run_dir / "timing.json", {"total_s": round(time.time() - self.t0, 2),
                                                 "stages_s": self.stages})
        return 1


def main_guard(fn) -> None:
    """執行步驟主程式：未預期的例外以結束碼 1 結束並印出原因。"""
    try:
        sys.exit(fn())
    except KeyboardInterrupt:
        print("\n使用者中斷")
        sys.exit(130)
