# CLAUDE.md — 1159999_期末專題

本檔案給 Claude Code 使用，說明在此資料夾工作時的目標、硬體事實、規範與必須遵守的規則。
- 完整專題計畫（4 階段、S1–S15）見 [Task.md](Task.md)；環境安裝見 [PATH.md](PATH.md)；技術、論文與執行方式見 [README.md](README.md)。
- **本檔案的規則優先於 Task.md**；Task.md 描述整個學期的計畫，本檔案定義「當前階段」要做什麼。

---

## 1. 當前階段的最終目標（P0：桌上的一本書）

> **建模出放置於桌上的一本書，並將此方法彙總成一個可重複使用的框架。**

### 完成的定義（全部達成才算完成本階段）
1. **拍攝**：Raspberry Pi 5 + 2 台 USB 相機，以 `S5/capture_rig.py` 繞書拍攝，影像存在 SSD（`/mnt/ssd/3dgs_capture/book_001/<session>/`）。
2. **重建**：在 Linux 伺服器上，以**一行指令** `python run_pipeline.py --scene book_001 ...` 依序完成
   匯入 → 前處理 → SfM → 尺度對齊 → 3DGS 訓練 → 清理 → 評估 → 網頁匯出，每一步都有輸出與品質門檻。
3. **成果**：瀏覽器開 `web/index.html?scene=book_001` 可自由旋轉、縮放觀看書的 3DGS 模型。
4. **量化**：以 `config/scenes/book_001.yaml` 的門檻驗收：
   - SfM 註冊率 ≥ 95%、平均重投影誤差 < 1 px
   - ChArUco 尺度誤差 < 1%（方格邊長 30 mm）
   - 測試視角 PSNR ≥ 28 dB
   - 書的長、寬、厚與實測值誤差 < 3%
5. **框架化**：換場景只需新增 `config/scenes/<name>.yaml`（之後的教室直接沿用同一套程式）；
   換工具只改 `config/pipeline.yaml`；所有參數與門檻在 `config/params.yaml`。
6. **文件**：README.md 寫明每支程式的執行方式與實際數據；PATH.md 可讓別人從乾淨系統重建環境。

### 本階段不做（列為下一階段，不要主動實作）
教室 / 建築拍攝（`room` / `outdoor` 場景的地面對齊）、S13 碰撞網格、Unity 匯入、壓縮比較實驗、GUI 主選單。
程式結構要**預留**這些擴充點（例如 align 的 `mode: floor` 先丟出「下一階段實作」的明確錯誤），但不實作。

---

## 2. 硬體與環境事實（已實測，寫程式時以此為準）

### 拍攝端
| 項目 | 事實 |
|------|------|
| 樹莓派 | Raspberry Pi 5 Model B Rev 1.1（2 GB），原廠散熱器 + 風扇，Raspberry Pi OS 64-bit（**Debian 13 trixie**，aarch64，有 conda base 環境），主機名稱 `immich-vv202`（只是名字，沒有跑 Immich） |
| 相機 | 與 1151125 同一批通用 USB 相機（USB ID `5843:e603`，名稱「WebCamera」，內建麥克風），共 4 台，**預設用 2 台**；USB 2.0 裝置 |
| 相機格式 | MJPG：2048×1536、1920×1080、1280×720…皆 30 fps；YUYV 最高 1280×720 @ 10 fps。**採用 MJPG，直接保存原始 JPEG bytes** |
| 相機接法 | cam0（下）= `/dev/v4l/by-path/platform-xhci-hcd.0-usb-0:2:1.0-video-index0`（Bus 001）；cam1（上）= `/dev/v4l/by-path/platform-xhci-hcd.1-usb-0:2:1.0-video-index0`（Bus 003）。**一律用 by-path 開相機**，`/dev/videoN` 會互換 |
| V4L2 控制項 | `focus_automatic_continuous` / `focus_absolute`（0–1023，**僅能軟體對焦**）；`auto_exposure`（預設 0 = 自動，1 = 手動）/ `exposure_time_absolute`（**回報範圍異常 min=65522 max=256**，**實測（2026-11-28）：min > max，驅動把任何寫入值夾到 256，手動曝光固定在 256，亮度改用 `gain` 調整**）；`white_balance_automatic` / `white_balance_temperature`（**只有 1–5 五段**）；`gain` 0–255；`power_line_frequency` 預設 50 Hz → **台灣要設 2（60 Hz）**；`sharpness` 維持 0 |
| SSD | SanDisk Extreme Portable 2 TB（USB 3.2 Gen 2，`uas`，Bus 002 5000M），已格式化為 **ext4**，以 UUID 掛載在 **`/mnt/ssd`**；影像根目錄 `/mnt/ssd/3dgs_capture/` |
| 供電 | Aukey PB-Y37（20,000 mAh / 74 Wh，5V 最高 3A），**不使用 UPS HAT**。`/boot/firmware/config.txt` 已加 `usb_max_current_enable=1`（2026-11-28 確認生效，`get_throttled=0x0`） |
| 操作方式 | 筆電在旁邊，以 SSH + 瀏覽器（`http://<Pi IP>:8000`）操作；不使用 VNC |

