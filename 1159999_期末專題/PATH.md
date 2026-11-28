# 環境安裝說明

本文件說明如何從乾淨的系統建立本專題的執行環境：
- **A. 拍攝端**：Raspberry Pi 5（Raspberry Pi OS 64-bit）
- **B. 運算端**：Linux 伺服器（**乾淨的 Ubuntu 26.04，只裝了 NVIDIA 驅動，沒有 sudo 權限**，GPU 為 RTX PRO 5000 Blackwell）
- **C. Windows 桌機**（選用：檢視網頁、備份資料）

每一節最後都有「驗證」，請把驗證輸出貼給 Claude 確認。指令中的 `<...>` 請換成實際值。

> 版本原則：外部工具一律從**官方來源**安裝並記錄版本（見 CLAUDE.md 第 5 節）。
> 撰寫本文件時無法在 Ubuntu 26.04 上實際驗證每個套件名稱；若 `apt` 找不到某個套件，以該工具的官方安裝文件為準，並把實際使用的指令補回本文件。

---

## A. 拍攝端：Raspberry Pi 5

### A1. 系統更新與基本工具
```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git v4l-utils tmux
sudo rpi-eeprom-update            # 顯示 bootloader 版本；有更新時依提示執行 sudo rpi-eeprom-update -a 後重開機
```
- `v4l-utils`：提供 `v4l2-ctl`，程式用它設定與讀回相機控制項。
- `tmux`：拍攝程式在 tmux 裡執行，SSH 斷線時程式不會中止。

### A2. 解除 USB 限流（使用行動電源 PB-Y37 時必做）
PB-Y37 只能提供 5V 3A，Pi 5 協商不到 5A 時會把所有 USB 埠的總輸出限制在 600 mA，2 台相機 + SSD 會不夠。
```bash
tail -5 /boot/firmware/config.txt                     # 確認最後一段是 [all]
grep -q usb_max_current_enable /boot/firmware/config.txt || \
  echo "usb_max_current_enable=1" | sudo tee -a /boot/firmware/config.txt
sudo reboot
# 重開機後
vcgencmd get_config usb_max_current_enable            # 應顯示 usb_max_current_enable=1
vcgencmd get_throttled                                # 應為 throttled=0x0
```

### A3. SSD 掛載（已完成，此處留作紀錄）
SSD 已格式化為 ext4 並以 UUID 掛載在 `/mnt/ssd`。重灌系統時：
```bash
lsblk -o NAME,SIZE,MODEL,TRAN,FSTYPE,MOUNTPOINTS       # 確認 SSD（Extreme 55AE、usb）是哪個裝置
UUID=$(sudo blkid -s UUID -o value /dev/sda1)          # 先確認 sda1 真的是 SSD
sudo mkdir -p /mnt/ssd
echo "UUID=$UUID  /mnt/ssd  ext4  defaults,nofail,noatime  0  2" | sudo tee -a /etc/fstab
sudo systemctl daemon-reload && sudo mount -a
sudo chown -R $USER:$USER /mnt/ssd && mkdir -p /mnt/ssd/3dgs_capture
```

### A4. 取得專案程式
```bash
cd ~
git clone <本倉庫的 URL> cv_course          # 或用 scp / USB 把整個倉庫複製過來
cd ~/cv_course/1159999_期末專題
```
Pi 只會用到 `1159999_期末專題/` 底下的 `common/pi_common.py`、`config/`、`S1/`、`S2/`、`S5/`。

### A5. Python 環境
Pi 上已有 conda（base）。建立獨立環境，避免影響其他程式：
```bash
conda create -n rig python=3.11 -y
conda activate rig
pip install -r requirements_pi.txt
```
沒有 conda 時可改用系統 Python：`python3 -m venv ~/rig && source ~/rig/bin/activate && pip install -r requirements_pi.txt`。

選用（實體按鈕 / 蜂鳴器）：`pip install gpiozero lgpio`，並在 `config/pi.yaml` 的 `gpio` 填 BCM 腳位。

