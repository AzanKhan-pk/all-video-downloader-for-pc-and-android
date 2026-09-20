import os
import re
import shutil
import sqlite3
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from flask import Flask, jsonify, render_template, request, send_file, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "analytics.db"
DOWNLOAD_ROOT = Path(tempfile.gettempdir()) / "vidloom_downloads"
DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

QUALITY_HEIGHTS = {
    "144p": 144, "240p": 240, "360p": 360, "480p": 480,
    "720p": 720, "1080p": 1080, "1440p": 1440, "4K": 2160,
}

APP_VERSION = os.getenv("VIDLOOM_VERSION", "1.1.0")
UPDATE_REPO = "AzanKhan-pk/all-video-downloader-for-pc-and-android"
JOBS = {}
LOCK = threading.Lock()

FFMPEG_PATH = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg")
FFPROBE_PATH = os.getenv("FFPROBE_PATH") or shutil.which("ffprobe")
FFMPEG_AVAILABLE = bool(FFMPEG_PATH and FFPROBE_PATH)


def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS downloads(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            platform TEXT,
            quality TEXT,
            file_type TEXT,
            source_url TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS visits(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT
        );
        """)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def clean_url(value):
    value = (value or "").strip()
    p = urlparse(value)
    if p.scheme not in {"http", "https"} or not p.hostname:
        raise ValueError("Please enter a valid public http or https URL.")
    host = p.hostname.lower().rstrip(".")
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.endswith(".local"):
        raise ValueError("Local or private addresses are not supported.")
    return value


def platform_for(url):
    h = (urlparse(url).hostname or "").lower().rstrip(".")
    known = {
        "youtube.com": "YouTube", "youtu.be": "YouTube",
        "tiktok.com": "TikTok", "tiktokv.com": "TikTok",
        "instagram.com": "Instagram",
        "facebook.com": "Facebook", "fb.watch": "Facebook",
        "twitter.com": "X / Twitter", "x.com": "X / Twitter",
        "reddit.com": "Reddit", "v.redd.it": "Reddit",
        "vimeo.com": "Vimeo", "dailymotion.com": "Dailymotion",
        "twitch.tv": "Twitch", "soundcloud.com": "SoundCloud",
        "bilibili.com": "Bilibili", "b23.tv": "Bilibili",
        "pinterest.com": "Pinterest",
    }
    for host, name in known.items():
        if h == host or h.endswith("." + host):
            return name
    return h or "Unknown"


def base_options():
    o = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "restrictfilenames": True,
        "windowsfilenames": True,
        "continuedl": True,
        "overwrites": True,
        "retries": 20,
        "fragment_retries": 20,
        "file_access_retries": 12,
        "extractor_retries": 8,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": 4,
        "retry_sleep_functions": {
            "http": lambda n: min(8, 1 + n),
            "fragment": lambda n: min(8, 1 + n),
            "extractor": lambda n: min(8, 1 + n),
        },
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    }

    if FFMPEG_PATH:
        p = Path(FFMPEG_PATH)
        o["ffmpeg_location"] = str(p.parent if p.is_file() else p)

    cookies = os.getenv("COOKIES_FILE") or str(BASE_DIR / "cookies.txt")
    if Path(cookies).is_file():
        o["cookiefile"] = cookies

    return o


def extract(url, download=False, options=None):
    options = options or base_options()
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=download)
    if not download and (info.get("age_limit") or 0) > 0:
        raise ValueError("Age-restricted media is not supported.")
    return info


def human_size(n):
    n = float(n or 0)
    units = ["B", "KB", "MB", "GB"]
    i = 0
    while n >= 1024 and i < 3:
        n /= 1024
        i += 1
    return f"{n:.1f} {units[i]}" if i else f"{int(n)} B"


def duration(info):
    d = info.get("duration")
    if not d:
        return "Not available"
    d = int(d)
    h, r = divmod(d, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def available_heights(info):
    out = set()
    for f in info.get("formats") or []:
        if f.get("height") and f.get("vcodec") not in (None, "none"):
            try:
                out.add(int(f["height"]))
            except Exception:
                pass
    return sorted(out)


def choose_format(info, requested):
    target = QUALITY_HEIGHTS.get(requested, 720)
    heights = available_heights(info)
    if not heights:
        raise ValueError("No downloadable video formats were exposed by the source.")

    exact_or_lower = [h for h in heights if h <= target]
    source = max(exact_or_lower, default=min(heights))

    progressive = (
        f"best[height={source}][ext=mp4]/"
        f"best[height={source}]"
    )

    if FFMPEG_AVAILABLE:
        merged = (
            f"bestvideo[height={source}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height={source}]+bestaudio"
        )
        return f"{merged}/{progressive}/best[height<={target}]/best", source

    return (
        f"{progressive}/"
        f"best[height<={target}][acodec!=none][vcodec!=none]/best",
        source,
    )


def estimate_size(info, requested):
    try:
        fmt, source = choose_format(info, requested)
        formats = info.get("formats") or []
        video = [
            f for f in formats
            if f.get("height") == source and f.get("vcodec") not in (None, "none")
        ]
        if not video:
            return 0
        vf = max(video, key=lambda f: f.get("filesize") or f.get("filesize_approx") or 0)
        total = vf.get("filesize") or vf.get("filesize_approx") or 0
        if FFMPEG_AVAILABLE and vf.get("acodec") in (None, "none"):
            audio = [
                f for f in formats
                if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")
            ]
            if audio:
                af = max(audio, key=lambda f: f.get("filesize") or f.get("filesize_approx") or 0)
                total += af.get("filesize") or af.get("filesize_approx") or 0
        return int(total or 0)
    except Exception:
        return 0


def new_job(url, mode, quality):
    jid = uuid.uuid4().hex
    folder = Path(tempfile.mkdtemp(prefix="vidloom_", dir=DOWNLOAD_ROOT))
    job = {
        "id": jid, "url": url, "mode": mode, "quality": quality,
        "status": "starting", "percent": 0.0,
        "downloaded_bytes": 0, "total_bytes": 0, "speed": 0,
        "eta": None, "error": None, "file_path": None, "filename": None,
        "title": None, "platform": platform_for(url),
        "temp_dir": str(folder), "source_height": None,
        "estimated_bytes": 0,
    }
    with LOCK:
        JOBS[jid] = job
    return jid


def get_job(jid):
    with LOCK:
        return JOBS.get(jid)


def set_job(jid, **kw):
    with LOCK:
        if jid in JOBS:
            JOBS[jid].update(kw)


def hook(jid):
    def cb(d):
        if d.get("status") == "downloading":
            done = int(d.get("downloaded_bytes") or 0)
            total = int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
            percent = (done / total * 100) if total else 0
            set_job(
                jid,
                status="downloading",
                downloaded_bytes=done,
                total_bytes=total,
                percent=percent,
                speed=float(d.get("speed") or 0),
                eta=d.get("eta"),
            )
        elif d.get("status") == "finished":
            done = int(d.get("downloaded_bytes") or 0)
            set_job(
                jid, status="processing", downloaded_bytes=done,
                total_bytes=done, percent=100, speed=0, eta=0,
            )
    return cb


def final_file(folder, mode):
    ext = ".mp3" if mode == "audio" else ".mp4"
    files = [
        p for p in folder.glob(f"*{ext}")
        if p.is_file() and p.stat().st_size >= 1024
    ]
    if not files:
        files = [
            p for p in folder.iterdir()
            if p.is_file()
            and p.suffix.lower() not in {
                ".part", ".ytdl", ".json", ".jpg", ".jpeg",
                ".png", ".webp", ".vtt", ".srt",
            }
            and p.stat().st_size >= 1024
        ]
    if not files:
        raise RuntimeError("The final media file was not produced.")
    return max(files, key=lambda p: p.stat().st_size)


def run_job(jid):
    j = get_job(jid)
    try:
        set_job(jid, status="preparing")
        info = extract(j["url"])
        title = info.get("title") or "Download"
        set_job(
            jid,
            title=title,
            estimated_bytes=estimate_size(info, j["quality"]) if j["mode"] == "video" else 0,
        )

        o = base_options()
        o["outtmpl"] = str(Path(j["temp_dir"]) / "%(title).180B [%(id)s].%(ext)s")
        o["progress_hooks"] = [hook(jid)]

        if j["mode"] == "audio":
            if not FFMPEG_AVAILABLE:
                raise RuntimeError("MP3 conversion requires FFmpeg on the server.")
            o["format"] = "bestaudio/best"
            o["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
            ft = "MP3"
        else:
            fmt, source = choose_format(info, j["quality"])
            o["format"] = fmt
            if FFMPEG_AVAILABLE:
                o["merge_output_format"] = "mp4"
            ft = "MP4"
            set_job(jid, source_height=source)

        set_job(jid, status="downloading")
        final_info = extract(j["url"], True, o)
        f = final_file(Path(j["temp_dir"]), j["mode"])
        size = f.stat().st_size

        with db() as c:
            c.execute(
                "INSERT INTO downloads(title,platform,quality,file_type,source_url,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    title, platform_for(j["url"]), j["quality"],
                    ft, j["url"], now(),
                ),
            )

        set_job(
            jid, status="completed", downloaded_bytes=size,
            total_bytes=size, percent=100, speed=0, eta=0,
            file_path=str(f), filename=f.name,
            title=final_info.get("title") or title,
        )
    except Exception as e:
        set_job(jid, status="error", error=str(e)[:500], speed=0, eta=None)


@app.get("/")
def index():
    with db() as c:
        c.execute("INSERT INTO visits(created_at) VALUES(?)", (now(),))
    return render_template("index.html")


@app.get("/downloads")
def downloads_page():
    return render_template("downloads.html", version=APP_VERSION)


@app.get("/privacy")
def privacy():
    return render_template("privacy.html")


@app.get("/terms")
def terms():
    return render_template("terms.html")


@app.get("/faq")
def faq():
    return render_template("faq.html")


@app.get("/manifest.json")
def manifest():
    return send_from_directory(
        BASE_DIR, "manifest.json", mimetype="application/manifest+json"
    )


@app.get("/service-worker.js")
def sw():
    r = send_from_directory(
        BASE_DIR, "service-worker.js", mimetype="application/javascript"
    )
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.get("/api/app-version")
def app_version():
    return jsonify(
        version=APP_VERSION,
        repository=UPDATE_REPO,
        release_url=f"https://github.com/{UPDATE_REPO}/releases/latest",
        windows_url=f"https://github.com/{UPDATE_REPO}/releases/latest/download/VidLoom-Setup.exe",
        android_url=f"https://github.com/{UPDATE_REPO}/releases/latest/download/VidLoom.apk",
    )


@app.post("/api/info")
def api_info():
    try:
        url = clean_url((request.get_json(silent=True) or {}).get("url"))
        info = extract(url)
        heights = available_heights(info)
        return jsonify(
            ok=True,
            video={
                "id": info.get("id"),
                "title": info.get("title") or "Untitled media",
                "creator": info.get("uploader") or info.get("channel") or "Unknown",
                "duration": duration(info),
                "views": info.get("view_count"),
                "thumbnail": info.get("thumbnail"),
                "platform": platform_for(url),
                "url": url,
                "heights": heights,
                "age_limit": info.get("age_limit", 0),
                "yt_dlp": getattr(yt_dlp, "__version__", "unknown"),
            },
        )
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:500]), 400


@app.post("/api/download")
def api_download():
    try:
        p = request.get_json(silent=True) or {}
        url = clean_url(p.get("url"))
        mode = p.get("mode", "video")
        quality = p.get("quality", "720p")
        if mode not in {"video", "audio"}:
            raise ValueError("Unsupported media type.")
        if mode == "video" and quality not in QUALITY_HEIGHTS:
            raise ValueError("Unsupported quality.")

        jid = new_job(url, mode, quality)
        threading.Thread(target=run_job, args=(jid,), daemon=True).start()
        return jsonify(ok=True, job_id=jid)
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:500]), 400


@app.get("/api/download/<jid>/status")
def status(jid):
    j = get_job(jid)
    if not j:
        return jsonify(ok=False, error="Download not found."), 404
    keys = [
        "id", "status", "percent", "downloaded_bytes", "total_bytes",
        "estimated_bytes", "speed", "eta", "error", "title",
        "filename", "source_height", "platform",
    ]
    return jsonify(ok=True, job={k: j.get(k) for k in keys})


@app.get("/download/<jid>")
def file_download(jid):
    j = get_job(jid)
    if not j or j.get("status") != "completed" or not j.get("file_path"):
        return "File is not ready.", 404
    p = Path(j["file_path"])
    if not p.is_file():
        return "File no longer exists.", 404
    return send_file(
        p, as_attachment=True,
        download_name=j.get("filename") or p.name,
        max_age=0,
    )


@app.get("/api/history")
def history():
    with db() as c:
        rows = c.execute(
            "SELECT title,platform,quality,file_type,created_at "
            "FROM downloads ORDER BY id DESC LIMIT 50"
        ).fetchall()
    return jsonify(ok=True, items=[dict(r) for r in rows])


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
