"""S2-2：供電燒機測試 —— 以電池供電，相機持續串流並定時寫入 SSD，監看電壓、溫度、相機是否斷線。

範例：
  python S2/check_power.py --minutes 30                  # 出發前的標準燒機測試
  python S2/check_power.py --minutes 2 --no-cameras      # 只看供電狀態

每秒記錄：get_throttled、EXT5V 輸入電壓、PMIC 電源軌功耗、溫度、CPU、各相機 fps。
結果存到 <capture_root>/_tests/power_<時間>/（power_test.csv、summary.json）。
通過條件：期間電壓不足 0 次、相機斷線 0 次、溫度 < 75°C。
注意：PMIC 讀數不含 USB 周邊（相機、SSD）的耗電，整機功耗請以 USB 電流表量測後記錄在 README。
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # 專題資料夾
from common.pi_common import (check_storage, decode_throttled, get_throttled, load_pi_config, open_cameras,  # noqa: E402
                       read_cpu_times, read_mem_used_mb, read_pmic, read_temp_c, stop_cameras, timestamp,
                       undervoltage_now, write_bytes_fsync, write_json_fsync)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path)
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--no-cameras", action="store_true", help="不開相機，只監看供電")
    ap.add_argument("--write-every", type=float, default=2.0, help="每幾秒寫入一組影像到 SSD（模擬拍攝），0 = 不寫")
    ap.add_argument("--max-temp", type=float, default=75.0)
    ap.add_argument("--allow-non-ssd", action="store_true")
    args = ap.parse_args()

    cfg = load_pi_config(args.config)
    ok, msg, _ = check_storage(cfg, args.allow_non_ssd)
    if not ok:
        print(f"無法開始：{msg}")
        return 1
    out = Path(cfg["capture_root"]) / "_tests" / f"power_{timestamp()}"
    (out / "frames").mkdir(parents=True, exist_ok=True)

    cams = [] if args.no_cameras else open_cameras(cfg)
    rows = []
    thr_start = get_throttled()
    uv_count, writes = 0, 0
    try:
        for c in cams:
            c.wait_first_frame()
        start_frames = {c.cam_id: c.frames for c in cams}
        idle0, total0 = read_cpu_times()
        t0 = last_write = time.monotonic()
        prev_frames = dict(start_frames)
        while time.monotonic() - t0 < args.minutes * 60:
            time.sleep(1.0)
            now = time.monotonic()
            idle1, total1 = read_cpu_times()
            cpu = 100.0 * (1 - (idle1 - idle0) / max(1, total1 - total0))
            idle0, total0 = idle1, total1
            thr = get_throttled()
            uv = undervoltage_now(thr)
            uv_count += int(uv)
            row = {"t_s": round(now - t0, 1), "throttled": hex(thr) if thr is not None else "",
                   "undervoltage_now": int(uv), "temp_c": read_temp_c(), "cpu_percent": round(cpu, 1),
                   "mem_used_mb": round(read_mem_used_mb() or 0, 1), **read_pmic()}
            for c in cams:
                row[f"cam{c.cam_id}_fps"] = c.frames - prev_frames[c.cam_id]
                prev_frames[c.cam_id] = c.frames
            if cams and args.write_every > 0 and now - last_write >= args.write_every:
                last_write = now
                writes += 1
                for c in cams:
                    f = c.latest()
                    if f:
                        write_bytes_fsync(out / "frames" / f"w{writes:05d}_cam{c.cam_id}.jpg", f.data)
            rows.append(row)
            print(f"\r{row['t_s']:7.0f}s 電壓不足:{uv_count} 溫度:{row['temp_c']}°C EXT5V:{row.get('ext5v_v')}V "
                  f"CPU:{row['cpu_percent']}% " + " ".join(f"cam{c.cam_id}:{row[f'cam{c.cam_id}_fps']}fps"
                                                           for c in cams), end="", flush=True)
    except KeyboardInterrupt:
        print("\n使用者中斷")
    finally:
        stop_cameras(cams)
    print()

    thr_end = get_throttled()
    disconnects = sum(len(c.disconnect_events) + c.reconnects for c in cams)
    max_temp = max((r["temp_c"] or 0) for r in rows) if rows else None
    min_v = min((r.get("ext5v_v") or 99) for r in rows) if rows else None
    checks = {"no_undervoltage": uv_count == 0 and not (thr_end and thr_end & (1 << 16)
                                                        and not (thr_start and thr_start & (1 << 16))),
              "no_disconnect": disconnects == 0,
              "temperature": max_temp is not None and max_temp < args.max_temp}
    summary = {"minutes": round(rows[-1]["t_s"] / 60, 2) if rows else 0, "cameras": len(cams),
               "undervoltage_samples": uv_count, "disconnects": disconnects, "max_temp_c": max_temp,
               "min_ext5v_v": min_v, "writes": writes, "throttled_start": thr_start, "throttled_end": thr_end,
               "throttled_end_text": decode_throttled(thr_end), "checks": checks, "passed": all(checks.values())}
    if rows:
        with open(out / "power_test.csv", "w", newline="", encoding="utf-8") as fp:
            w = csv.DictWriter(fp, fieldnames=list(rows[-1].keys()))
            w.writeheader()
            w.writerows(rows)
    write_json_fsync(out / "summary.json", summary)
    write_bytes_fsync(out / "README.txt", "frames/ 只是模擬寫入負載，可刪除。\n".encode("utf-8"))
    for k, v in checks.items():
        print(f"  {'OK' if v else '!!'} {k}")
    print(f"電壓不足 {uv_count} 次、斷線 {disconnects} 次、最高溫 {max_temp}°C、最低 EXT5V {min_v} V")
    print("結果：", "通過" if summary["passed"] else "未通過", f"（{out}）")
    if not checks["no_undervoltage"]:
        print("建議：確認 /boot/firmware/config.txt 有 usb_max_current_enable=1、換 5A 短線，"
              "仍不足時改用外接供電 USB Hub（Task.md S2-2 方案 C）。")
    return 0 if summary["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
