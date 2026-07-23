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

                // params.createIntent() arma un filtro MIME estricto a partir
                // del accept="..." del <input>. Muchos gestores de archivos
                // (y CSVs en particular) no reportan un MIME type que calce
                // con ese filtro exacto, así que el archivo queda gris o
                // directamente no aparece. Se usa "*/*" como tipo real (deja
                // ver y elegir cualquier archivo) y los tipos originales solo
                // como sugerencia vía EXTRA_MIME_TYPES, que la mayoría de los
                // gestores tratan como filtro rápido opcional, no un bloqueo.
                val intent = Intent(Intent.ACTION_GET_CONTENT).apply {
                    type = "*/*"
                    addCategory(Intent.CATEGORY_OPENABLE)
                    putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params?.mode == FileChooserParams.MODE_OPEN_MULTIPLE)
                    val tipos = params?.acceptTypes?.filter { it.isNotBlank() }?.toTypedArray()
                    if (!tipos.isNullOrEmpty()) {
                        putExtra(Intent.EXTRA_MIME_TYPES, tipos)
                    }
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
