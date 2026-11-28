"""S1：相機檢查 —— 格式、同時串流的 fps / 掉幀、CPU / 記憶體、供電狀態。

範例：
  python S1/check_cameras.py                     # 使用 config/pi.yaml 的相機（2 台），串流 60 秒
  python S1/check_cameras.py --all-usb --seconds 600   # 所有插著的 USB 相機（測試 4 台），10 分鐘

結果存到 <capture_root>/_tests/check_cameras_<時間>/summary.json（SSD 未掛載時只印在畫面上）。
通過條件：每台實際 fps ≥ 設定值的 80%、無效影格 = 0、重新連線 = 0、期間未出現電壓不足。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # 專題資料夾
from common.pi_common import (check_storage, decode_throttled, get_throttled, list_usb_video_devices,  # noqa: E402
                       load_pi_config, open_cameras, read_cpu_times, read_mem_used_mb, read_pmic,
                       read_temp_c, run_cmd, stop_cameras, timestamp, undervoltage_now,
                       write_bytes_fsync, write_json_fsync)


def check_format(device: str, stream: dict) -> bool:
    code, out = run_cmd(["v4l2-ctl", "-d", device, "--list-formats-ext"])
    if code != 0:
        return False
    want = f"{stream['width']}x{stream['height']}"
    section = out.split("'MJPG'", 1)[-1].split("[1]", 1)[0] if "'MJPG'" in out else ""
    return want in section


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path)
    ap.add_argument("--seconds", type=float, default=60.0, help="串流測試秒數")
    ap.add_argument("--all-usb", action="store_true", help="測試所有插著的 USB 相機（而非 pi.yaml 中的相機）")
    ap.add_argument("--allow-non-ssd", action="store_true")
    args = ap.parse_args()

    cfg = load_pi_config(args.config)
    stream = cfg["stream"]
    devices = list_usb_video_devices() if args.all_usb else [c["device"] for c in cfg["cameras"]]
    print(f"測試相機 {len(devices)} 台，格式 MJPG {stream['width']}x{stream['height']} @ {stream['fps']} fps")
    for i, d in enumerate(devices):
        print(f"  cam{i}: {d} -> 支援此格式：{'是' if check_format(d, stream) else '否'}")

    cams = open_cameras(cfg, devices=devices if args.all_usb else None)
    samples = []
    try:
        for cam in cams:
            if not cam.wait_first_frame():
                print(f"cam{cam.cam_id} 5 秒內沒有影格")
        start_frames = {c.cam_id: c.frames for c in cams}
        idle0, total0 = read_cpu_times()
        thr_start = get_throttled()
        t0 = time.monotonic()
        max_gap = {c.cam_id: 0.0 for c in cams}
        last_seen = {c.cam_id: c.frames for c in cams}
        last_change = {c.cam_id: t0 for c in cams}
        while time.monotonic() - t0 < args.seconds:
            time.sleep(1.0)
            now = time.monotonic()
            for c in cams:
                if c.frames != last_seen[c.cam_id]:
                    last_seen[c.cam_id] = c.frames
                    last_change[c.cam_id] = now
                max_gap[c.cam_id] = max(max_gap[c.cam_id], now - last_change[c.cam_id])
            thr = get_throttled()
            samples.append({"t": round(now - t0, 1), "throttled": thr, "undervoltage": undervoltage_now(thr),
                            "temp_c": read_temp_c(), **read_pmic()})
            fps_txt = "  ".join(f"cam{c.cam_id}:{(c.frames - start_frames[c.cam_id]) / (now - t0):5.1f}fps"
                                for c in cams)
            print(f"\r{now - t0:6.0f}s  {fps_txt}  溫度 {samples[-1]['temp_c']}°C", end="", flush=True)
        print()
        elapsed = time.monotonic() - t0
        idle1, total1 = read_cpu_times()
        cpu = 100.0 * (1 - (idle1 - idle0) / max(1, total1 - total0))
        thr_end = get_throttled()
    finally:
        stop_cameras(cams)

    per_cam = []
    for c in cams:
        fps = (c.frames - start_frames[c.cam_id]) / elapsed
        per_cam.append({"cam": c.cam_id, "device": c.device, "opened_size": c.opened_size,
                        "fps": round(fps, 2), "invalid_frames": c.invalid, "reconnects": c.reconnects,
                        "max_gap_s": round(max_gap[c.cam_id], 2),
                        "raw_mjpeg": bool(c.latest().raw) if c.latest() else None,
                        "disconnect_events": c.disconnect_events})
    uv_seen = any(s["undervoltage"] for s in samples) or bool(thr_end and thr_end & (1 << 16) and not
                                                               (thr_start and thr_start & (1 << 16)))
    checks = {
        "fps": all(p["fps"] >= 0.8 * float(stream["fps"]) for p in per_cam),
        "no_invalid_frames": all(p["invalid_frames"] == 0 for p in per_cam),
        "no_reconnect": all(p["reconnects"] == 0 and not p["disconnect_events"] for p in per_cam),
        "no_undervoltage": not uv_seen,
        "raw_mjpeg": all(p["raw_mjpeg"] for p in per_cam),
    }
    summary = {"time": timestamp(), "seconds": round(elapsed, 1), "n_cameras": len(cams), "stream": stream,
               "cpu_percent": round(cpu, 1), "mem_used_mb": read_mem_used_mb(),
               "throttled_start": thr_start, "throttled_end": thr_end,
               "throttled_end_text": decode_throttled(thr_end),
               "max_temp_c": max((s["temp_c"] or 0) for s in samples) if samples else None,
               "per_camera": per_cam, "checks": checks, "passed": all(checks.values())}

    print(json.dumps({k: summary[k] for k in ("cpu_percent", "mem_used_mb", "max_temp_c", "checks")},
                     ensure_ascii=False, indent=2))
    for p in per_cam:
        print(f"cam{p['cam']}: {p['fps']} fps，無效影格 {p['invalid_frames']}，重連 {p['reconnects']}，"
              f"最長中斷 {p['max_gap_s']} s，原始 MJPEG：{p['raw_mjpeg']}")
    if not checks["raw_mjpeg"]:
        print("注意：OpenCV 沒有輸出 MJPEG 原始資料，程式會改為重新壓縮成 JPEG（品質 95），CPU 負擔較高。")
    print("結果：", "通過" if summary["passed"] else "未通過")

    ok, msg, _ = check_storage(cfg, args.allow_non_ssd)
    if ok:
        out = Path(cfg["capture_root"]) / "_tests" / f"check_cameras_{summary['time']}"
        out.mkdir(parents=True, exist_ok=True)
        write_json_fsync(out / "summary.json", summary)
        write_bytes_fsync(out / "samples.json", json.dumps(samples).encode())
        for c in cams:
            f = c.latest()
            if f:
                write_bytes_fsync(out / f"cam{c.cam_id}_sample.jpg", f.data)
        print(f"已存檔：{out}")
    else:
        print(f"未存檔：{msg}")
    return 0 if summary["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
