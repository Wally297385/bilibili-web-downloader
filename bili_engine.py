import os
import re
import sys
import time
import json
import random
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import Callable, Optional, Dict, Any, List, Tuple

def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', '_', name)
    return name.strip()

def extract_bvid(url_or_str: str) -> Optional[str]:
    url_or_str = url_or_str.strip()
    match = re.search(r'(BV[a-zA-Z0-9]{10})', url_or_str, re.IGNORECASE)
    if match:
        return match.group(1)
    
    match_av = re.search(r'av(\d+)', url_or_str, re.IGNORECASE)
    if match_av:
        return f"av{match_av.group(1)}"
    
    return None

# Bilibili share links: b23.tv/xxxx (also bili2233.cn), often pasted with the share text around them
SHORT_URL_RE = re.compile(r'(?:https?://)?(?:b23\.tv|bili2233\.cn)/[A-Za-z0-9]+', re.IGNORECASE)

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def resolve_short_url(url_or_str: str, max_hops: int = 3) -> str:
    """Expand a b23.tv short link to its bilibili.com URL; other input is returned unchanged."""
    match = SHORT_URL_RE.search(url_or_str)
    if not match:
        return url_or_str.strip()
    url = match.group(0)
    if not url.lower().startswith('http'):
        url = 'https://' + url
    opener = urllib.request.build_opener(_NoRedirect)
    for _ in range(max_hops):
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with opener.open(req, timeout=10):
                return url
        except urllib.error.HTTPError as e:
            location = e.headers.get('Location')
            if e.code in (301, 302, 303, 307, 308) and location:
                url = urllib.parse.urljoin(url, location)
                if 'bilibili.com' in url:
                    return url
                continue
            raise RuntimeError(f"短網址解析失敗 ({url}): HTTP {e.code}")
    return url

def is_bilibili_input(url_or_str: str) -> bool:
    """Bilibili links/IDs go to the native engine; any other http(s) URL goes to yt-dlp."""
    s = url_or_str.strip()
    if SHORT_URL_RE.search(s) or re.search(r'bilibili\.com', s, re.IGNORECASE):
        return True
    if re.search(r'https?://', s, re.IGNORECASE):
        return False
    return extract_bvid(s) is not None

import urllib.parse
from functools import reduce
from hashlib import md5

mixinKeyEncTab = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52
]

