package com.constellation.kiosk

import android.service.dreams.DreamService
import android.webkit.WebView

/** The screensaver. Android runs this DreamService when the device goes idle
 *  (Settings → Screensaver → Constellation). Shows the ambient loop full-screen;
 *  any touch/remote press wakes and exits, as a screensaver should. */
class ConstellationDream : DreamService() {
    private var web: WebView? = null

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        isInteractive = false          // a touch dismisses it (normal screensaver)
        isFullscreen = true
        isScreenBright = true
        val wv = KioskWebView.create(this, Prefs.url(this))
        web = wv
        setContentView(wv)
    }

    override fun onDetachedFromWindow() {
        web?.let { it.loadUrl("about:blank"); it.destroy() }
        web = null
        super.onDetachedFromWindow()
    }
}
