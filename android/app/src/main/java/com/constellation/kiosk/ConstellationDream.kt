package com.constellation.kiosk

import android.service.dreams.DreamService
import android.webkit.WebView

/** The screensaver. Android runs this DreamService when the device goes idle
 *  (Settings → Screensaver → Constellation). Shows the ambient loop full-screen;
 *  any touch/remote press wakes and exits, as a screensaver should. */
class ConstellationDream : DreamService() {
    private var web: WebView? = null

    private companion object {
        const val UNSET_PAGE =
            "data:text/html,<body style='background:%23010409;color:%23dfeefa;" +
                "font:16px sans-serif;display:flex;align-items:center;" +
                "justify-content:center;height:100%25;margin:0'>" +
                "Set your Constellation server address in the app first.</body>"
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        isInteractive = false          // a touch dismisses it (normal screensaver)
        isFullscreen = true
        isScreenBright = true
        // Nothing configured yet — say so rather than dreaming a blank screen.
        val url = if (Prefs.isUnset(this)) UNSET_PAGE else Prefs.url(this)
        val wv = KioskWebView.create(this, url)
        web = wv
        setContentView(wv)
    }

    override fun onDetachedFromWindow() {
        web?.let { it.loadUrl("about:blank"); it.destroy() }
        web = null
        super.onDetachedFromWindow()
    }
}