### A6. 釋放資源
Pi 只有 2 GB RAM，拍攝時停用不需要的常駐服務：
```bash
systemctl list-units --type=service --state=running   # 檢視正在執行的服務
docker ps 2>/dev/null                                 # 若有 Docker 容器，拍攝前停止：sudo systemctl stop docker
```

### A7. 網路（筆電連線）
```bash
hostname -I                                           # 查 Pi 的 IP；筆電瀏覽器開 http://<IP>:8000
```
現場沒有 Wi-Fi 時，讓 Pi 開熱點，筆電連上去（Pi 的 IP 通常為 10.42.0.1）：
```bash
sudo nmcli device wifi hotspot ssid rig-pi password <至少 8 碼密碼>
```
也可以用網路線直接連接 Pi 與筆電。

### A8. 驗證
```bash
conda activate rig && cd ~/cv_course/1159999_期末專題
python -c "import cv2, numpy, yaml; print(cv2.__version__, hasattr(cv2, 'aruco'))"   # 版本 >= 4.8、True
lsusb -t                                              # 相機在 Bus 001 / 003（480M），SSD 在 5000M
python S1/set_controls.py --list                      # 讀出兩台相機的控制項
python S1/check_cameras.py --seconds 30               # 2 台同時串流：fps、無效影格、電壓
python S2/check_power.py --minutes 2 --no-cameras     # 供電狀態
```

### A9. 日常拍攝
```bash
tmux new -s rig                                       # SSH 斷線後用 tmux attach -t rig 回來
conda activate rig && cd ~/cv_course/1159999_期末專題
python S5/capture_rig.py --scene book_001 --profile object
# 結束：在終端機按 q；拔電前執行
sudo shutdown -h now
```

---

## B. 運算端：Linux 伺服器（Ubuntu 26.04 + RTX PRO 5000 Blackwell，**沒有 sudo 權限**）

**全部安裝在使用者家目錄，不需要 sudo**：系統套件（編譯器、CMake、CUDA Toolkit、COLMAP…）一律改用 conda 安裝，
不使用 `apt`。需要 root 的事（擴增硬碟、在伺服器上掛載 SSD）只能請管理者處理，或改用其他做法（見 B7、B8）。

安裝順序：確認現況 → conda 環境 → 編譯器與 CUDA Toolkit → PyTorch → gsplat → COLMAP → 專案套件 → 驗證。

### B0. 確認現況
```bash
nvidia-smi                                            # RTX PRO 5000 Blackwell、Driver 595.99
nvidia-smi --query-compute-apps=pid,used_memory --format=csv   # 是否有其他人正在使用 GPU
df -h ~                                               # 家目錄的可用空間（目前約 64 GB）
which git tmux rsync gcc; gcc --version | head -1     # 系統已經有哪些工具
```
**空間估計**（家目錄只有約 64 GB，要先規劃）：

| 項目 | 約略大小 |
|------|---------|
| `gs3d` 環境（PyTorch、CUDA Toolkit、編譯器） | 12–15 GB |
| `colmap` 環境 | 2–4 GB |
| gsplat 原始碼與編譯產物 | 1–2 GB |
| 一個物品場景（原始影像 + COLMAP + 訓練） | 5–10 GB |

安裝完成後用 `conda clean -a -y && pip cache purge` 清掉安裝快取（可省下數 GB）。

### B1. conda 與 Python 環境
伺服器上**已安裝 Miniconda**（家目錄，不需要 sudo），直接使用。系統已有 git、tmux、rsync；系統 GCC 是 15.2（太新，B2 改用 conda 的 GCC 13）。

Ubuntu 26.04 的系統 Python 較新，部分套件可能還沒有預編譯版本，因此建立 **Python 3.12** 環境。
Miniconda 預設使用 Anaconda 的 `defaults` 頻道（新版 conda 可能要求先同意服務條款），本專題**一律使用 conda-forge**：
```bash
conda info --base                                    # Miniconda 的位置（通常是 ~/miniconda3），B5 會用到
conda config --set auto_activate_base false
conda config --add channels conda-forge
conda config --set channel_priority strict
conda create -n gs3d --override-channels -c conda-forge python=3.12 cmake ninja -y
conda activate gs3d
python --version                                     # Python 3.12.x
```
`--override-channels -c conda-forge`：只從 conda-forge 安裝，不碰 `defaults` 頻道。若 conda 仍要求同意服務條款，
表示某個指令用到了 `defaults`，請把該指令補上 `--override-channels -c conda-forge`。

