# 115-基於多視角影像與 3D 高斯潑濺技術之建築物快速三維重建與視覺化研究

## 需求

將學校的建築建模（一棟即可，或者是一間教室），讓學生可使用電腦檢視，並將方法統整成一個框架。
模型可用於網頁校園導覽或遊戲設計。

## 可用設備

| 設備 | 規格 | 角色 |
|------|------|------|
| 相機 | 與 1151125 同一批的通用 USB 相機（USB ID `5843:e603`，名稱「WebCamera」，內建麥克風）；MJPG 最高 **2048×1536 @ 30 fps**（另有 1920×1080、1280×720）；僅能軟體對焦（`focus_absolute` 0–1023）；共 4 台，**預設使用 2 台**（S1 測試 Pi 能否同時跑 4 台） | 拍攝 |
| 樹莓派 | Raspberry Pi 5 Model B Rev 1.1（2 GB），原廠散熱器 + 風扇，Linux | 隨相機移動，同步擷取、存檔 |
| 外接 SSD | SanDisk Extreme Portable SSD 2 TB（USB 3.2 Gen 2） | 拍攝存檔、原始資料備份 |
| 行動電源 | Aukey PB-Y37（20,000 mAh / 74 Wh，USB-C PD 65W，5V 最高 3A）；不加購 UPS HAT | Pi、相機、SSD 供電 |
| 筆電 | 遠端連線（SSH / 網頁）到 Pi，拍攝時在旁邊 | 預覽、操作、現場覆蓋檢查 |
| Linux 伺服器（主運算） | AMD Ryzen 9 9950X、RAM 64 GB、**NVIDIA RTX PRO 5000 Blackwell 48 GB**、驅動 595.99（CUDA 13.2）、可用硬碟約 64 GB（可擴增） | S7–S14：SfM、3DGS 訓練、評估、匯出 |
| Windows 桌機（輔助） | Intel i7-13700、RAM 64 GB、**RTX 4060 Ti 16 GB**、驅動 591.86（CUDA 13.1）、硬碟約 1 TB | 網頁 / Unity 開發、SuperSplat 清理、物品測試的備援訓練、資料備份 |

---

## 摘要

本專題建立一套**校園建築三維重建框架**，把流程拆成 **4 個階段、15 個步驟**：
「準備（環境、拍攝設備、標定）→ 拍攝（規劃、Pi 同步擷取、資料匯入）→ 重建（前處理、SfM、尺度對齊、3DGS 訓練、評估）→ 輸出（清理、碰撞網格、壓縮、網頁導覽、Unity）」。

- 拍攝端：2 台相機上下排列固定在同一支架上（camera rig），由 Raspberry Pi 5 同步擷取，影像存在外接 SSD；整組設備以電池供電，可手持移動。
- 運算端：Linux 伺服器（RTX PRO 5000 Blackwell 48 GB）執行 COLMAP（SfM）與 3D Gaussian Splatting（3DGS）訓練；Windows 桌機（RTX 4060 Ti）負責網頁、Unity 開發與備援。
- 先以**物品小規模試行**跑通整個流程，再於假日拍攝教室。
- 與課程的銜接：相機內參與 rig 內的相對外參沿用 [1151125_3D稀疏點雲](../1151125_3D稀疏點雲/) 的標定流程，作為 SfM 的先驗，讓位姿估計更穩定、模型帶有真實尺度（公尺）。

每一步都有固定的輸入、輸出、品質門檻與獨立腳本，可以單獨重跑或替換工具。本期先完成一間教室，再視時程擴充到一棟建築外觀。

**關鍵字**：3D Gaussian Splatting、Structure-from-Motion、COLMAP、多相機標定、Camera Rig、Raspberry Pi、網頁 3D 視覺化、數位孿生

---

## 動機

1. **校園導覽需求**：新生、訪客、招生活動需要「不到現場也能看」的校園介紹；360° 環景照片只能在固定點觀看，無法自由移動。
2. **傳統建模成本高**：人工建模（SketchUp / Blender）耗時，材質光影難以逼真；雷射掃描儀昂貴。
3. **3DGS 成熟**：3DGS 能從一般照片在數十分鐘內訓練出照片級的場景，並可即時渲染 [1]，適合導覽與遊戲。
4. **延續課程成果**：3DGS 以 SfM 稀疏點雲初始化 [1]，正好接續 1151125 的標定、特徵配對與三角測量。

---

## 文獻作法整理（別人怎麼做、本專題借用什麼）

| 文獻 | 他們的做法 | 本專題借用 | 對應步驟 |
|------|-----------|-----------|---------|
| Kerbl et al. 2023 [1]（3DGS 原始論文） | COLMAP 稀疏點初始化；30k 次迭代；損失 = (1−λ)·L1 + λ·D-SSIM，λ = 0.2；第 500–15,000 次迭代間每 100 次做 densification（梯度門檻 0.0002）；每 3,000 次重設不透明度；球諧階數每 1,000 次升一階到 3 階；每 8 張取 1 張當測試影像 | 作為**基準訓練設定**與評估切分方式 | S10、S11 |
| Kerbl et al. 2024 [2]（Hierarchical 3DGS） | 大場景切成多個區塊（chunk）分別訓練再合併；建立層級結構做 LOD；加入單目深度正則化與逐張曝光補償 | 曝光補償、深度正則化列為可選項；整棟建築時採分塊訓練 | S10、延伸 |
| Ye et al. 2025 [3]（gsplat） | 開源 CUDA 光柵化函式庫；記憶體約為原始實作的 1/4；提供預設與 MCMC 兩種 densification 策略、抗鋸齒、深度渲染 | **主要訓練工具**；以 MCMC 策略的高斯數量上限控制檔案大小；用渲染深度產生網格 | S10、S13 |
| Wu et al. 2024 [4]、Fei et al. 2024 [5]（綜述） | 整理 3DGS 在重建、壓縮、表面提取等方向的方法 | 撰寫文獻回顧、挑選替代方法 | 報告 |
| Fang et al. 2025 [6]（室內 3DGS 評估） | 自行拍攝室內資料集，比較 COLMAP、ContextCapture、NeRF、3DGS，再針對室內場景調整 3DGS 參數 | **比較實驗設計**：3DGS vs. 傳統攝影測量；室內參數調整 | S11 |
| Yu et al. 2025 [7]（建築 3DGS vs. LiDAR） | 以一棟現代建築為例，從資料取得、視覺化、語意分割、成果傳播等面向比較 3DGS 與 LiDAR，主張兩者整合 | 評估面向不只看畫質，也看**取得成本、展示與傳播** | S11、報告 |
| Remondino et al. 2023 [8]（NeRF vs. 攝影測量） | 以參考資料比較雜訊程度、幾何精度、所需影像數量 | 幾何評估指標：平面雜訊、距離誤差、影像數量的影響 | S11 |
| Clini et al. 2024 [17]（建築遺產 NeRF / GS / SfM-MVS，研討會論文） | 外觀用無人機、室內用 360° 相機；以 TLS 為基準；結論：GS 適合視覺化，SfM-MVS 幾何最準 | 以 **COLMAP MVS 稠密點雲作為幾何參考**；導覽用 3DGS、碰撞用網格的分工 | S11、S13 |
| Chen et al. 2024 [9]（PGSR） | 把高斯壓平成平面，渲染深度與法向量，加入多視角幾何一致性約束，再以 TSDF 融合成網格 | 建築以平面為主，作為**高品質碰撞網格**的進階選項 | S13 |
| Chen et al. 2025 [10]（HAC++） | 以錨點 + 雜湊格網做熵編碼，平均壓縮約 100 倍 | 作為壓縮率的比較上限；網頁實作採剪枝 + 量化 + 壓縮格式 | S14 |
| Zhang 2000 [11]、Garrido-Jurado 2014 [12] | 平面標定法、ArUco 標記 | 沿用 1151125 的內參與 rig 外參標定 | S3 |
| Snavely 2008 [13]、Schönberger 2016 [16] | 增量式 SfM：特徵配對 → 初始像對 → 逐張加入 → BA | COLMAP 位姿估計，加上固定內參與 rig 約束 | S8 |

> 編號對應文末「參考資料」。

---

## 前提條件