class WbiSigner:
    def __init__(self):
        self.img_key = None
        self.sub_key = None
        self.mixin_key = None
        self.last_update = 0

    def get_mixin_key(self, orig: str):
        return reduce(lambda s, i: s + orig[i], mixinKeyEncTab, '')[:32]

    def update_keys(self, headers: Dict[str, str]):
        now = time.time()
        if self.mixin_key and (now - self.last_update < 3600 * 6):
            return
        try:
            req = urllib.request.Request('https://api.bilibili.com/x/web-interface/nav', headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                wbi_img = data.get('data', {}).get('wbi_img', {})
                self.img_key = wbi_img.get('img_url', '').split('/')[-1].split('.')[0]
                self.sub_key = wbi_img.get('sub_url', '').split('/')[-1].split('.')[0]
                if self.img_key and self.sub_key:
                    self.mixin_key = self.get_mixin_key(self.img_key + self.sub_key)
                    self.last_update = now
        except Exception as e:
            # Fallback to last known keys if nav fails
            if not self.mixin_key:
                # Default fallback keys
                self.mixin_key = self.get_mixin_key("7cd084941338484aae1ad9425b84077c4932caff0ff746eab6f01bf08b70ac45")

    def sign(self, params: dict, headers: dict) -> dict:
        self.update_keys(headers)
        params['wts'] = round(time.time())
        # Filter special characters
        params = {k: ''.join(filter(lambda c: c not in "!*'()", str(v))) for k, v in params.items()}
        params = dict(sorted(params.items()))
        query = urllib.parse.urlencode(params)
        w_rid = md5((query + self.mixin_key).encode()).hexdigest()
        params['w_rid'] = w_rid
        return params

class BiliDownloadEngine:
    def __init__(self, storage_base_dir: str, cookie_file: str = ""):
        self.storage_base_dir = storage_base_dir
        self.cookie_file = cookie_file
        self.wbi_signer = WbiSigner()
        self.anon_cookie = ""
        self.anon_cookie_time = 0

    def get_anonymous_cookie(self) -> str:
        now = time.time()
        if self.anon_cookie and (now - self.anon_cookie_time < 3600 * 12):
            return self.anon_cookie
        try:
            req = urllib.request.Request(
                'https://api.bilibili.com/x/frontend/finger/spi',
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                b_3 = data.get('data', {}).get('b_3', '')
                b_4 = data.get('data', {}).get('b_4', '')
                if b_3 or b_4:
                    self.anon_cookie = f"buvid3={b_3}; buvid4={b_4}"
                    self.anon_cookie_time = now
                    return self.anon_cookie
        except Exception:
            pass
        return ""

    def get_cookie_header(self) -> str:
        if self.cookie_file and os.path.exists(self.cookie_file):
            try:
                with open(self.cookie_file, 'r', encoding='utf-8') as f:
                    c = f.read().strip()
                    if c:
                        return c
            except Exception:
                pass
        # If no user cookie, return anonymous buvid cookie
        return self.get_anonymous_cookie()

    def get_headers(self, bvid: str = "") -> Dict[str, str]:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
            'Referer': f'https://www.bilibili.com/video/{bvid}/' if bvid else 'https://www.bilibili.com/'
        }
        cookie = self.get_cookie_header()
        if cookie:
            headers['Cookie'] = cookie
        return headers

    @staticmethod
    def _id_params(bvid: str, key: str = 'aid') -> Dict[str, Any]:
        # av numbers can't be sent as `bvid`: the view API calls them `aid`, playurl calls them `avid`
        if bvid.lower().startswith('av'):
            return {key: int(bvid[2:])}
        return {'bvid': bvid}

    def get_video_info(self, bvid: str) -> Dict[str, Any]:
        headers = self.get_headers(bvid)
        id_params = self._id_params(bvid)

        # 1. Try WBI signed view API (Required by Bilibili to avoid HTTP 412)
        try:
            params = self.wbi_signer.sign(dict(id_params), headers)
            url = 'https://api.bilibili.com/x/web-interface/wbi/view?' + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if data.get('code') == 0 and data.get('data'):
                    return data['data']
        except Exception:
            # If WBI fails, try fallback
            pass

        # 2. Fallback to standard view API
        url = "https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode(id_params)
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data.get('code') != 0:
                raise RuntimeError(f"Bilibili API 錯誤: {data.get('message', '未知錯誤')} (代碼: {data.get('code')})")
            return data['data']

    def get_audio_stream(self, bvid: str, cid: int, quality: str = "64k") -> Tuple[List[str], str]:
        """Return (candidate URLs, output extension). `best` prefers Hi-Res FLAC when the account can get it."""
        headers = self.get_headers(bvid)
        # fnval 16 = DASH; 4048 = every DASH feature, which is what returns dash.flac (Hi-Res, needs a VIP cookie)
        fnval = 16 if quality == "64k" else 4048
        base_params = {**self._id_params(bvid, 'avid'), 'cid': cid, 'fnval': fnval, 'fnver': 0, 'fourk': 1, 'qn': 64}
        data = None

        # 1. Try WBI signed playurl
        try:
            params = self.wbi_signer.sign(dict(base_params), headers)
            url = 'https://api.bilibili.com/x/player/wbi/playurl?' + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                res = json.loads(resp.read().decode('utf-8'))
                if res.get('code') == 0:
                    data = res
        except Exception:
            pass

        # 2. Fallback to standard playurl
        if not data:
            url = "https://api.bilibili.com/x/player/playurl?" + urllib.parse.urlencode(base_params)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))

        if not data or data.get('code') != 0:
            err_msg = data.get('message') if data else "未知錯誤"
            raise RuntimeError(f"PlayURL 錯誤 (cid: {cid}): {err_msg}")

        dash = data.get('data', {}).get('dash') or {}
        audios = dash.get('audio') or []
        flac = ((dash.get('flac') or {}).get('audio')) if quality != "64k" else None

        ext = ".m4a"
        if flac and flac.get('baseUrl'):
            selected, ext = flac, ".flac"
        elif not audios:
            raise RuntimeError(f"未找到可用音訊串流 (cid: {cid})")
        elif quality == "64k":
            selected = next((a for a in audios if a.get('id') == 30216), None)
            if not selected:
                selected = min(audios, key=lambda x: x.get('bandwidth', 9999999))
        else:  # "best" without FLAC
            selected = max(audios, key=lambda x: x.get('bandwidth', 0))

        urls = [selected['baseUrl']]
        if selected.get('backupUrl'):
            urls.extend(selected['backupUrl'])
        return urls, ext

    def get_audio_stream_urls(self, bvid: str, cid: int, quality: str = "64k") -> List[str]:
        return self.get_audio_stream(bvid, cid, quality)[0]

    def verify_audio_file(self, filepath: str, expected_duration: float) -> Tuple[bool, str]:
        return verify_audio_file(filepath, expected_duration)

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

        resolved_url = resolve_short_url(target_url)
        if resolved_url != target_url.strip():
            emit_log(f"短網址已解析: {resolved_url.split('?')[0]}")
        bvid = extract_bvid(resolved_url)
        if not bvid:
            raise ValueError(f"無法從輸入中辨識有效的 Bilibili BV號或網址: {target_url}")

        emit_log(f"正在解析 Bilibili 資訊: {bvid} ...")
        info = self.get_video_info(bvid)
        raw_title = info.get('title', 'bilibili_audio')
        uploader = (info.get('owner') or {}).get('name', '')
        clean_title = custom_subfolder if custom_subfolder else sanitize_filename(raw_title)
        pages = info.get('pages', [])
        total_pages = len(pages)

        if total_pages == 0:
            raise RuntimeError(f"該影片沒有可用的分 P 內容 (pages is empty)")

        target_dir = os.path.join(self.storage_base_dir, clean_title)
        os.makedirs(target_dir, exist_ok=True)
        set_shared_perms(target_dir)

        emit_log(f"==================================================")
        emit_log(f"標題: {raw_title}")
        emit_log(f"總集數: {total_pages} 集")
        emit_log(f"存儲目錄: {target_dir}")
        emit_log(f"下載音質: {'64kbps (M4A)' if quality == '64k' else '最高音質（有 Hi-Res 權限時為 FLAC，否則 M4A）'}")
        emit_log(f"間隔延遲: {min_delay}~{max_delay} 秒 (防風控保護)")
        emit_log(f"==================================================")

        def page_base(p: Dict[str, Any]) -> str:
            return os.path.join(target_dir, f"P{p['page']:02d}_{sanitize_filename(p['part'])}")

        def page_tags(p: Dict[str, Any]) -> Dict[str, str]:
            return {
                'title': p.get('part') or raw_title,
                'album': raw_title,
                'artist': uploader,
                'track': f"{p['page']}/{total_pages}",
                'comment': f"https://www.bilibili.com/video/{bvid}?p={p['page']}",
            }

        # Audit existing files
        emit_log("正在檢查現有檔案完整性...")
        valid_count = 0
        cleaned_count = 0
        upgraded_count = 0
        needed_pages = []

        for p in pages:
            expected_dur = float(p.get('duration', 0))
            filepath = find_existing_audio(page_base(p), ('.m4a', '.flac'))

            if not filepath:
                needed_pages.append(p)
                continue
            is_valid, reason = verify_audio_file(filepath, expected_dur)
            if not is_valid:
                cleaned_count += 1
                emit_log(f"[{p['page']:02d}/{total_pages}] 既有檔案未通過檢驗（{reason}），刪除重抓")
                try: os.remove(filepath)
                except OSError: pass
                needed_pages.append(p)
                continue
            valid_count += 1
            # files from older versions are raw DASH fragments: repackage them in place (no re-encode)
            if is_dash_fragmented(filepath):
                try:
                    finalize_audio(filepath, filepath, page_tags(p))
                    upgraded_count += 1
                except Exception as e:
                    emit_log(f"[{p['page']:02d}/{total_pages}] 舊檔重新封裝失敗（保留原檔）: {e}")

        emit_log(f"現有已驗證完整檔案: {valid_count} 首 | 損壞清理: {cleaned_count} 首 | 待下載: {len(needed_pages)} 首")
        if upgraded_count:
            emit_log(f"已將 {upgraded_count} 個舊格式（DASH 分段）檔案重新封裝為標準格式")

        if progress_callback:
            percent = (valid_count / total_pages) * 100.0
            progress_callback(valid_count, total_pages, percent, f"已就緒 {valid_count}/{total_pages}")

        success_count = valid_count
        failed_episodes = []

        for idx, page_info in enumerate(needed_pages):
            if check_cancelled and check_cancelled():
                emit_log("任務已被使用者手動取消。")
                break

            p_num = page_info['page']
            cid = page_info['cid']
            expected_dur = float(page_info.get('duration', 0))
            base = page_base(page_info)
            temp_filepath = base + ".part"

            emit_log(f"[{p_num:02d}/{total_pages}] 開始處理: {os.path.basename(base)} (時長: {expected_dur/60:.1f} 分鐘)...")

            max_attempts = 5
            download_ok = False

            for attempt in range(1, max_attempts + 1):
                if check_cancelled and check_cancelled():
                    break

                filepath = None
                try:
                    urls, ext = self.get_audio_stream(bvid, cid, quality)
                    selected_url = urls[(attempt - 1) % len(urls)]
                    filepath = base + ext

                    dl_req = urllib.request.Request(selected_url, headers={
                        'User-Agent': self.get_headers(bvid)['User-Agent'],
                        'Referer': 'https://www.bilibili.com/'
                    })

                    with urllib.request.urlopen(dl_req, timeout=60) as resp:
                        expected_content_length = resp.headers.get('Content-Length')
                        expected_bytes = int(expected_content_length) if expected_content_length else None

                        downloaded_bytes = 0
                        chunk_size = 512 * 1024
                        with open(temp_filepath, 'wb') as f:
                            while True:
                                if check_cancelled and check_cancelled():
                                    break
                                chunk = resp.read(chunk_size)
                                if not chunk:
                                    break
                                f.write(chunk)
                                downloaded_bytes += len(chunk)

                    if check_cancelled and check_cancelled():
                        break

                    # Check 1: Content-Length exact match
                    if expected_bytes and downloaded_bytes != expected_bytes:
                        raise RuntimeError(f"Content-Length 不符合: 下載 {downloaded_bytes}, 預期 {expected_bytes}")

                    # Repackage the DASH fragment as a normal file with tags (no re-encode)
                    finalize_audio(temp_filepath, filepath, page_tags(page_info))
                    os.remove(temp_filepath)

                    # Check 2: size, bitrate, duration and a full decode
                    is_valid, reason = verify_audio_file(filepath, expected_dur)
                    if not is_valid:
                        raise RuntimeError(f"完整性檢驗未通過: {reason}")

                    size_mb = os.path.getsize(filepath) / (1024 * 1024)
                    emit_log(f"[{p_num:02d}/{total_pages}] 下載並驗證成功 -> {os.path.basename(filepath)} ({size_mb:.2f} MB)")
                    download_ok = True
                    success_count += 1
                    break

                except Exception as e:
                    emit_log(f"[{p_num:02d}/{total_pages}] 第 {attempt}/{max_attempts} 次嘗試失敗: {e}")
                    for leftover in (temp_filepath, filepath):
                        if leftover and os.path.exists(leftover):
                            try: os.remove(leftover)
                            except OSError: pass
                    time.sleep(attempt * 2)

            if not download_ok:
                emit_log(f"[{p_num:02d}/{total_pages}] 重試次數耗盡，此集下載失敗: {os.path.basename(base)}")
                failed_episodes.append(p_num)

            if progress_callback:
                percent = (success_count / total_pages) * 100.0
                progress_callback(success_count, total_pages, percent, f"進度 {success_count}/{total_pages}")

            # Jitter delay
            if idx < len(needed_pages) - 1 and not (check_cancelled and check_cancelled()):
                delay = random.uniform(min_delay, max_delay)
                emit_log(f"緩衝休眠 {delay:.1f} 秒...")
                time.sleep(delay)

        emit_log(f"\n================ 任務總結 ================")
        emit_log(f"總集數: {total_pages}")
        emit_log(f"成功完成: {success_count}/{total_pages}")
        emit_log(f"失敗分集: {failed_episodes if failed_episodes else '無 (0 失敗)'}")
        emit_log(f"存儲目錄: {target_dir}")
        emit_log(f"存儲子目錄: {clean_title}/")
        emit_log(f"==========================================")

        return {
            "title": raw_title,
            "subfolder": clean_title,
            "target_dir": target_dir,
            "total_pages": total_pages,
            "success_count": success_count,
            "failed_count": len(failed_episodes),
            "failed_episodes": failed_episodes,
            "completed": (success_count == total_pages)
        }


