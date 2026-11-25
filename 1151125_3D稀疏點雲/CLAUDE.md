# CLAUDE.md — 1151125_3D稀疏點雲

本檔案給 Claude Code 使用，說明在此資料夾工作時的目標、規範與必須遵守的規則。
作業題目與完整步驟見 [Task.md](Task.md)，本檔案的規則優先於 Task.md 中的「建議專案結構」。

## 專案背景
- 115 學年度「電腦視覺實務」作業，倉庫根目錄是每週作業的備份，本資料夾是 115/11/25 的作業。
- 設備：4 個 UVC 鏡頭、1 塊 ChArUco 標定板。
- 目標：完成 4 鏡頭的內參、外參標定，拍攝物品後以三角測量建立 **3D 稀疏點雲**。
- 參考教材：`../class_ppt/`（Day03 標定、Day04 對極幾何、Day05 SIFT/ORB、Day06 多相機標定/BA）。
  教材內的方法與 API 優先採用；用到課堂沒教的技術時，要在 README 中說明原因。
- 後續週次的作業可能會調用本週結果（內外參、點雲），所以**輸出必須完整、格式穩定、有文件說明**。

## 最重要的規則：所有照片與參數都要保存
1. **拍過的每一張照片都要保存**，包含模糊、偵測失敗、後來被剔除的照片。
   - 不可刪除或覆寫原始照片；剔除只能「記錄在清單中」，不能動檔案。
   - 照片一律存成無損格式 **PNG**，不可存 JPG。
   - `data/` 已列在 `.gitignore`，**照片只保存在本機、不上傳 GitHub**。不要修改這條規則，也不要把照片複製到其他會被追蹤的位置。
   - 每次拍攝同時存一份 metadata（時間、相機編號、解析度、對焦、曝光、白平衡等實際讀回的設定值）。
2. **計算過的每一組參數都要保存**，包含中間結果，不只最終結果。
   - 每次執行都輸出到新的 `run_YYYYMMDD_HHMMSS/` 資料夾，**不可覆寫舊的執行結果**。
   - 每個步驟的資料夾放一個 `latest.txt`，內容是最新一次「採用的」run 資料夾名稱
     （Windows 上不使用 symlink）。下一步驟預設讀取 `latest.txt` 指向的結果。
   - 每個 run 資料夾都要存 `config_used.yaml`（本次使用的所有參數與門檻值）和 `run.log`。
3. 參數存檔格式：
   - 矩陣類（K、dist、R、t、P、F）：`cv2.FileStorage` 的 `.yaml`，方便 OpenCV 直接讀回。
   - 大量點資料（角點、特徵點、配對、3D 點）：`.npz`。
   - 統計數據與報告（誤差、數量、篩選結果）：`.json` 或 `.csv`。
   - 點雲：`.ply`（含顏色）。
   - 所有長度單位統一為 **mm**，與標定板量測值一致；檔案中要註明單位。
4. 上傳 GitHub 的範圍：`.gitignore` 已排除 `output/` 中所有 `.png` 預覽圖（去畸變對照、配對圖、
   點雲截圖、相機位置圖），只上傳參數檔（yaml / npz / json / csv / log）與點雲（ply）。
   需要被後續週次調用的結果**不可只存成 png**，一定要有對應的參數檔。

## 目錄結構
```
1151125_3D稀疏點雲/
├── CLAUDE.md / Task.md / README.md
├── config/
│   ├── board.yaml           # ChArUco：字典、squaresX/Y、方格邊長、ArUco 邊長（實際量測值，mm）
│   ├── cameras.yaml         # 相機裝置編號、解析度、對焦、曝光、白平衡
│   └── params.yaml          # 各步驟門檻：RMS 上限、ratio test、對極距離、重投影誤差、夾角...
├── data/                    # 原始照片，只增不刪
│   ├── intrinsic/cam{0..3}/        img_YYYYMMDD_HHMMSS_xxx.png (+ .json metadata)
│   ├── extrinsic/frame_xxx/        cam0.png ~ cam3.png (+ frame.json)
│   └── object/shot_xxx/            cam0.png ~ cam3.png (+ shot.json)
├── output/
│   ├── 01_intrinsic/cam{i}/run_*/
│   │   ├── intrinsics.yaml         # K、dist、image_size、RMS
│   │   ├── detections.npz          # 每張照片的 ChArUco 角點與 id
│   │   ├── per_image_errors.csv    # 每張照片的重投影誤差、是否採用、剔除原因
│   │   ├── rvecs_tvecs.npz         # 每張照片的標定板位姿
│   │   └── undistort_preview/      # 去畸變前後對照圖
│   ├── 02_extrinsic/run_*/
│   │   ├── board_poses.npz         # 每個 frame、每台相機的 solvePnP 結果
│   │   ├── extrinsics_init.yaml    # BA 前的外參
│   │   ├── extrinsics.yaml         # BA 後的外參：每台相機 R、t、P = K[R|t]
│   │   ├── ba_report.json          # BA 前後各相機重投影誤差、迭代資訊
│   │   ├── baselines.csv           # 相機間距離：計算值 vs 實測值
│   │   └── camera_layout.png       # 相機位置與朝向示意圖
│   ├── 03_features/shot_xxx/run_*/
│   │   ├── keypoints_cam{i}.npz    # SIFT 關鍵點座標與描述子
│   │   ├── matches_{i}{j}.npz      # 每階段的配對：原始 / ratio test 後 / 對極約束後
│   │   ├── F_{i}{j}.yaml           # 由外參算出的 F（以及 RANSAC 估計的 F，供比較）
│   │   ├── match_stats.json        # 各階段配對數量
│   │   └── match_vis/              # 配對視覺化圖
│   └── 04_reconstruction/shot_xxx/run_*/
│       ├── points_raw.npz          # 三角化後、篩選前的所有點
│       ├── points_filtered.npz     # 篩選後的點、顏色、來源相機、重投影誤差
│       ├── filter_stats.json       # 每個篩選條件剔除的點數
│       ├── cloud_raw.ply / cloud.ply   # 篩選前 / 最終點雲
│       └── screenshots/            # 多角度截圖
└── src/
    ├── common/                     # 共用工具：讀寫 yaml/npz、讀 latest.txt、建立 run 資料夾、log
    ├── capture.py                  # 拍照（內參 / 外參 / 物品三種模式）
    ├── calib_intrinsic.py          # 步驟 1-1
    ├── calib_extrinsic.py          # 步驟 1-2（含 BA）
    ├── match_features.py           # 步驟 2-2
    ├── reconstruct.py              # 步驟 2-3、2-4
    └── visualize.py                # 相機位置、點雲顯示與截圖
```

