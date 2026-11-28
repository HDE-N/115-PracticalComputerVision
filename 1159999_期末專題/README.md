# 1159999_期末專題 — 基於多視角影像與 3D 高斯潑濺之快速三維重建框架

## 作業目標
以 Raspberry Pi 5 + 2 台 USB 相機拍攝，在 GPU 伺服器上以 COLMAP + 3D Gaussian Splatting（3DGS）重建，
成果可在瀏覽器中自由觀看。整學期的目標是校園建築 / 教室（見 [Task.md](Task.md)）。

**當前階段（P0）：建模出放置於桌上的一本書，並將此方法彙總成一個框架。**
同一套程式之後只要新增場景設定檔，就能用在教室與建築。

| 文件 | 內容 |
|------|------|
| [Task.md](Task.md) | 整學期計畫：動機、文獻作法、15 個步驟、門檻、時程 |
| [CLAUDE.md](CLAUDE.md) | 給 Claude Code 的規範：當前目標、硬體事實、設計決策、進度與待辦 |
| [PATH.md](PATH.md) | 環境安裝：Raspberry Pi、Ubuntu 26.04 伺服器、Windows |
| README.md（本檔） | 技術與論文、檔案說明、所有程式的執行方法、輸出 |

## 依賴的前期結果
- [../1151125_3D稀疏點雲](../1151125_3D稀疏點雲/)：**沿用標定程式**（`src/calib_intrinsic.py`、`src/calib_extrinsic.py`）與 ChArUco 標定板設定（`config/board.yaml`）。
  不沿用其標定結果（1151125 在 Windows / DSHOW 下拍攝，對焦設定不同），本專題在 Pi 上重新拍標定照片。

---

## 系統架構

```
 ┌──────────── 拍攝端：Raspberry Pi 5（電池供電、手持） ────────────┐
 │ cam0（下）、cam1（上） ──USB──▶ capture_rig.py ──▶ SSD /mnt/ssd     │
 │ 筆電瀏覽器 http://<Pi>:8000：預覽、拍攝、警告                      │
 └────────────────────────────┬────────────────────────────────────┘
                              │ rsync / scp（伺服器沒有 sudo，不能掛載 SSD）
 ┌────────────────────────────▼──── 運算端：Ubuntu + RTX PRO 5000 ───┐
 │ S6 匯入 → S7 前處理 → S8 SfM（COLMAP）→ S9 尺度對齊（ChArUco）     │
 │ → S10 3DGS 訓練（gsplat）→ S12 清理 → S11 評估 → S14 網頁匯出       │
 └────────────────────────────┬────────────────────────────────────┘
                              ▼
                 瀏覽器（web/index.html，GaussianSplats3D）
```

| 步驟 | 內容 | 程式 | 狀態 |
|------|------|------|------|
| S1 | 相機與環境檢查、鎖定相機參數 | `S1/check_cameras.py`、`S1/set_controls.py`、`S1/check_env.py` | **完成，等待實機驗證**（[S1/README.md](S1/README.md)） |
| S2 | 行動供電燒機測試 | `S2/check_power.py` | 已寫，S1 通過後驗證（[S2/README.md](S2/README.md)） |
| S3 | 相機標定（內參 + rig 外參） | `S5/capture_rig.py --mode calib`、`S3/calib.py` | Pi 端已寫；calib.py 待實作 |
| S5 | 同步拍攝 | `S5/capture_rig.py` | 已寫，S1、S2 通過後驗證（[S5/README.md](S5/README.md)） |
| S6 | 資料匯入與完整性檢查 | `S6/ingest.py` | 待實作 |
| S7 | 影像前處理（模糊、重複、曝光） | `S7/preprocess.py` | 待實作 |
| S8 | 位姿估計（SfM） | `S8/sfm.py` | 待實作 |
| S9 | 尺度與座標對齊 | `S9/align.py` | 待實作 |
| S10 | 3DGS 訓練 | `S10/train.py` | 待實作 |
| S12 | 模型清理 | `S12/cleanup.py` | 待實作 |
| S11 | 評估 | `S11/evaluate.py` | 待實作 |
| S14 | 網頁匯出與檢視 | `S14/export.py`、`web/index.html` | 待實作 |
| — | 一鍵執行 S6–S14 | `run_pipeline.py` | 待實作 |