# ---------------------------------------------------------------------------
# Shared audio helpers (also used by ytdlp_engine)
# ---------------------------------------------------------------------------

# bit/s floor for near-empty streams. Kept low on purpose: old Bilibili uploads are genuinely ~32 kbps,
# and truncation/corruption is caught by the duration match and the full decode below.
MIN_BITRATE = 16000
DURATION_TOLERANCE = 0.01    # 1 % of the expected length ...
DURATION_TOLERANCE_MIN = 2.0 # ... but never stricter than 2 seconds


def set_shared_perms(path: str) -> None:
    """Group-writable, not world-writable (files 664, directories 775)."""
    try:
        os.chmod(path, 0o775 if os.path.isdir(path) else 0o664)
    except OSError:
        pass


def find_existing_audio(base: str, exts: Tuple[str, ...]) -> Optional[str]:
    for ext in exts:
        if os.path.exists(base + ext):
            return base + ext
    return None


def _ffprobe_format(filepath: str) -> Dict[str, Any]:
    cmd = ['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', filepath]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip()[-200:] or f"ffprobe exit {res.returncode}")
    return json.loads(res.stdout or '{}').get('format', {})


def is_dash_fragmented(filepath: str) -> bool:
    try:
        tags = _ffprobe_format(filepath).get('tags', {}) or {}
        return tags.get('major_brand', '').strip().lower() in ('dash', 'iso5', 'iso6', 'msdh')
    except Exception:
        return False


