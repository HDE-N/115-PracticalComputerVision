# 1151125_3D稀疏點雲

## 作業目標
用 4 個鏡頭和 ChArUco 標定板完成多相機標定，再用 OpenCV 三角測量建立物品的 3D 稀疏點雲。
詳細步驟見 [Task.md](Task.md)，資料保存規則見 [CLAUDE.md](CLAUDE.md)。

## 依賴的前期結果
- 無

## 檔案說明
| 檔案 | 用途 |
|------|------|
| Task.md | 作業題目與實作步驟 |
| CLAUDE.md | 專案規範：目錄結構、資料保存規則、品質門檻 |
| requirements.txt | Python 套件 |
| config/board.yaml | ChArUco 板參數（字典、方格數、方格/ArUco 邊長，mm） |
| config/cameras.yaml | 相機裝置編號、解析度、手動 3A 設定、相機間實測距離 |
| config/params.yaml | 各步驟參數與品質門檻 |
| src/capture.py | 拍照：內參（單台）/ 外參（多台同時）/ 物品（多台同時） |
| src/calib_intrinsic.py | 步驟 1-1：內參標定（calibrateCamera，逐張剔除誤差最大的照片） |
| src/calib_extrinsic.py | 步驟 1-2：外參標定（solvePnP 初值 + Bundle Adjustment） |
| src/match_features.py | 步驟 2-2：SIFT 特徵配對 + 對極約束篩選（另與 RANSAC / LMedS 比較） |
| src/reconstruct.py | 步驟 2-3、2-4：三角測量、篩選、離群點濾除、輸出 PLY 與截圖 |
| src/visualize.py | 顯示相機位置、點雲，量測點雲中兩點距離 |
| src/common/ | 共用工具（讀寫檔、run 資料夾、標定板、相機、幾何、繪圖） |

## 執行方式
```bash
pip install -r requirements.txt

# 0. 先修改 config/board.yaml（實測尺寸）與 config/cameras.yaml（裝置編號、對焦、曝光）

# 1-1 內參：每台相機各拍 20–30 張，再標定
python src/capture.py intrinsic --cam 0      # 依序對 cam0 ~ cam3 執行
python src/calib_intrinsic.py --cam all

# 1-2 外參：相機固定後，4 台同時拍 10–20 組，再標定
python src/capture.py extrinsic
python src/calib_extrinsic.py
python src/visualize.py layout               # 檢查相機位置是否與實際擺放一致

# 2 物品：拍攝 -> 特徵配對 -> 三角測量
python src/capture.py object
python src/match_features.py                 # 預設處理最新一組 shot，可用 --shot shot_002 / all
python src/reconstruct.py
python src/visualize.py cloud                # 顯示點雲
python src/visualize.py measure              # 量兩點距離，驗證尺度（需要 open3d）
```
- 拍照視窗：空白鍵拍照，q / ESC 結束。每按一次都會存檔（不論是否偵測到角點）。
- 每個步驟只有**通過 config/params.yaml 的門檻**時才會更新 `latest.txt`，下一步驟預設讀取 latest。
  未通過時程式會列出原因；確認要採用時可加 `--force-latest`，或手動編輯 `latest.txt`。
- 想用某一次舊結果時，各腳本都可以指定 run（例如 `--intrinsic-runs 0:run_xxx`、`--extrinsic-run run_xxx`）。

## 輸出結果（供後續作業調用）
| 檔案 | 格式 | 內容 |
|------|------|------|
| output/01_intrinsic/cam{0..3}/run_*/intrinsics.yaml | OpenCV FileStorage | 每台相機的 K、dist、影像尺寸、RMS |
| output/02_extrinsic/run_*/extrinsics.yaml | OpenCV FileStorage | 每台相機的 K、dist、R、t、rvec、P = K[R\|t]、光心 center |
| output/03_features/shot_xxx/run_*/keypoints_cam{i}.npz、matches_{i}{j}.npz | npz | SIFT 特徵與各階段配對 |
| output/04_reconstruction/shot_xxx/run_*/cloud.ply | PLY（ASCII，含顏色） | 最終 3D 稀疏點雲 |
| output/04_reconstruction/shot_xxx/run_*/points_filtered.npz | npz | 點座標 `X_mm`、顏色、每點的視角、重投影誤差、三角化夾角 |

- 座標與單位：**世界座標 = cam0 的相機座標**（X 右、Y 下、Z 前），外參定義為 `P_cam_i = R_i · P_world + t_i`，單位 mm。
- `P_i` 作用在**去畸變後**的像素座標（先用 `cv2.undistortPoints(pts, K, dist, P=K)`）。
- 讀取方式（檔案路徑含中文，Windows 上請用 `src/common/calib_io.py`，或先讀成字串再交給 FileStorage）：
  ```python
  import sys; sys.path.insert(0, "../1151125_3D稀疏點雲/src")
  from common.calib_io import load_extrinsics
  from common.io_utils import resolve_run
  from pathlib import Path
  run = resolve_run(Path("../1151125_3D稀疏點雲/output/02_extrinsic"))   # 讀 latest.txt
  cams = load_extrinsics(run / "extrinsics.yaml")    # {cam_id: {"K","dist","R","t","P","center","image_size"}}
  ```

> 每個步驟資料夾中的 `latest.txt` 記錄目前採用的 run 資料夾名稱，調用時請先讀它。
> 原始照片在 `data/`，`output/` 中的 `.png` 預覽圖也是一樣，**只存在本機，不在 GitHub 上**。
> 所有參數（yaml / npz / json / csv）與點雲（ply）都會上傳，詳見 [CLAUDE.md](CLAUDE.md)。

> ⚠ 內外參只在**相機設定與擺放位置不變**時有效。後續作業若換了相機設定或位置，就要重新標定。

## 課堂以外使用的技術與原因
| 技術 | 用在 | 原因 |
|------|------|------|
| 最大生成樹串接外參初值、旋轉平均（chordal mean） | calib_extrinsic.py | 從多組觀測中挑共同觀測最多的相機對串接，降低誤差傳播（Day06 p3–7），並得到穩定的 BA 初值 |
| 雙向配對檢查（mutual check） | match_features.py | 在 ratio test 之外再剔除一對多的錯誤配對 |
| Union-find 串接多視角 track、多視角 DLT | reconstruct.py | 同一特徵被 3 台以上相機看到時一起三角化，精度較高；Task.md 2-3 的加分項 |
| 只優化 3D 點的非線性最小平方 | reconstruct.py | 線性三角化不是重投影誤差的最佳解，微調後誤差更小 |
| 統計式離群點濾除 | reconstruct.py | 與 open3d `remove_statistical_outlier` 相同的演算法，自行以 scipy 實作，未安裝 open3d 也能執行 |

## 備註
- ChArUco 板參數：（待補，實際量測後填入 config/board.yaml 並記錄於此）
- 相機設定（解析度、對焦、曝光）：（待補）
- 各步驟誤差：（待補，實際拍攝後填入內參 RMS、BA 前後 RMS、基線計算值 vs 實測值、點數）
- 程式已用合成資料（已知內外參、渲染的 ChArUco 照片與紋理立方體）跑過完整流程驗證：
  內參 RMS 約 0.11–0.14 px；外參旋轉誤差 < 0.13°、平移誤差 < 1.5 mm（相機距物品約 650 mm）；
  點雲到立方體表面距離中位數 0.26 mm。合成資料不在本資料夾內。
