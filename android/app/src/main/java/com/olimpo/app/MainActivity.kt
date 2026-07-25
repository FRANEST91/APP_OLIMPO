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

private const val OLIMPO_URL = "https://ofertaspotify.com"

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
            // Diagnóstico temporal: confirma qué llega realmente de vuelta
            // (resultCode, si hay data, cuántos URIs) para saber si el app
            // elegido (ej. Telegram) devuelve algo utilizable o no.
            Toast.makeText(
                this@MainActivity,
                "resultCode=${result.resultCode} uris=${uris?.size ?: 0}",
                Toast.LENGTH_LONG
            ).show()
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

                // El accept="..." del <input> (ej. ".csv") llega en
                // params.acceptTypes como extensión literal, no como MIME
                // type real (text/csv). Pasar eso en EXTRA_MIME_TYPES no lo
                // trata todo gestor de archivos como sugerencia descartable:
                // en algunos (MIUI incluido) corta la selección entera y
                // cancela sin mostrar ningún archivo. "*/*" alcanza como
                // único filtro — deja ver y elegir cualquier archivo.
                val intent = Intent(Intent.ACTION_GET_CONTENT).apply {
                    type = "*/*"
                    addCategory(Intent.CATEGORY_OPENABLE)
                    putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params?.mode == FileChooserParams.MODE_OPEN_MULTIPLE)
                }

                return try {
                    fileChooserLauncher.launch(Intent.createChooser(intent, "Elegir archivo"))
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