def finalize_audio(src: str, dst: str, tags: Dict[str, str]) -> None:
    """Remux without re-encoding into a standard container (moov up front for .m4a) and write tags.

    src may equal dst (in-place upgrade of an old file)."""
    ext = os.path.splitext(dst)[1].lower()
    tmp = dst + '.remux' + ext
    cmd = ['ffmpeg', '-v', 'error', '-y', '-i', src, '-map', '0:a:0', '-vn', '-c', 'copy', '-map_metadata', '-1']
    for k, v in tags.items():
        if v:
            cmd += ['-metadata', f'{k}={v}']
    if ext in ('.m4a', '.mp4'):
        cmd += ['-movflags', '+faststart']
    cmd.append(tmp)
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=600)
    if res.returncode != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError(f"ffmpeg 重新封裝失敗: {res.stderr.strip()[-300:]}")
    os.replace(tmp, dst)
    set_shared_perms(dst)


def verify_audio_file(filepath: str, expected_duration: float) -> Tuple[bool, str]:
    """Size floor, bitrate floor, duration match against the source's metadata, and a full decode."""
    if not os.path.exists(filepath):
        return False, "File does not exist"

    size = os.path.getsize(filepath)
    if size == 0:
        return False, "Empty file"
    if expected_duration > 0:
        expected_min = expected_duration * MIN_BITRATE / 8 * 0.95
        if size < expected_min:
            return False, f"Truncated: size {size/1048576:.2f}MB < min expected {expected_min/1048576:.2f}MB"

    try:
        fmt = _ffprobe_format(filepath)
    except Exception as e:
        return False, f"ffprobe error: {e}"

    try:
        duration = float(fmt.get('duration'))
    except (TypeError, ValueError):
        return False, "ffprobe could not read the duration"
    try:
        bitrate = int(fmt.get('bit_rate'))
    except (TypeError, ValueError):
        bitrate = int(size * 8 / duration) if duration > 0 else 0
    if bitrate < MIN_BITRATE:
        return False, f"Bitrate too low ({bitrate/1000:.1f} kbps)"

    if expected_duration > 0:
        tolerance = max(DURATION_TOLERANCE_MIN, expected_duration * DURATION_TOLERANCE)
        if abs(duration - expected_duration) > tolerance:
            return False, f"Duration mismatch: file {duration:.1f}s vs expected {expected_duration:.1f}s"

    # Full decode: catches corruption in the middle of the stream (~0.05 s per minute of audio on the M1)
    res = subprocess.run(['ffmpeg', '-v', 'error', '-i', filepath, '-map', '0:a:0', '-f', 'null', '-'],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=600)
    errors = [l for l in res.stderr.splitlines() if l.strip()]
    if res.returncode != 0 or errors:
        return False, f"Decode errors: {(errors[0] if errors else 'ffmpeg exit ' + str(res.returncode))[:150]}"

    return True, "Valid"