---

## 技術與論文

### 本專題使用的技術
| 技術 | 用在 | 為什麼這樣做 | 來源 / 論文 |
|------|------|-------------|-------------|
| ChArUco 標定、平面標定法 | S3 內參 | 有編碼的標定板，局部入鏡也能辨識角點；沿用 1151125 | Zhang, TPAMI 2000 [11]；Garrido-Jurado et al., Pattern Recognition 2014 [12]；OpenCV `cv2.aruco` |
| solvePnP + 最大生成樹 + Bundle Adjustment | S3 rig 外參 | 求兩台相機的相對位姿並最小化重投影誤差；沿用 1151125 | Triggs et al., BA；Hartley & Zisserman [21] |
| 固定式 camera rig + 軟體同步 | S5 | 一次多拍一個角度；USB 相機無硬體同步，採停拍並挑時間差最小的影格組合 | — |
| 保存 MJPEG 原始資料 | S5 | Pi 不解碼、不重新壓縮，CPU / 記憶體負擔最小、無二次壓縮損失 | V4L2 / OpenCV `CAP_PROP_CONVERT_RGB=0` |
| Laplacian 變異數清晰度 | S7 | 快速剔除動態模糊與失焦影像 | — |
| 增量式 Structure-from-Motion | S8 | 由影像估計相機位姿與稀疏點雲，作為 3DGS 的初始化 | Snavely et al., IJCV 2008 [13]；Schönberger & Frahm, CVPR 2016 [16]（COLMAP） |
| SIFT 特徵 | S8 | COLMAP 預設特徵，穩定、GPU 加速 | Lowe, IJCV 2004 [14] |
| 固定內參 + rig 約束 | S8 | 減少自由度、提升位姿穩定性；同一組影像共用 rig 位姿 | COLMAP rig 支援（3.12+） |
| ChArUco 三角化 + Umeyama 相似轉換 | S9 | 讓模型以公尺為單位、桌面水平（Y 軸朝上），量測與網頁檢視都方便 | Umeyama, TPAMI 1991 |
| 3D Gaussian Splatting | S10 | 數十分鐘訓練出照片級場景、可即時渲染，適合網頁檢視 | Kerbl et al., ACM TOG 2023 [1] |
| gsplat | S10 | 開源 CUDA 函式庫，記憶體約原始實作的 1/4；提供 default / MCMC 策略 | Ye et al., JMLR 2025 [3] |
| 統計式離群點濾除、不透明度 / 尺度過濾 | S12 | 去除漂浮雜點（floaters）；離群點濾除沿用 1151125 | — |
| PSNR / SSIM / LPIPS | S11 | 3DGS 論文的標準畫質指標（每 8 張取 1 張當測試影像） | [1] |
| `.splat` 格式 + GaussianSplats3D | S14 | 每顆高斯 32 bytes，瀏覽器可直接載入 | mkkellogg/GaussianSplats3D |

### 下一階段（教室 / 建築）預計使用
| 技術 | 用途 | 來源 / 論文 |
|------|------|-------------|
| 地面 RANSAC + 曼哈頓對齊 | 教室的尺度與水平 | — |
| sequential 配對 + vocabulary tree 迴路偵測、GLOMAP | 大範圍拍攝的位姿估計 | Pan et al., ECCV 2024 [15] |
| 曝光補償、深度正則化、分塊訓練與 LOD | 大場景畫質與規模 | Kerbl et al., ACM TOG 2024 [2] |
| 2DGS / PGSR 網格提取 | 遊戲碰撞網格 | Huang et al., SIGGRAPH 2024 [18]；Chen et al., IEEE TVCG 2024 [9] |
| 剪枝、量化、壓縮 | 網頁模型 < 100 MB | Chen et al., IEEE TPAMI 2025 [10]（HAC++，比較用） |
| Unity Gaussian Splatting 外掛 | 遊戲引擎展示 | aras-p/UnityGaussianSplatting |

