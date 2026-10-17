import os
import sys
import time
import json
import uuid
import asyncio
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any, Set
from pydantic import BaseModel, Field

from config import settings
from bili_engine import BiliDownloadEngine, is_bilibili_input
from ytdlp_engine import YtDlpEngine

class TaskItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    url: str
    quality: str = "64k"
    min_delay: float = 5.0
    max_delay: float = 15.0
    custom_subfolder: str = ""
    status: str = "queued" # queued, running, completed, failed, cancelled
    title: str = "等待解析..."
    current: int = 0
    total: int = 0
    percent: float = 0.0
    current_msg: str = "排隊中"
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    log_file: Optional[str] = None
    target_dir: Optional[str] = None
    webdav_path: Optional[str] = None
    error_msg: Optional[str] = None

class TaskManager:
    def __init__(self):
        self.tasks: List[TaskItem] = []
        self.history: List[TaskItem] = []
        self.current_task_id: Optional[str] = None
        self.is_running: bool = False
        self.cancel_current_flag: bool = False
        self.subscribers: Set[asyncio.Queue] = set()
        self.worker_task: Optional[asyncio.Task] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.engine = BiliDownloadEngine(
            storage_base_dir=settings.STORAGE_BASE_DIR,
            cookie_file=settings.COOKIE_FILE
        )
        self.ytdlp_engine = YtDlpEngine(
            storage_base_dir=settings.STORAGE_BASE_DIR,
            verify=self.engine.verify_audio_file
        )
        self.load_history()

    def start(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.is_running = True
        self.worker_task = self.loop.create_task(self._worker_loop())

    def stop(self):
        self.is_running = False
        self.cancel_current_flag = True
        if self.worker_task:
            self.worker_task.cancel()

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue()
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self.subscribers.discard(q)

    def broadcast_sync(self, message: Dict[str, Any]):
        if not self.loop or self.loop.is_closed():
            return
        
        async def _send():
            for q in list(self.subscribers):
                try:
                    await q.put(message)
                except Exception:
                    pass
        
        asyncio.run_coroutine_threadsafe(_send(), self.loop)

    def load_history(self):
        h_file = Path(settings.HISTORY_FILE)
        if h_file.exists():
            try:
                data = json.loads(h_file.read_text(encoding="utf-8"))
                self.history = [TaskItem(**item) for item in data]
            except Exception as e:
                print(f"Error loading history: {e}")
                self.history = []

    def save_history(self):
        h_file = Path(settings.HISTORY_FILE)
        h_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = [t.dict() for t in self.history]
            h_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"Error saving history: {e}")

    def add_batch_tasks(
        self,
        urls: List[str],
        quality: str = "64k",
        min_delay: float = 5.0,
        max_delay: float = 15.0,
        custom_subfolder: str = ""
    ) -> List[TaskItem]:
        created = []
        for url in urls:
            url = url.strip()
            if not url:
                continue
            task = TaskItem(
                url=url,
                quality=quality,
                min_delay=min_delay,
                max_delay=max_delay,
                custom_subfolder=custom_subfolder
            )
            self.tasks.append(task)
            created.append(task)
            
        self.broadcast_sync({
            "type": "queue_updated",
            "data": {"tasks": [t.dict() for t in self.tasks]}
        })
        return created

    def stop_and_cancel_all(self):
        self.cancel_current_flag = True
        for t in self.tasks:
            if t.status in ["queued", "running"]:
                t.status = "cancelled"
                t.completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                t.current_msg = "已手動取消"
                self.history.append(t)
        self.tasks.clear()
        self.current_task_id = None
        self.save_history()
        
        self.broadcast_sync({
            "type": "queue_updated",
            "data": {"tasks": []}
        })
        self.broadcast_sync({
            "type": "history_updated",
            "data": {"history": [t.dict() for t in reversed(self.history)]}
        })
        self.broadcast_sync({
            "type": "log_line",
            "data": {"task_id": "SYSTEM", "line": "⚠️ 已停止所有正在執行與排隊中的任務。"}
        })

    def clear_completed_tasks(self):
        self.tasks = [t for t in self.tasks if t.status in ["queued", "running"]]
        self.broadcast_sync({
            "type": "queue_updated",
            "data": {"tasks": [t.dict() for t in self.tasks]}
        })

    def clear_history(self):
        self.history.clear()
        self.save_history()
        self.broadcast_sync({
            "type": "history_updated",
            "data": {"history": []}
        })

    async def _worker_loop(self):
        while self.is_running:
            queued_tasks = [t for t in self.tasks if t.status == "queued"]
            if not queued_tasks:
                self.current_task_id = None
                await asyncio.sleep(1)
                continue

            task = queued_tasks[0]
            self.current_task_id = task.id
            self.cancel_current_flag = False
            task.status = "running"
            task.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            task.current_msg = "正在解析與下載..."

            # Setup log file
            logs_dir = Path(settings.LOGS_DIR)
            logs_dir.mkdir(parents=True, exist_ok=True)
            log_path = logs_dir / f"{task.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            task.log_file = str(log_path)

            self.broadcast_sync({
                "type": "task_started",
                "data": {"task": task.dict()}
            })
            self.broadcast_sync({
                "type": "queue_updated",
                "data": {"tasks": [t.dict() for t in self.tasks]}
            })

            log_file_handle = open(log_path, "w", encoding="utf-8")

            def log_callback(msg: str):
                ts = datetime.now().strftime("%H:%M:%S")
                line = f"[{ts}] {msg}"
                log_file_handle.write(line + "\n")
                log_file_handle.flush()
                self.broadcast_sync({
                    "type": "log_line",
                    "data": {"task_id": task.id, "line": line}
                })

            def progress_callback(curr: int, tot: int, pct: float, msg: str):
                task.current = curr
                task.total = tot
                task.percent = round(pct, 1)
                task.current_msg = msg
                self.broadcast_sync({
                    "type": "task_progress",
                    "data": {"task": task.dict()}
                })

            def check_cancelled():
                return self.cancel_current_flag

            # Execute download in worker thread
            try:
                engine = self.engine if is_bilibili_input(task.url) else self.ytdlp_engine
                res = await asyncio.to_thread(
                    engine.execute_task,
                    task_id=task.id,
                    target_url=task.url,
                    quality=task.quality,
                    min_delay=task.min_delay,
                    max_delay=task.max_delay,
                    custom_subfolder=task.custom_subfolder,
                    log_callback=log_callback,
                    progress_callback=progress_callback,
                    check_cancelled=check_cancelled
                )
                
                task.title = res["title"]
                task.target_dir = res["target_dir"]
                task.webdav_path = f"{settings.STORAGE_URL_PREFIX}/{res['subfolder']}/"
                
                # Copy log to target directory
                try:
                    target_log = Path(res["target_dir"]) / "download.log"
                    target_log.write_text(Path(log_path).read_text(encoding="utf-8"), encoding="utf-8")
                except Exception:
                    pass

                if self.cancel_current_flag:
                    task.status = "cancelled"
                    task.current_msg = "已手動取消"
                elif res.get("completed", False):
                    task.status = "completed"
                    task.percent = 100.0
                    task.current_msg = f"下載完成 ({res['success_count']}/{res['total_pages']} 集)"
                else:
                    task.status = "completed" if res['success_count'] > 0 else "failed"
                    task.current_msg = f"部分完成 ({res['success_count']}/{res['total_pages']} 集, {res['failed_count']} 失敗)"

            except Exception as e:
                task.status = "failed"
                task.error_msg = str(e)
                task.current_msg = f"錯誤: {e}"
                log_callback(f"❌ 發生異常錯誤: {e}")
            finally:
                log_file_handle.close()
                task.completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                # Move to history
                if task in self.tasks:
                    self.tasks.remove(task)
                self.history.append(task)
                self.current_task_id = None
                self.save_history()

                self.broadcast_sync({
                    "type": "task_completed",
                    "data": {"task": task.dict()}
                })
                self.broadcast_sync({
                    "type": "queue_updated",
                    "data": {"tasks": [t.dict() for t in self.tasks]}
                })
                self.broadcast_sync({
                    "type": "history_updated",
                    "data": {"history": [t.dict() for t in reversed(self.history)]}
                })

            await asyncio.sleep(1)

manager = TaskManager()
