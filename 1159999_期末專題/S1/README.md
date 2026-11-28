# S1：環境與相機檢查

## 目標
確認拍攝端（Pi）與運算端（Linux 伺服器）的環境都能正常運作，並**找出相機的對焦值與曝光值**，寫回 `config/pi.yaml`。
這一步通過後，後面的拍攝與重建才有穩定的基礎。

## 檔案
| 檔案 | 在哪裡執行 | 用途 |
|------|-----------|------|
| `set_controls.py` | Pi | 套用並讀回相機控制項（對焦、曝光、白平衡…）；掃描某個控制項找出合適的值 |
| `check_cameras.py` | Pi | 相機格式、2 台（或 4 台）同時串流的 fps、無效影格、重新連線、CPU、電壓 |
| `check_env.py` | 伺服器 | CUDA、PyTorch、gsplat（GPU 實測）、COLMAP、OpenCV、1151125 標定程式、硬碟空間 |

共用程式：`../common/pi_common.py`（Pi）、`../common/core/`（伺服器）。設定：`../config/pi.yaml`、`../config/pipeline.yaml`。

## 前置作業
- Pi：[PATH.md](../PATH.md) A1–A7（特別是 **A2 解除 USB 限流**）。
- 伺服器：[PATH.md](../PATH.md) B0–B6（**沒有 sudo**，全部裝在家目錄的 conda 環境 `gs3d`、`colmap`）。

---

## 驗證步驟

以下指令都在 `1159999_期末專題/` 下執行（Pi：`conda activate rig`；伺服器：`conda activate gs3d`）。

### 1. Pi：讀取相機控制項
```bash
python S1/set_controls.py --list
```
應列出 cam0、cam1 的 `focus_absolute`、`exposure_time_absolute`、`white_balance_temperature` 等數值。

### 2. Pi：套用設定並讀回比對
```bash
python S1/set_controls.py --profile object
```
每一行應為 `OK`。出現 `!!` 代表該控制項沒有生效。曝光、白平衡這類控制項，要先關閉對應的自動模式才會生效。

### 3. Pi：找對焦值（書放在約 0.6 m 處）
把書（或任何有文字的物品）放在相機前約 0.6 m，**佔滿畫面中央**：
```bash
python S1/set_controls.py --profile object --sweep focus_absolute --values 0:1023:100
```
程式會列出每個對焦值的「清晰度」，最後印出最清晰的值。再以該值前後 ±100、間隔 20 細掃一次，例如最清晰的是 300 時：
```bash
python S1/set_controls.py --profile object --sweep focus_absolute --values 200:400:20
```
把最清晰的值填入 `config/pi.yaml` 的 `profiles.object.focus_absolute`。

### 4. Pi：找曝光值
這台相機回報的曝光範圍不正常（min=65522、max=256），先確認數值的意義：
```bash
python S1/set_controls.py --profile object --sweep exposure_time_absolute \
  --values 65522,65524,65526,65528,65530,65532,65534,10,50,100,200
```
看每個值的「亮度」與「讀回」：
- 亮度隨數值單調變化的那組就是有效範圍。
- 選**亮度約 100–150、過曝 < 1%** 的值，填入 `config/pi.yaml` 的 `controls.exposure_time_absolute`。
- 曝光時間越長越容易手震模糊。亮度不夠時，優先把室內燈開亮，其次才是調高 `gain`。

掃描的照片存在 `/mnt/ssd/3dgs_capture/_tests/sweep_*/`，可以傳到筆電檢查清晰度。

### 5. Pi：重新套用並確認
```bash
python S1/set_controls.py --profile object          # 全部 OK
```

### 6. Pi：2 台同時串流 10 分鐘
```bash
python S1/check_cameras.py --seconds 600
```

### 7. （選用）Pi：測試 4 台相機
4 台都插上後執行（Pi 只有 4 個 USB 孔，SSD 也要一個孔，所以需要外接供電的 USB Hub）：
```bash
python S1/check_cameras.py --all-usb --seconds 600
```

### 8. 伺服器：環境檢查
```bash
python S1/check_env.py
```

---

## 通過條件
| 項目 | 條件 |
|------|------|
| 控制項 | `set_controls.py --profile object` 全部 `OK` |
| 對焦 | 書在 0.6 m 處清晰（掃描照片目視確認文字清楚） |
| 曝光 | 亮度約 100–150、過曝 < 1% |
| 2 台串流 10 分鐘 | 每台 fps ≥ 24、無效影格 0、重新連線 0、電壓不足 0、原始 MJPEG = True |
| 伺服器 | `check_env.py` 全部必要項目 `OK`（結束碼 0）。標 `??` 的是選用項目（COLMAP 的 CUDA / GPU SIFT、編譯器、open3d），不影響通過，但請一併回報：GPU SIFT 不能用時，S8 會改用 CPU 版 |

## 請貼回給 Claude 的資訊
1. 步驟 2 的輸出（各控制項的設定值與讀回值）。
2. 步驟 3、4 掃描結果的表格，以及你選定的對焦值、曝光值。
3. 步驟 6 最後的摘要（`checks`、每台的 fps / 無效影格 / 重連、`cpu_percent`、`max_temp_c`）。
4. （選用）步驟 7 的摘要。
5. 步驟 8 的完整輸出（或 `output/01_env/<主機名稱>/run_*/env_report.json`）。

## 常見問題
- **`裝置不存在`**：相機插孔與 `config/pi.yaml` 的 by-path 不符。執行 `ls -l /dev/v4l/by-path/` 對照後修改設定，或把相機插回原本的孔。
- **`原始 MJPEG = False`**：OpenCV 沒有輸出相機的原始 JPEG，程式會改成重新壓縮（品質 95），可以使用，但 CPU 負擔較高。請回報 OpenCV 版本。
- **電壓不足**：確認 `vcgencmd get_config usb_max_current_enable` 為 1；換用 5A 規格的短 USB-C 線。
- **fps 偏低**：用 `top` 檢查是否有其他程式佔用 CPU（例如 Docker 容器）。