### 運算端
| 機器 | 規格 | 角色 |
|------|------|------|
| Linux 伺服器 | AMD Ryzen 9 9950X、RAM 64 GB、**NVIDIA RTX PRO 5000 Blackwell 48 GB**（compute capability 12.0 / `sm_120`）、驅動 595.99；**乾淨的 Ubuntu 26.04，只裝了 GPU 驅動，使用者沒有 sudo 權限**；硬碟已擴增為 492 GB（2026-11-28 維護後可用約 **416 GB**） | 主運算：COLMAP、3DGS 訓練、評估、匯出 |
| Windows 桌機 | i7-13700、RAM 64 GB、RTX 4060 Ti 16 GB、硬碟約 1 TB；目前的開發機（本資料夾所在） | 開發、網頁檢視、SuperSplat、資料備份 |

注意：
- **伺服器沒有 sudo**：不可使用 `apt`、不可寫入系統目錄、不可掛載 SSD。所有工具裝在家目錄的 conda 環境：
  `gs3d`（Python 3.12、conda 的 GCC 13 + CUDA Toolkit 12.8、PyTorch cu128、gsplat）與 `colmap`（conda-forge CUDA 版，`~/miniconda3/envs/colmap/bin/colmap`；伺服器使用既有的 Miniconda，一律以 conda-forge 頻道安裝）。OpenCV 用 headless 版（沒有 libGL）。拍攝資料以 rsync 從 Pi 傳到 `~/capture_in/<scene>/`。
  給使用者的指令一律不能含 `sudo`；需要 root 的事改提替代方案或請使用者洽管理者。
- conda-forge 的 COLMAP 不一定有 `sm_120` 的 GPU SIFT：S8 要能自動退回 CPU 版 SIFT（`colmap.use_gpu: auto`）。
- 伺服器硬碟已擴增（可用約 416 GB），空間不再是限制；仍保留「中間 checkpoint 只留最後一個」的設定（`trainer.keep_last_ckpt_only`），原始資料以 SSD 為主、Windows 備份。
- Blackwell 必須用 CUDA 12.8 以上編譯（gsplat、COLMAP 都要），PyTorch 用 cu128 或更新版本。
- 開發機（Windows）沒有 COLMAP / gsplat，只能測試純 Python 的部分；Pi 與伺服器的程式要寫成可在 Linux 執行，**不要假設能在本機跑完整流程**。

---

## 3. 系統架構與步驟（本階段範圍）

### 分階段實作（資料夾 = 階段）
程式依步驟放在各自的資料夾 `S1/`、`S2/`、`S3/`、`S5/`、`S6/` …（S4 是拍攝規劃，沒有程式）。**一次只完成一個階段**：
寫好程式 → 用合成資料測試能測的部分 → 更新該階段的 `README.md`（目標、執行、通過條件、要貼回的資訊）→ **請使用者在實機上驗證** →
使用者回報通過後，才開始下一個階段。不要一次寫完多個階段。

