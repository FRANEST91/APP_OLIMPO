package com.olimpo.app

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
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
                // Toast temporal para diagnosticar: si nunca aparece al tocar
                // "Elegir archivo", el problema es que esta función no se está
                // llamando (no depende de createIntent/launch). Si aparece
                // pero el selector no abre, el error queda en el segundo Toast.
                Toast.makeText(this@MainActivity, "Abriendo selector de archivos…", Toast.LENGTH_SHORT).show()

                filePathCallback?.onReceiveValue(null)
                filePathCallback = callback

                return try {
                    val intent = params?.createIntent() ?: Intent(Intent.ACTION_GET_CONTENT).apply {
                        type = "*/*"
                        addCategory(Intent.CATEGORY_OPENABLE)
                    }
                    fileChooserLauncher.launch(intent)
                    true
                } catch (e: Exception) {
                    filePathCallback = null
                    Toast.makeText(
                        this@MainActivity,
                        "No se pudo abrir el selector: ${e.message}",
                        Toast.LENGTH_LONG
                    ).show()
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
