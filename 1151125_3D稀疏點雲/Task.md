# 電腦視覺作業

## 現有設備: 鏡頭*4、ChArUco標定板

## 最終目標: 使用OpenCV與一些工具建出3D稀疏點雲

## 參考資料: ./class_ppt
- Day03：相機標定、ChArUco、3A 手動設定、重投影誤差
- Day04：對極幾何、基礎/本質矩陣、立體標定 stereoCalibrate
- Day05：特徵點（SIFT / ORB / SURF）
- Day06：多相機聯合標定、誤差傳播、光束法平差 (Bundle Adjustment)

## 建議工具
- Python 3.10+、`opencv-contrib-python` ≥ 4.8（含 `cv2.aruco` 新 API：`CharucoDetector`）
- `numpy`、`scipy`（用 `least_squares` 做 Bundle Adjustment）
- `open3d`（點雲視覺化、離群點濾除、存 `.ply`）

## 建議專案結構
```
data/calib_intrinsic/cam{0..3}/              每台相機各自的內參標定照片
data/calib_extrinsic/frame_xxx/cam{0..3}.png 4 台同時拍攝的外參標定照片
data/object/cam{0..3}.png                    拍攝物品的照片
output/intrinsics_cam{i}.yaml                內參
output/extrinsics.yaml                       外參
output/cloud.ply                             點雲
src/                                         各步驟腳本
```

---

## 實作步驟

### 0. 前置準備（最容易被忽略、但最影響結果）
1. **確認 ChArUco 板參數並記錄**：字典（如 `DICT_5X5_100`）、方格數 (squaresX, squaresY)、方格邊長、ArUco 邊長。
   **用尺實際量測**印出後的尺寸（印表機會縮放），單位建議統一用 mm。
2. 標定板需**平整、硬質**（貼在壓克力板或厚紙板上），否則平面假設失效。
3. **鎖定相機設定**（Day03 p33–34）：關閉自動對焦、自動曝光、自動白平衡，固定解析度。
   ⚠ 標定後**不可再改焦距或解析度**，否則內參失效。
4. 擺放相機（建議）：4 台相機環繞物品，相鄰相機夾角約 30°–60°，
   確保**相鄰相機視野有大量重疊**，且物品在每台相機中都清楚、佔畫面一定比例。
   固定好之後**相機不能再移動**（移動就要重做外參）。

### 1. 對 4 個鏡頭做相機標定，並將標定結果儲存

#### 1-1 內參標定（每台相機各自做）
1. 每台相機拍 **20–30 張** ChArUco 照片：板子要涵蓋畫面四角和邊緣，
   並有不同距離與傾斜角度（約 ±45°），避免全部正對鏡頭。
2. 用 `cv2.aruco.CharucoDetector.detectBoard()` 偵測角點與 id，丟掉角點太少（如 < 6 個）的照片。
3. 用 `cv2.calibrateCamera()`（或 `cv2.aruco.calibrateCameraCharuco()`）求內參 K 與畸變係數 dist。
4. **檢查重投影誤差**：目標 RMS < 0.5 px（Day03 p39）。過大時刪掉誤差最大的照片再算一次。
5. 用 `cv2.undistort()` 看校正後的影像，直線應該是直的。
6. 用 `cv2.FileStorage` 存成 `intrinsics_cam{i}.yaml`（K、dist、影像尺寸、RMS）。

#### 1-2 外參標定（把 4 台相機放到同一個世界座標）
ChArUco 有編碼，即使只拍到局部也能知道角點編號（Day03 p29），很適合多相機標定。
1. 定義世界座標：**以 cam0 的相機座標為世界座標**（R0 = I, t0 = 0）。
2. 把標定板放在多台相機都看得到的位置，**4 台同時拍**，換多個位置與角度，拍約 10–20 組。
   （若 4 台同時看到有困難，至少要讓相鄰相機成對看到：0-1、1-2、2-3、3-0。）
3. 每台相機的每張照片用 `cv2.solvePnP()`（輸入角點 3D 座標、2D 座標，以及 1-1 的 K、dist）
   求「標定板 → 相機」的位姿 (R_bi, t_bi)。
4. 同一組中 cam0 與 cam i 都看到板子時：
   `T_0→i = T_board→i · (T_board→0)^-1`，即得 cam i 相對 cam0 的外參。
   多組結果取平均（或取重投影誤差最小的一組）作為初值。
   > 替代作法：相鄰兩台用 `cv2.stereoCalibrate(..., flags=cv2.CALIB_FIX_INTRINSIC)` 再串接；
   > 但誤差會累積傳播（Day06 p3–7）。
