# S5：同步拍攝

> 狀態：程式已寫，**待 S1、S2 通過後驗證**。S3（標定）也使用本程式的標定模式拍照。

## 目標
每次觸發時 2 台相機各存一張 MJPEG 原始影像，記錄時間差、電壓、溫度、對焦讀回值，有問題時提示重拍。

## 檔案
| 檔案 | 在哪裡執行 | 用途 |
|------|-----------|------|
| `capture_rig.py` | Pi | 同步拍攝（場景 / 標定模式），筆電網頁預覽與操作 |

## 執行
```bash
tmux new -s rig
python S5/capture_rig.py --mode calib --profile object          # S3 標定照片
python S5/capture_rig.py --scene book_001 --profile object       # 拍書
```
筆電瀏覽器開 `http://<Pi 的 IP>:8000`：按「拍攝」或空白鍵；終端機 Enter 也可拍攝，`q` 結束。
結束後執行 `sudo shutdown -h now` 再拔電。

## 輸出
`/mnt/ssd/3dgs_capture/<scene>/<session>/`：`session.json`、`group_xxxxx/cam{0,1}.jpg`、`group_xxxxx/group.json`、`session_summary.json`。

## 通過條件
時間差 ≤ 20 ms 的組 ≥ 95%、掉幀 0、電壓不足 0。