| 項目 | 條件 | 對設計的影響 |
|------|------|-------------|
| 重建對象 | 先拍**物品**試行（P0）→ 一間教室（MVP）→ 一棟建築外觀（加分） | 物品可在平日、室內反覆測試，用來除錯整個流程；教室只能**假日**拍攝，每次拍攝都要有把握 |
| 製作時間 | 5–6 週 | 全部使用開源工具，重點放在流程整合、框架化與比較實驗 |
| 相機 | 2 台同型號 USB 相機，無硬體同步 | 3DGS / SfM 本來就適用單台相機移動拍攝 [1]；2 台的作用是每個定點多拍一個角度。代價是拍攝時間約為 4 台的 1.5–2 倍。框架以設定檔指定相機數量，若 S1 測試 Pi 能穩定跑 4 台，可直接改用 4 台。採「停下 → 觸發 → 再走」的拍攝方式，以軟體同步並記錄時間差 |
| 拍攝端 | Pi 5（2 GB） | 只擷取、存檔、預覽；不解碼、不重新編碼、不做重建 |
| 供電 | 拍攝現場無法插電，Pi 隨相機移動；只有 PB-Y37（5V 3A），不加購 UPS | 解除 Pi 5 的 USB 限流；監控電壓旗標；一顆電池約 4–5 小時，足夠一次教室拍攝；換電池前先安全關機（見 S2-2） |
| 運算端 | Linux 伺服器 RTX PRO 5000 Blackwell（48 GB VRAM）、RAM 64 GB | VRAM 充足，單間教室不需分塊；Blackwell 架構需以 CUDA 12.8 以上編譯（PyTorch 選 cu128 或更新版本），驅動 595 已支援 |
| 儲存 | 伺服器可用硬碟約 64 GB | 空間吃緊：原始資料存 SSD 與 Windows 桌機，伺服器只放進行中的場景；中間 checkpoint 只留最後一個（見「儲存空間規劃」） |
| 相機對焦 | 僅能軟體對焦 | 每種對焦設定（物品 / 教室）各標定一次內參；每組影像記錄讀回的對焦值，與標定時不同就警告 |
| 使用者 | 學生以電腦瀏覽器檢視 | 網頁模型 < 100 MB，一般筆電 ≥ 30 FPS |

---

## 預期目標與驗收標準

學生打開網址，即可在瀏覽器中以 WASD + 滑鼠在重建出的教室中自由移動，畫面接近實景照片；同一份模型也能匯入 Unity，作為可行走的遊戲場景。

| 項目 | 標準 | 驗證步驟 |
|------|------|---------|
| 端到端 | 依 README，從 Pi 拍攝到網頁可瀏覽，`run_pipeline.py` 可完整重現 | S15 |
| 位姿估計 | ≥ 95% 影像註冊成功；平均重投影誤差 < 1 px | S8 |
| 尺度 | 3 段以上實測距離（黑板寬、門高、教室長），誤差 < 3% | S9、S11 |
| 畫質 | 測試視角 PSNR ≥ 25 dB、SSIM ≥ 0.85、LPIPS ≤ 0.25（初值，第一次拍攝後再調整） | S11 |
| 訓練時間 | 單間教室 30k 次迭代 ≤ 30 分鐘 | S10 |
| 網頁 | 模型檔 < 100 MB；一般筆電 Chrome ≥ 30 FPS | S14、S15 |
| 遊戲 | Unity 中以第一人稱角色行走，不會穿牆、穿地板 | S13、S15 |
| 可擴充 | 換場地只需新增 `config/scenes/<name>.yaml`；換工具只改 `config/pipeline.yaml` | S15 |

---

## 系統架構總覽

```
 階段 A：準備            階段 B：拍攝                 階段 C：重建（Linux 伺服器）                       階段 D：輸出
┌──────────────┐   ┌──────────────────┐   ┌──────────────────────────────────────┐   ┌───────────────────┐
│S1 環境建置     │   │S4 拍攝規劃         │   │S7  影像前處理（模糊/重複/曝光/遮罩）     │   │S12 模型清理         │
│S2 Rig 與供電   │──▶│S5 Pi 同步擷取      │──▶│S8  SfM 位姿估計（固定內參 + rig）      │──▶│S13 碰撞網格         │
│S3 標定         │   │S6 資料匯入與檢查    │   │S9  尺度與座標對齊（公尺、Y 朝上）       │   │S14 壓縮匯出         │
└──────────────┘   └──────────────────┘   │S10 3DGS 訓練                          │   │S15 網頁導覽 / Unity  │
                                           │S11 評估（畫質、幾何、效率）             │   └───────────────────┘
                                           └──────────────────────────────────────┘
                     不合格（覆蓋不足、模糊過多）→ 回到 S4 補拍
```

### 步驟間資料格式（模組只透過這些檔案溝通）

| 步驟 | 主要輸入 | 主要輸出 |
|------|---------|---------|
| S3 標定 | ChArUco 照片 | `config/rig.yaml`（2 台 K、dist、rig 內 R/t，mm） |
| S5 擷取 | 相機 | `data/raw/<scene>/<session>/group_xxxxx/cam{0,1}.jpg` + `group.json` |
| S6 匯入 | 原始資料夾 | `manifest.csv`（每張影像的雜湊、時間差、狀態） |
| S7 前處理 | 原始影像 | `images/`（連結或複製）、`masks/`、`frames.csv`（清晰度、保留與否、原因） |
| S8 SfM | 影像 + rig 設定 | COLMAP `sparse/0/`、去畸變影像 `undistorted/` |
| S9 對齊 | COLMAP 模型 | 對齊後的 `sparse_aligned/`（公尺、Y 朝上）、`alignment.json` |
| S10 訓練 | 對齊後模型 + 去畸變影像 | `point_cloud.ply`、`ckpt/`、訓練曲線 |
| S11 評估 | 模型 + 測試影像 + 實測距離 | `metrics.json`、`distances.csv`、測試視角渲染圖 |
| S12 清理 | `point_cloud.ply` | `cleaned.ply`、`cleanup_stats.json` |
| S13 網格 | 清理後模型 | `collision.obj`（簡化網格）、`mesh_stats.json` |
| S14 壓縮 | `cleaned.ply` | `scene.spz` / `scene.sog`、`compress_stats.json` |
| S15 展示 | 壓縮模型 + 網格 | `web/`（靜態網頁）、`unity/`（資產與腳本） |

---

## 框架共同規則（沿用 1151125）

1. **原始資料只增不刪**：拍過的每張影像都保存，剔除只記錄在清單中，不刪檔案。`data/` 不上傳 GitHub。
2. **每次執行一個新資料夾**：`output/<步驟>/<scene>/run_YYYYMMDD_HHMMSS/`，內含 `config_used.yaml` 與 `run.log`，不覆寫舊結果。
3. **`latest.txt` + 品質門檻**：結果通過 `config/params.yaml` 的門檻才更新 `latest.txt`，下一步預設讀取 latest；未通過時列出原因，可用 `--force-latest` 強制採用。
4. **設定檔驅動**：門檻、路徑、工具選擇都寫在 `config/`，程式不寫死數值。
   - `config/rig.yaml`：相機內參與 rig 外參
   - `config/pipeline.yaml`：每一步使用的工具（例：`sfm: colmap | glomap`、`trainer: gsplat | splatfacto`）
   - `config/params.yaml`：各步驟參數與品質門檻
   - `config/scenes/<scene>.yaml`：場景名稱、session、裁切範圍、實測距離、導覽點
5. **單位與座標**：標定沿用 mm；S9 之後一律使用**公尺**、**Y 軸朝上**、地面 y = 0（與 Unity / three.js 一致）。
6. **每一步都記錄耗時**（`timing.json`），作為「快速重建」的量化依據。
7. **例外：checkpoint 可以刪除**。伺服器空間有限，訓練的中間 checkpoint 只保留最後一個；`config_used.yaml`、log、最終 `.ply`、評估結果一律保留。

### 儲存空間規劃

| 資料 | 單一教室估計 | 存放位置 |
|------|------------|---------|
| 原始影像（1280×720 MJPEG，約 150–300 KB/張 × 2,400 張） | 約 0.5–0.7 GB | **SSD（主）**、Windows 桌機（備份）、伺服器（進行中才放） |
| COLMAP 資料庫 + 稀疏模型 + 去畸變影像 | 約 2–3 GB | 伺服器 |
| 一次 3DGS 訓練（1.5–3 M 顆高斯：`.ply` 約 0.4–0.8 GB，最後 checkpoint 約 2–3 GB） | 約 3–4 GB / run | 伺服器；完成後最終 `.ply` 備份到 Windows 桌機 |
| 比較實驗（約 10–15 次訓練） | 約 30–50 GB | 伺服器 |

> 64 GB 勉強夠做完一間教室與比較實驗；建議擴增到 **≥ 200 GB**，否則每做完一組實驗就要把結果搬到 Windows 桌機。

### 物品小規模試行（P0）

教室只能在假日拍，每次拍攝都要有把握，因此先用**物品**把 S1–S14 整條流程跑通（只換場景設定檔，程式完全相同）：