## 實作規範
- 環境：Python 3.10+、`opencv-contrib-python` ≥ 4.8（使用 `cv2.aruco.CharucoDetector` 新 API）、
  `numpy`、`scipy`、`open3d`。
- 每個腳本都可以單獨執行，用 `argparse` 接參數，預設值從 `config/*.yaml` 讀取，
  **不要把門檻值或板子尺寸寫死在程式碼裡**。
- **每個程式都要有簡易 UI**：不加參數執行時開啟參數視窗（`src/common/gui.py` 的 `ScriptGUI`），
  能在 UI 上輸入執行參數與相關設定（例如標定板版本、門檻值），並加進 `src/app.py` 主選單。
  UI 修改的設定以 `--set` 傳給程式，必須記錄在 `config_used.yaml`。
- 路徑一律用 `pathlib` 和相對於本資料夾的路徑，不可寫死 `C:\Users\...` 這類絕對路徑。
- 拍照時：
  - 依 Day03 p33–34 關閉自動對焦、自動曝光、自動白平衡（`cv2.CAP_DSHOW`），
    設定完成後**讀回實際值**寫進 metadata。
  - 預覽視窗即時顯示偵測到的 ChArUco 角點數，按鍵拍照，避免拍到無效照片。
  - 外參與物品照片要 4 台**盡量同時**擷取，並記錄每台相機的擷取時間差。
- 座標系統：世界座標 = cam0 相機座標（R0 = I, t0 = 0）。外參 R、t 的定義為
  `P_cam_i = R_i · P_world + t_i`（與 Day04 p3 一致），存檔時要在 yaml 註解中寫明。
- 程式註解與輸出訊息使用繁體中文。

## 品質門檻（實際值寫在 `config/params.yaml`）
| 項目 | 建議門檻 |
|------|----------|
| 內參 RMS 重投影誤差 | < 0.5 px |
| 單張照片最少角點數 | ≥ 6 |
| 外參（BA 後）重投影誤差 | < 1 px |
| Lowe ratio test | 0.7–0.8 |
| 對極線距離 | < 2–3 px |
| 三角化點重投影誤差 | < 2 px |
| 三角化夾角 | > 2° |

結果超過門檻時，**不要自行放寬門檻**，要把數據回報給使用者，並建議原因（例如照片角度不夠多樣、板子不平）。

## 工作流程規則
1. 依步驟順序實作：拍照 → 內參 → 外參 → 特徵配對 → 三角測量 → 後處理。
   每完成一步就實際跑一次，檢查輸出檔都有產生、數值合理，再做下一步。
2. 需要實體操作的事（拍照、量測板子尺寸、量相機間距離）由使用者完成；
   Claude 負責寫好拍照程式，並清楚告訴使用者要拍什麼、拍幾張。
3. 不可修改或刪除 `data/` 中已存在的照片，也不可刪除 `output/` 中舊的 run 資料夾。
4. `../class_ppt/` 只能讀取、不可修改。其他週的資料夾只在使用者要求調用前期結果時才讀取，且一律不修改。
5. 每完成一個步驟，更新本資料夾的 `README.md`：
   - 「檔案說明」「執行方式」補上新的腳本。
   - 「輸出結果」寫明檔案路徑、格式與內容，讓後續週次可以直接調用。
   - 「備註」記錄實際的板子參數、相機設定、各步驟的誤差數據。
6. 不要自行 commit 或 push，除非使用者要求。
