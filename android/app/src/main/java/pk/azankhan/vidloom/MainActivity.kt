package pk.azankhan.vidloom

import android.app.AlertDialog
import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.webkit.CookieManager
import android.webkit.DownloadListener
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONObject

class MainActivity : AppCompatActivity() {
    private lateinit var webView: WebView
    private val appUrl = "https://avd.up.railway.app/"
    private val releaseApi = "https://api.github.com/repos/AzanKhan-pk/all-video-downloader-for-pc-and-android/releases/latest"
    private val appVersion = "1.1.0"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        webView = WebView(this)
        setContentView(webView)
        configureWebView()
        webView.loadUrl(appUrl)
        checkForUpdate()
    }

    private fun configureWebView() {
        CookieManager.getInstance().setAcceptCookie(true)
        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.settings.allowFileAccess = false
        webView.settings.allowContentAccess = true
        webView.settings.mediaPlaybackRequiresUserGesture = false
        webView.settings.setSupportZoom(false)
        webView.isLongClickable = true
        webView.setOnLongClickListener { false }
        webView.webChromeClient = WebChromeClient()
        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean = false
        }
        webView.setDownloadListener(DownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
            try {
                val request = DownloadManager.Request(Uri.parse(url))
                request.setMimeType(mimeType)
                request.addRequestHeader("User-Agent", userAgent)
                val cookie = CookieManager.getInstance().getCookie(url)
                if (!cookie.isNullOrBlank()) request.addRequestHeader("Cookie", cookie)
                request.setTitle("VidLoom download")
                request.setDescription("Downloading media")
                request.setNotificationVisibility(
                    DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED
                )
                request.setDestinationInExternalPublicDir(
                    Environment.DIRECTORY_DOWNLOADS,
                    "VidLoom-Download"
                )
                val manager = getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
                manager.enqueue(request)
                Toast.makeText(this, "Download started in Downloads", Toast.LENGTH_SHORT).show()
            } catch (error: Throwable) {
                Toast.makeText(this, "Download could not be started", Toast.LENGTH_LONG).show()
            }
        })
    }

    private fun checkForUpdate() {
        Thread {
            try {
                val connection = URL(releaseApi).openConnection() as HttpURLConnection
                connection.connectTimeout = 5000
                connection.readTimeout = 5000
                connection.setRequestProperty("User-Agent", "VidLoom-Android-Updater")
                val data = JSONObject(connection.inputStream.bufferedReader().use { it.readText() })
                val latest = data.optString("tag_name").removePrefix("v")
                val assets = data.optJSONArray("assets") ?: return@Thread
                var apkUrl = ""
                for (i in 0 until assets.length()) {
                    val asset = assets.getJSONObject(i)
                    if (asset.optString("name") == "VidLoom.apk") {
                        apkUrl = asset.optString("browser_download_url")
                        break
                    }
                }
                if (latest.isNotBlank() && compareVersions(latest, appVersion) > 0 && apkUrl.isNotBlank()) {
                    runOnUiThread {
                        AlertDialog.Builder(this)
                            .setTitle("VidLoom update available")
                            .setMessage("A newer version is ready. Open the download and install it when Android asks.")
                            .setNegativeButton("Later", null)
                            .setPositiveButton("Update") { _, _ ->
                                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(apkUrl)))
                            }
                            .show()
                    }
                }
            } catch (_: Throwable) {
                // Update checks are optional and must never block the app.
            }
        }.start()
    }

    private fun compareVersions(a: String, b: String): Int {
        val aa = a.split(".").map { it.toIntOrNull() ?: 0 }
        val bb = b.split(".").map { it.toIntOrNull() ?: 0 }
        for (i in 0 until maxOf(aa.size, bb.size)) {
            val x = aa.getOrElse(i) { 0 }
            val y = bb.getOrElse(i) { 0 }
            if (x != y) return x.compareTo(y)
        }
        return 0
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }

    override fun onDestroy() {
        webView.destroy()
        super.onDestroy()
    }
}