```
1159999_期末專題/
├── CLAUDE.md / Task.md / PATH.md / README.md / requirements*.txt / .gitignore
├── config/            pi.yaml、pipeline.yaml、params.yaml、rig.yaml、board.yaml、scenes/<scene>.yaml
├── common/
│   ├── pi_common.py   Pi 共用（只依賴 numpy / opencv / PyYAML）
│   └── core/          伺服器共用框架：io_utils、step、geometry、colmap_txt（之後加 gs_ply、charuco）
├── S1/ … S14/         各階段程式 + README.md（驗證清單）
├── run_pipeline.py    依序執行 S6–S14
└── web/               網頁檢視器（S14）
```
每個階段腳本開頭以 `sys.path.insert(0, <專題資料夾>)` 後 `from common.xxx import ...`。

```
Pi：S1 檢查 → S2 供電燒機 → S3 標定照片 / S5 場景拍攝
          │ rsync / scp 傳到伺服器 ~/capture_in/（伺服器沒有 sudo，不能掛載 SSD）
伺服器：S3 標定計算 → S6 匯入 → S7 前處理 → S8 SfM → S9 對齊 → S10 訓練 → S12 清理 → S11 評估 → S14 網頁匯出
```

| 步驟 | 程式 | 輸入 → 輸出 | 狀態 |
|------|------|------------|------|
| S1 | `S1/check_cameras.py`、`S1/set_controls.py`、`S1/check_env.py` | Pi：相機格式、fps、控制項讀回、掃描對焦 / 曝光；伺服器：環境檢查 | **已完成，等待使用者驗證**（合成測試通過） |
| S2 | `S2/check_power.py` | 燒機測試（電壓旗標、溫度、斷線） | 已寫，未實測 |
| S3 | `S5/capture_rig.py --mode calib` → `S3/calib.py` | Pi 拍 ChArUco → 1151125 標定程式 → `config/rig.yaml` | Pi 端已寫；`S3/calib.py` **待實作** |
| S5 | `S5/capture_rig.py --scene book_001` | `group_xxxxx/cam{0,1}.jpg` + `group.json` | 已寫，未實測 |
| S6 | `S6/ingest.py` | rsync 傳來的 session（`~/capture_in/<scene>/<session>`）→ `data/raw/<scene>/<session>/`、`manifest.csv`（SHA-256） | 待實作 |
| S7 | `S7/preprocess.py` | 模糊 / 重複 / 曝光檢查 → `images/cam{i}/`、`frames.csv` | 待實作 |
| S8 | `S8/sfm.py` | COLMAP（固定內參 + rig）→ `undistorted/`、`sparse_txt/` | 待實作 |
| S9 | `S9/align.py` | ChArUco 三角化 + Umeyama → 公尺、Y 朝上；產生 gsplat 用的 `dataset/` | 待實作 |
| S10 | `S10/train.py` | gsplat `simple_trainer.py` → `point_cloud.ply`、val 指標 | 待實作 |
| S12 | `S12/cleanup.py` | 裁切、不透明度 / 尺度過濾、統計式離群點濾除 → `cleaned.ply` | 待實作 |
| S11 | `S11/evaluate.py` | 彙整指標、書的尺寸誤差（OBB vs 實測）→ `metrics.json`、`summary.md` | 待實作 |
| S14 | `S14/export.py` + `web/index.html` | `.ply` → `.splat`，網頁檢視器（GaussianSplats3D） | 待實作 |
| — | `run_pipeline.py` | 依序執行 S6–S14，門檻未過即停止 | 待實作 |
| — | `common/core/`（io_utils、step、geometry、colmap_txt） | 共用框架 | 已寫；`gs_ply.py`、`charuco.py` 待實作 |

### 已決定的設計（不要任意更改，要改先問使用者）
- **相機控制用 `v4l2-ctl` 子行程**設定與讀回（OpenCV 屬性對這台相機不可靠）；開始串流**之後**再套用控制項。
- **MJPEG 原始資料**：OpenCV `CAP_PROP_CONVERT_RGB = 0` 取得 JPEG bytes 直接寫檔，不重新壓縮；不支援時才以品質 95 重新編碼並標記 `raw_mjpeg: false`。
- **同步**：觸發後每台各收 3 張，挑時間差最小的組合；超過 `max_skew_ms`（20 ms）標記待重拍。拍攝方式為「停下 → 觸發 → 再走」。
- **尺度與座標（物品場景）**：ChArUco 板平放在**書旁**（不能被書蓋住），S9 在去畸變影像上偵測角點、用 SfM 位姿三角化，再以 Umeyama 求相似轉換到板子座標：
  公尺、**Y 軸朝上**（板子座標 → 世界：`BOARD_TO_YUP = [[1,0,0],[0,0,-1],[0,1,0]]`）、原點在板子中心。**在訓練前對齊**，之後所有步驟都在同一座標系。
