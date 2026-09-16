import os
import re
import shutil
import smtplib
import sqlite3
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.utils import DownloadCancelled as YtDlpStopSignal
from flask import Flask, jsonify, render_template, request, send_file, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "analytics.db"
DOWNLOAD_ROOT = Path(tempfile.gettempdir()) / "vidloom_downloads"
DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret-key")

# Minimum size (in bytes) a finished file must have before it is trusted as
# real media rather than a stub, an empty write, or a truncated fragment.
MIN_VALID_FILE_BYTES = 1024

QUALITY_HEIGHTS = {
    "360p": 360,
    "480p": 480,
    "720p": 720,
    "1080p": 1080,
    "1440p": 1440,
    "4K": 2160,
}

# Only hosts that a real, currently-maintained yt-dlp extractor can reliably
# read are allowed here. Nothing is listed unless it has actually been
# exercised against the download pipeline below.
ALLOWED_HOSTS = {
    # YouTube (videos + Shorts)
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
    "youtube-nocookie.com", "www.youtube-nocookie.com",

    # TikTok
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com",

    # Instagram (public posts, reels)
    "instagram.com", "www.instagram.com",

    # Facebook (public videos)
    "facebook.com", "www.facebook.com", "m.facebook.com", "fb.watch",

    # X / Twitter (public videos)
    "twitter.com", "www.twitter.com", "x.com", "www.x.com",

    # Reddit (public videos)
    "reddit.com", "www.reddit.com", "old.reddit.com", "new.reddit.com",
    "v.redd.it", "redd.it",

    # Vimeo
    "vimeo.com", "www.vimeo.com", "player.vimeo.com",

    # Dailymotion
    "dailymotion.com", "www.dailymotion.com", "dai.ly",

    # Twitch (public clips/videos)
    "twitch.tv", "www.twitch.tv", "m.twitch.tv", "clips.twitch.tv",

    # SoundCloud (public audio)
    "soundcloud.com", "www.soundcloud.com", "snd.sc",

    # Bilibili
    "bilibili.com", "www.bilibili.com", "b23.tv",

    # Pinterest (public pins/videos)
    "pinterest.com", "www.pinterest.com", "pin.it",
    # Pinterest
"pinterest.com",
"www.pinterest.com",
"pin.it",
"www.pin.it",
"i.pinimg.com",
"images.pinimg.com",

}

DOWNLOAD_JOBS = {}
DOWNLOAD_JOBS_LOCK = threading.Lock()

FFMPEG_PATH = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg")
FFPROBE_PATH = os.getenv("FFPROBE_PATH") or shutil.which("ffprobe")
FFMPEG_AVAILABLE = bool(FFMPEG_PATH and FFPROBE_PATH)

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def get_db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with get_db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS visits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                platform TEXT NOT NULL,
                quality TEXT NOT NULL,
                file_type TEXT NOT NULL,
                source_url TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                comment TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def send_notification(subject, body):
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    recipient = os.getenv("NOTIFICATION_EMAIL", "azankokarai1122@gmail.com")

    if not all([smtp_host, smtp_username, smtp_password, recipient]):
        return

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_username
    message["To"] = recipient
    message.set_content(body)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(message)
    except Exception as error:
        app.logger.warning("Notification email failed: %s", error)


def clean_url(value):
    value = (value or "").strip()
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    bare_host = host[4:] if host.startswith("www.") else host

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Please enter a valid public http or https URL.")

    allowed_bare = {h[4:] if h.startswith("www.") else h for h in ALLOWED_HOSTS}
    if host not in ALLOWED_HOSTS and bare_host not in allowed_bare:
        raise ValueError("This platform is not currently supported.")

    return value