| 項目 | 作法 |
|------|------|
| 物品 | 20–50 cm、紋理豐富、不反光（例：書堆、盆栽、模型）；可與 1151125 拍過的物品相同，方便比較稀疏點雲 |
| 擺放 | 物品放在桌上，下方墊 ChArUco 板（提供尺度、幫助 SfM）；**物品不動、人繞著走**（不要用轉盤：背景與物品相對移動會破壞 SfM 的靜態場景假設） |
| 對焦 | 使用「物品」對焦設定（約 0.5–1 m），並用此設定做一次 S3 標定 |
| 拍攝 | 繞物品 3 圈：rig 下俯約 0°、30°、60°，每 10–15° 停拍一組，約 80–120 組（160–240 張），10–15 分鐘 |
| 處理 | `config/scenes/object_xxx.yaml` → `run_pipeline.py` 跑 S6–S14 |
| 門檻 | 註冊率 ≥ 95%；ChArUco 尺度誤差 < 1%；測試 PSNR ≥ 28 dB（物品場景較容易，門檻比教室高） |
| 檢討 | 拍攝時間、電量消耗、模糊比例、時間差、哪些角度漏拍 → 修正拍攝 SOP 後再拍教室 |

---

## 實作步驟

### 階段 A：準備

#### S1 環境建置

**目標**：拍攝端與運算端的軟體都能正常執行，並先用公開資料集跑通 3DGS。

1. **運算端：Linux 伺服器（RTX PRO 5000 Blackwell 48 GB）**
   - 驅動 595.99 已可用；安裝 CUDA Toolkit 12.8 以上（gsplat 需要在本機編譯 CUDA 核心，Blackwell 為 `sm_120`），以及 cu128 或更新版本的 PyTorch。
   - 建議用 conda / venv 建立獨立環境，避免影響伺服器上的其他使用者。
   - 安裝 COLMAP（含 CUDA，需要支援 rig 的版本）、`pycolmap`、gsplat [3]、Nerfstudio（可選）、`opencv-contrib-python`、`open3d`、`numpy`、`scipy`、`pyyaml`、`lpips`。
   - 執行 `src/tools/check_env.py`：檢查 GPU、CUDA、COLMAP 版本，以及 gsplat CUDA 核心能否編譯，結果存成 `env_report.json`。
   - **跑通範例**：用 Mip-NeRF 360 的一個室內場景（如 `room`）訓練 30k 次迭代，記錄 PSNR 與耗時，作為環境正確的基準。
   - 資料集與輸出放在可用空間最大的分割區，執行前先用 `df -h` 確認空間。
2. **輔助：Windows 桌機（RTX 4060 Ti 16 GB）**
   - 安裝 COLMAP（Windows 版）、Python 環境、SuperSplat（網頁版即可）、Unity。
   - 16 GB VRAM 足以訓練物品場景，作為伺服器忙碌時的備援；教室場景以高斯數量上限（例：1.5 M）也能訓練，但較慢。
3. **拍攝端（Pi 5）**
   - Raspberry Pi OS 64-bit、`opencv-python`（V4L2）、`v4l-utils`、`gpiozero`。
   - 拍攝時停用 Pi 上其他常駐服務（例：Docker 容器），把 2 GB RAM、CPU 與電力留給擷取程式。
   - 執行 `pi/check_cameras.py`：列出相機的 `/dev/video*`、支援的格式（確認有 MJPG 1280×720）與 fps；以 `lsusb -t` 確認相機分散在不同的 USB 控制器上。
   - **2 台 vs. 4 台測試**：分別同時開 2 台、4 台，以 1–2 組 / 秒擷取 10 分鐘，記錄實際 fps、掉幀數、CPU / RAM 使用率、`get_throttled`、功耗。4 台全部通過 → 可改用 4 台；4 台出現電壓不足 → 需加外接供電 USB Hub（S2-2 方案 C）才能使用。
   - 執行 `pi/check_power.py`：讀取 `vcgencmd get_throttled`、`vcgencmd pmic_read_adc`（電壓、電流）、溫度，作為 S2-2 燒機測試的工具。
   - **相機參數改在 Pi 上設定**：1151125 在 Windows 使用 DSHOW，Linux 的 V4L2 控制項名稱與數值範圍不同（例：曝光改用 `exposure_time_absolute`，單位 100 µs），Windows 上鎖定的數值不能直接沿用，要在 Pi 上重新鎖定一次。

**輸出**：`output/00_env/run_*/env_report.json`、範例場景的訓練結果。
**門檻**：範例場景 PSNR 與論文數值相差 ≤ 1 dB [1][3]；Pi 同時開 2 台時 10 分鐘內掉幀 = 0、電壓不足 = 0。

---

#### S2 拍攝設備（camera rig）設計與組裝

**目標**：2 台相機與 Pi、SSD、電池組成一套可手持移動的拍攝設備；兩台相機視野重疊 ≥ 30%，組裝後相對位置不再改變。

##### S2-1 相機配置：上下排列

水平方向可以靠「走動 + 轉向」補足，天花板和上半牆卻需要額外繞圈仰拍。因此 2 台相機採**上下排列、朝同一方向、俯仰角不同**，讓上面那台負責高處，省下專門仰拍的一圈。

| 場景 | 建議配置 | 理由 |
|------|---------|------|
| 教室內部 | cam0 平視 0°、cam1 仰角約 +30°；上下間距 10–15 cm；手持高度約 1.5 m | 一般 720p 網路攝影機的垂直視角約 40°–45°，夾角 30° 時兩台約有 30% 重疊，可做外參標定與特徵配對 |
| 建築外觀 | cam0 約 −5°、cam1 約 +25° | cam0 涵蓋牆腳到 2 樓，cam1 涵蓋上方樓層；高樓層需再繞一圈並把整支 rig 仰起 |

- 材料：鋁擠型或相機橫桿 + 1/4" 雲台座，螺絲加螺絲膠防鬆；Pi、SSD 固定在握把下方，PB-Y37 放背包或腰包。
- 物品試行（P0）：物品距離近，兩台的夾角改為約 15°–20°，讓兩台都看得到物品；改了夾角就要重新做 S3 的外參標定。
- 線材固定：移動中 USB 接頭鬆脫是相機斷線的常見原因。使用短的 L 型 USB 線，並以束帶把線固定在支架上（應力釋放）。
- 相機 3A：在 **Pi 上**以 1151125 `camera_setup.py`（`cameras.yaml` 的 `backend` 改為 `V4L2`）或 `v4l2-ctl` 鎖定對焦、曝光、白平衡，**2 台相機設定相同**，避免高斯模型在不同視角間出現色差。
- 本相機在 Linux（V4L2）下的控制項（`v4l2-ctl --list-ctrls` 實測）：

  | 控制項 | 範圍 | 鎖定方式與注意事項 |
  |--------|------|------------------|
  | `focus_automatic_continuous` / `focus_absolute` | 0/1；0–1023 | 先設 `focus_automatic_continuous=0`，`focus_absolute` 才會生效 |
  | `auto_exposure` / `exposure_time_absolute` | 0–3；回報範圍異常（min=65522、max=256） | 預設 0（自動），設 1 為手動。曝光值疑似是 Windows DSHOW 的 2 的次方值以 16 位元無號數表示（65529 = −7，約 1/128 s），需實拍測試確認 |
  | `white_balance_automatic` / `white_balance_temperature` | 0/1；**只有 1–5 五段**（不是 K 值） | 先關自動白平衡，再選固定段數 |
  | `gain` | 0–255 | 固定，盡量低以減少雜訊 |
  | `power_line_frequency` | 0–2，預設 1（50 Hz） | **台灣市電為 60 Hz，改為 2**，避免日光燈造成的條紋與閃爍 |
  | `sharpness` | 0–255，預設 0 | 維持 0，避免銳化光暈影響 3DGS |

- 解析度：相機支援 2048×1536，比 1151125 使用的 1280×720 多約 3.4 倍像素。物品試行（P0）時兩種解析度各拍一次比較清晰度與重建畫質，再決定教室使用的解析度；決定後就不再更改（內參與解析度綁定）。
- 對焦設定檔（僅能軟體對焦）：`object`（約 0.5–1 m）、`room`（約 2–3 m）、`outdoor`（無限遠）。每個設定檔的對焦值寫在 `config/rig.yaml`，**每個設定檔各做一次 S3 標定**；拍攝時讀回對焦值，與標定時不同就警告。

##### S2-2 行動供電（無法插電時的穩定供電方案）

**1. 功耗估算**

| 元件 | 估計功耗 |
|------|---------|
| Pi 5（擷取、存檔，不做運算）+ 主動散熱器 | 約 5–8 W |
| USB 相機 × 2 | 約 1–2.5 W × 2 |
| USB SSD | 約 2–4 W（寫入時尖峰） |
| 小螢幕 / 蜂鳴器（可選） | 約 1–2 W |
| **合計** | **平均約 10 W，尖峰約 15 W**（5 V 下約 2–3 A） |

> 實際數值以 USB 電流表量測為準，量測結果寫進 README。

