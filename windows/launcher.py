import os
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

import requests
import webview

APP_URL = os.environ.get("VIDLOOM_URL", "https://avd.up.railway.app/")
DOWNLOAD_DIR = Path.home() / "Downloads"


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
        result = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG, directory=self.folder)
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
        with requests.get(url, stream=True, timeout=60) as response:
            response.raise_for_status()
            with target.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        return str(target)


def main():
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