### 外部程式（只用官方 / 原作者來源，版本裝好後填入）
| 程式 | 來源 | 版本 / commit | 授權 | 用途 |
|------|------|--------------|------|------|
| COLMAP | github.com/colmap/colmap（conda-forge 預編譯） | 4.2.1（build `cuda_129gpl_h960d87c_409`，CUDA 12.9 執行環境） | BSD | S8 |
| gsplat | github.com/nerfstudio-project/gsplat | v1.5.3（`937e29912570c372bed6747a5c9bf85fed877bae`） | Apache-2.0 | S10 |
| PyTorch | download.pytorch.org（cu128） | 2.11.0+cu128 | BSD | S10 |
| GaussianSplats3D | github.com/mkkellogg/GaussianSplats3D（CDN） | （待填） | MIT | S14 |
| SuperSplat | superspl.at（PlayCanvas） | 網頁版 | MIT | S12 手動清理 |
| 1151125 標定程式 | 本倉庫 `../1151125_3D稀疏點雲` | — | — | S3 |

### 參考論文
期刊論文（分區以學校 JCR 為準）：
1. B. Kerbl, G. Kopanas, T. Leimkühler, G. Drettakis, "3D Gaussian Splatting for Real-Time Radiance Field Rendering," *ACM TOG*, 42(4), 2023.
2. B. Kerbl et al., "A Hierarchical 3D Gaussian Representation for Real-Time Rendering of Very Large Datasets," *ACM TOG*, 43(4), 2024.
3. V. Ye et al., "gsplat: An Open-Source Library for Gaussian Splatting," *JMLR*, 26(34), 2025.
4. T. Wu et al., "Recent Advances in 3D Gaussian Splatting," *Computational Visual Media*, 10, 2024.
5. B. Fei et al., "3D Gaussian Splatting as a New Era: A Survey," *IEEE TVCG*, 2024.
6. Fang et al., "Performance Evaluation and Optimization of 3D Gaussian Splatting in Indoor Scene Generation and Rendering," *ISPRS IJGI*, 2025.
7. Yu et al., "From comparison to integration: A workflow evaluation of 3D Gaussian splatting and LiDAR point cloud for modern architectural heritage," *Automation in Construction*, 180, 106509, 2025.
8. F. Remondino et al., "A Critical Analysis of NeRF-Based 3D Reconstruction," *Remote Sensing*, 15(14), 3585, 2023.
9. D. Chen et al., "PGSR: Planar-based Gaussian Splatting for Efficient and High-Fidelity Surface Reconstruction," *IEEE TVCG*, 2024.
10. Y. Chen et al., "HAC++: Towards 100X Compression of 3D Gaussian Splatting," *IEEE TPAMI*, 47(11), 2025.
11. Z. Zhang, "A Flexible New Technique for Camera Calibration," *IEEE TPAMI*, 22(11), 2000.
12. S. Garrido-Jurado et al., "Automatic generation and detection of highly reliable fiducial markers under occlusion," *Pattern Recognition*, 47(6), 2014.
13. N. Snavely, S. M. Seitz, R. Szeliski, "Modeling the World from Internet Photo Collections," *IJCV*, 80(2), 2008.
14. D. G. Lowe, "Distinctive Image Features from Scale-Invariant Keypoints," *IJCV*, 60(2), 2004.

會議論文與其他：