**2. Pi 5 的供電限制（最容易出問題的地方）**
- Pi 5 官方建議使用 **5V / 5A（27 W）USB-C PD** 電源。
- 電源若無法協商出 5V 5A（多數行動電源只有 5V 3A），Pi 5 會把**所有 USB 埠的總輸出限制在 600 mA**。2 台相機 + SSD 很容易超過這個值，造成相機斷線、SSD 寫入失敗。
- 解決方式（擇一）：
  - 使用能提供 5V 5A 的電源（規格明確寫出 5V⎓5A，或標示支援 Pi 5 的 UPS / 行動電源）。
  - 使用品質良好的 5V 3A 電源，並在 `/boot/firmware/config.txt` 加上 `usb_max_current_enable=1` 解除 USB 限流。前提是**總功耗確實低於 15 W**，否則會因電壓不足而當機。

**3. 供電架構（本專題採用方案 B）**

```
 Aukey PB-Y37（USB-C PD，5V 3A）
        │ USB-C 短線（5A 規格，≤ 50 cm）
        ▼
 Pi 5（usb_max_current_enable=1）──USB──▶ cam0、cam1、SSD
```

| 方案 | 優點 | 缺點 | 用途 |
|------|------|------|------|
| **B. 只用行動電源（採用）** | 最便宜、最簡單；PB-Y37 一顆約可用 4–5 小時，夠一次教室拍攝 | 換電池必須先關機；Pi 讀不到行動電源電量；電量歸零會直接斷電 | **主方案** |
| C. 加裝外接供電 USB Hub | 相機與 SSD 改由 Hub 供電，減輕 Pi 的負擔 | 多一條線與一個 Hub | 燒機測試出現電壓不足，或改用 4 台相機時再加 |
| A. UPS HAT + 行動電源 | 換電池不用關機；可讀電量、自動安全關機 | 需額外購買 | 目前不採用；若之後要長時間拍攝建築外觀再考慮 |

**目前採用的電池：Aukey PB-Y37**
- 5V 最高只有 3A（65W 是 20V 檔，Pi 5 用不到），Pi 5 協商不到 5A，因此**必須**在 `/boot/firmware/config.txt` 加上 `usb_max_current_enable=1`。
- 若燒機測試仍出現電壓不足，改用方案 C：以 PB-Y37 的 USB-A 埠供電給外接供電 USB Hub，相機與 SSD 接 Hub。
- 功耗可用 `vcgencmd pmic_read_adc`（Pi 5 內建 PMIC 量測）讀取各電源軌的電壓與電流，搭配 USB 電流表交叉確認。

**4. 續航估算**
- 20,000 mAh（3.7 V）行動電源 ≈ 74 Wh，扣除轉換損失（約 80%）≈ 59 Wh；以平均 10–12 W 計算，**約可使用 4–5 小時**。
- 一間教室預估拍攝 1–2 小時（含補拍），**一顆 PB-Y37 充飽即可**；物品試行約 15 分鐘。
- 若要更換電池：先在筆電上執行安全關機（`sudo shutdown -h now`），等綠燈熄滅再拔電。

**5. 穩定措施（寫進 `pi/capture_rig.py` 與拍攝 SOP）**
- 使用短而粗的 USB-C 線（≤ 50 cm、標示 5A），避免線材壓降造成電壓不足。
- 每拍一組就讀取 `vcgencmd get_throttled`，記錄在 `group.json`；出現電壓不足旗標時嗶聲警告，該組標記為「待重拍」。
- Pi 讀不到 PB-Y37 的電量，改用**時間提醒**：開機累計 3.5 小時時提示檢查行動電源的電量燈；休息時順便看一次。
- `get_throttled` 連續出現電壓不足時，視為電量將盡或線材有問題：停止拍攝、寫完當前這組、`sync`、安全關機。
- 每組影像寫完立即 `fsync`，斷電時最多只遺失正在寫的那一組。
- 系統放在 SD 卡、影像放在 SSD，兩者分開。
- SSD：原有資料備份後**格式化為 ext4**（GPT、單一分割區、標籤 `ssd3dgs`），影像存在 `/mnt/ssd/3dgs_capture/`。
  - ext4 有日誌，突然斷電時檔案系統較不易損壞；仍搭配每組寫完 `fsync`、電壓不足時停止拍攝、換電池或結束時一律安全關機。
  - 以 UUID 在 `/etc/fstab` 固定掛載到 `/mnt/ssd`（`nofail,noatime`），不依賴桌面環境的自動掛載，路徑也不會變。
  - 擷取程式啟動時檢查 `/mnt/ssd` 確實是 SSD 且剩餘空間足夠，否則拒絕開始（避免 SSD 沒掛上時誤寫到 SD 卡）。
  - Windows 無法直接讀 ext4：拍完把 SSD 接到 Linux 伺服器，或由 Pi / 伺服器以 `rsync`、`scp` 經網路傳到 Windows 桌機備份。
- 加裝 Pi 5 主動散熱器；設備放背包時要保留通風，避免過熱降頻。
- **出發前做 30 分鐘燒機測試**：以電池供電連續拍攝，確認 `get_throttled` = `0x0`、沒有相機斷線、溫度 < 75°C。

**輸出**：rig 照片、`config/rig_layout.yaml`（相機排列、俯仰角、實測相機間距 mm）、`output/02_power/run_*/power_test.csv`（燒機測試的電壓旗標、PMIC 電壓電流、溫度、時間）。
**門檻**：燒機測試 30 分鐘內電壓不足次數 = 0、相機斷線次數 = 0。
**注意**：rig 組裝、對焦鎖定後就**不可再動**，否則 S3 要重做。

---

#### S3 相機標定（內參 + rig 外參）

**目標**：取得 2 台相機的內參與 rig 內的相對外參，轉成 COLMAP 可用的格式。**每個對焦設定檔（object / room / outdoor）各做一次**。

0. **標定照片在 Pi 上拍**：用 `pi/capture_rig.py --mode calib`，以實際拍攝時相同的解析度、MJPEG 格式與對焦值拍 ChArUco；照片傳到伺服器或筆電，再用 1151125 的標定程式計算（不在 Pi 上跑標定，避免 2 GB RAM 不夠）。
   - 1151125 的照片是在 Windows / DSHOW 下拍的，對焦設定不同，**內參不能直接沿用**，只沿用程式與流程。

1. **內參**：沿用 1151125 `calib_intrinsic.py`（每台 20–30 張 ChArUco，RMS < 0.5 px）[11][12]。
2. **Rig 外參**：只有一對相機，比 4 台串接簡單。
   - 沿用 1151125 `calib_extrinsic.py`（2 台同時拍 15–20 組 ChArUco，solvePnP 初值 + BA），得到 cam1 相對 cam0 的 `R, t`（mm）。
   - 標定板要放在**兩台的重疊區**（上下視野交界處），並涵蓋不同距離（1–4 m）與傾斜角度。
   - 以 `cv2.stereoCalibrate(..., flags=CALIB_FIX_INTRINSIC)` 再算一次，兩者結果互相驗證。
   - 基線只有 10–15 cm，**相機間距要用游標卡尺量測鏡頭中心**，1 mm 誤差就約等於 1% 的尺度誤差。
3. **轉換**：`src/tools/export_rig.py`
   - 內參轉成 COLMAP `OPENCV` 相機模型（fx, fy, cx, cy, k1, k2, p1, p2）。
   - Rig 外參轉成 COLMAP rig 設定（以 cam0 為參考感測器，其餘相機的相對位姿），平移單位換成**公尺**。
   - 輸出 `config/rig.yaml` 與 `rig_config.json`。
4. **驗證**：相機間距的計算值與 S2 實測值比較；用 1151125 `visualize.py layout` 確認相機排列與實物一致。

**輸出**：`config/rig.yaml`（依對焦設定檔分段）、`output/03_rig/<profile>/run_*/rig_config.json`、`baselines.csv`。
**門檻**：內參 RMS < 0.5 px；BA 後外參重投影誤差 < 1 px；相機間距誤差 < 2%。

---

### 階段 B：拍攝

#### S4 拍攝規劃

**目標**：拍攝前先規劃路線與數量，確保覆蓋完整、重疊足夠。

- **路線**（寫進 `config/scenes/<scene>.yaml` 與 README 的拍攝 SOP）：
  - 教室（共 3 圈；天花板由 cam1 負責，不需專門仰拍）：
    1. 沿牆內側繞一圈，rig 朝向教室中央。
    2. 走中間走道往返，rig 朝向四面牆；每個定點左右各轉約 30° 多拍一組。
    3. 手持高度降到約 0.8 m、整支 rig 下俯約 30°，再繞一圈，補拍桌面、椅子與地板。
  - 建築外觀：距牆約 5–10 m 繞行一圈；轉角處每 15° 拍一組；樓層較高時，第二圈把整支 rig 仰起約 30°。
