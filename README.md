# 📻 Bilibili 遠端排隊音訊下載器 (Bilibili Remote Audio Downloader)

一款專為 **Bilibili 長篇音訊、有聲小說、多 P 播客** 打造的現代化異步排隊下載器與 Web GUI 控制台。

支援一鍵 Docker 容器化部署、自帶反風控動態簽名算法、支援有聲書超小體積特化與高音質無損串流，並內建完整的 WebSocket 進度推播與歷史紀錄。

---

## ✨ 核心特性

- 🚀 **異步排隊任務隊列**：支援批量輸入多個 BV 號 / av 號 / 分 P 影片 / **b23.tv 短網址（可直接貼整段分享文字）**，後台自動循序排隊下載，不卡死瀏覽器。
- ▶️ **YouTube（yt-dlp）**：非 B 站網址自動改用 yt-dlp，支援單支影片與播放清單；`64k` 轉為 AAC 64kbps M4A，`best` 保留原始音訊（通常為 Opus）不轉碼。映像內含 yt-dlp 所需的 deno。
- 🛡️ **B 站最新 WBI 動態簽名算法**：內建最新 WbiSigner，定時自動向 B 站同步密鑰生成 `w_rid` 與 `wts`，徹底解決雲端伺服器容易遭遇的 `HTTP 412` 反爬風控。
- 🎵 **雙音質模式**：
  - **`64k` (推薦有聲小說)**：極省容量特化（約 36~64 kbps M4A），音質清晰無雜音，平均 1 小時音訊僅約 20MB，極致節省儲存空間與行動串流頻寬。
  - **`best` (音樂與廣播劇)**：抓取最高可用碼率的 DASH 音訊串流。
- 🔍 **音訊完整性核驗**：下載中比對 Content-Length；下載後檢查檔案大小下限、位元率（≥ 16 kbps，讀不到即判定失敗；部分 B 站老影片原始音訊僅約 32 kbps）、**實際時長與來源時長比對**（誤差 > 2 秒或 1% 判定失敗），並以 `ffmpeg` **完整解碼一次**（10 分鐘音訊約 0.4 秒）。下載前也會用同樣規則掃描既有檔案，不合格者自動刪除重抓。
- 📦 **標準封裝與標籤**：B 站 DASH 分段音訊會不轉碼地重新封裝為標準 M4A（moov 前置，播放器可正確顯示時長與拖曳），並寫入標題、專輯、作者、集數；舊版下載的 DASH 檔會在重新執行同一任務時自動升級。
- 🎼 **Hi-Res FLAC**：`best` 模式在帳號有權限（大會員 Cookie）時下載 B 站 Hi-Res 無損 FLAC，否則取最高碼率 AAC。
- ⚡ **即時 WebSocket 推播**：前端採用 TailwindCSS + JetBrains Mono 打造現代暗黑風控制台，進度條、即時日誌與下載狀態即時跳動。
- 🍪 **B 站帳號 Cookie 支援**：支援免登入匿名下載（自動獲取 `buvid3/4` 設備指紋）；亦可在網頁端一鍵貼入個人 Cookie，解鎖大會員高音質與專享分集。
- 🔒 **內建 HTTP Basic Auth**：預設全站密碼防護，防止未授權公網存取；僅在區網 / VPN 內使用時可設 `AUTH_ENABLED=false` 關閉。

---

## 🚀 快速開始 (Docker Compose 推薦)

### 1. 下載專案
```bash
git clone https://github.com/your-repo/bilibili-web-downloader.git
cd bilibili-web-downloader
```

### 2. 建立目錄與設定
複製範本設定檔並修改密碼：
```bash
cp .env.example .env
```
編輯 `.env`（或直接在 `docker-compose.yml` 修改）：
```bash
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your_secure_password_here
```

### 3. 一鍵啟動
```bash
docker compose up -d
```

啟動後，使用瀏覽器打開：
👉 **`http://localhost:8087`**（輸入你設定的帳號與密碼即可登入）

---

## 🛠️ 本地直接運行 (手動開發模式)