沒有 conda 的機器：從 conda-forge 官方 GitHub Releases 下載 Miniforge（`Miniforge3-Linux-x86_64.sh`），以 `bash <檔名> -b -p ~/miniforge3` 安裝到家目錄。

### B2. 編譯器與 CUDA Toolkit（裝在 conda 環境中）
Blackwell（compute capability 12.0，`sm_120`）需要 **CUDA 12.8 以上**；PyTorch 用 cu128，因此 Toolkit 也用 12.8，讓 gsplat 的編譯環境與 PyTorch 一致。
CUDA 12.8 的 `nvcc` 不支援太新的 GCC，因此同時從 conda-forge 安裝 **GCC 13**，不使用系統的編譯器。
```bash
conda activate gs3d
conda install -y --override-channels -c conda-forge "gcc_linux-64=13" "gxx_linux-64=13"
conda install -y --override-channels -c "nvidia/label/cuda-12.8.1" -c conda-forge cuda-toolkit
mkdir -p $CONDA_PREFIX/etc/conda/activate.d
nano $CONDA_PREFIX/etc/conda/activate.d/gs3d_env.sh        # 貼上下方 6 行後存檔（或用 vim）
conda deactivate && conda activate gs3d
nvcc --version | tail -2                     # release 12.8
$CXX --version | head -1                     # x86_64-conda-linux-gnu-c++ ... 13.x
```
`gs3d_env.sh` 的內容（每次 `conda activate gs3d` 時自動設定）：
```bash
export CUDA_HOME=$CONDA_PREFIX
export CUDAHOSTCXX=$CXX
export CPATH=$CONDA_PREFIX/targets/x86_64-linux/include:${CPATH}
export LIBRARY_PATH=$CONDA_PREFIX/targets/x86_64-linux/lib:${LIBRARY_PATH}
export TORCH_CUDA_ARCH_LIST="12.0"
export MAX_JOBS=8
```
- `CUDAHOSTCXX`：讓 nvcc 使用 conda 的 g++ 13；`TORCH_CUDA_ARCH_LIST="12.0"`：只為 Blackwell 編譯，縮短時間。
- `CPATH` / `LIBRARY_PATH`：讓編譯器找到 conda 版 CUDA 的標頭檔；少了它常見 `cuda_runtime.h: No such file`。
- 驅動 595 支援到 CUDA 13.2，12.8 的程式可以正常執行，**不需要也不能重裝驅動**。
- **替代方案**（conda 的 CUDA 套件有問題時）：從 NVIDIA 官方 CUDA 12.8 下載頁取得 runfile，安裝到家目錄：
  ```bash
  mkdir -p ~/tmp && sh cuda_12.8.*_linux.run --silent --toolkit --toolkitpath=$HOME/cuda-12.8 --tmpdir=$HOME/tmp --override
  ```
  然後把 `gs3d_env.sh` 的 `CUDA_HOME` 改為 `$HOME/cuda-12.8`、刪除 `CPATH` / `LIBRARY_PATH` 兩行，並加上
  `export PATH=$HOME/cuda-12.8/bin:$PATH` 與 `export LD_LIBRARY_PATH=$HOME/cuda-12.8/lib64:${LD_LIBRARY_PATH}`。

### B3. PyTorch（cu128）
```bash
conda activate gs3d
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available(), torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0))"
# 應顯示 ... 12.8 True NVIDIA RTX PRO 5000 Blackwell ... (12, 0)
```