- **拍攝間距**：相鄰兩組重疊 ≥ 60%，教室每 0.3–0.5 m 拍一組。
- **預估數量**：教室約 600–1,200 組（1,200–2,400 張），拍攝約 1–1.5 小時。
- **電池**：出發前 PB-Y37 充滿，並完成 S2-2 的燒機測試。
- **拍攝日期**：教室只能在**假日**拍攝（全天可用）。建議上午拍攝、下午在現場或回去後跑 S6 覆蓋檢查與 S7–S8，有問題時當天補拍；正式拍攝前一個平日先完成物品試行（P0）。
- **環境控制**：拉上窗簾或選陰天，避免直射光；燈具統一全開；移除會移動的物品（例：電扇停轉）。
- **尺度參考**：在場景中放 1–2 塊 ChArUco 板（平放於講桌與地面），並先用捲尺量好 3–5 段距離寫入場景設定檔，供 S9、S11 使用。
- 外觀拍攝不得拍到可辨識的路人臉部；若有，S7 加上遮罩。

**輸出**：`config/scenes/<scene>.yaml`（路線說明、實測距離、ChArUco 位置）。

---

#### S5 拍攝端：Raspberry Pi 5 同步擷取

**目標**：每次觸發時 2 台相機各存一張，影像完整、時間差小，且不受 Pi 2 GB RAM 與電池供電的影響。

1. **擷取方式**（`pi/capture_rig.py`）
   - 2 台相機各一個執行緒持續讀取最新影格（buffer = 1），觸發時同時取出 2 張，記錄每台的時間戳。
   - 兩台相機接在不同的 USB 控制器上，SSD 接 USB 3.0 埠；只開 2 路，頻寬與同步都比 4 台穩定。
     - 已確認的接法（`lsusb -t`）：cam 分別在 Bus 001、Bus 003（480M），SSD 在 Bus 002（5000M，uas）。
     - 對應的實體路徑：cam0（下）= `/dev/v4l/by-path/platform-xhci-hcd.0-usb-0:2:1.0-video-index0`（目前為 `/dev/video0`）；cam1（上）= `/dev/v4l/by-path/platform-xhci-hcd.1-usb-0:2:1.0-video-index0`（目前為 `/dev/video2`）。每台相機的 `video-index1` 是 metadata 節點，不用來擷取。
   - **以實體 USB 孔辨識相機**：`/dev/videoN` 的編號在重新開機或重新插拔後可能互換，若 cam0 / cam1 對調，標定結果就會套錯相機。程式一律以 `/dev/v4l/by-path/...-video-index0`（綁定實體 USB 孔）開啟相機，對應關係寫在 `config/rig.yaml`；兩台相機的插孔固定，並在線上貼標籤。
   - **直接保存相機輸出的 MJPEG 原始資料**（V4L2 關閉 RGB 轉換，取得 JPEG bytes 直接寫檔）：Pi 不必解碼再壓縮，CPU 與 RAM 負擔最小，也沒有二次壓縮損失。
     > 這與 1151125「一律存 PNG」的規則不同：相機輸出本身就是 MJPEG，原樣保存就是最原始的資料。
   - 影像寫入**外接 USB SSD**（SD 卡寫入速度不足）。
2. **觸發模式**
   - **停拍模式（建議）**：走到定點、停 1 秒、按 GPIO 按鈕拍一組。手持 rig 靜止時，2 台的時間差影響很小。
   - **定時模式**：每 0.5–1 秒自動拍一組，需慢走；以時間差與 S7 的模糊偵測把關。
3. **即時檢查**（筆電在旁邊遠端連線：Pi 開一個輕量網頁 `http://<pi>:8000` 顯示預覽，並以 SSH 操作；不使用 VNC 遠端桌面，以節省 Pi 的 CPU 與電力。連線方式可用網路線直連或 Pi 的 Wi-Fi 熱點）
   - 2 張縮圖、本組時間差、SSD 剩餘空間、已拍組數、**電壓旗標、溫度、累計運行時間**。
   - 觸發方式：GPIO 按鈕，或在筆電網頁上按「拍攝」。
   - 時間差 > `capture.max_skew_ms`（預設 20 ms）、相機掉幀，或 `get_throttled` 出現電壓不足時嗶聲警告，該組標記為「待重拍」。
   - 相機斷線時自動重新開啟，並在 log 記錄斷線時間。
4. **供電監控**：依 S2-2 的規則記錄電壓旗標與運行時間；連續電壓不足時停止拍攝並安全關機。
5. **對焦檢查**：每組讀回對焦值，與 `config/rig.yaml` 中該設定檔的值不同時警告（軟體對焦可能在重新開機或重新插拔後被重設）。
6. **Metadata**：`group.json`（時間戳、各相機實際讀回的曝光、增益、色溫、對焦、時間差、`get_throttled`、溫度）；session 層級存 `session.json`（場景、rig 版本、標定 run）。

**輸出**：`data/raw/<scene>/<session>/group_xxxxx/cam{0,1}.jpg`、`group.json`、`session.json`。
**門檻**：時間差 ≤ 20 ms 的組數 ≥ 95%；掉幀組數 = 0；電壓不足組數 = 0（有問題的組一律重拍）。

---

#### S6 資料匯入與完整性檢查

**目標**：把 Pi 的資料完整搬到伺服器，並在離開現場前確認是否需要補拍。

1. `src/ingest.py`：SSD 直接接到 Linux 伺服器（或以 `rsync` 經網路傳輸），複製到伺服器的 `data/raw/`，計算每張影像的 SHA-256，產生 `manifest.csv`；同時備份一份到 Windows 桌機。複製前先確認伺服器剩餘空間。
2. **快速覆蓋檢查（現場在筆電上跑，或從現場連回伺服器跑）**：從所有組中每 10 組取 1 組，以 COLMAP 快速跑一次低解析度 SfM，在俯視圖上看相機軌跡是否涵蓋整個教室、是否斷成多段。
3. 斷成多段或有區域沒拍到 → 回 S4 補拍，**同一 session 追加**新的 group。

**輸出**：`output/06_ingest/<scene>/run_*/manifest.csv`、`coverage_preview.png`。
**門檻**：雜湊全部吻合；快速 SfM 只有 1 個模型（沒有斷開）。

---

### 階段 C：重建（Linux 伺服器）

#### S7 影像前處理

**目標**：剔除會拖累重建的影像，並準備遮罩。

1. **模糊偵測**：計算 Laplacian 變異數；低於「該相機所有影像的第 10 百分位數」或低於絕對下限者剔除。以「組」為單位：同一組有 2 張以上模糊時整組剔除（rig 約束需要整組）。
2. **去重複**：相鄰兩組的特徵位移中位數 < `preprocess.min_motion_px` 時只保留一組。
3. **曝光檢查**：統計每張的亮度直方圖，過曝（> 5% 像素飽和）或過暗者標記；鎖定 3A 後應該很少。
4. **遮罩（可選）**：以 YOLO-seg 偵測 `person`，產生遮罩；COLMAP 特徵擷取與 3DGS 損失都忽略遮罩區域。
5. 所有決定寫入 `frames.csv`（每張的清晰度、亮度、是否保留、剔除原因），不動原始檔。

**輸出**：`output/07_preprocess/<scene>/run_*/frames.csv`、`images/`（保留影像的連結）、`masks/`。
**門檻**：保留比例 ≥ 80%（低於此值代表拍攝品質有問題，應回 S4）。

---

#### S8 位姿估計（SfM）

**目標**：估計每張影像的相機位姿與稀疏點雲，作為 3DGS 初始化 [1]。

1. **特徵擷取**：COLMAP SIFT（GPU），相機模型 `OPENCV`，**每台相機一個共用內參**（4 個資料夾各對應一台相機），內參以 S3 結果固定。
2. **Rig 設定**：以 S3 的 `rig_config.json` 設定 rig，讓同一組 2 張影像共用一個 rig 位姿、相機之間的相對位姿固定。BA 只估計各組的 rig 位姿與 3D 點。
3. **配對**：
   - 序列配對（sequential，前後各 10 組，含同組內 2 張互相配對）。
   - 加上 vocabulary tree 配對，偵測回到起點時的迴路（loop closure），降低繞一圈後的累積漂移。
4. **重建**：增量式 mapper [13][16]（可選 GLOMAP 全域式 [15] 做比較）；BA 時**不優化內參**。
5. **去畸變**：`image_undistorter` 輸出 `PINHOLE` 模型的去畸變影像與模型（3DGS 訓練需要無畸變的針孔模型 [1]）。
6. **比較組（實驗用）**：同一批影像以「COLMAP 自行估內參、無 rig」再跑一次，比較註冊率、誤差與尺度（見 S11 實驗 1）。

**輸出**：`output/08_sfm/<scene>/run_*/sparse/0/`、`undistorted/`、`sfm_stats.json`（註冊影像數、3D 點數、平均軌跡長度、平均重投影誤差）。
**門檻**：註冊率 ≥ 95%；平均重投影誤差 < 1 px；只產生 1 個模型。

---

#### S9 尺度與座標對齊

