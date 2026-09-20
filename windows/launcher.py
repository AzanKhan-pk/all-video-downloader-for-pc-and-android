import os
import sys
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

import requests
import webview

APP_URL = os.environ.get("VIDLOOM_URL", "https://avd.up.railway.app/")
APP_VERSION = "1.1.0"
RELEASE_API = "https://api.github.com/repos/AzanKhan-pk/all-video-downloader-for-pc-and-android/releases/latest"
DOWNLOAD_DIR = Path.home() / "Downloads"


def version_tuple(value):
    nums = [int(x) for x in str(value).lstrip("v").split(".") if x.isdigit()]
    return tuple(nums + [0] * (3 - len(nums)))[:3]


def check_update():
    try:
        req = urllib.request.Request(RELEASE_API, headers={"User-Agent": "VidLoom-Updater"})
        with urllib.request.urlopen(req, timeout=8) as response:
            data = __import__("json").load(response)
        latest = str(data.get("tag_name", "")).lstrip("v")
        if not latest or version_tuple(latest) <= version_tuple(APP_VERSION):
            return False
        asset = next((a for a in data.get("assets", []) if a.get("name") == "VidLoom-Setup.exe"), None)
        if not asset:
            return False
        target = Path(tempfile.gettempdir()) / f"VidLoom-Setup-{latest}.exe"
        with requests.get(asset["browser_download_url"], stream=True, timeout=60) as response:
            response.raise_for_status()
            with target.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        subprocess.Popen([str(target), "/SILENT", "/CLOSEAPPLICATIONS"])
        return True
    except Exception:
        return False


def check_url():
    try:
        with urllib.request.urlopen(APP_URL, timeout=15) as response:
            return 200 <= response.status < 500
    except Exception:
        return False


class NativeApi:
    def __init__(self):
        self.folder = str(DOWNLOAD_DIR)
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    def choose_download_folder(self):
        result = webview.windows[0].create_file_dialog(
            webview.FOLDER_DIALOG, directory=self.folder
        )
        if result:
            self.folder = result[0] if isinstance(result, (list, tuple)) else result
        return self.folder

    def open_downloads_folder(self):
        folder = Path(self.folder)
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(str(folder))
        return str(folder)

    def save_file(self, relative_url, filename):
        target_dir = Path(self.folder)
        target_dir.mkdir(parents=True, exist_ok=True)
        safe = Path(filename or "VidLoom-Download").name
        target = target_dir / safe
        if not relative_url.startswith("/"):
            raise ValueError("Invalid download path")
        url = urljoin(APP_URL, relative_url)
        with requests.get(url, stream=True, timeout=120) as response:
            response.raise_for_status()
            with target.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        return str(target)


def main():
    if check_update():
        return

    api = NativeApi()
    if not check_url():
        webview.create_window(
            "VidLoom Video Downloader",
            html="<html><body style='font-family:Segoe UI,Arial;background:#111827;color:white;display:flex;align-items:center;justify-content:center;height:100vh'><div style='max-width:620px;padding:32px;text-align:center'><h2>VidLoom is temporarily unavailable</h2><p>Please check your internet connection and try again.</p></div></body></html>",
            width=1100,
            height=760,
        )
    else:
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.create_window(
            "VidLoom Video Downloader",
            APP_URL,
            width=1100,
            height=760,
            min_size=(820, 620),
            resizable=True,
            text_select=True,
            js_api=api,
        )
    webview.start(debug=False)


if __name__ == "__main__":
    main()