15. L. Pan et al., "Global Structure-from-Motion Revisited," ECCV 2024.（GLOMAP）
16. J. L. Schönberger, J.-M. Frahm, "Structure-from-Motion Revisited," CVPR 2016.（COLMAP）
17. Clini et al., "3D representation of Architectural Heritage: a comparative analysis of NeRF, Gaussian Splatting, and SfM-MVS reconstructions using low-cost sensors," ISPRS Archives, 2024.
18. B. Huang et al., "2D Gaussian Splatting for Geometrically Accurate Radiance Fields," SIGGRAPH 2024.
19. S. Umeyama, "Least-squares estimation of transformation parameters between two point patterns," *IEEE TPAMI*, 13(4), 1991.
20. B. Triggs et al., "Bundle Adjustment — A Modern Synthesis," Vision Algorithms: Theory and Practice, 2000.
21. R. Hartley, A. Zisserman, *Multiple View Geometry in Computer Vision*, Cambridge University Press.

---

## 檔案說明

程式**依階段分資料夾**（`S1/`、`S2/`、`S3/`、`S5/`、`S6/` …），每個資料夾都有 `README.md`，寫明該階段的目標、執行方式、通過條件與要回報的資訊。
一次完成並驗證一個階段，再進行下一個。共用程式放在 `common/`。


| 檔案 | 用途 |
|------|------|
| `config/pi.yaml` | 拍攝端：相機實體路徑、解析度、V4L2 控制項、對焦設定檔、同步 / 供電檢查、網頁埠 |
| `config/pipeline.yaml` | 運算端：COLMAP / gsplat 的路徑與指令樣板（換工具只改這裡） |
| `config/params.yaml` | 各步驟參數與品質門檻 |
| `config/rig.yaml` | 相機內參與 rig 外參（S3 自動寫入，依對焦設定檔分組） |
| `config/board.yaml` | ChArUco 標定板（自 1151125 複製） |
| `config/scenes/book_001.yaml` | 場景設定：類型、對焦設定檔、對齊方式、裁切範圍、實測尺寸、門檻覆寫、網頁初始視角、拍攝計畫 |
| `common/pi_common.py` | Pi 共用：V4L2 控制、相機讀取執行緒、同步挑選、供電 / 溫度 / SSD 檢查、fsync 寫檔 |
| `S1/set_controls.py` | 套用並讀回相機控制項；掃描對焦 / 曝光值 |
| `S1/check_cameras.py` | 相機格式、同時串流 fps、掉幀、CPU、電壓（可測 4 台） |
| `S1/check_env.py` | 伺服器環境檢查：CUDA、PyTorch、gsplat（GPU 實測）、COLMAP、OpenCV、1151125 程式、硬碟空間 |
| `S2/check_power.py` | 電池供電燒機測試 |
| `S5/capture_rig.py` | 同步拍攝（場景 / 標定模式），網頁預覽與操作 |
| `common/core/io_utils.py` | 設定讀取與覆寫、run 資料夾、latest.txt、log、檔案讀寫 |
| `common/core/step.py` | 步驟框架：共用參數、計時、外部指令、門檻、結束碼 |
| `common/core/geometry.py` | 四元數、Umeyama、多視角三角化、相似轉換套用到位姿 |
| `common/core/colmap_txt.py` | COLMAP 文字模型讀寫與統計 |
| `requirements.txt` / `requirements_pi.txt` | 運算端 / 拍攝端 Python 套件 |

---

## 執行方法

環境安裝見 [PATH.md](PATH.md)（伺服器沒有 sudo，全部裝在使用者空間）。以下指令都在 `1159999_期末專題/` 下執行。
伺服器上的拍攝資料以 rsync 從 Pi 傳到 `~/capture_in/<scene>/`（PATH.md B7）。
Pi 指令在 `conda activate rig` 後、伺服器指令在 `conda activate gs3d` 後執行。標示「待實作」的指令是框架預定的介面。

### 1. 拍攝端（Raspberry Pi）

