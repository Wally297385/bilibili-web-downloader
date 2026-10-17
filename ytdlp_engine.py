import os
import glob
import random
import subprocess
import time
from typing import Callable, Optional, Dict, Any, List

import yt_dlp

from bili_engine import sanitize_filename, finalize_audio, set_shared_perms

AUDIO_EXTS = ('.m4a', '.opus', '.ogg', '.mp3', '.aac', '.flac', '.webm')


class _Cancelled(Exception):
    pass


class YtDlpEngine:
    """Audio downloads for YouTube (and other sites yt-dlp supports), same task contract as BiliDownloadEngine.

    Output layout matches the Bilibili engine: <storage>/<playlist or video title>/P01_<title>.<ext>
      64k  -> re-encoded to AAC 64 kbps .m4a (audiobook size)
      best -> best audio stream kept as-is (no re-encode; usually .opus or .m4a)
    """

    def __init__(self, storage_base_dir: str, verify: Callable[[str, float], tuple]):
        self.storage_base_dir = storage_base_dir
        self.verify = verify

    @staticmethod
    def _base_opts() -> Dict[str, Any]:
        return {
            'quiet': True,
            'no_warnings': True,
            'noprogress': True,
            'socket_timeout': 30,
            'retries': 3,
            'fragment_retries': 3,
        }

    def _list_entries(self, url: str) -> tuple:
        with yt_dlp.YoutubeDL({**self._base_opts(), 'extract_flat': 'in_playlist'}) as ydl:
            info = ydl.extract_info(url, download=False)
        if info.get('_type') in ('playlist', 'multi_video'):
            entries = [e for e in (info.get('entries') or []) if e]
        else:
            entries = [info]
        return info.get('title') or info.get('id') or 'audio', entries

    @staticmethod
    def _entry_url(entry: Dict[str, Any]) -> str:
        return entry.get('webpage_url') or entry.get('url') or entry.get('id')

    @staticmethod
    def _existing(base_path: str) -> Optional[str]:
        for f in glob.glob(glob.escape(base_path) + '.*'):
            if f.lower().endswith(AUDIO_EXTS):
                return f
        return None

    def execute_task(
        self,
        task_id: str,
        target_url: str,
        quality: str = "64k",
        min_delay: float = 5.0,
        max_delay: float = 15.0,
        custom_subfolder: str = "",
        log_callback: Optional[Callable[[str], None]] = None,
        progress_callback: Optional[Callable[[int, int, float, str], None]] = None,
        check_cancelled: Optional[Callable[[], bool]] = None
    ) -> Dict[str, Any]:

        def emit_log(msg: str):
            if log_callback:
                log_callback(msg)
            print(f"[{task_id}] {msg}", flush=True)

        def cancelled() -> bool:
            return bool(check_cancelled and check_cancelled())

        url = target_url.strip()
        emit_log(f"正在以 yt-dlp 解析: {url} ...")
        raw_title, entries = self._list_entries(url)
        total = len(entries)
        if total == 0:
            raise RuntimeError("沒有可下載的項目（播放清單為空或影片不可用）")

        clean_title = custom_subfolder if custom_subfolder else sanitize_filename(raw_title)
        target_dir = os.path.join(self.storage_base_dir, clean_title)
        os.makedirs(target_dir, exist_ok=True)
        set_shared_perms(target_dir)

        emit_log("==================================================")
        emit_log(f"標題: {raw_title}")
        emit_log(f"總集數: {total} 集")
        emit_log(f"存儲目錄: {target_dir}")
        emit_log(f"下載音質: {'AAC 64kbps (M4A)' if quality == '64k' else '最高音質原始串流（不轉碼）'}")
        emit_log(f"間隔延遲: {min_delay}~{max_delay} 秒")
        emit_log("==================================================")

        success = 0
        failed: List[int] = []
        downloaded_any = False

        for idx, entry in enumerate(entries, 1):
            if cancelled():
                emit_log("任務已被使用者手動取消。")
                break

            title = sanitize_filename(entry.get('title') or entry.get('id') or f'item{idx}')
            expected_dur = float(entry.get('duration') or 0)
            base = os.path.join(target_dir, f"P{idx:02d}_{title}")
            tag = f"[{idx:02d}/{total}]"

            existing = self._existing(base)
            if existing:
                ok, reason = self.verify(existing, expected_dur)
                if ok:
                    success += 1
                    emit_log(f"{tag} 已存在且驗證通過，略過: {os.path.basename(existing)}")
                    continue
                emit_log(f"{tag} 既有檔案驗證失敗（{reason}），重新下載")
                os.remove(existing)

            if downloaded_any:
                delay = random.uniform(min_delay, max_delay)
                emit_log(f"緩衝休眠 {delay:.1f} 秒...")
                time.sleep(delay)
                if cancelled():
                    emit_log("任務已被使用者手動取消。")
                    break

            tags = {
                'title': entry.get('title') or title,
                'album': raw_title,
                'artist': entry.get('channel') or entry.get('uploader') or '',
                'track': f"{idx}/{total}",
                'comment': self._entry_url(entry),
            }
            emit_log(f"{tag} 開始處理: {title}" + (f" (時長: {expected_dur/60:.1f} 分鐘)" if expected_dur else ""))
            ok = False
            for attempt in range(1, 4):
                try:
                    path = self._download_one(self._entry_url(entry), base, quality, cancelled, tags)
                    downloaded_any = True
                    valid, reason = self.verify(path, expected_dur)
                    if not valid:
                        os.remove(path)
                        raise RuntimeError(f"完整性檢驗未通過: {reason}")
                    size_mb = os.path.getsize(path) / (1024 * 1024)
                    emit_log(f"{tag} 下載並驗證成功 -> {os.path.basename(path)} ({size_mb:.2f} MB)")
                    ok = True
                    break
                except _Cancelled:
                    break
                except Exception as e:
                    emit_log(f"{tag} 第 {attempt}/3 次嘗試失敗: {e}")
                    for f in glob.glob(glob.escape(base) + '.*'):
                        try:
                            os.remove(f)
                        except OSError:
                            pass
                    time.sleep(attempt * 2)

            if ok:
                success += 1
            elif not cancelled():
                emit_log(f"{tag} 重試次數耗盡，此集下載失敗")
                failed.append(idx)

            if progress_callback:
                progress_callback(success, total, success / total * 100.0, f"進度 {success}/{total}")

        emit_log("\n================ 任務總結 ================")
        emit_log(f"總集數: {total}")
        emit_log(f"成功完成: {success}/{total}")
        emit_log(f"失敗分集: {failed if failed else '無 (0 失敗)'}")
        emit_log(f"存儲目錄: {target_dir}")
        emit_log("==========================================")

        return {
            "title": raw_title,
            "subfolder": clean_title,
            "target_dir": target_dir,
            "total_pages": total,
            "success_count": success,
            "failed_count": len(failed),
            "failed_episodes": failed,
            "completed": success == total,
        }

    def _download_one(self, url: str, base: str, quality: str, cancelled: Callable[[], bool],
                      tags: Dict[str, str]) -> str:
        def hook(_d):
            if cancelled():
                raise _Cancelled()

        opts = {
            **self._base_opts(),
            'format': 'bestaudio[ext=m4a]/bestaudio/best' if quality == '64k' else 'bestaudio/best',
            'outtmpl': base + '.src.%(ext)s',
            'progress_hooks': [hook],
            'noplaylist': True,
        }
        if quality != '64k':
            # keep the original codec, only drop video/container overhead
            opts['outtmpl'] = base + '.%(ext)s'
            opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'best'}]

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        if quality != '64k':
            path = self._existing(base)
            if not path:
                raise RuntimeError("yt-dlp 未產生音訊檔")
            finalize_audio(path, path, tags)  # tags + standard container, no re-encode
            return path

        src = next(iter(glob.glob(glob.escape(base) + '.src.*')), None)
        if not src:
            raise RuntimeError("yt-dlp 未產生音訊檔")
        out = base + '.m4a'
        tmp = base + '.tmp.m4a'
        cmd = ['ffmpeg', '-v', 'error', '-y', '-i', src, '-map', '0:a:0', '-vn', '-c:a', 'aac', '-b:a', '64k',
               '-map_metadata', '-1']
        for k, v in tags.items():
            if v:
                cmd += ['-metadata', f'{k}={v}']
        cmd += ['-movflags', '+faststart', tmp]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        os.remove(src)
        if res.returncode != 0:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise RuntimeError(f"ffmpeg 轉碼失敗: {res.stderr.strip()[-300:]}")
        os.replace(tmp, out)
        set_shared_perms(out)
        return out
