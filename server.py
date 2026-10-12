import os
import secrets
import asyncio
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, status, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse, FileResponse, Response, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from config import settings
from task_manager import manager

app = FastAPI(title="Bilibili Remote Downloader Web GUI")
security = HTTPBasic()

def authenticate(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, settings.ADMIN_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, settings.ADMIN_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

template_path = Path(__file__).parent / "templates" / "index.html"

@app.on_event("startup")
async def on_startup():
    loop = asyncio.get_event_loop()
    manager.start(loop)

@app.on_event("shutdown")
async def on_shutdown():
    manager.stop()

@app.get("/")
@app.get("/bili")
@app.get("/bili/")
async def index_page(user: str = Depends(authenticate)):
    if template_path.exists():
        content = template_path.read_text(encoding="utf-8")
        content = content.replace('id="auth-user">admin</span>', f'id="auth-user">{user}</span>')
        return HTMLResponse(content=content)
    return HTMLResponse("<h1>Templates not found</h1>")

class CreateTaskRequest(BaseModel):
    urls: List[str]
    quality: str = "64k" # "64k" or "best"
    min_delay: float = 5.0
    max_delay: float = 15.0
    custom_subfolder: Optional[str] = ""

@app.post("/bili/api/tasks")
async def create_tasks(req: CreateTaskRequest, user: str = Depends(authenticate)):
    if not req.urls:
        raise HTTPException(status_code=400, detail="URLs list cannot be empty")
    
    tasks = manager.add_batch_tasks(
        urls=req.urls,
        quality=req.quality,
        min_delay=req.min_delay,
        max_delay=req.max_delay,
        custom_subfolder=req.custom_subfolder or ""
    )
    return {"status": "success", "count": len(tasks), "tasks": [t.dict() for t in tasks]}

@app.get("/bili/api/tasks")
async def get_tasks(user: str = Depends(authenticate)):
    return {
        "current_task_id": manager.current_task_id,
        "tasks": [t.dict() for t in manager.tasks]
    }

@app.post("/bili/api/tasks/stop")
async def stop_all_tasks(user: str = Depends(authenticate)):
    manager.stop_and_cancel_all()
    return {"status": "success", "message": "All running and queued tasks stopped"}

@app.post("/bili/api/tasks/clear_completed")
async def clear_completed(user: str = Depends(authenticate)):
    manager.clear_completed_tasks()
    return {"status": "success"}

@app.get("/bili/api/history")
async def get_history(user: str = Depends(authenticate)):
    return {"history": [t.dict() for t in reversed(manager.history)]}

@app.post("/bili/api/history/clear")
async def clear_history(user: str = Depends(authenticate)):
    manager.clear_history()
    return {"status": "success"}

class CookieUpdateRequest(BaseModel):
    cookie: str

@app.get("/bili/api/cookie")
async def get_cookie(user: str = Depends(authenticate)):
    cookie_file = Path(settings.COOKIE_FILE)
    if cookie_file.exists():
        content = cookie_file.read_text(encoding="utf-8").strip()
        is_set = len(content) > 0
        preview = (content[:15] + "..." + content[-10:]) if len(content) > 30 else ("已設定" if is_set else "未設定")
        return {"is_set": is_set, "preview": preview}
    return {"is_set": False, "preview": "未設定"}

@app.post("/bili/api/cookie")
async def save_cookie(req: CookieUpdateRequest, user: str = Depends(authenticate)):
    cookie_file = Path(settings.COOKIE_FILE)
    cookie_file.parent.mkdir(parents=True, exist_ok=True)
    cookie_file.write_text(req.cookie.strip(), encoding="utf-8")
    return {"status": "success", "is_set": bool(req.cookie.strip())}

@app.get("/bili/api/logs/{task_id}")
async def get_task_log(task_id: str, user: str = Depends(authenticate)):
    logs_dir = Path(settings.LOGS_DIR)
    # Search for log file starting with task_id
    matching = list(logs_dir.glob(f"{task_id}*.log"))
    if not matching:
        raise HTTPException(status_code=404, detail="Log file not found")
    
    log_file = matching[0]
    return FileResponse(
        str(log_file),
        media_type="text/plain; charset=utf-8",
        filename=f"bilibili_download_{task_id}.log"
    )

@app.get("/bili/api/folders")
async def list_downloaded_folders(user: str = Depends(authenticate)):
    base = Path(settings.STORAGE_BASE_DIR)
    if not base.exists():
        return {"folders": []}
    
    folders = []
    for item in sorted(base.iterdir()):
        if item.is_dir():
            files = list(item.glob("*.m4a"))
            total_size = sum(f.stat().st_size for f in files)
            folders.append({
                "name": item.name,
                "file_count": len(files),
                "size_mb": round(total_size / (1024 * 1024), 2),
                "webdav_url": f"{settings.STORAGE_URL_PREFIX}/{item.name}/",
                "has_log": (item / "download.log").exists()
            })
    return {"folders": folders}

@app.websocket("/bili/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    queue = manager.subscribe()
    try:
        # Send initial snapshot
        await websocket.send_json({
            "type": "queue_updated",
            "data": {"tasks": [t.dict() for t in manager.tasks]}
        })
        await websocket.send_json({
            "type": "history_updated",
            "data": {"history": [t.dict() for t in reversed(manager.history)]}
        })
        
        while True:
            msg = await queue.get()
            await websocket.send_json(msg)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        manager.unsubscribe(queue)