### B4. gsplat（3DGS 訓練，官方：nerfstudio-project/gsplat，Apache-2.0）
```bash
conda activate gs3d
pip install jaxtyping rich
cd ~ && git clone --recursive https://github.com/nerfstudio-project/gsplat.git
cd ~/gsplat
git tag --sort=-creatordate | head -5                 # 選最新的正式版 tag
git checkout <tag> && git submodule update --init --recursive
pip install -e . --no-build-isolation                 # 編譯 CUDA 核心（約 5–15 分鐘）
pip install -r examples/requirements.txt --no-build-isolation
git rev-parse HEAD                                    # 記下 commit，寫進 README 的版本表
```
- `examples/requirements.txt` 會安裝 gsplat 官方指定的相依套件（讀 COLMAP 模型的 pycolmap、fused-ssim 等）。
- 編譯錯誤時先確認：`echo $CUDA_HOME $CUDAHOSTCXX $TORCH_CUDA_ARCH_LIST` 都有值、`nvcc --version` 是 12.8。
- 訓練程式路徑 `~/gsplat/examples/simple_trainer.py` 與 `config/pipeline.yaml` 的 `trainer.gsplat_dir` 一致。

### B5. COLMAP（SfM，官方：colmap/colmap，BSD）
需要 **3.12 以上**（rig 功能）。沒有 sudo 無法用 `apt` 裝編譯所需的函式庫，因此**使用 conda-forge 預編譯的 CUDA 版**，
裝在獨立環境 `colmap`（避免與 PyTorch 的 CUDA 函式庫互相干擾）。
```bash
conda create -n colmap --override-channels -c conda-forge "colmap=*=*cuda*" -y
$(conda info --base)/envs/colmap/bin/colmap -h | head -3      # 版本 >= 3.12、第一行有 "with CUDA"
```
`config/pipeline.yaml` 的 `colmap.bin` 預設為 `~/miniconda3/envs/colmap/bin/colmap`；`conda info --base` 不是 `~/miniconda3` 時要修改。

- **GPU 特徵擷取在 Blackwell 上不能用時**（預編譯版可能沒有針對 `sm_120` 編譯）：S8 會改用 CPU 版 SIFT。
  書只有約 200 張影像，Ryzen 9 9950X 跑 CPU 版也只要幾分鐘，對結果沒有影響。
- **進階：自行編譯**（需要 GPU 版 SIFT 或特定版本時）：所有相依套件從 conda-forge 安裝到一個環境，再以該環境的編譯器編譯：
  ```bash
  conda create -n colmap-build -c conda-forge -y cmake ninja "gxx_linux-64=13" boost-cpp eigen ceres-solver \
    cgal-cpp flann freeimage glog gflags metis sqlite glew suitesparse libcurl openssl
  conda activate colmap-build && conda install -y -c "nvidia/label/cuda-12.8.1" cuda-toolkit
  git clone https://github.com/colmap/colmap.git ~/colmap && cd ~/colmap && git checkout <最新正式版 tag>
  cmake -S . -B build -GNinja -DCMAKE_BUILD_TYPE=Release -DGUI_ENABLED=OFF -DCMAKE_CUDA_ARCHITECTURES=120 \
    -DCMAKE_PREFIX_PATH=$CONDA_PREFIX -DCMAKE_INSTALL_PREFIX=$HOME/.local
  cmake --build build && cmake --install build          # 裝到 ~/.local/bin/colmap
  ```
  CMake 回報缺少套件時，依訊息從 conda-forge 補裝（套件名稱以 COLMAP 官方 Installation 文件為準）；
  完成後把 `config/pipeline.yaml` 的 `colmap.bin` 改為 `~/.local/bin/colmap`。

### B6. 專案程式與套件
```bash
conda activate gs3d
cd ~ && git clone <本倉庫的 URL> cv_course && cd ~/cv_course/1159999_期末專題
pip install -r requirements.txt
# gsplat 的範例套件可能裝了 opencv-python，與本專題的 opencv-contrib-python-headless 衝突（都提供 cv2）
pip uninstall -y opencv-python opencv-python-headless opencv-contrib-python
pip install --force-reinstall --no-deps "opencv-contrib-python-headless>=4.10.0.84,<5"
conda clean -a -y && pip cache purge                 # 清除安裝快取，節省家目錄空間
```
- 伺服器沒有螢幕，使用 **headless** 版 OpenCV（不需要系統的 libGL；`apt install libgl1` 需要 sudo）。
- `config/pipeline.yaml` 的 `python` 預設是 `python`（在 `gs3d` 環境中執行即可）；在 tmux 中執行時，也要先 `conda activate gs3d`。