def platform_for(url):
    host = (urlparse(url).hostname or "").lower()

    if "youtube" in host or host == "youtu.be":
        return "YouTube"
    if "tiktok" in host:
        return "TikTok"
    if "instagram" in host:
        return "Instagram"
    if "facebook" in host or host == "fb.watch":
        return "Facebook"
    if host in {"twitter.com", "www.twitter.com", "x.com", "www.x.com"}:
        return "X / Twitter"
    if "reddit" in host or host in {"v.redd.it", "redd.it"}:
        return "Reddit"
    if "vimeo" in host:
        return "Vimeo"
    if "dailymotion" in host or host == "dai.ly":
        return "Dailymotion"
    if "twitch" in host:
        return "Twitch"
    if "soundcloud" in host or host == "snd.sc":
        return "SoundCloud"
    if "bilibili" in host or host == "b23.tv":
        return "Bilibili"
    return host or "Unknown"


def strip_ansi(text):
    return ANSI_ESCAPE_RE.sub("", text or "")


def humanize_error(error):
    """Turn a raw yt-dlp/network exception into a clear, user-facing message."""
    message = strip_ansi(str(error)).strip()
    if message.startswith("ERROR: "):
        message = message[len("ERROR: "):]
    lowered = message.lower()

    if "unsupported url" in lowered:
        return "This link is not from a supported platform."
    if "private video" in lowered or "private" in lowered and "video" in lowered:
        return "This media is private and cannot be downloaded."
    if "login" in lowered or "sign in" in lowered or "cookies" in lowered:
        return "This media requires a login and cannot be downloaded here."
    if "drm" in lowered:
        return "This media is DRM-protected and cannot be downloaded."
    if "geo" in lowered and "restrict" in lowered:
        return "This media is region-restricted and unavailable from this server."
    if "video unavailable" in lowered or "this content isn" in lowered:
        return "This media is unavailable, was removed, or the link is incorrect."
    if "no video formats" in lowered or "requested format is not available" in lowered:
        return "No downloadable format was found for the requested quality. Try a different quality."
    if "ffprobe" in lowered or "ffmpeg" in lowered:
        return "The server's FFmpeg installation could not process this media. Please try again later."
    if not message:
        return "Could not read this link. It may be private, unsupported, or temporarily unavailable."

    return message[:300]


def is_network_error(error):
    message = str(error).lower()
    network_errors = (
        "timed out", "timeout", "connection reset", "connection aborted",
        "connection refused", "network is unreachable", "temporary failure",
        "incomplete read", "remote end closed", "connection error",
        "urlopen error", "http error 429", "http error 502",
        "http error 503", "http error 504",
    )
    return any(item in message for item in network_errors)


def find_stop_signal(error):
    """Walk an exception's cause/context chain looking for our stop signal.

    yt-dlp is expected to let ``DownloadCancelled`` raised from a progress
    hook propagate untouched, but if any wrapper re-raises it as a different
    exception type, Python's automatic exception chaining still preserves a
    reference to it via ``__cause__``/``__context__``. Checking the chain
    keeps pause/cancel reliable either way.
    """
    seen = set()
    current = error
    while current is not None and id(current) not in seen:
        if isinstance(current, YtDlpStopSignal):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def base_ydl_options():
    """Options shared by every yt-dlp call: the same reliable extraction
    path that already works for these platforms from the command line."""
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "restrictfilenames": True,
        "windowsfilenames": True,
        "continuedl": True,
        "overwrites": True,
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 5,
        "socket_timeout": 30,
        "extractor_args": {
            # Helps avoid YouTube's bot-check on some networks; harmless
            # for every other extractor, which simply ignores this key.
            "youtube": {"player_client": ["android", "web"]},
        },
    }

    if FFMPEG_PATH:
        ffmpeg_path = Path(FFMPEG_PATH)
        options["ffmpeg_location"] = str(ffmpeg_path.parent if ffmpeg_path.is_file() else ffmpeg_path)

    cookies_path = os.getenv("COOKIES_FILE") or str(BASE_DIR / "cookies.txt")
    if Path(cookies_path).is_file():
        options["cookiefile"] = cookies_path

    return options


