# VidLoom Video Downloader — All Video Downloader for Free

A Flask web app that lets people paste a public video/audio link and download
it as MP4 (video) or MP3 (audio).

## What changed in this fix

The previous version tried to download media with a hand-written regex/HTML
scraper (`extract_direct_media` in the old `app.py`). That approach is
fragile: it breaks whenever a platform changes its page markup, it cannot
reliably tell a real media file apart from an error page, and it does not
truly support most of the requested platforms.

This version replaces that scraper with **[yt-dlp](https://github.com/yt-dlp/yt-dlp)**,
an actively-maintained, widely used extraction library — the same tool that
already downloads TikTok links correctly from the command line. The Flask
backend now calls yt-dlp directly instead of re-implementing extraction by
hand. All existing routes, the UI, the pause/resume/cancel controls, the
progress bar, the feedback form, the privacy/about/FAQ/terms pages,
analytics, the admin dashboard, the PWA/install support, and the
AdSense/`ads.txt` setup are unchanged.

### Reliably supported platforms

- YouTube (including Shorts)
- TikTok
- Instagram (public posts/reels)
- Facebook (public videos)
- X / Twitter (public videos)
- Reddit (public videos)
- Vimeo
- Dailymotion
- Twitch (public clips/videos)
- SoundCloud (public audio)
- Bilibili
- Other public pages that yt-dlp's own extractors support — the site's
  "Supported platforms" section links this out explicitly instead of
  implying guaranteed support for every possible site.

Two site URLs that were previously in the allow-list but were never actually
reliable or shown as a supported platform on the site (`box.com`,
`snapchat.com`) were removed so the app does not claim to support links it
cannot actually download.

### Download correctness fixes

- Downloads are only marked **completed** once the real output file exists
  on disk, has a non-zero size above a sanity threshold, and is not a
  `.part`/`.ytdl`/thumbnail/metadata file. The code also refuses to serve a
  file that starts with an HTML error page instead of real media.
- Video quality selection now asks yt-dlp for the closest real format at or
  below the requested height, instead of blindly downloading whatever a
  regex happened to find — this is what was producing tiny, corrupt files
  (e.g. a 138 KB "video" for YouTube).
- MP3 conversion and MP4 merging both go through yt-dlp's own
  FFmpeg-based post-processors. If FFmpeg/ffprobe are not installed, the
  app now shows a clear error instead of writing a broken file.
- Pause/Resume/Cancel still work exactly as before from the UI's point of
  view — the backend now signals yt-dlp to stop mid-download and resumes
  with `continuedl` on the same job when you press Resume.
- Private, login-only, DRM-protected, region-blocked, or removed media
  produce a clear, human-readable error instead of a fake success.

### Other fixes in this pass

- Removed a broken backup file (`app_backup.py`) that had an actual Python
  `SyntaxError`, a duplicate/stale backup (`app-backup.py`), a stray `git`
  text file, stale `__pycache__` bytecode, and orphaned root-level
  `index.html`/`script.js`/`style.css` copies that were not referenced by
  any Flask route or template (the real, live UI files are the ones in
  `templates/` and `static/`). The Vercel entry-point shims
  (`index.py`, `api/index.py`) were kept since `vercel.json` depends on them.
- SEO: page `<title>`, meta description, and structured data now clearly
  include both **"VidLoom Video Downloader"** and **"All Video Downloader
  for Free"**, and the meta description accurately explains pasting a
  supported public link to get MP4/MP3.
- Added Reddit, Vimeo, Twitch, and Bilibili to the "Supported platforms"
  section on the homepage to match what the backend can now actually do.
- No AdSense approval or ad display is claimed or guaranteed anywhere —
  the existing `ads.txt` and loader script are unchanged and still just
  make the site eligible to show ads if/when AdSense approves it.

## Requirements

- Python 3.10+ (3.13 also works)
- [FFmpeg](https://www.gyan.dev/ffmpeg/builds/) available on your `PATH`
  (required for MP3 audio and for merging separate video/audio streams
  into MP4 at higher qualities). Without FFmpeg, video downloads still
  work for qualities that already come as a single file, and audio
  downloads will show a clear error.

## Run it on Windows

Open **Command Prompt** or **PowerShell** in the project folder and run:

```bat
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open **http://127.0.0.1:5000** in your browser.

To stop the server, press `Ctrl+C` in the terminal.

### Installing FFmpeg on Windows

1. Download a build from https://www.gyan.dev/ffmpeg/builds/ (the
   "release essentials" zip is enough).
2. Extract it, e.g. to `C:\ffmpeg`.
3. Add `C:\ffmpeg\bin` to your Windows `PATH` environment variable, then
   open a **new** terminal window.
4. Confirm it works: `ffmpeg -version`

Alternatively, set an `FFMPEG_PATH` environment variable pointing directly
at `ffmpeg.exe` before running `python app.py`.

### Keeping downloads working

Platforms change their sites often, and yt-dlp is updated frequently to
keep up. If a platform that used to work stops working, update yt-dlp:

```bat
pip install -U yt-dlp
```

## Optional environment variables

| Variable | Purpose |
| --- | --- |
| `PORT` | Port to run on (default `5000`) |
| `SECRET_KEY` | Flask secret key |
| `ADMIN_KEY` | Key required as `?key=` on `/admin` |
| `FFMPEG_PATH` / `FFPROBE_PATH` | Explicit paths if not on `PATH` |
| `COOKIES_FILE` | Path to a `cookies.txt` file for sites that need a login-free-but-verified session (place a `cookies.txt` next to `app.py` and it is picked up automatically) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `NOTIFICATION_EMAIL` | Optional email notification for feedback form submissions |

## Deployment

- `Dockerfile` and `nixpacks.toml` already install FFmpeg, and now also
  install yt-dlp via `requirements.txt` — suitable for Railway/Docker-based
  hosts.
- `vercel.json` / `api/index.py` are kept for Vercel, but note that
  serverless platforms are a poor fit for this app's background download
  threads and temp-file usage regardless of extraction method — a
  long-running host (Docker/Railway/a VPS) is recommended for real use.