- **對焦設定檔**：`object`（書，約 0.5–1 m）/ `room` / `outdoor`；每個設定檔各標定一次內參。拍攝時每組讀回對焦值比對。
- **SfM**：每台相機各自 `feature_extractor`（`--ImageReader.single_camera 1` + 該相機內參），再 `rig_configurator`；
  rig 或 `ba_refine_sensor_from_rig` 不被支援時**自動退回不使用 rig** 並記錄警告。沒有標定結果時讓 COLMAP 自行估內參（`OPENCV`）。
  物品場景用 `exhaustive_matcher`。去畸變後轉成 TXT 給後續步驟。
- **訓練資料夾**：S9 輸出 `dataset/images/`（檔名攤平為 `cam0__group_00001.jpg`，硬連結）+ `dataset/sparse/0/*.txt`，直接給 gsplat。
- **gsplat 指令以 `config/pipeline.yaml` 的樣板組成**（版本差異只改設定檔）；若沒產生 `.ply`，從最後的 checkpoint（`splats`：means、scales、quats、opacities、sh0、shN）轉出。
- **網頁格式**：`.splat`（每顆 32 bytes：位置 f32×3、尺度 f32×3、RGBA u8×4、四元數 u8×4），以 GaussianSplats3D 顯示（`sharedMemoryForWorkers: false`，用 `python -m http.server` 開啟）。

---

## 4. 相機標定（參考 1151125）

- 前置作業 [../1151125_3D稀疏點雲](../1151125_3D稀疏點雲/)（README、Task.md、CLAUDE.md）已完成 ChArUco 多相機標定的完整程式：
  `calib_intrinsic.py`（calibrateCamera、逐張剔除）、`calib_extrinsic.py`（solvePnP 初值 + 最大生成樹 + Bundle Adjustment）。
- **沿用程式、不沿用結果**：1151125 是在 Windows / DSHOW 下拍的，對焦與控制項數值和 Pi（V4L2）不同，內參不能直接用。
- 標定板：`config/board.yaml`（自 1151125 複製）——型號 CC400-30-22.5、`DICT_5X5_1000`、7×5 方格、方格 30 mm、ArUco 22.5 mm。
  方格數 / 字典若與實物不符，用 1151125 的 `detect_board.py` 偵測後更新。
- 流程（`S3/calib.py` 待實作）：
  1. Pi：`python S5/capture_rig.py --mode calib --profile object`，2 台同時拍 20–30 組 ChArUco（涵蓋畫面四角、不同距離與傾斜；兩台重疊區也要有），畫面會顯示每台的角點數。
  2. 伺服器：`S3/calib.py --session <路徑> --profile object` 建立工作區 `output/03_calib/<profile>/run_*/workspace/`，
     把 `group_xxxxx/cam{i}.jpg` 轉成 1151125 的資料結構（`data/intrinsic/cam{i}/*.png`、`data/extrinsic/frame_xxxxx/cam{i}.png`），
     寫入 `config/{board,cameras,params}.yaml`，再以子行程執行
     `python ../1151125_3D稀疏點雲/src/calib_intrinsic.py --root <workspace> --nogui` 與 `calib_extrinsic.py --root <workspace> --nogui`。
  3. 讀回 `extrinsics.yaml`（cv2.FileStorage：`cam{i}_K`、`cam{i}_dist`、`cam{i}_R`、`cam{i}_t`，mm）→ 寫入 `config/rig.yaml` 的 `profiles.<profile>`
     （COLMAP `FULL_OPENCV` 參數、cam_from_rig 的 R / t（**公尺**）、基線、來源 run、RMS）。
- **不要修改 1151125 的任何檔案**；只能讀取與以子行程呼叫。1151125 的 `common` 套件與本專題 `common/core` 名稱不同，不要互相 import（需要時以 `importlib` 依檔案路徑載入，例如 `common/board.py`）。
- 門檻沿用 1151125：內參 RMS < 0.5 px、BA 後外參 < 1 px；另加相機間距（游標卡尺實測）誤差 < 2%。

