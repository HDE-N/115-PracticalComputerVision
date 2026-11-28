"""S5：Raspberry Pi 同步擷取 —— 每次觸發時每台相機各存一張（MJPEG 原始資料），並即時檢查。

範例：
  python S5/capture_rig.py --scene book_001 --profile object         # 拍攝場景
  python S5/capture_rig.py --mode calib --profile object              # 拍 S3 標定用的 ChArUco 照片
  python S5/capture_rig.py --scene book_001 --profile object --session 20261129_101500   # 接續同一個 session

操作：
  - 筆電瀏覽器開 http://<Pi 的 IP>:8000 ：即時預覽、按「拍攝」或空白鍵、看警告（會發出提示音）
  - SSH 終端機：Enter = 拍攝，s = 狀態，q = 結束
  - （選用）GPIO 按鈕：config/pi.yaml gpio.button_pin

輸出：<capture_root>/<scene>/<session>/
  session.json                      相機路徑、控制項設定與讀回值、設定檔快照
  group_00001/cam0.jpg、cam1.jpg     每台相機的 MJPEG 原始影格
  group_00001/group.json             時間戳、時間差、電壓旗標、溫度、對焦讀回值、是否需要重拍
  session_summary.json              結束時的統計
所有檔案寫入後都會 fsync；結束後請在 SSH 執行 sudo shutdown -h now 再拔電。
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # 專題資料夾
from common.pi_common import (CONFIG_DIR, BoardCounter, apply_controls, check_storage, cv2,  # noqa: E402
                       decode_reduced, decode_throttled, fsync_dir, get_throttled, load_pi_config,
                       now_iso, open_cameras, pick_synchronized, read_temp_c, run_cmd, sharpness,
                       stop_cameras, timestamp, undervoltage_now, v4l2_get, write_bytes_fsync,
                       write_json_fsync)


class Capturer:
    def __init__(self, cfg: dict, args, session_dir: Path):
        self.cfg = cfg
        self.args = args
        self.session_dir = session_dir
        self.cap_cfg = cfg.get("capture") or {}
        self.power_cfg = cfg.get("power") or {}
        self.lock = threading.Lock()
        self.cams = []
        self.focus_wanted = (cfg.get("profiles") or {}).get(args.profile, {}).get("focus_absolute")
        self.group_idx = max([int(p.name.split("_")[1]) for p in session_dir.glob("group_*")
                              if p.name.split("_")[1].isdigit()] or [0])
        self.n_ok = 0
        self.n_retake = 0
        self.uv_streak = 0
        self.stopped_reason = ""
        self.last_group = None          # 最近一組：{"bytes": {cam: data}, "info": {...}}
        self.warnings: list[str] = []
        self.t_start = time.monotonic()
        self.reminded = False
        self.board = None
        if args.mode == "calib":
            try:
                self.board = BoardCounter(CONFIG_DIR / cfg.get("board_yaml", "board.yaml"))
            except Exception as e:  # noqa: BLE001
                print(f"（無法建立 ChArUco 偵測器，標定模式不顯示角點數：{e}）")

    # ---------------------------------------------------------------- 啟動
    def start(self) -> None:
        self.cams = open_cameras(self.cfg)
        for cam in self.cams:
            if not cam.wait_first_frame():
                raise RuntimeError(f"cam{cam.cam_id} 5 秒內沒有影格")
        time.sleep(0.5)
        controls = {}
        for c in self.cfg["cameras"]:
            res = apply_controls(c["device"], self.cfg, self.args.profile)
            controls[f"cam{c['id']}"] = res
            if res["mismatch"] or res["errors"]:
                self.warn(f"cam{c['id']} 控制項未生效：{res['mismatch'] or res['errors']}")
        info = {
            "created": now_iso(), "mode": self.args.mode, "scene": self.args.scene, "profile": self.args.profile,
            "session": self.session_dir.name, "stream": self.cfg["stream"],
            "cameras": [{"id": c["id"], "name": c.get("name"), "device": c["device"],
                         "opened_size": cam.opened_size} for c, cam in zip(self.cfg["cameras"], self.cams)],
            "controls": controls, "pi_config": self.cfg,
        }
        p = self.session_dir / "session.json"
        if p.exists():  # 接續拍攝時保留第一次的紀錄
            p = self.session_dir / f"session_resume_{timestamp()}.json"
        write_json_fsync(p, info)

    def stop(self) -> None:
        stop_cameras(self.cams)
        summary = {"ended": now_iso(), "groups_total": self.group_idx, "groups_ok_this_run": self.n_ok,
                   "groups_retake_this_run": self.n_retake, "stopped_reason": self.stopped_reason,
                   "runtime_min": round((time.monotonic() - self.t_start) / 60, 1),
                   "camera_disconnects": {f"cam{c.cam_id}": c.disconnect_events for c in self.cams}}
        write_json_fsync(self.session_dir / "session_summary.json", summary)
        fsync_dir(self.session_dir)

    def warn(self, text: str) -> None:
        msg = f"{time.strftime('%H:%M:%S')} {text}"
        print(f"\n!! {msg}")
        self.warnings = (self.warnings + [msg])[-20:]
        self.beep()

    def beep(self) -> None:
        pin = (self.cfg.get("gpio") or {}).get("buzzer_pin")
        if pin is None:
            print("\a", end="", flush=True)
            return
        try:
            from gpiozero import Buzzer
            Buzzer(pin).beep(on_time=0.15, off_time=0.1, n=2, background=True)
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- 拍攝
    def capture(self) -> dict:
        with self.lock:
            if self.stopped_reason:
                return {"ok": False, "error": f"已停止拍攝：{self.stopped_reason}"}
            t_trig = time.monotonic()
            n = int(self.cap_cfg.get("frames_after_trigger", 3))
            timeout = float(self.cap_cfg.get("timeout_s", 2.0))
            cands = {cam.cam_id: cam.frames_after(t_trig, n, timeout) for cam in self.cams}
            missing = [cid for cid, fs in cands.items() if not fs]
            if missing:
                self.warn(f"相機 {missing} 沒有影格（檢查 USB 連線），本組未存檔")
                return {"ok": False, "error": f"相機 {missing} 沒有影格"}
            frames, skew = pick_synchronized(cands)

            self.group_idx += 1
            gname = f"group_{self.group_idx:05d}"
            gdir = self.session_dir / gname
            gdir.mkdir(parents=True, exist_ok=True)
            reasons = []
            info = {"group": gname, "t_trigger_wall": time.time(), "time": now_iso(),
                    "skew_ms": round(skew, 2), "frames": []}
            for cid, f in sorted(frames.items()):
                fname = f"cam{cid}.jpg"
                write_bytes_fsync(gdir / fname, f.data)
                info["frames"].append({"cam": cid, "file": fname, "t_mono": f.t_mono, "t_wall": f.t_wall,
                                       "bytes": len(f.data), "raw_mjpeg": f.raw})
            if skew > float(self.cap_cfg.get("max_skew_ms", 20)):
                reasons.append(f"時間差 {skew:.1f} ms")

            if self.power_cfg.get("check_throttled", True):
                thr = get_throttled()
                info["throttled"] = thr
                info["throttled_text"] = decode_throttled(thr)
                if undervoltage_now(thr):
                    self.uv_streak += 1
                    reasons.append("電壓不足")
                else:
                    self.uv_streak = 0
            info["temp_c"] = read_temp_c()

            if self.cap_cfg.get("focus_check", True) and self.focus_wanted is not None:
                fb = {f"cam{c['id']}": v4l2_get(c["device"], ["focus_absolute"]).get("focus_absolute")
                      for c in self.cfg["cameras"]}
                info["focus_readback"] = fb
                bad = [k for k, v in fb.items() if v != int(self.focus_wanted)]
                if bad:
                    reasons.append(f"{bad} 對焦值與設定 {self.focus_wanted} 不同")

            if self.board is not None:
                corners = {}
                for cid, f in frames.items():
                    img = decode_reduced(f.data, 2)
                    corners[f"cam{cid}"] = self.board.count(img) if img is not None else 0
                info["charuco_corners"] = corners
                if min(corners.values()) < 6:
                    reasons.append(f"ChArUco 角點不足 {corners}")

            info["retake"] = bool(reasons) and self.args.mode != "calib"
            info["retake_reasons"] = reasons
            write_json_fsync(gdir / "group.json", info)
            fsync_dir(gdir)
            fsync_dir(self.session_dir)

            self.last_group = {"bytes": {cid: f.data for cid, f in frames.items()}, "info": info}
            if reasons:
                self.n_retake += int(info["retake"])
                self.warn(f"{gname}：{'；'.join(reasons)}" + ("（標記待重拍，請在同一位置再拍一次）"
                                                               if info["retake"] else ""))
            else:
                self.n_ok += 1
            self._check_power_and_time()
            print(f"\r已拍 {self.group_idx} 組（本次正常 {self.n_ok}、待重拍 {self.n_retake}）"
                  f"  時間差 {skew:5.1f} ms  溫度 {info['temp_c']}°C", end="", flush=True)
            return {"ok": True, "group": gname, "skew_ms": info["skew_ms"], "retake": info["retake"],
                    "reasons": reasons, "corners": info.get("charuco_corners")}

    def _check_power_and_time(self) -> None:
        limit = int(self.power_cfg.get("stop_after_undervoltage", 3))
        if self.uv_streak >= limit:
            self.stopped_reason = f"連續 {self.uv_streak} 組電壓不足（電量將盡或線材問題）"
            self.warn(f"停止拍攝：{self.stopped_reason}。請安全關機（sudo shutdown -h now）後更換電池 / 線材")
            if self.power_cfg.get("auto_shutdown", False):
                self.stop()
                run_cmd(["sudo", "shutdown", "-h", "now"])
        hours = float(self.power_cfg.get("runtime_remind_hours", 3.5))
        if not self.reminded and (time.monotonic() - self.t_start) / 3600 >= hours:
            self.reminded = True
            self.warn(f"已運行 {hours} 小時，請檢查行動電源電量燈")

    # ---------------------------------------------------------------- 狀態與預覽
    def status(self) -> dict:
        return {"scene": self.args.scene, "mode": self.args.mode, "profile": self.args.profile,
                "session": self.session_dir.name, "groups": self.group_idx, "ok": self.n_ok,
                "retake": self.n_retake, "stopped": self.stopped_reason,
                "runtime_min": round((time.monotonic() - self.t_start) / 60, 1),
                "temp_c": read_temp_c(), "warnings": self.warnings[-8:],
                "last": self.last_group["info"] if self.last_group else None,
                "cams": [{"id": c.cam_id, "frames": c.frames, "reconnects": c.reconnects,
                          "age_s": round(time.monotonic() - c.last_frame_mono, 2) if c.last_frame_mono else None}
                         for c in self.cams]}

    def thumbnail(self, cam_id: int, live: bool) -> bytes | None:
        if live:
            cam = next((c for c in self.cams if c.cam_id == cam_id), None)
            f = cam.latest() if cam else None
            data = f.data if f else None
        else:
            data = self.last_group["bytes"].get(cam_id) if self.last_group else None
        if data is None:
            return None
        img = decode_reduced(data, 4)
        if img is None:
            return None
        w = int((self.cfg.get("web") or {}).get("thumb_width", 480))
        if img.shape[1] > w:
            img = cv2.resize(img, (w, int(img.shape[0] * w / img.shape[1])), interpolation=cv2.INTER_AREA)
        cv2.putText(img, f"sharp {sharpness(img):.0f}", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return buf.tobytes() if ok else None


PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>3DGS 拍攝</title>
<style>
body{font-family:system-ui,"Microsoft JhengHei",sans-serif;margin:12px;background:#111;color:#eee}
button{font-size:22px;padding:14px 36px;border:0;border-radius:10px;background:#2b7;color:#fff;cursor:pointer}
button:disabled{background:#555} .row{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start}
img{background:#222;border-radius:6px;max-width:48vw} .warn{color:#fb4} .bad{color:#f66} .ok{color:#6d6}
#st{font-size:15px;line-height:1.6} label{margin-left:14px}
</style></head><body>
<div class="row"><button id="b" onclick="cap()">拍攝（空白鍵）</button>
<label><input type="checkbox" id="live" checked> 即時預覽</label></div>
<p id="msg"></p>
<div class="row" id="imgs"></div>
<div id="st"></div>
<script>
let ac=null; function tone(f,ms){try{ac=ac||new AudioContext();const o=ac.createOscillator(),g=ac.createGain();
o.frequency.value=f;o.connect(g);g.connect(ac.destination);o.start();setTimeout(()=>o.stop(),ms);}catch(e){}}
let ncam=0, busy=false;
async function cap(){ if(busy) return; busy=true; document.getElementById('b').disabled=true;
  try{ const r=await (await fetch('/capture',{method:'POST'})).json();
    const m=document.getElementById('msg');
    if(!r.ok){m.className='bad';m.textContent='失敗：'+r.error;tone(220,600);}
    else if(r.reasons.length){m.className='warn';m.textContent=r.group+'：'+r.reasons.join('；')+(r.retake?'（待重拍）':'');tone(330,500);}
    else{m.className='ok';m.textContent=r.group+' 完成，時間差 '+r.skew_ms+' ms'+(r.corners?' 角點 '+JSON.stringify(r.corners):'');tone(880,120);}
  }finally{busy=false;document.getElementById('b').disabled=false;}}
document.addEventListener('keydown',e=>{if(e.code==='Space'){e.preventDefault();cap();}});
async function poll(){ try{ const s=await (await fetch('/status')).json();
  if(ncam!==s.cams.length){ncam=s.cams.length;document.getElementById('imgs').innerHTML=
    s.cams.map(c=>`<div>cam${c.id}<br><img id="i${c.id}" width="480"></div>`).join('');}
  const live=document.getElementById('live').checked;
  s.cams.forEach(c=>{document.getElementById('i'+c.id).src=`/thumb/${c.id}?live=${live?1:0}&t=${Date.now()}`;});
  document.getElementById('st').innerHTML=`場景 <b>${s.scene}</b>（${s.mode}，對焦 ${s.profile}） session ${s.session}<br>
  已拍 <b>${s.groups}</b> 組，本次正常 ${s.ok}、待重拍 <span class="warn">${s.retake}</span>，運行 ${s.runtime_min} 分，溫度 ${s.temp_c}°C<br>
  ${s.cams.map(c=>`cam${c.id}: 重連 ${c.reconnects}，最後影格 ${c.age_s}s 前`).join('；')}<br>
  ${s.stopped?'<span class="bad">已停止：'+s.stopped+'</span><br>':''}
  ${s.warnings.map(w=>'<span class="warn">'+w+'</span>').join('<br>')}`;
 }catch(e){document.getElementById('st').innerHTML='<span class="bad">與 Pi 的連線中斷</span>';}
 setTimeout(poll,700);}
poll();
</script></body></html>"""