**目標**：讓模型以公尺為單位、地面水平、牆面對齊座標軸，方便後續量測、網頁導覽與遊戲引擎使用。**在訓練前做**，之後所有步驟就都在同一個座標系統中。

1. **尺度**
   - 初始尺度：rig 約束下，SfM 的尺度由 S3 的相機間距決定（公尺）。2 台相機只有一段 10–15 cm 的基線，尺度精度有限，因此下一項**列為必做**。
   - ChArUco 檢查（必做）：在去畸變影像中偵測場景中的 ChArUco 板，三角化角點後計算方格邊長，與實際 30 mm 比較；兩者差距 > 1% 時改以 ChArUco 尺度為準，並在報告中說明兩者差距。
   - 實測距離：再以 S4 量好的 3–5 段距離交叉驗證（S11 正式評估）。
2. **水平**：以 RANSAC 從稀疏點雲擬合地面平面（法向量最接近「所有相機上方向平均」的大平面），旋轉使地面法向量 = +Y、地面 y = 0。
3. **牆面對齊（曼哈頓假設）**：擬合兩面主要牆面，繞 Y 軸旋轉使牆面平行 X / Z 軸。
4. **原點**：設在 `config/scenes/<scene>.yaml` 指定的位置（例：教室前門地面）。
5. 以相似轉換（尺度 s、旋轉 R、平移 t）套用到相機位姿與 3D 點，存成新模型。

**輸出**：`output/09_align/<scene>/run_*/sparse_aligned/`、`alignment.json`（s, R, t、地面擬合殘差、ChArUco 尺度驗證結果）。
**門檻**：ChArUco 方格邊長誤差 < 1%；地面擬合殘差中位數 < 1 cm。

---

#### S10 3D 高斯潑濺訓練

**目標**：訓練出畫質良好、高斯數量受控的 3DGS 模型。

1. **基準設定（依原始論文 [1]）**
   - 30,000 次迭代；損失 `(1 − 0.2)·L1 + 0.2·D-SSIM`。
   - Densification：第 500–15,000 次迭代間每 100 次，梯度門檻 0.0002；每 3,000 次重設不透明度。
   - 球諧階數每 1,000 次升一階，最高 3 階。
   - 測試切分：每 8 張取 1 張不參與訓練（以「組」為單位挑選，避免同組影像同時出現在訓練與測試中）。
2. **工具**：gsplat [3] 的訓練腳本（Nerfstudio `splatfacto` 作為替代）。`config/pipeline.yaml` 選擇 densification 策略：
   - `default`：同原始論文。
   - `mcmc`：設定高斯數量上限（例：教室 1.5–3 M 顆），直接控制模型大小，方便 S14 壓縮達標。
3. **可選改進**（各自以開關控制，S11 做消融比較）：
   - **曝光補償**：每張影像學習一組色彩仿射參數，吸收殘留的亮度差 [2]。
   - **深度正則化**：以單目深度估計作為弱監督，改善白牆等無紋理區域的漂浮點 [2]。
   - **抗鋸齒**：減少遠處牆面與黑板文字的閃爍。
   - **遮罩**：S7 的遮罩區域不計算損失。
4. 以 TensorBoard 記錄損失、PSNR、高斯數量、VRAM；每 7,000 次存一次 checkpoint 與測試視角渲染圖。

**輸出**：`output/10_train/<scene>/run_*/point_cloud.ply`、`ckpt/`、`tb/`、`train_stats.json`（高斯數、耗時、峰值 VRAM）。
**門檻**：訓練 ≤ 30 分鐘；測試 PSNR ≥ 25 dB（未達標時檢查 S7 / S8，不要只調訓練參數）。

---

#### S11 評估

**目標**：從畫質、幾何、效率三方面量化成果，並完成比較實驗。

**評估指標**

| 類別 | 指標 | 作法 | 參考 |
|------|------|------|------|
| 畫質 | PSNR、SSIM、LPIPS | 測試視角渲染圖 vs. 原圖 | [1] |
| 幾何：尺度 | 實測距離誤差（%） | 在模型中點選端點量測，與捲尺值比較 | [8] |
| 幾何：平面 | 牆面 / 地面平面度（擬合殘差的中位數與 90 百分位數） | 對牆面、地面區域的高斯中心擬合平面 | [8] |
| 幾何：參考比較 | 與 COLMAP MVS 稠密點雲的雙向最近點距離（cloud-to-cloud） | 以 MVS 作為幾何參考（Clini 等人以 TLS 為參考；本專題沒有 TLS，改用 MVS） | [6][17] |
| 效率 | 拍攝時間、各步驟耗時、模型大小、渲染 FPS | `timing.json` 彙整 | [7] |

**比較實驗**

1. **標定先驗的效果**（與課程最直接的連結）

   | 組別 | 內參 | Rig 約束 | 比較 |
   |------|------|---------|------|
   | A | COLMAP 自行估計 | 無 | 註冊率、重投影誤差、PSNR、尺度（需手動對齊） |
   | B | 固定（S3） | 無 | 同上 |
   | C | 固定（S3） | 有 | 同上 + 自動公尺尺度的誤差 |

2. **相機數量**：同一次拍攝只用 cam0（模擬單台相機）vs. 2 台，比較註冊率、PSNR、漏拍區域（特別是天花板），並估算單台相機要達到同樣覆蓋所需的拍攝時間。
3. **影像數量**（參考 [8] 對影像數量的分析）：保留 25% / 50% / 100% 的組，觀察畫質與訓練時間的取捨，作為拍攝 SOP 的建議間距。
4. **方法比較**（參考 [6] 的室內比較設計）：3DGS vs. COLMAP MVS + 網格，比較視覺品質、幾何精度、處理時間。
5. **訓練消融**：基準 vs. +曝光補償 vs. +深度正則化 vs. MCMC 數量上限。

**輸出**：`output/11_eval/<scene>/run_*/metrics.json`、`distances.csv`、`planarity.csv`、`c2c.json`、`renders/`、比較表格 `experiments.csv`。

---

### 階段 D：輸出

#### S12 模型清理

**目標**：去除教室範圍外的內容與漂浮雜點。

1. **裁切**：以場景設定檔中的包圍盒（公尺，S9 座標）刪除範圍外的高斯，例如窗外景物。
2. **自動濾除**：
   - 不透明度 < `cleanup.min_opacity`（例：0.005）。
   - 最大軸尺度 > `cleanup.max_scale_m`（例：0.5 m），這類高斯常是霧狀雜點。
   - 統計式離群點濾除（沿用 1151125 的方法，k 近鄰平均距離 > 平均 + n·σ）。
3. **人工修補**：匯出後以 SuperSplat 網頁編輯器檢查，刪除殘留雜點；手動刪除的範圍另存成 `manual_edits.json`，以便重跑時套用。
4. 清理後重新計算 S11 的畫質指標，確認畫質下降 < 0.3 dB。

**輸出**：`output/12_cleanup/<scene>/run_*/cleaned.ply`、`cleanup_stats.json`（各條件刪除的數量）。

---

#### S13 碰撞網格（遊戲用）

**目標**：高斯沒有表面，需要另外產生一份**簡化網格**作為 Unity 的隱形碰撞體（地板、牆、桌椅）。

| 方法 | 作法 | 優缺點 |
|------|------|-------|
| **A. 渲染深度 + TSDF（MVP）** | 用訓練好的模型從所有訓練視角渲染深度圖 [3]，以 Open3D TSDF 融合後提取網格 | 不需重新訓練；牆面可能稍微凹凸 |
| B. COLMAP MVS + Poisson | 稠密重建後 Poisson 表面重建 | 傳統方法、幾何穩定；白牆可能有破洞 |
| C. PGSR [9] / 2DGS [18] | 以平面化高斯重新訓練，再 TSDF 提取 | 建築平面最平整；需額外訓練時間 |

- 後處理：二次誤差（quadric）簡化到 ≤ 50k 個三角形；移除小碎片；在 Unity 中作為 `MeshCollider`。
- 評估：網格到 MVS 點雲的距離、地面平面度；Unity 中實際行走測試。

**輸出**：`output/13_mesh/<scene>/run_*/collision.obj`、`mesh_stats.json`。

---

#### S14 壓縮與匯出

**目標**：網頁模型 < 100 MB，畫質下降可接受。

1. **剪枝**：依「對所有訓練視角的貢獻度」（累積不透明度 × 覆蓋像素）排序，刪除貢獻最低的 20–50%，微調 1–2k 次迭代恢復畫質。
2. **降低球諧階數**：3 階 → 1 或 2 階（球諧係數佔檔案大部分），比較畫質差異。
3. **量化與格式**：轉成網頁檢視器支援的壓縮格式（`.spz` 或 `.sog`），與原始 `.ply` 比較大小。
4. **參考上限**：HAC++ [10] 等學習式壓縮可達約 100 倍，但需要專用解碼器、不適合直接上網頁，在報告中作為比較基準即可。
5. 每種設定都記錄檔案大小、PSNR、筆電 FPS，畫成「大小–畫質」曲線，選出預設值。