---

## 5. 參考論文與開源程式

### 使用外部程式碼的規則
1. **只從受信任的作者與官方來源取得**：論文作者或其實驗室的官方 GitHub、知名組織（下表）。不使用來路不明的 fork、二次打包或個人重新上傳的版本。
2. **以 `git clone` 到程式外部資料夾（例：`~/gsplat`）並固定版本**（release tag 或 commit hash），把版本記錄在 README 與該 run 的 `config_used.yaml`。不要把外部原始碼複製進本倉庫。
3. **先確認授權**：例如 Inria 原始 3DGS 為非商業研究授權，只能用於本課程研究；gsplat（Apache-2.0）、COLMAP（BSD）可自由使用。授權寫進 README。
4. 新增外部依賴前先在 README「技術與論文」表加一列（用途、來源、版本、授權），並說明為何不用既有工具。
5. 執行外部安裝腳本前先閱讀內容；不要以 `curl ... | sh` 執行未檢視的腳本（Miniforge、NVIDIA 官方等例外，但仍要確認網址是官方網域）。

### 受信任來源（可直接使用）
| 用途 | 來源 | 論文 |
|------|------|------|
| SfM / 去畸變 | `github.com/colmap/colmap`（含 GLOMAP 全域式 SfM） | Schönberger & Frahm, CVPR 2016；Pan et al., ECCV 2024 |
| 3DGS 訓練 | `github.com/nerfstudio-project/gsplat`（**主要**）、`github.com/graphdeco-inria/gaussian-splatting`（原始實作，對照用） | Kerbl et al., ACM TOG 2023；Ye et al., JMLR 2025 |
| 大場景（下一階段） | `github.com/graphdeco-inria/hierarchical-3d-gaussians` | Kerbl et al., ACM TOG 2024 |
| 表面 / 網格（下一階段） | `github.com/hbb1/2d-gaussian-splatting`、`github.com/zju3dv/PGSR` | Huang et al., SIGGRAPH 2024；Chen et al., IEEE TVCG 2024 |
| 壓縮（比較用） | `github.com/YihangChen-ee/HAC-plus` | Chen et al., IEEE TPAMI 2025 |
| 網頁檢視 | `github.com/mkkellogg/GaussianSplats3D`、`github.com/playcanvas/supersplat` | — |
| Unity（下一階段） | `github.com/aras-p/UnityGaussianSplatting` | — |
| 標定 | OpenCV（`cv2.aruco`）、1151125 | Zhang, TPAMI 2000；Garrido-Jurado et al., Pattern Recognition 2014 |

完整論文清單（含期刊分區）見 Task.md「參考資料」與 README「技術與論文」。實作某步驟前，先看對應論文 / 官方程式的做法（例如 3DGS 的訓練設定、COLMAP 的 rig 用法），在 README 記錄「參考了誰的做法、改了什麼、為什麼」。

---

## 6. 資料保存規則（沿用 1151125，並有兩個例外）

1. **原始資料只增不刪**：Pi 拍的每張影像都保存（含模糊、待重拍的組）；剔除只記錄在 `frames.csv`，不刪檔。`data/` 不上傳 GitHub。
2. **每次執行一個新資料夾**：`output/<步驟>/<scene 或 profile>/run_YYYYMMDD_HHMMSS/`，內含 `config_used.yaml`、`run.log`、`stats.json`、`checks.json`、`timing.json`，不覆寫舊結果。
3. **`latest.txt`**：通過 `config/params.yaml` 門檻才更新；下一步預設讀 latest，可用 `--input-run` 指定；確定要採用未過門檻的結果時用 `--force-latest`。
4. **例外 1（影像格式）**：相機輸出本身是 MJPEG，原樣保存的 `.jpg` 就是原始資料（不同於 1151125「一律 PNG」）。標定時轉成 PNG 給 1151125 程式讀取。
5. **例外 2（checkpoint）**：伺服器空間有限，訓練中間 checkpoint 只留最後一個；`config_used.yaml`、log、最終 `.ply`、評估結果一律保留。
6. 格式：矩陣 / 設定用 yaml，大量點資料用 npz，統計用 json / csv，點雲與高斯用 ply；**S9 之後長度單位一律公尺**，標定沿用 mm（檔案中註明單位）。
7. GitHub 只上傳程式、設定、文件與小型結果（yaml / json / csv / log / md）；`.ply`、`.splat`、影像、COLMAP 資料庫、checkpoint 都不上傳（需新增本資料夾的 `.gitignore`）。