def make_handler(capturer: Capturer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # 不在終端機印每個請求
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/" or self.path.startswith("/?"):
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path.startswith("/status"):
                self._send(200, json.dumps(capturer.status(), ensure_ascii=False, default=str).encode(),
                           "application/json")
            elif self.path.startswith("/thumb/"):
                part = self.path[len("/thumb/"):]
                cam_id = int(part.split("?")[0])
                live = "live=1" in part
                data = capturer.thumbnail(cam_id, live)
                if data:
                    self._send(200, data, "image/jpeg")
                else:
                    self._send(404, b"", "text/plain")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path.startswith("/capture"):
                res = capturer.capture()
                self._send(200, json.dumps(res, ensure_ascii=False).encode(), "application/json")
            else:
                self._send(404, b"not found", "text/plain")
    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path)
    ap.add_argument("--mode", choices=["scene", "calib"], default="scene")
    ap.add_argument("--scene", help="場景名稱（與 config/scenes/<scene>.yaml 相同）；標定模式預設 calib_<profile>")
    ap.add_argument("--profile", required=True, help="對焦設定檔：object / room / outdoor")
    ap.add_argument("--session", help="session 名稱（預設為目前時間）；填既有名稱可接續拍攝")
    ap.add_argument("--no-web", action="store_true", help="不開網頁，只用終端機 / GPIO 操作")
    ap.add_argument("--port", type=int, help="網頁埠（預設 config/pi.yaml web.port）")
    ap.add_argument("--allow-non-ssd", action="store_true", help="測試用：允許不存到 SSD")
    args = ap.parse_args()
    if args.mode == "calib" and not args.scene:
        args.scene = f"calib_{args.profile}"
    if not args.scene:
        ap.error("拍攝場景時需要 --scene")

    cfg = load_pi_config(args.config)
    ok, msg, _ = check_storage(cfg, args.allow_non_ssd)
    print(("OK " if ok else "!! ") + msg)
    if not ok:
        return 1
    session_dir = Path(cfg["capture_root"]) / args.scene / (args.session or timestamp())
    session_dir.mkdir(parents=True, exist_ok=True)
    print(f"存檔位置：{session_dir}")

    def _on_term(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _on_term)   # kill 時也會寫完 session_summary.json

    capturer = Capturer(cfg, args, session_dir)
    capturer.start()

    server = None
    if not args.no_web:
        web = cfg.get("web") or {}
        port = args.port or int(web.get("port", 8000))
        server = ThreadingHTTPServer((web.get("host", "0.0.0.0"), port), make_handler(capturer))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"網頁：http://<Pi 的 IP>:{port}  （查 IP：hostname -I）")

    pin = (cfg.get("gpio") or {}).get("button_pin")
    if pin is not None:
        try:
            from gpiozero import Button
            btn = Button(pin, bounce_time=0.2)
            btn.when_pressed = lambda: capturer.capture()
            print(f"GPIO 按鈕：BCM {pin}")
        except Exception as e:  # noqa: BLE001
            print(f"（GPIO 按鈕無法使用：{e}）")

    print("Enter = 拍攝，s = 狀態，q = 結束" if sys.stdin.isatty() else "（背景執行：以網頁 / GPIO 操作，Ctrl+C 或 kill 結束）")
    try:
        if not sys.stdin.isatty():
            threading.Event().wait()
        while True:
            cmd = input().strip().lower()
            if cmd == "q":
                break
            if cmd == "s":
                print(json.dumps(capturer.status(), ensure_ascii=False, indent=1, default=str))
            else:
                capturer.capture()
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        if server:
            server.shutdown()
        capturer.stop()
        print(f"\n結束：共 {capturer.group_idx} 組，存於 {session_dir}")
        print("拔電前請執行：sudo shutdown -h now")
    return 0


if __name__ == "__main__":
    sys.exit(main())