**輸出**：`output/14_export/<scene>/run_*/scene.spz`（或 `.sog`）、`scene_unity.ply`、`compress_stats.json`。
**門檻**：檔案 < 100 MB；相對 S12 的 PSNR 下降 < 1 dB。

---

#### S15 展示：網頁導覽、Unity，與框架整合

**1. 網頁校園導覽（`web/`）**
- three.js 系高斯檢視器（Spark 或 GaussianSplats3D）。
- 操作：WASD 移動、滑鼠轉視角、Shift 加速；手機觸控搖桿。
- 視角高度固定在 1.6 m（S9 已把地面設為 y = 0）；載入 S13 的簡化網格作為不可見的碰撞，防止穿牆。
- 導覽點：在場景設定檔中定義（講台、後門、窗邊），點選後平滑飛到該視角；可加說明文字。
- 載入進度條；靜態網頁可部署到學校伺服器或 GitHub Pages（模型檔視大小放在 Releases 或學校伺服器）。

**2. 遊戲引擎（`unity/`）**
- 以開源 Gaussian Splatting 外掛匯入 `scene_unity.ply` 作為外觀，S13 網格作為 `MeshCollider`（不渲染）。
- 第一人稱角色控制器，驗證可行走、不穿牆、不掉落。
- 只保留設定、腳本與說明，大型資產不上傳 GitHub。

**3. 框架整合**
- `src/run_pipeline.py --scene classroom_xxx --from S7 --to S14`：依序執行各步驟，每一步讀上一步的 `latest.txt`，遇到門檻未通過就停止並列出原因。
- `src/app.py`：沿用 1151125 的主選單 UI，列出 S1–S15，按鈕開啟各步驟的參數視窗。
- README：拍攝 SOP、各步驟指令、輸出檔說明、實際數據。

**驗收**：從新的拍攝資料到網頁可瀏覽，只需執行 S6 匯入與一行 `run_pipeline.py`。

---

## 品質門檻總表（實際值寫在 `config/params.yaml`）

| 步驟 | 項目 | 門檻 |
|------|------|------|
| S1 | 範例場景 PSNR 與論文差距 | ≤ 1 dB |
| S3 | 內參 RMS / 外參 BA 後誤差 / 相機間距誤差 | < 0.5 px / < 1 px / < 2% |
| S2 | 燒機測試 30 分鐘的電壓不足 / 相機斷線次數 | 0 / 0 |
| S5 | 同組時間差 ≤ 20 ms 的比例；掉幀組數；電壓不足組數 | ≥ 95%；0；0 |
| S6 | 快速 SfM 模型數 | 1 |
| S7 | 影像保留比例 | ≥ 80% |
| S8 | 註冊率 / 平均重投影誤差 | ≥ 95% / < 1 px |
| S9 | ChArUco 尺度誤差 / 地面擬合殘差中位數 | < 1% / < 1 cm |
| S10 | 訓練時間 | ≤ 30 分鐘 |
| S11 | PSNR / SSIM / LPIPS / 實測距離誤差 | ≥ 25 dB / ≥ 0.85 / ≤ 0.25 / < 3% |
| S12 | 清理後 PSNR 下降 | < 0.3 dB |
| S14 | 檔案大小 / PSNR 下降 | < 100 MB / < 1 dB |
| S15 | 筆電瀏覽 FPS | ≥ 30 |

未達門檻時**不要直接放寬門檻**，先依「預期困難與對策」找原因；確定要放寬時，在 README 記錄理由。

---

## 設備與工具

### 硬體
- 2 台同型號 USB 相機、自製 rig（鋁擠型 / 雲台座）、手持握把、L 型短 USB 線、束帶
- Raspberry Pi 5（2 GB）+ 主動散熱器、外接 USB SSD、GPIO 按鈕、蜂鳴器
- 供電：Aukey PB-Y37 行動電源、5A USB-C 短線、USB 電流表（量測功耗）；外接供電 USB Hub（必要時）
- 儲存：SanDisk Extreme Portable SSD 2 TB
- 游標卡尺（量相機間距）
- ChArUco 標定板（沿用 1151125）、捲尺 / 雷射測距儀
- Linux 伺服器：Ryzen 9 9950X、RAM 64 GB、RTX PRO 5000 Blackwell 48 GB
- Windows 桌機：i7-13700、RAM 64 GB、RTX 4060 Ti 16 GB
- 筆電（現場遠端操作 Pi）

### 軟體
| 用途 | 工具 |
|------|------|
| 拍攝端 | Raspberry Pi OS 64-bit、`opencv-python`（V4L2）、`gpiozero` |
| 標定 | 1151125 工具（`opencv-contrib-python`、`scipy`） |
| SfM | COLMAP（CUDA）、`pycolmap`、GLOMAP（可選） |
| 3DGS | CUDA 12.8+、cu128 版 PyTorch、gsplat、Nerfstudio（可選） |
| 前處理 / 評估 | `ultralytics`（YOLO-seg）、`lpips`、`scikit-image`、`open3d` |
| 清理 / 展示 | SuperSplat、three.js + Spark / GaussianSplats3D、Unity + Gaussian Splatting 外掛 |

---

## 建議專案結構

```
1159999_期末專題/
├── README.md                     專題說明、拍攝 SOP、執行方式、實際數據
├── Task.md                       本文件
├── requirements.txt              運算端套件
├── requirements_pi.txt           拍攝端套件
├── config/
│   ├── rig.yaml                  S3：2 台內參 + rig 外參
│   ├── rig_layout.yaml           S2：相機排列與實測間距
│   ├── pipeline.yaml             各步驟使用的工具
│   ├── params.yaml               各步驟參數與品質門檻
│   └── scenes/
│       └── classroom_xxx.yaml    S4：路線、實測距離、ChArUco 位置、裁切範圍、導覽點
├── pi/
│   ├── check_cameras.py          S1：相機格式與 fps 檢查
│   ├── check_power.py            S1、S2-2：電壓旗標、PMIC 電壓電流、溫度、燒機測試
│   └── capture_rig.py            S5：同步擷取（停拍 / 定時）
├── src/
│   ├── app.py                    主選單 UI
│   ├── run_pipeline.py           S7–S14 一鍵串接
│   ├── ingest.py                 S6
│   ├── preprocess.py             S7
│   ├── sfm.py                    S8
│   ├── align.py                  S9
│   ├── train.py                  S10
│   ├── evaluate.py               S11
│   ├── cleanup.py                S12
│   ├── mesh.py                   S13
│   ├── export.py                 S14
│   ├── common/                   run 資料夾、latest.txt、設定檔讀寫、計時、log
│   └── tools/
│       ├── check_env.py          S1
│       ├── export_rig.py         S3：1151125 結果 → COLMAP rig
│       └── measure.py            在模型中量兩點距離
├── web/                          S15：網頁導覽
├── unity/                        S15：Unity 設定與腳本
├── data/                         原始影像（不上傳 GitHub）
│   └── raw/<scene>/<session>/group_xxxxx/cam{0,1}.jpg
└── output/
    ├── 00_env/  02_power/  03_rig/  06_ingest/  07_preprocess/  08_sfm/  09_align/
    └── 10_train/  11_eval/  12_cleanup/  13_mesh/  14_export/
```

---

## 預期困難與對策

| 困難 | 對策 | 步驟 |
|------|------|------|
| 只有 2 台相機，拍攝時間較長、容易漏拍 | 上下排列由 cam1 負責天花板，省一圈；S6 快速覆蓋檢查後再離開現場 | S2、S4、S6 |
| 移動中電壓不足、相機斷線、檔案損壞 | `usb_max_current_enable=1`；5A 短線；每組 `fsync`；記錄 `get_throttled`；SSD 用 ext4；出發前燒機測試；必要時加外接供電 Hub | S2-2、S5 |
| 電池用完（Pi 讀不到行動電源電量） | 一顆 PB-Y37 約 4–5 小時，足夠一次拍攝；3.5 小時提醒；換電池前先安全關機 | S2-2 |
| 軟體對焦被重設 | 每組讀回對焦值並比對；每個對焦設定檔各自標定 | S2、S3、S5 |
| 伺服器硬碟只有約 64 GB | 中間 checkpoint 只留最後一個；原始資料存 SSD 與 Windows 桌機；建議擴增到 ≥ 200 GB | 共同規則 |
| 教室只能假日拍，失敗代價高 | 先以物品試行（P0）跑通流程；拍攝當天就跑覆蓋檢查與 SfM，有問題當天補拍 | P0、S4、S6 |
| 移動中 USB 接頭鬆脫 | L 型短線 + 束帶固定；程式自動重連並記錄 | S2、S5 |
| 基線只有 10–15 cm，自動尺度精度有限 | 游標卡尺量測基線；S9 的 ChArUco 尺度檢查列為必做 | S3、S9 |
| Pi 5 只有 2 GB RAM | 直接存 MJPEG 原始資料，不解碼不重新編碼；預覽縮圖 | S5 |
| 無硬體同步 | 停拍模式；記錄時間差，超過門檻就重拍 | S5 |
| 白牆、黑板等無紋理區域 | rig 約束穩定位姿；深度正則化；必要時暫貼海報，拍完移除 | S8、S10 |
| 繞一圈後累積漂移 | vocabulary tree 迴路偵測；rig 約束 | S8 |
| 玻璃窗反光、窗外過曝 | 鎖定曝光；拉窗簾；窗外以裁切去除 | S4、S12 |
| 漂浮雜點 | 增加角度覆蓋；深度正則化；自動濾除 + SuperSplat | S10、S12 |
| Blackwell（RTX PRO 5000）套件相容性 | CUDA Toolkit 12.8 以上編譯 gsplat、cu128 或更新版 PyTorch；S1 先跑通範例 | S1 |
| 高斯沒有表面，遊戲無法碰撞 | 另產生簡化碰撞網格 | S13 |
| 網頁載入慢 | 高斯數量上限、剪枝、降球諧階數、壓縮格式 | S10、S14 |
| 拍攝時有人走動 | 無課時段拍攝；YOLO-seg 遮罩 | S4、S7 |