---

## 7. 實作規範

- Python 3.10+（伺服器 conda 環境 `gs3d`，Python 3.12；Pi 用 conda 環境 `rig`）。各階段腳本放 `S<n>/`，伺服器共用模組放 `common/core/`，Pi 共用模組為 `common/pi_common.py`。
  **Pi 執行的腳本（S1 的 set_controls / check_cameras、S2、S5）只能 import `common.pi_common`**（只依賴 numpy、opencv、PyYAML 與系統指令），不可 import `common.core`。
- 每個伺服器步驟使用 `core.step.Step`：共用參數 `--scene`、`--root`、`--input-run`、`--force-latest`、`--set KEY=VALUE`；
  `step.stage()` 計時、`step.run_cmd()` 執行外部指令並寫 `logs/<name>.log`、`step.check()` 登記門檻、`step.finish()` 決定是否更新 latest。
  結束碼：0 通過、2 未過門檻、1 執行錯誤（`run_pipeline.py` 依此決定是否繼續）。
- **門檻、路徑、工具指令都寫在 `config/`，不寫死在程式**。場景差異放 `config/scenes/<scene>.yaml` 的 `params_override`。
- 路徑用 `pathlib`、相對於專題資料夾；不寫死 `C:\Users\...` 或 `/home/...`。中文路徑讀寫影像用 `core.io_utils.imread/imwrite`。
- 外部指令以 list 傳給 subprocess（不用 shell=True）。
- 程式註解、log、輸出訊息使用**繁體中文**；每支腳本開頭 docstring 寫用途、輸入、輸出、範例指令。
- 沒有硬體或工具時可以測的部分（COLMAP TXT 讀寫、三角化 + Umeyama、PLY 讀寫、.splat 轉換、前處理）要用**合成資料**實際跑過再交付；合成資料與測試腳本放 scratchpad，不放進本資料夾。

---

## 8. 品質門檻（實際值在 `config/params.yaml` 與場景檔）

| 步驟 | 門檻（book_001） |
|------|-----------------|
| S1 | 2 台同時串流 10 分鐘：fps ≥ 24、無效影格 0、重連 0、電壓不足 0 |
| S2 | 燒機 30 分鐘：電壓不足 0、斷線 0、溫度 < 75°C |
| S3 | 內參 RMS < 0.5 px；外參 < 1 px；基線誤差 < 2% |
| S5 | 時間差 ≤ 20 ms 的組 ≥ 95%；掉幀 0；電壓不足 0 |
| S7 | 保留比例 ≥ 80% |
| S8 | 註冊率 ≥ 95%；平均重投影誤差 < 1 px；只有 1 個模型 |
| S9 | ChArUco 方格邊長誤差 < 1%；角點對齊 RMS < 1.5 mm |
| S10 | PSNR ≥ 28 dB；訓練 ≤ 20 分鐘 |
| S11 | 書的尺寸誤差 < 3% |
| S14 | `.splat` < 100 MB |

**結果未達門檻時不要自行放寬門檻**：回報數據與可能原因（例如角度不足、模糊、對焦不對、板子被遮住），由使用者決定。

---

## 9. 工作流程規則

1. 依順序推進：S1 → S2 → S3 → S5 → S6 → … → S14。每完成一步就請使用者在實機上執行一次，檢查輸出與數值合理，再做下一步。
2. 實體操作（插線、拍攝、量書的尺寸、量相機間距、在 Pi / 伺服器上執行指令）由使用者完成；Claude 負責寫程式，並**清楚告訴使用者要執行的指令、要拍什麼、拍幾組、要貼回哪些輸出**。
3. 需要使用者提供資訊時直接列出所需的指令與輸出，使用者表示會盡量提供。
4. **破壞性或不可逆的操作**（格式化、刪除資料、覆寫設定、`git push`）先列出目標並取得使用者確認；格式化等指令要先請使用者貼出 `lsblk` 等確認結果。
5. `../1151125_3D稀疏點雲/` 與 `../class_ppt/` 只能讀取、不可修改。
6. 每完成一個步驟，更新 README.md（執行方式、輸出、實際數據）與本檔案第 3 節的「狀態」欄。
7. 不要自行 commit 或 push，除非使用者要求。

