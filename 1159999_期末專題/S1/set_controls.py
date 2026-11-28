"""鎖定相機參數（對焦、曝光、白平衡…），並可掃描某個控制項找出合適的值。

範例：
  python S1/set_controls.py --profile object                 # 套用 controls + object 對焦設定，讀回比對
  python S1/set_controls.py --list                           # 只列出目前的值
  python S1/set_controls.py --profile object --sweep focus_absolute --values 0:1023:100
  python S1/set_controls.py --profile object --sweep exposure_time_absolute --values 65524,65526,65528,65530,65532

掃描結果（每個值的亮度、中央清晰度與縮圖）存到 <capture_root>/_tests/sweep_<時間>/：
  對焦：選中央清晰度（sharpness）最高的值；曝光：選亮度適中（平均約 100–150）且不過曝的值。
選定後寫回 config/pi.yaml 的 controls / profiles。
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # 專題資料夾
from common.pi_common import (apply_controls, check_storage, decode_reduced, load_pi_config, open_cameras,  # noqa: E402
                       sharpness, stop_cameras, timestamp, v4l2_get, v4l2_set, wanted_controls,
                       write_bytes_fsync)


def parse_values(text: str) -> list[int]:
    if ":" in text:
        a, b, step = (int(v) for v in text.split(":"))
        return list(range(a, b + 1, step))
    return [int(v) for v in text.split(",") if v.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, help="pi.yaml 路徑（預設 config/pi.yaml）")
    ap.add_argument("--profile", help="對焦設定檔：object / room / outdoor")
    ap.add_argument("--cam", default="all", help="相機 id，例如 0 或 0,1（預設全部）")
    ap.add_argument("--list", action="store_true", help="只列出目前的控制項數值")
    ap.add_argument("--sweep", help="要掃描的控制項名稱，例如 focus_absolute")
    ap.add_argument("--values", help="掃描的值：a,b,c 或 起:迄:間隔")
    ap.add_argument("--settle", type=float, default=0.8, help="每次改值後等待秒數")
    ap.add_argument("--allow-non-ssd", action="store_true", help="測試用：允許結果不存在 SSD")
    args = ap.parse_args()

    cfg = load_pi_config(args.config)
    cam_ids = None if args.cam == "all" else [int(v) for v in args.cam.split(",")]
    specs = [c for c in cfg["cameras"] if cam_ids is None or int(c["id"]) in cam_ids]
    names = list(wanted_controls(cfg, args.profile or next(iter(cfg.get("profiles") or {}), None)))

    if args.list:
        for c in specs:
            print(f"== cam{c['id']} ({c.get('name', '')}) {c['device']}")
            for k, v in v4l2_get(c["device"], names).items():
                print(f"   {k:28s} {v}")
        return 0

    # 先開始串流再設定控制項（部分相機在開始串流時會重設參數）
    cams = open_cameras(cfg, cam_ids)
    try:
        for cam in cams:
            if not cam.wait_first_frame():
                print(f"cam{cam.cam_id} 沒有影格，請檢查連線")
                return 1
        time.sleep(0.5)
        bad = False
        for c in specs:
            res = apply_controls(c["device"], cfg, args.profile)
            print(f"== cam{c['id']} ({c.get('name', '')})")
            for k, want in res["wanted"].items():
                got = res["readback"].get(k)
                mark = "OK" if got == int(want) else "!!"
                print(f"   {mark} {k:28s} 設定 {want:>6}  讀回 {got}")
            for e in res["errors"]:
                print(f"   錯誤：{e}")
            bad = bad or bool(res["mismatch"] or res["errors"])
        if bad:
            print("\n有控制項沒有生效（!!）：檢查名稱與範圍（v4l2-ctl -d <裝置> --list-ctrls），"
                  "部分控制項要先關閉對應的自動模式才會生效。")

        if not args.sweep:
            return 1 if bad else 0
        if not args.values:
            print("--sweep 需要搭配 --values")
            return 1

        ok, msg, _ = check_storage(cfg, args.allow_non_ssd)
        if not ok:
            print(f"無法存檔：{msg}")
            return 1
        out_dir = Path(cfg["capture_root"]) / "_tests" / f"sweep_{args.sweep}_{timestamp()}"
        out_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        for value in parse_values(args.values):
            for c in specs:
                v4l2_set(c["device"], {args.sweep: value})
            time.sleep(args.settle)
            t0 = time.monotonic()
            for cam in cams:
                frames = cam.frames_after(t0, 3, timeout=2.0)
                if not frames:
                    print(f"cam{cam.cam_id} 值 {value}：沒有影格")
                    continue
                f = frames[-1]
                img = decode_reduced(f.data, 2)
                gray_mean = float(img.mean())
                sat = float((img.max(axis=2) >= 250).mean())
                sharp = sharpness(img)
                readback = v4l2_get(cam.device, [args.sweep]).get(args.sweep)
                write_bytes_fsync(out_dir / f"cam{cam.cam_id}_{args.sweep}_{value}.jpg", f.data)
                rows.append({"cam": cam.cam_id, "value": value, "readback": readback,
                             "brightness": round(gray_mean, 1), "saturated_ratio": round(sat, 4),
                             "sharpness": round(sharp, 1)})
                print(f"cam{cam.cam_id} {args.sweep}={value:>6} 讀回 {readback}  "
                      f"亮度 {gray_mean:6.1f}  過曝 {sat * 100:5.2f}%  清晰度 {sharp:8.1f}")
        with open(out_dir / "sweep.csv", "w", newline="", encoding="utf-8") as fp:
            w = csv.DictWriter(fp, fieldnames=list(rows[0].keys()) if rows else ["cam"])
            w.writeheader()
            w.writerows(rows)
        for cid in sorted({r["cam"] for r in rows}):
            rs = [r for r in rows if r["cam"] == cid]
            best = max(rs, key=lambda r: r["sharpness"])
            print(f"cam{cid}：清晰度最高的值 = {best['value']}（{best['sharpness']}）")
        print(f"結果：{out_dir}（掃描後相機停留在最後一個值，請重新執行 --profile 套用設定）")
        return 0
    finally:
        stop_cameras(cams)


if __name__ == "__main__":
    sys.exit(main())
