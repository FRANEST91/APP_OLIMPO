package com.olimpo.app

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.addCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity

private const val OLIMPO_URL = "https://olimpoapk-production.up.railway.app"

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private var filePathCallback: ValueCallback<Array<Uri>>? = null

    // Sin esto, un WebView normal ignora <input type="file"> por completo:
    // el botón de "Elegir archivo" del navegador no hace nada, no tira
    // error, simplemente nunca abre el selector nativo de Android.
    private val fileChooserLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            val data = result.data
            val uris = when {
                result.resultCode != Activity.RESULT_OK || data == null -> null
                data.clipData != null -> {
                    val clip = data.clipData!!
                    Array(clip.itemCount) { i -> clip.getItemAt(i).uri }
                }
                data.data != null -> arrayOf(data.data!!)
                else -> null
            }
            filePathCallback?.onReceiveValue(uris)
            filePathCallback = null
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        webView = WebView(this)
        setContentView(webView)

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            loadWithOverviewMode = true
            useWideViewPort = true
        }
        webView.webViewClient = WebViewClient()
        webView.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView?,
                callback: ValueCallback<Array<Uri>>,
                params: FileChooserParams?
            ): Boolean {
                filePathCallback?.onReceiveValue(null)
                filePathCallback = callback

                val intent = params?.createIntent() ?: Intent(Intent.ACTION_GET_CONTENT).apply {
                    type = "*/*"
                    addCategory(Intent.CATEGORY_OPENABLE)
                }
                return try {
                    fileChooserLauncher.launch(intent)
                    true
                } catch (e: Exception) {
                    filePathCallback = null
                    false
                }
            }
        }
        webView.loadUrl(OLIMPO_URL)

        onBackPressedDispatcher.addCallback(this) {
            if (webView.canGoBack()) webView.goBack() else finish()
        }
    }
}