---

## 10. 目前進度與待辦（2026-11-28）

| 階段 | 狀態 |
|------|------|
| S1 環境與相機檢查 | **程式完成，等待使用者驗證**（見 `S1/README.md` 的驗證步驟與要貼回的資訊） |
| S2 供電燒機 | 程式已寫（`S2/check_power.py`），S1 通過後驗證 |
| S3 標定 | Pi 拍照用 `S5/capture_rig.py --mode calib`（已寫）；`S3/calib.py` 待實作 |
| S5 拍攝 | 程式已寫（`S5/capture_rig.py`），S1、S2 通過後驗證 |
| S6–S14、run_pipeline、web | 待實作（依序一次一個階段） |

已完成：
- `config/`：pi.yaml、pipeline.yaml、params.yaml、rig.yaml（空）、board.yaml、scenes/book_001.yaml
- `common/pi_common.py`、`common/core/`（io_utils、step、geometry、colmap_txt）
- `S1/`：set_controls.py、check_cameras.py、check_env.py、README.md；`S2/`、`S5/`：程式 + README
- 合成資料測試已通過：同步挑選、JPEG 完整性、throttled / PMIC / v4l2-ctl 輸出解析、四元數往返（`R_to_qvec` 正負號已修正）、
  Umeyama、DLT 三角化、位姿相似轉換、GPU 架構相容判斷；所有腳本 `--help` 可執行；`check_env.py` 在 Windows 上可產出報告。
- 文件：Task.md、CLAUDE.md、PATH.md、README.md、`.gitignore`

伺服器環境安裝進度（PATH.md B 部分，2026-11-28）：
- B0–B5 完成：Miniconda 位於 `/home/dluser/miniconda3`；`gs3d` 環境 Python 3.12.16、conda GCC 13、CUDA Toolkit 12.8（nvcc 實測可編譯 sm_120 並執行）、
  PyTorch 2.11.0+cu128（含 sm_120）、gsplat v1.5.3（commit 937e299，GPU rasterization 測試通過，numpy 被範例套件固定為 1.26.4，不要升級）；
  `colmap` 環境 COLMAP **4.2.1**（conda-forge，with CUDA，CUDA 12.9 執行環境），rig_configurator 可用，GPU SIFT 參數名稱為 `--FeatureExtraction.use_gpu`。
- gsplat simple_trainer 參數為連字號格式（`--data-dir` 等），pipeline.yaml 已改。COLMAP 是 4.x，S8 實作時要以 `colmap <指令> -h` 確認參數名稱（例如 Mapper 的 rig 相關選項）。
- 維護後硬碟擴增，可用約 416 GB。尚未完成：B6（專案套件、移除 opencv-python 改裝 opencv-contrib-python-headless）、B10 / `S1/check_env.py` 驗證。

等待使用者提供（S1 驗證）：
- `set_controls.py --profile object` 的讀回結果；對焦掃描與曝光掃描的結果與選定值（寫回 `config/pi.yaml`）。
- `check_cameras.py --seconds 600` 的摘要（以及選用的 4 台測試）。
- 伺服器 `check_env.py` 的輸出。

之後階段需要的資訊：
- 解析度選擇（2048×1536 vs 1280×720）：P0 兩種各拍一次比較後決定，決定後不再更改。
- 書的實際尺寸（長、寬、厚，mm）→ `config/scenes/book_001.yaml measured.object_dims_mm`。
- 兩台相機鏡頭中心的間距（游標卡尺，mm）→ S3 驗證基線。

待實作（依序，每個階段完成後先請使用者驗證）：
S2 驗證 → S3（`S3/calib.py`、`common/core/charuco.py`）→ S5 驗證 → S6 `ingest.py` → S7 `preprocess.py` → S8 `sfm.py` →
S9 `align.py` → S10 `train.py`（`common/core/gs_ply.py`）→ S12 `cleanup.py` → S11 `evaluate.py` → S14 `export.py` + `web/index.html` → `run_pipeline.py`。