---

## 時程規劃（6 週，最後一週為緩衝）

| 週次 | 日期 | 步驟 | 工作項目 | 里程碑 |
|------|------|------|----------|--------|
| W1 | 11/25 – 12/01 | S1、S2、S5 | 伺服器環境與範例場景；Pi 相機測試（2 台 vs. 4 台）與在 Pi 上鎖定 3A；組裝 rig；Pi 擷取程式；燒機測試 | 範例場景訓練成功、Pi 以電池供電同步拍攝 |
| W2 | 12/02 – 12/08 | S3、**P0**、S4、S6–S8 | 平日：object / room 兩組標定；**物品試行**跑通 S6–S14；**週六 12/05 第一次拍攝教室**，當天跑 S6–S8 | 物品模型完成；教室相機位姿與稀疏點雲 |
| W3 | 12/09 – 12/15 | S9、S10、S12 | 教室對齊、訓練、清理；檢討問題，**週六 12/12 補拍** | 第一版教室高斯模型（公尺尺度） |
| W4 | 12/16 – 12/22 | S14、S15、整合 | 壓縮匯出、網頁導覽、`run_pipeline.py`；**週六 12/19 備用拍攝日**（補拍或建築外觀） | **端到端框架完成、網頁可瀏覽** |
| W5 | 12/23 – 12/29 | S11、S13、S15 | 比較實驗；碰撞網格與 Unity；（加分）建築外觀 | 實驗數據、遊戲展示 |
| W6 | 12/30 – 01/05 | — | 緩衝；報告、README（拍攝 SOP）、簡報、Demo 影片 | 繳交 |

> 若只有 5 週：S13 Unity 與建築外觀列為延伸，W5 後半同時準備報告。

---

## 預期成果

1. 可重現的建築三維重建框架：rig 設計、Pi 擷取程式、S1–S15 腳本、設定檔、README
2. 拍攝 SOP：之後重建其他建築的人照著做即可
3. 一間教室（加分：一棟建築外觀）的 3DGS 模型與網頁導覽頁面
4. Unity 場景（高斯外觀 + 碰撞網格）
5. 比較實驗數據：標定先驗、相機數量、影像數量、3DGS vs. MVS、訓練消融、壓縮
6. Demo 影片

---

## 報告建議內容

- 系統架構與 15 個步驟的流程圖；各步驟耗時（「快速重建」的依據）
- Rig 設計照片、標定結果（內參、外參、相機間距計算值 vs. 實測值）
- 拍攝統計：組數、時間差分佈、前處理剔除原因
- SfM 結果：註冊率、重投影誤差、相機軌跡俯視圖
- 尺度與對齊：ChArUco 驗證、實測距離誤差
- 畫質：測試視角對照圖（原圖 / 渲染 / 誤差圖）、PSNR / SSIM / LPIPS
- 比較實驗表格與討論，並與文獻 [6][7][8][17] 的結論比較
- 網頁與 Unity 展示截圖、FPS、檔案大小
- 限制與改進方向

---

## 後續延伸方向

1. **擴大範圍**：整棟建築內外、多棟建築；採分塊訓練與層級 LOD [2]，配合校園平面圖切換。
2. **高品質網格與打光**：PGSR [9] / 2DGS [18] 提取網格，讓遊戲引擎能套用動態光影。
3. **硬體升級**：全域快門相機 + 硬體觸發、加裝 IMU，邊走邊拍不需停頓。
4. **互動導覽**：熱點說明、語音導覽、VR 頭盔。
5. **自動化服務**：網頁上傳影像 → 伺服器排程 → 回傳導覽連結。

---

## 參考資料

### 期刊論文（Q1–Q2，分區請以學校 JCR 為準）

**3DGS 核心與工具**
1. B. Kerbl, G. Kopanas, T. Leimkühler, G. Drettakis, "3D Gaussian Splatting for Real-Time Radiance Field Rendering," *ACM Transactions on Graphics*, 42(4), 2023.（Q1）
2. B. Kerbl et al., "A Hierarchical 3D Gaussian Representation for Real-Time Rendering of Very Large Datasets," *ACM Transactions on Graphics*, 43(4), 2024.（Q1）
3. V. Ye et al., "gsplat: An Open-Source Library for Gaussian Splatting," *Journal of Machine Learning Research*, 26(34), pp. 1–17, 2025.（Q1）
4. T. Wu et al., "Recent Advances in 3D Gaussian Splatting," *Computational Visual Media*, 10, pp. 613–642, 2024.（Q1）
5. B. Fei et al., "3D Gaussian Splatting as a New Era: A Survey," *IEEE Transactions on Visualization and Computer Graphics*, 2024（依 arXiv:2402.07181 標註）.（Q1）

**建築 / 室內應用與評估**

6. Fang et al., "Performance Evaluation and Optimization of 3D Gaussian Splatting in Indoor Scene Generation and Rendering," *ISPRS International Journal of Geo-Information*, 2025.（約 Q2）
7. Yu, Verbree, van Oosterom, Pottgiesser, Peng, Poux, "From comparison to integration: A workflow evaluation of 3D Gaussian splatting and LiDAR point cloud for modern architectural heritage," *Automation in Construction*, 180, 106509, 2025.（Q1）
8. F. Remondino et al., "A Critical Analysis of NeRF-Based 3D Reconstruction," *Remote Sensing*, 15(14), 3585, 2023.（Q1–Q2）

**表面提取與壓縮**

9. D. Chen et al., "PGSR: Planar-based Gaussian Splatting for Efficient and High-Fidelity Surface Reconstruction," *IEEE TVCG*, 2024, DOI 10.1109/TVCG.2024.3494046.（Q1）
10. Y. Chen et al., "HAC++: Towards 100X Compression of 3D Gaussian Splatting," *IEEE TPAMI*, 47(11), pp. 10210–10226, 2025.（Q1）

**標定與 SfM 基礎**

11. Z. Zhang, "A Flexible New Technique for Camera Calibration," *IEEE TPAMI*, 22(11), 2000.（Q1）
12. S. Garrido-Jurado et al., "Automatic generation and detection of highly reliable fiducial markers under occlusion," *Pattern Recognition*, 47(6), 2014.（Q1）
13. N. Snavely, S. M. Seitz, R. Szeliski, "Modeling the World from Internet Photo Collections," *IJCV*, 80(2), 2008.（Q1）
14. D. G. Lowe, "Distinctive Image Features from Scale-Invariant Keypoints," *IJCV*, 60(2), 2004.（Q1）

### 會議論文與其他（無 Q 分區，作為補充）

15. L. Pan et al., "Global Structure-from-Motion Revisited," ECCV 2024.（GLOMAP）
16. J. L. Schönberger, J.-M. Frahm, "Structure-from-Motion Revisited," CVPR 2016.（COLMAP）
17. Clini et al., "3D representation of Architectural Heritage: a comparative analysis of NeRF, Gaussian Splatting, and SfM-MVS reconstructions using low-cost sensors," *ISPRS Archives*, XLVIII-2/W8, 2024.
18. B. Huang et al., "2D Gaussian Splatting for Geometrically Accurate Radiance Fields," SIGGRAPH 2024.
19. [1151125_3D稀疏點雲](../1151125_3D稀疏點雲/)：多相機標定、Bundle Adjustment、三角測量（本專題前置作業）
20. COLMAP 文件：https://colmap.github.io；gsplat：https://github.com/nerfstudio-project/gsplat；Nerfstudio：https://docs.nerf.studio
21. R. Hartley, A. Zisserman, *Multiple View Geometry in Computer Vision*, Cambridge University Press.
