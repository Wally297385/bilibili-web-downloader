import os
from pathlib import Path
from pydantic_settings import BaseSettings

# 自動判斷 .env 檔案位置 (優先順序: 當前目錄 .env -> 環境變數指定 -> /app/.env)
def get_env_file():
    if Path(".env").exists():
        return ".env"
    if Path("/app/.env").exists():
        return "/app/.env"
    if Path("/srv/bili-web/.env").exists():
        return "/srv/bili-web/.env"
    return None

class Settings(BaseSettings):
    HOST: str = "0.0.0.0"
    PORT: int = 8087
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin123"
    STORAGE_BASE_DIR: str = "./storage"
    LOGS_DIR: str = "./data/logs"
    HISTORY_FILE: str = "./data/history.json"
    COOKIE_FILE: str = "./data/cookie.txt"
    SECRET_KEY: str = "bili-downloader-secret-key-change-me"
    STORAGE_URL_PREFIX: str = "/storage"
    
    class Config:
        env_file = get_env_file()
        extra = "ignore"

settings = Settings()

# 確保基礎資料目錄存在
Path(settings.LOGS_DIR).mkdir(parents=True, exist_ok=True)
Path(settings.STORAGE_BASE_DIR).mkdir(parents=True, exist_ok=True)