5. **Bundle Adjustment 優化**（Day06 p10–14）：以 `scipy.optimize.least_squares`
   同時優化所有相機外參（和各組標定板位姿），最小化所有角點的總重投影誤差，降低串接帶來的誤差傳播。
6. **驗證外參**：
   - 每台相機的重投影誤差（建議 < 1 px）。
   - 由 t 算出相機間距離（基線長），與捲尺實際量測值比對。
   - 用 Open3D 畫出 4 台相機的位置與朝向，確認與實際擺放一致。
7. 存成 `extrinsics.yaml`：每台相機的 R_i、t_i，並計算投影矩陣 **P_i = K_i [R_i | t_i]**。

### 2. 拍攝物品，根據內外參計算物品的 3D 稀疏點雲

#### 2-1 拍攝
1. 相機位置與設定**完全不變**，4 台同時（或物品靜止時逐台）拍攝。
2. 物品要有**豐富紋理**（純色、反光、透明物品很難找到特徵點）。
   可貼紋理貼紙或在背景放報紙，也可以把物品放在 ChArUco 板上一起拍。
3. 光線均勻、避免強烈陰影與反光。

#### 2-2 特徵擷取與配對（Day05、Day06 p22）
1. 每張影像擷取 **SIFT** 特徵（`cv2.SIFT_create()`，比 ORB 穩定，適合稀疏重建）。
   可先用遮罩（mask）只保留物品區域，減少背景雜點。
2. 每一對相鄰相機做配對：`cv2.BFMatcher` / FLANN + **Lowe ratio test（0.7–0.8）**。
3. **用已知外參篩除錯誤配對（比 RANSAC 更準）**：
   由標定結果算出基礎矩陣 `F_ij = K_j^-T [t_ij]× R_ij K_i^-1`，
   丟掉離對極線距離 > 2–3 px 的配對（Day04 對極約束）。
   > 也可另用 `cv2.findFundamentalMat(..., cv2.FM_RANSAC)` 做比較，當成報告內容。

#### 2-3 三角測量
1. 先用 `cv2.undistortPoints(pts, K, dist, P=K)` 對配對點做去畸變。
2. 用 `cv2.triangulatePoints(P_i, P_j, pts_i, pts_j)` 得到齊次座標，除以第 4 維轉成 3D。
3. **篩選點**：
   - 深度為正（點必須在兩台相機前方）。
   - 投影回各相機的重投影誤差 < 2 px。
   - 三角化夾角太小（< 2°）的點不準，刪除。
4. **多視角融合（加分項）**：把同一個特徵在多台相機中的配對串成 track（例如用 union-find），
   被 ≥ 3 台相機看到的點用多視角 DLT 一起三角化，精度更高；可再做一次只優化 3D 點的 BA。
5. 從原影像取像素顏色給每個 3D 點。

#### 2-4 後處理與輸出
1. Open3D `remove_statistical_outlier()` 濾除離群點；也可以用 bounding box 裁掉背景點。
2. 存成 `output/cloud.ply`，並用 `open3d.visualization.draw_geometries()` 同時顯示點雲與相機位置。
3. **驗證尺度**：世界座標的單位就是標定板的單位（mm），
   可以量物品上兩點的實際距離，和點雲中的距離比對。

### 3. 報告建議內容
- 每台相機的內參、畸變係數、重投影誤差（表格）
- 外參：相機擺放示意圖、基線長（計算值 vs 實測值）、BA 前後誤差比較
- 特徵配對數量：原始 → ratio test → 對極約束篩選後
- 點雲截圖（多個角度）、點數、平均重投影誤差
- 遇到的問題與改進（例：缺乏紋理的區域沒有點、反光造成錯誤配對）
- 加分方向：與 COLMAP 結果比較，或嘗試 ORB / SuperPoint 等不同特徵的差異

---

### 附註：若相機擺放方式不同
- **4 台並排、同方向**：流程相同；相鄰相機基線短，深度精度較差，建議拉開間距。
- **只能逐張移動相機拍（非固定架）**：內參仍用步驟 1-1；外參無法事先標定，
  改用 SfM：`findEssentialMat` → `recoverPose` 取得相對位姿，
  再用 `solvePnPRansac` 加入新視角並做 BA；尺度可由畫面中的 ChArUco 板決定。