```bash
# S1：相機檢查與參數鎖定
python S1/set_controls.py --list                                   # 目前的控制項
python S1/set_controls.py --profile object                         # 套用 controls + object 對焦，讀回比對
python S1/set_controls.py --profile object --sweep focus_absolute --values 0:1023:100      # 找最清晰的對焦值
python S1/set_controls.py --profile object --sweep exposure_time_absolute --values 65524,65526,65528,65530,65532
python S1/check_cameras.py --seconds 600                           # 2 台同時串流 10 分鐘
python S1/check_cameras.py --all-usb --seconds 600                 # 測試 4 台

# S2：燒機測試（以 PB-Y37 供電）
python S2/check_power.py --minutes 30

# S3：拍標定照片（ChArUco 板入鏡，畫面顯示每台的角點數）
python S5/capture_rig.py --mode calib --profile object

# S5：拍攝場景（在 tmux 中執行）
python S5/capture_rig.py --scene book_001 --profile object
python S5/capture_rig.py --scene book_001 --profile object --session <既有 session>   # 接續拍攝
```
拍攝操作：筆電瀏覽器開 `http://<Pi 的 IP>:8000`，按「拍攝」或空白鍵；終端機 Enter 也可拍攝，`q` 結束。
有警告（時間差過大、電壓不足、對焦被重設、角點不足）時會發出提示音並標記「待重拍」。結束後 `sudo shutdown -h now` 再拔電。

**書的拍攝方式**（`config/scenes/book_001.yaml` 的 `capture_plan`）：
書平放在桌上，ChArUco 板平放在書旁（不要被蓋住），兩者都不能移動；繞書 3 圈（rig 下俯約 0°、30°、60°，距離 0.5–0.8 m），每 10–15° 停下拍一組，共約 80–120 組。

### 2. 運算端（Linux 伺服器）

```bash
# S1：環境檢查
python S1/check_env.py

# S3：標定計算（待實作）→ 寫入 config/rig.yaml 的 profiles.object
python S3/calib.py --profile object --session <SSD 上的 calib_object/<session> 路徑>

# 一鍵執行 S6–S14（待實作）
python run_pipeline.py --scene book_001 --source ~/capture_in/book_001/<session>
python run_pipeline.py --scene book_001 --from sfm --to export        # 只重跑部分步驟

# 逐步執行（待實作；每一步預設讀上一步的 latest.txt）
python S6/ingest.py     --scene book_001 --source ~/capture_in/book_001/<session>             # S6
python S7/preprocess.py --scene book_001                                                      # S7
python S8/sfm.py        --scene book_001                                                      # S8
python S9/align.py      --scene book_001                                                      # S9
python S10/train.py      --scene book_001                                                      # S10
python S12/cleanup.py    --scene book_001                                                      # S12
python S11/evaluate.py   --scene book_001                                                      # S11
python S14/export.py     --scene book_001                                                      # S14
```
所有伺服器步驟共用的參數：

| 參數 | 用途 |
|------|------|
| `--scene` | 場景名稱（`config/scenes/<scene>.yaml`） |
| `--input-run run_xxx` | 指定上一步驟的 run（預設讀 latest.txt） |
| `--force-latest` | 未通過門檻也更新 latest.txt |
| `--set KEY=VALUE` | 只在本次覆寫設定，例：`--set params.sfm.min_registered_ratio=0.9`、`--set pipeline.trainer.strategy=mcmc` |

結束碼：0 = 通過門檻；2 = 未通過門檻（latest.txt 不更新，run_pipeline 停止）；1 = 執行錯誤。

### 3. 檢視成果
```bash
cd web && python -m http.server 8080
# 瀏覽器開 http://<主機>:8080/?scene=book_001
```

### 4. 新增場景
複製 `config/scenes/book_001.yaml` 為 `config/scenes/<新名稱>.yaml`，修改類型、對焦設定檔、裁切範圍、實測尺寸；
Pi 以 `--scene <新名稱>` 拍攝，伺服器以 `--scene <新名稱>` 執行，程式不需修改。

