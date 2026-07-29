package com.constellation.kiosk

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Color
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient

/** A full-screen WebView tuned for an ambient Constellation display: JavaScript
 *  on, video/animation autoplay without a tap, hardware-accelerated, no zoom
 *  controls, black background so there's no flash between pages. */
object KioskWebView {
    @SuppressLint("SetJavaScriptEnabled")
    fun create(ctx: Context, url: String): WebView {
        val wv = WebView(ctx)
        wv.setBackgroundColor(Color.BLACK)
        with(wv.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true                 // the wall's localStorage loop
            mediaPlaybackRequiresUserGesture = false // videos autoplay on the wall
            loadWithOverviewMode = true
            useWideViewPort = true
            cacheMode = android.webkit.WebSettings.LOAD_DEFAULT
        }
        wv.webViewClient = WebViewClient()           // keep navigation in-app
        wv.webChromeClient = WebChromeClient()
        wv.isVerticalScrollBarEnabled = false
        wv.isHorizontalScrollBarEnabled = false
        wv.loadUrl(url)
        return wv
    }
}