def extract_info_with_retry(url, download=False, options=None):
    """Resolve metadata (and optionally download) via yt-dlp, with a small
    TikTok-specific retry for share links that the extractor sometimes
    rejects on the first attempt."""
    options = options or base_ydl_options()

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(url, download=download)
    except Exception as first_error:
        if find_stop_signal(first_error) is not None:
            raise

        host = (urlparse(url).hostname or "").lower()
        match = re.search(r"/video/(\d+)", url)
        if "tiktok.com" not in host or not match:
            raise

        retry_url = f"https://www.tiktok.com/@_/video/{match.group(1)}"
        app.logger.warning("TikTok extraction failed, retrying with %s", retry_url)
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(retry_url, download=download)


def get_media_info(url):
    return extract_info_with_retry(url, download=False)


def format_duration(info):
    if info.get("duration_string"):
        return info["duration_string"]
    duration = info.get("duration")
    if duration:
        duration = int(duration)
        hours, remainder = divmod(duration, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"
    return "Not available"


def format_views(info):
    view_count = info.get("view_count")
    if isinstance(view_count, (int, float)):
        return f"{int(view_count):,}"
    return "Not available"


def choose_video_format(info, requested_height):
    """Build a yt-dlp format selector for the requested quality.

    Falls back gracefully when the extractor does not expose a full
    ``formats`` list with heights (common on simpler platforms) instead of
    raising, so quality selection never blocks a download that would
    otherwise succeed with yt-dlp's own "best" fallback.
    """
    formats = info.get("formats") or []
    heights = sorted({
        int(fmt["height"])
        for fmt in formats
        if fmt.get("height") and fmt.get("vcodec") not in (None, "none")
    })

    if heights:
        lower_or_equal = [h for h in heights if h <= requested_height]
        source_height = max(lower_or_equal) if lower_or_equal else min(heights)
        height_clause = f"={source_height}"
    else:
        source_height = requested_height
        height_clause = f"<={requested_height}"

    progressive = f"best[height{height_clause}][ext=mp4]/best[height{height_clause}]"

    if FFMPEG_AVAILABLE:
        split_streams = (
            f"bestvideo[height{height_clause}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height{height_clause}]+bestaudio"
        )
        selected_format = f"{progressive}/{split_streams}/best[height<={requested_height}]/best"
    else:
        # Without FFmpeg we cannot merge separate video/audio streams, so
        # restrict the selection to formats that already contain both.
        selected_format = (
            f"{progressive}/"
            f"best[height<={requested_height}][acodec!=none][vcodec!=none]/"
            "best[acodec!=none][vcodec!=none]"
        )

    return selected_format, source_height


def new_download_job(url, mode, quality):
    job_id = uuid.uuid4().hex
    temp_dir = Path(tempfile.mkdtemp(prefix="vidloom_", dir=DOWNLOAD_ROOT))

    job = {
        "id": job_id,
        "url": url,
        "mode": mode,
        "quality": quality,
        "status": "starting",
        "percent": 0.0,
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "speed": 0,
        "eta": None,
        "error": None,
        "file_path": None,
        "filename": None,
        "title": None,
        "platform": platform_for(url),
        "file_type": "MP3" if mode == "audio" else "MP4",
        "temp_dir": str(temp_dir),
        "pause_requested": False,
        "cancel_requested": False,
    }

    with DOWNLOAD_JOBS_LOCK:
        DOWNLOAD_JOBS[job_id] = job
    return job_id


def get_job(job_id):
    with DOWNLOAD_JOBS_LOCK:
        return DOWNLOAD_JOBS.get(job_id)


def update_job(job_id, **values):
    with DOWNLOAD_JOBS_LOCK:
        job = DOWNLOAD_JOBS.get(job_id)
        if job:
            job.update(values)


def cleanup_job_files(job):
    temp_dir = job.get("temp_dir") if job else None
    if temp_dir:
        shutil.rmtree(temp_dir, ignore_errors=True)


def progress_hook(job_id):
    def hook(data):
        job = get_job(job_id)
        if not job or job.get("cancel_requested") or job.get("pause_requested"):
            raise YtDlpStopSignal("Download stopped by user request.")

        status = data.get("status")
        if status == "downloading":
            downloaded = int(data.get("downloaded_bytes") or 0)
            total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
            percent = (downloaded / total * 100) if total else 0.0
            update_job(
                job_id,
                status="downloading",
                downloaded_bytes=downloaded,
                total_bytes=total,
                percent=max(0.0, min(100.0, percent)),
                speed=float(data.get("speed") or 0),
                eta=data.get("eta"),
            )
        elif status == "finished":
            downloaded = int(data.get("downloaded_bytes") or 0)
            total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or downloaded)
            update_job(
                job_id,
                status="processing",
                downloaded_bytes=downloaded,
                total_bytes=total,
                percent=100.0 if total else 0.0,
                speed=0,
                eta=0,
            )

    return hook


