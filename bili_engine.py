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
from typing import Callable, Optional, Dict, Any, List

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

class BiliDownloadEngine:
    def __init__(self, storage_base_dir: str, cookie_file: str = ""):
        self.storage_base_dir = storage_base_dir
        self.cookie_file = cookie_file

    def get_cookie_header(self) -> str:
        if self.cookie_file and os.path.exists(self.cookie_file):
            try:
                with open(self.cookie_file, 'r', encoding='utf-8') as f:
                    c = f.read().strip()
                    if c:
                        return c
            except Exception:
                pass
        return ""

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

    def get_video_info(self, bvid: str) -> Dict[str, Any]:
        headers = self.get_headers(bvid)
        
        # 1. Try WBI signed view API (Required by Bilibili to avoid HTTP 412)
        try:
            params = self.wbi_signer.sign({'bvid': bvid}, headers)
            url = 'https://api.bilibili.com/x/web-interface/wbi/view?' + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if data.get('code') == 0 and data.get('data'):
                    return data['data']
        except Exception as e:
            # If WBI fails, try fallback
            pass

        # 2. Fallback to standard view API
        url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data.get('code') != 0:
                raise RuntimeError(f"Bilibili API 錯誤: {data.get('message', '未知錯誤')} (代碼: {data.get('code')})")
            return data['data']

    def get_audio_stream_urls(self, bvid: str, cid: int, quality: str = "64k") -> List[str]:
        headers = self.get_headers(bvid)
        data = None

        # 1. Try WBI signed playurl
        try:
            params = self.wbi_signer.sign({'bvid': bvid, 'cid': cid, 'fnval': 16, 'qn': 64}, headers)
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
            url = f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&fnval=16&qn=64"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))

        if not data or data.get('code') != 0:
            err_msg = data.get('message') if data else "未知錯誤"
            raise RuntimeError(f"PlayURL 錯誤 (cid: {cid}): {err_msg}")
        
        audios = data.get('data', {}).get('dash', {}).get('audio', [])
        if not audios:
            raise RuntimeError(f"未找到可用音訊串流 (cid: {cid})")
        
        if quality == "64k":
            selected = next((a for a in audios if a.get('id') == 30216), None)
            if not selected:
                selected = min(audios, key=lambda x: x.get('bandwidth', 9999999))
        else: # "best"
            selected = max(audios, key=lambda x: x.get('bandwidth', 0))
        
        urls = [selected['baseUrl']]
        if 'backupUrl' in selected and selected['backupUrl']:
            urls.extend(selected['backupUrl'])
        return urls

    def verify_audio_file(self, filepath: str, expected_duration: float) -> tuple[bool, str]:
        if not os.path.exists(filepath):
            return False, "File does not exist"
        
        file_size = os.path.getsize(filepath)
        size_mb = file_size / (1024 * 1024)
        expected_min_mb = (expected_duration * 4500) / (1024 * 1024) # ~36 kbps min threshold
        if size_mb < expected_min_mb:
            return False, f"Truncated: size {size_mb:.2f}MB < min expected {expected_min_mb:.2f}MB"
            
        # Check bitrate via ffprobe
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=bit_rate', '-of', 'default=noprint_wrappers=1:nokey=1', filepath]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            br_str = res.stdout.strip()
            if br_str.isdigit():
                br = int(br_str)
                if br < 35000: # Minimum acceptable bitrate
                    return False, f"Bitrate too low ({br/1000:.1f} kbps)"
        except Exception as e:
            return False, f"ffprobe error: {e}"
            
        return True, "Valid"

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

        bvid = extract_bvid(target_url)
        if not bvid:
            raise ValueError(f"無法從輸入中辨識有效的 Bilibili BV號或網址: {target_url}")

        emit_log(f"正在解析 Bilibili 資訊: {bvid} ...")
        info = self.get_video_info(bvid)
        raw_title = info.get('title', 'bilibili_audio')
        clean_title = custom_subfolder if custom_subfolder else sanitize_filename(raw_title)
        pages = info.get('pages', [])
        total_pages = len(pages)

        if total_pages == 0:
            raise RuntimeError(f"該影片沒有可用的分 P 內容 (pages is empty)")

        target_dir = os.path.join(self.storage_base_dir, clean_title)
        os.makedirs(target_dir, exist_ok=True)

        emit_log(f"==================================================")
        emit_log(f"標題: {raw_title}")
        emit_log(f"總集數: {total_pages} 集")
        emit_log(f"存儲目錄: {target_dir}")
        emit_log(f"下載音質: {quality} (M4A)")
        emit_log(f"間隔延遲: {min_delay}~{max_delay} 秒 (防風控保護)")
        emit_log(f"==================================================")

        # Audit existing files
        emit_log("正在檢查現有檔案完整性...")
        valid_count = 0
        cleaned_count = 0
        needed_pages = []

        for p in pages:
            p_num = p['page']
            p_part = sanitize_filename(p['part'])
            expected_dur = float(p.get('duration', 0))
            filename = f"P{p_num:02d}_{p_part}.m4a"
            filepath = os.path.join(target_dir, filename)

            if os.path.exists(filepath):
                is_valid, reason = self.verify_audio_file(filepath, expected_dur)
                if is_valid:
                    valid_count += 1
                else:
                    cleaned_count += 1
                    try: os.remove(filepath)
                    except: pass
                    needed_pages.append(p)
            else:
                needed_pages.append(p)

        emit_log(f"現有已驗證完整檔案: {valid_count} 首 | 損壞清理: {cleaned_count} 首 | 待下載: {len(needed_pages)} 首")

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
            part_title = sanitize_filename(page_info['part'])
            cid = page_info['cid']
            expected_dur = float(page_info.get('duration', 0))
            filename = f"P{p_num:02d}_{part_title}.m4a"
            filepath = os.path.join(target_dir, filename)
            temp_filepath = filepath + ".tmp"

            emit_log(f"[{p_num:02d}/{total_pages}] 開始處理: {filename} (時長: {expected_dur/60:.1f} 分鐘)...")

            max_attempts = 5
            download_ok = False

            for attempt in range(1, max_attempts + 1):
                if check_cancelled and check_cancelled():
                    break

                try:
                    urls = self.get_audio_stream_urls(bvid, cid, quality)
                    selected_url = urls[(attempt - 1) % len(urls)]

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

                    os.replace(temp_filepath, filepath)
                    os.chmod(filepath, 0o666)

                    # Check 2: Size & Bitrate validation
                    is_valid, reason = self.verify_audio_file(filepath, expected_dur)
                    if not is_valid:
                        raise RuntimeError(f"完整性檢驗未通過: {reason}")

                    size_mb = os.path.getsize(filepath) / (1024 * 1024)
                    emit_log(f"[{p_num:02d}/{total_pages}] 下載並驗證成功 -> {filename} ({size_mb:.2f} MB)")
                    download_ok = True
                    success_count += 1
                    break

                except Exception as e:
                    emit_log(f"[{p_num:02d}/{total_pages}] 第 {attempt}/{max_attempts} 次嘗試失敗 ({filename}): {e}")
                    if os.path.exists(temp_filepath):
                        try: os.remove(temp_filepath)
                        except: pass
                    if os.path.exists(filepath):
                        try: os.remove(filepath)
                        except: pass
                    time.sleep(attempt * 2)

            if not download_ok:
                emit_log(f"[{p_num:02d}/{total_pages}] 重試次數耗盡，此集下載失敗: {filename}")
                failed_episodes.append(p_num)

            if progress_callback:
                percent = (success_count / total_pages) * 100.0
                progress_callback(success_count, total_pages, percent, f"進度 {success_count}/{total_pages}")

            # Jitter delay
            if idx < len(needed_pages) - 1 and not (check_cancelled and check_cancelled()):
                delay = random.uniform(min_delay, max_delay)
                emit_log(f"緩衝休眠 {delay:.1f} 秒...")
                time.sleep(delay)

        # Set final permissions
        try:
            subprocess.run(["chmod", "-R", "777", target_dir], check=False)
        except Exception:
            pass

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
