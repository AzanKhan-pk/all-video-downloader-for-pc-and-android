import os
import sys
import time
import threading
import urllib.request

import webview

APP_URL = os.environ.get("VIDLOOM_URL", "https://avd.up.railway.app/")


def check_url():
    try:
        with urllib.request.urlopen(APP_URL, timeout=15) as response:
            return 200 <= response.status < 500
    except Exception:
        return False


def main():
    # Keep the exact existing web preview/UI; the desktop app is only a native
    # window around the same live application.
    if not check_url():
        webview.create_window(
            "VidLoom Video Downloader",
            html="""
            <html><body style='font-family:Segoe UI,Arial;background:#111827;color:white;display:flex;align-items:center;justify-content:center;height:100vh'>
            <div style='max-width:620px;padding:32px;text-align:center'>
            <h2>VidLoom is temporarily unavailable</h2>
            <p>Please check your internet connection and try again.</p>
            </div></body></html>
            """,
            width=1100,
            height=760,
        )
    else:
        webview.create_window(
            "VidLoom Video Downloader",
            APP_URL,
            width=1100,
            height=760,
            min_size=(820, 620),
            resizable=True,
            text_select=True,
        )
    webview.start(debug=False)


if __name__ == "__main__":
    main()
