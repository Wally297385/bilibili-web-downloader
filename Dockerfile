FROM python:3.12-slim

# 安裝 ffmpeg (供音訊完整性校驗與處理使用) 與 ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先複製依賴文件利用 Docker 層快取
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製應用程序所有代碼與靜態資源
COPY . .

# 預先建立預設數據目錄與儲存目錄
RUN mkdir -p /app/data/logs /app/storage

EXPOSE 8087

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8087"]