---

## 輸出結果（供後續調用）

| 路徑 | 格式 | 內容 |
|------|------|------|
| `/mnt/ssd/3dgs_capture/<scene>/<session>/group_xxxxx/cam{0,1}.jpg` | MJPEG | 原始影像（相機輸出原樣保存） |
| `.../group_xxxxx/group.json` | json | 時間戳、時間差、電壓旗標、溫度、對焦讀回、是否待重拍 |
| `.../session.json`、`session_summary.json` | json | 相機路徑、控制項設定與讀回、統計 |
| `/mnt/ssd/3dgs_capture/_tests/` | csv / json / jpg | check_cameras、check_power、set_controls 掃描結果 |
| `config/rig.yaml` | yaml | 各對焦設定檔的內參（COLMAP FULL_OPENCV）與 rig 外參（公尺） |
| `output/03_calib/<profile>/run_*/` | — | 標定工作區與 1151125 程式的輸出 |
| `output/06_ingest/<scene>/run_*/manifest.csv` | csv | 每張影像的 SHA-256、大小、狀態 |
| `output/07_preprocess/<scene>/run_*/frames.csv` | csv | 每張影像的清晰度、亮度、保留與否、原因 |
| `output/08_sfm/<scene>/run_*/undistorted/`、`sparse_txt/` | COLMAP | 去畸變影像與模型 |
| `output/09_align/<scene>/run_*/alignment.json`、`dataset/` | json / COLMAP | 相似轉換（s, R, t）、尺度驗證；gsplat 訓練資料 |
| `output/10_train/<scene>/run_*/point_cloud.ply` | 3DGS PLY | 訓練結果與 val 指標 |
| `output/12_cleanup/<scene>/run_*/cleaned.ply` | 3DGS PLY | 清理後模型（公尺、Y 朝上） |
| `output/11_eval/<scene>/run_*/metrics.json`、`summary.md` | json / md | 所有步驟的指標、尺寸誤差、耗時 |
| `output/14_export/<scene>/run_*/<scene>.splat`、`web/assets/` | splat | 網頁模型 |

每個 run 資料夾都有 `config_used.yaml`、`run.log`、`stats.json`、`checks.json`、`timing.json`；各步驟資料夾的 `latest.txt` 記錄目前採用的 run。

- 座標（S9 之後）：公尺、Y 軸朝上（垂直桌面）、原點在 ChArUco 板中心。
- 原始影像、`.ply`、`.splat`、COLMAP 資料庫只存在本機 / SSD，不上傳 GitHub。

---

## 實際數據（待補）
- 相機設定：解析度、對焦值、曝光值（實測後填入）
- 供電：整機功耗（USB 電流表）、燒機測試結果
- 標定：內參 RMS、外參誤差、基線計算值 vs 實測值
- 書：拍攝組數與時間、註冊率、尺度誤差、PSNR / SSIM / LPIPS、尺寸誤差、各步驟耗時、模型大小

---

## 附錄：專題投票案（原 README 內容）

### 1. 建模學校
- 主旨：透過多相機與相關技術（如：3DGS）建模出學校或教室
- 預期成果：建立一個建模框架，輸入多相機影像，計算並建模，模型可用於網頁檢視或遊戲

### 2. 低解析度圖片還原
- 主旨：透過 diffusion 或影像生成技術，將低解析度之圖片重建成高解析度
- 預期成果：輸入低解析影像，輸出富含細節之高解析度圖片

### 3. 遊戲鎖頭外掛
- 主旨：透過 OBS 串流螢幕畫面並透過 YOLO 來輔助玩家
- 預期成果：將螢幕畫面即時輸入模型判定敵人位置，並自動移動滑鼠

### 4. 家具擺放模擬
- 主旨：XXX
- 預期成果：透過攝影機掃描房間，判斷可用空間，並將虛擬家具放入真實場景，提供遮擋及家具尺寸建議