如果你希望直接在 Python 環境中執行：

### 系統依賴
本專案依賴 `ffmpeg` 進行音訊分析與處理，請先確保系統已安裝：
- **Ubuntu / Debian**: `sudo apt update && sudo apt install -y ffmpeg`
- **macOS**: `brew install ffmpeg`
- **Windows**: 請下載 `ffmpeg` 並加入系統環境變數 `PATH`。

### 啟動步驟
```bash
# 1. 建立虛擬環境
python3 -m venv venv
source venv/bin/activate  # Windows: .\venv\Scripts\activate

# 2. 安裝依賴
pip install -r requirements.txt

# 3. 啟動服務
uvicorn server:app --host 0.0.0.0 --port 8087 --reload
```

---

## ⚙️ 環境變數設定說明

| 變數名稱 | 預設值 | 說明 |
| :--- | :--- | :--- |
| `HOST` | `0.0.0.0` | 監聽主機位址 |
| `PORT` | `8087` | 監聽連接埠 |
| `ADMIN_USERNAME` | `admin` | Web 登入帳號 |
| `ADMIN_PASSWORD` | `admin123` | Web 登入密碼 (⚠️ 生產環境務必修改) |
| `AUTH_ENABLED` | `true` | `false` 時取消登入（只適合不對公網開放的部署；WebSocket 本身無驗證） |
| `STORAGE_BASE_DIR` | `./storage` | 音訊檔案下載儲存實體路徑 |
| `LOGS_DIR` | `./data/logs` | 任務下載日誌存儲路徑 |
| `HISTORY_FILE` | `./data/history.json` | 歷史記錄持久化檔案 |
| `COOKIE_FILE` | `./data/cookie.txt` | B 站 Cookie 儲存檔案 |
| `STORAGE_URL_PREFIX` | `/storage` | 前端顯示之儲存路徑前綴 |

---

## 🌐 反向代理設定 (Nginx / OpenResty)

如果需要綁定網域名稱並啟用 HTTPS，推薦使用 Nginx 進行反向代理。請注意需開啟 WebSocket 支援：

```nginx
location ^~ /bili {
    proxy_pass http://127.0.0.1:8087; 
    proxy_set_header Host $host; 
    proxy_set_header X-Real-IP $remote_addr; 
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; 
    proxy_set_header Upgrade $http_upgrade; 
    proxy_set_header Connection $http_connection; 
    proxy_set_header X-Forwarded-Proto $scheme; 
    proxy_http_version 1.1; 
    add_header Cache-Control no-cache; 
}
```

---

## 📂 檔案存檔與媒體庫整合

下載的音訊會以獨立資料夾形式儲存於 `STORAGE_BASE_DIR`，命名結構如下：
```
storage/
└── 凡人修仙傳_有聲小說/
    ├── P01_第01回_山邊小村.m4a
    ├── P02_第02回_青牛小鎮.m4a
    ├── P03_第03回_七玄門.m4a
    └── download.log
```

- **Alist / WebDAV 整合**：直接將 `./storage` 資料夾掛載為本機儲存，即可透過手機或串流播放器線上聆聽。
- **Audiobookshelf / Jellyfin 整合**：將 `./storage` 映射至有聲書媒體庫，即可自動刮削並記錄聽書進度。

---

## 💡 使用小秘訣與防風控指南

1. **單集延遲設定**：
   - 預設隨機延遲為 `5.0 ~ 15.0` 秒，此頻率對於絕大部分 B 站分 P 都是安全且不觸發驗證碼的。
   - 如果批量抓取數百集以上的超長有聲書，建議將延遲調高至 `10.0 ~ 20.0` 秒。
2. **斷點續傳機制**：
   - 如果任務中途取消或伺服器重啟，再次貼上同一個 BV 號時，系統會先掃描已下載的檔案，自動跳過校驗通過的分集，僅補抓未完成的集數！

---

## 📄 開源授權

本專案採用 [MIT License](LICENSE) 授權開源。歡迎提交 PR 或 Issue 共同改進！