def find_output_file(temp_dir, mode):
    """Locate the real final media file yt-dlp produced, ignoring partial,
    metadata, thumbnail, or otherwise incomplete artifacts. Never returns a
    ``.part``/``.ytdl`` temp file, and never treats an HTML/JSON error body
    saved to disk as a completed download."""
    expected_ext = "mp3" if mode == "audio" else "mp4"
    skip_suffixes = {
        ".part", ".ytdl", ".temp", ".description", ".json",
        ".jpg", ".jpeg", ".png", ".webp", ".vtt", ".srt",
    }

    candidates = [p for p in temp_dir.glob(f"*.{expected_ext}") if p.is_file()]
    if not candidates:
        candidates = [
            p for p in temp_dir.iterdir()
            if p.is_file() and p.suffix.lower() not in skip_suffixes and not p.name.endswith(".part")
        ]

    if not candidates:
        raise RuntimeError("Downloaded file could not be found after processing.")

    final_path = max(candidates, key=lambda p: p.stat().st_size)
    size = final_path.stat().st_size

    if size < MIN_VALID_FILE_BYTES:
        raise RuntimeError("The source returned an empty or invalid media file.")

    with open(final_path, "rb") as handle:
        header = handle.read(32).lstrip().lower()
    if header.startswith(b"<!doctype") or header.startswith(b"<html"):
        raise RuntimeError("The source returned an error page instead of real media.")

    return final_path, size