### B7. 取得拍攝資料（伺服器上不能掛載 SSD）
在伺服器上掛載 USB 硬碟需要 root，因此**以網路傳輸**：
- **Pi 直接傳到伺服器**（Pi 與伺服器網路互通時，最簡單）：在 Pi 上執行
  ```bash
  rsync -avP /mnt/ssd/3dgs_capture/book_001/ <伺服器帳號>@<伺服器 IP>:~/capture_in/book_001/
  ```
- **經筆電轉傳**（Pi 連不到伺服器時）：筆電先 `scp -r <pi>:/mnt/ssd/3dgs_capture/book_001 .`，再 `scp -r book_001 <伺服器>:~/capture_in/`。
- 傳完後在伺服器上以 `S6/ingest.py --source ~/capture_in/book_001/<session>` 匯入；S6 會用 SHA-256 檢查檔案是否完整。

### B8. 硬碟空間
目前家目錄可用約 64 GB，扣掉 B0 估計的環境大小後，只剩約 40 GB 給資料與輸出。
- 請管理者擴增家目錄空間，或提供另一個可寫入的大容量位置（例如 `/data/<帳號>`）；建議總共 ≥ 200 GB。
- 有可寫入的大容量位置時，把專題的 `data/`、`output/` 改成符號連結（不需要 sudo）：
  ```bash
  mkdir -p /data/<帳號>/gs3d/{data,output}
  ln -s /data/<帳號>/gs3d/data   ~/cv_course/1159999_期末專題/data
  ln -s /data/<帳號>/gs3d/output ~/cv_course/1159999_期末專題/output
  ```
- 隨時可清：`conda clean -a -y`、`pip cache purge`、`~/gsplat/build/`、測試用的資料集。

### B9. 與其他使用者共用 GPU
- 執行前用 `nvidia-smi` 看是否有其他人在用；訓練會長時間佔用 GPU，可先與其他使用者協調時段。
- 長時間的工作在 `tmux` 中執行（`tmux new -s gs3d`），SSH 斷線後用 `tmux attach -t gs3d` 回來。

### B10. 驗證
```bash
conda activate gs3d && cd ~/cv_course/1159999_期末專題
nvcc --version | tail -2
python -c "import torch, gsplat, cv2; print(torch.__version__, torch.version.cuda, torch.cuda.get_device_capability(0), gsplat.__version__, cv2.__version__, hasattr(cv2,'aruco'))"
$(conda info --base)/envs/colmap/bin/colmap -h | head -3
python S1/check_env.py                                # 一次檢查全部項目（S1 驗證用），結束碼 0 = 通過
```
選用：以官方資料集測試完整訓練（Mip-NeRF 360 約十多 GB，**家目錄空間不足時跳過**，或測試完立刻刪除）：
```bash
cd ~/gsplat/examples
python datasets/download_dataset.py                   # 下載到 examples/data/360_v2
python simple_trainer.py default --data_dir data/360_v2/room --data_factor 4 --result_dir results/room --disable_viewer
```
請把 B10 的輸出貼給 Claude。

---

## C. Windows 桌機（選用）

- **檢視網頁模型**：在 `1159999_期末專題/web/` 執行 `python -m http.server 8080`，瀏覽器開 `http://localhost:8080/?scene=book_001`
  （網頁檢視器需要以 http 開啟，直接雙擊 html 會因瀏覽器安全限制讀不到模型）。
- **手動清理模型**：瀏覽器開 SuperSplat（`https://superspl.at/editor`，PlayCanvas 官方），拖入 `cleaned.ply` 檢查與刪除雜點。
- **從伺服器備份資料**（Windows 10 / 11 內建 `scp`）：
  ```powershell
  scp -r <使用者>@<伺服器 IP>:~/cv_course/1159999_期末專題/output/14_export D:\gs3d_backup\
  ```
- SSD 是 ext4，Windows 無法直接讀取，請經 Pi 以網路傳輸（`scp`）。
