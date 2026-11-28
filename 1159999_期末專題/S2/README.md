# S2：行動供電燒機測試

> 狀態：程式已寫，**待 S1 通過後驗證**。

## 目標
以 Aukey PB-Y37 行動電源供電，模擬實際拍攝（相機串流 + 定時寫入 SSD）30 分鐘，確認不會出現電壓不足、相機斷線或過熱。

## 檔案
| 檔案 | 在哪裡執行 | 用途 |
|------|-----------|------|
| `check_power.py` | Pi | 每秒記錄電壓旗標、EXT5V 輸入電壓、PMIC 功耗、溫度、CPU、各相機 fps |

## 執行
```bash
python S2/check_power.py --minutes 2 --no-cameras    # 先看供電狀態
python S2/check_power.py --minutes 30                # 正式燒機測試（以 PB-Y37 供電）
```
結果存在 `/mnt/ssd/3dgs_capture/_tests/power_<時間>/`（`power_test.csv`、`summary.json`）。

## 通過條件
電壓不足 0 次、相機斷線 0 次、最高溫度 < 75°C。
PMIC 讀數不包含相機與 SSD 的耗電，整機功耗請用 USB 電流表量測，記錄在 README。

## 請貼回給 Claude 的資訊
`summary.json` 的內容，以及 USB 電流表的讀數（若有）。