def run_download_job(job_id):
    job = get_job(job_id)
    if not job:
        return

    temp_dir = Path(job["temp_dir"])
    url = job["url"]
    mode = job["mode"]
    quality = job["quality"]

    try:
        update_job(job_id, status="preparing", error=None)

        info = extract_info_with_retry(url, download=False)
        title = info.get("title") or "video"
        update_job(job_id, title=title, platform=platform_for(url))

        options = base_ydl_options()
        options.update({
            "outtmpl": str(temp_dir / "%(id)s.%(ext)s"),
            "progress_hooks": [progress_hook(job_id)],
        })

        if mode == "audio":
            if not FFMPEG_AVAILABLE:
                raise RuntimeError(
                    "MP3 conversion requires FFmpeg and ffprobe to be installed on the server."
                )
            options["format"] = "bestaudio/best"
            options["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
            file_type = "MP3"
        else:
            requested_height = QUALITY_HEIGHTS.get(quality, 720)
            selected_format, source_height = choose_video_format(info, requested_height)
            app.logger.info("Requested quality=%s, selected source height=%s", quality, source_height)
            options["format"] = selected_format
            if FFMPEG_AVAILABLE:
                options["merge_output_format"] = "mp4"
            file_type = "MP4"

        update_job(job_id, status="downloading")

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)

        final_path, final_size = find_output_file(temp_dir, mode)
        title = info.get("title") or title
        platform = platform_for(url)

        with get_db() as connection:
            connection.execute(
                """
                INSERT INTO downloads(title, platform, quality, file_type, source_url, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (title, platform, quality, file_type, url, now_iso()),
            )

        update_job(
            job_id,
            status="completed",
            downloaded_bytes=final_size,
            total_bytes=final_size,
            percent=100.0,
            speed=0,
            eta=0,
            file_path=str(final_path),
            filename=final_path.name,
            title=title,
            platform=platform,
            file_type=file_type,
        )

    except Exception as error:
        if find_stop_signal(error) is not None:
            job = get_job(job_id) or {}
            if job.get("cancel_requested"):
                update_job(job_id, status="cancelled", cancel_requested=False)
                cleanup_job_files(job)
            else:
                update_job(job_id, status="paused", pause_requested=False)
            return

        app.logger.exception("Download job failed: %s", error)
        if is_network_error(error):
            update_job(
                job_id,
                status="network_error",
                error="Network issue detected. Your partial download was kept. It will resume automatically.",
            )
        else:
            update_job(job_id, status="error", error=humanize_error(error))


@app.route("/")
def index():
    with get_db() as connection:
        connection.execute("INSERT INTO visits(created_at) VALUES (?)", (now_iso(),))
    return render_template("index.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/ads.txt")
def ads_txt():
    return send_from_directory(BASE_DIR, "ads.txt", mimetype="text/plain")


@app.route("/manifest.json")
def manifest():
    return send_from_directory(BASE_DIR, "manifest.json", mimetype="application/manifest+json")


@app.route("/service-worker.js")
def service_worker():
    response = send_from_directory(BASE_DIR, "service-worker.js", mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/sitemap.xml")
def sitemap():
    sitemap_xml = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">
  <url><loc>https://avd.up.railway.app/</loc></url>
  <url><loc>https://avd.up.railway.app/about</loc></url>
  <url><loc>https://avd.up.railway.app/how-to-use</loc></url>
  <url><loc>https://avd.up.railway.app/faq</loc></url>
  <url><loc>https://avd.up.railway.app/privacy</loc></url>
  <url><loc>https://avd.up.railway.app/terms</loc></url>
</urlset>"""
    return sitemap_xml, 200, {"Content-Type": "application/xml"}


@app.post("/api/info")
def api_info():
    try:
        payload = request.get_json(silent=True) or {}
        url = clean_url(payload.get("url", ""))
        info = get_media_info(url)
        return jsonify({
            "ok": True,
            "video": {
                "id": info.get("id"),
                "title": info.get("title") or "Untitled media",
                "creator": info.get("uploader") or info.get("channel") or "Unknown creator",
                "duration": format_duration(info),
                "views": format_views(info),
                "thumbnail": info.get("thumbnail"),
                "platform": platform_for(url),
                "url": url,
            },
        })
    except Exception as error:
        app.logger.warning("Info error: %s", error)
        return jsonify({"ok": False, "error": humanize_error(error)}), 400


@app.post("/api/download")
def api_download():
    try:
        payload = request.get_json(silent=True) or {}
        url = clean_url(payload.get("url", ""))
        mode = payload.get("mode", "video")
        quality = payload.get("quality", "720p")

        if mode not in {"video", "audio"}:
            raise ValueError("Unsupported media type.")
        if mode == "video" and quality not in QUALITY_HEIGHTS:
            raise ValueError("Unsupported video quality.")

        job_id = new_download_job(url, mode, quality)
        threading.Thread(target=run_download_job, args=(job_id,), daemon=True).start()
        return jsonify({"ok": True, "job_id": job_id})
    except Exception as error:
        app.logger.exception("Could not start download: %s", error)
        return jsonify({"ok": False, "error": humanize_error(error)}), 400


@app.get("/api/download/<job_id>/status")
def download_status(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Download job not found."}), 404
    return jsonify({
        "ok": True,
        "job": {
            "id": job["id"],
            "status": job["status"],
            "percent": round(float(job.get("percent", 0)), 2),
            "downloaded_bytes": job.get("downloaded_bytes", 0),
            "total_bytes": job.get("total_bytes", 0),
            "speed": job.get("speed", 0),
            "eta": job.get("eta"),
            "error": job.get("error"),
            "title": job.get("title"),
        },
    })


@app.post("/api/download/<job_id>/pause")
def pause_download(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Download job not found."}), 404
    if job["status"] != "downloading":
        return jsonify({"ok": False, "error": "Download is not currently running."}), 400
    update_job(job_id, pause_requested=True)
    return jsonify({"ok": True})


@app.post("/api/download/<job_id>/resume")
def resume_download(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Download job not found."}), 404
    if job["status"] not in {"paused", "network_error"}:
        return jsonify({"ok": False, "error": "Download is not paused or waiting for network."}), 400

    update_job(job_id, status="starting", pause_requested=False, cancel_requested=False, error=None)
    threading.Thread(target=run_download_job, args=(job_id,), daemon=True).start()
    return jsonify({"ok": True})


@app.post("/api/download/<job_id>/cancel")
def cancel_download(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Download job not found."}), 404
    update_job(job_id, cancel_requested=True)
    return jsonify({"ok": True})


@app.get("/api/download/<job_id>/file")
def download_file(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"ok": False, "error": "Download job not found."}), 404
    if job["status"] != "completed":
        return jsonify({"ok": False, "error": "Download is not ready yet."}), 409

    path = Path(job.get("file_path") or "")
    if not path.exists() or path.stat().st_size <= 0:
        return jsonify({"ok": False, "error": "Downloaded file is missing."}), 404

    extension = "mp3" if job.get("mode") == "audio" else "mp4"
    safe_name = re.sub(r"[^\w\s.-]", "", job.get("title") or "video").strip()[:90] or "video"
    return send_file(
        path,
        as_attachment=True,
        download_name=f"{safe_name}.{extension}",
        mimetype="audio/mpeg" if extension == "mp3" else "video/mp4",
    )


@app.post("/api/comments")
def api_comments():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()[:80]
    comment = (payload.get("comment") or "").strip()[:1000]

    if len(name) < 2 or len(comment) < 3:
        return jsonify({"ok": False, "error": "Add your name and a useful comment."}), 400

    timestamp = now_iso()
    with get_db() as connection:
        connection.execute("INSERT INTO comments(name, comment, created_at) VALUES (?, ?, ?)", (name, comment, timestamp))

    send_notification("VidLoom: new feedback", f"User name: {name}\nComment: {comment}\nDate/time: {timestamp}")
    return jsonify({"ok": True, "message": "Thanks, your feedback is saved."})


@app.route("/admin")
def admin():
    if request.args.get("key") != os.getenv("ADMIN_KEY", "change-admin-key"):
        return "Admin access denied.", 403

    with get_db() as connection:
        visits = connection.execute("SELECT COUNT(*) AS count FROM visits").fetchone()["count"]
        downloads = connection.execute("SELECT COUNT(*) AS count FROM downloads").fetchone()["count"]
        comments = connection.execute("SELECT COUNT(*) AS count FROM comments").fetchone()["count"]
        top_videos = connection.execute("SELECT title, COUNT(*) AS count FROM downloads GROUP BY title ORDER BY count DESC LIMIT 5").fetchall()
        platforms = connection.execute("SELECT platform, COUNT(*) AS count FROM downloads GROUP BY platform ORDER BY count DESC").fetchall()

    return render_template("index.html", admin_data={
        "visits": visits,
        "downloads": downloads,
        "comments": comments,
        "top_videos": top_videos,
        "platforms": platforms,
    })


@app.route("/googledd736139896dc604.html")
def google_verification():
    return send_from_directory(BASE_DIR, "googledd736139896dc604.html")


@app.get("/about")
def about():
    return render_template("about.html")


@app.get("/how-to-use")
def how_to_use():
    return render_template("how-to-use.html")


@app.get("/faq")
def faq():
    return render_template("faq.html")


@app.get("/terms")
def terms():
    return render_template("terms.html")


@app.get("/robots.txt")
def robots():
    return (
        "User-agent: *\nAllow: /\nSitemap: https://avd.up.railway.app/sitemap.xml\n",
        200,
        {"Content-Type": "text/plain"},
    )


init_db()

if not FFMPEG_AVAILABLE:
    app.logger.warning(
        "FFmpeg/ffprobe not found on PATH. MP3 downloads and merging separate "
        "video/audio streams into MP4 will not be available until FFmpeg is installed."
    )

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
