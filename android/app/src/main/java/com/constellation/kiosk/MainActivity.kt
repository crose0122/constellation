package com.constellation.kiosk

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.view.WindowManager
import android.webkit.WebView

/** Kiosk / always-on display mode. Launching the app shows the Constellation
 *  loop full-screen with the screen kept awake — good for a dedicated photo
 *  display. A long-press opens Settings (to change the server URL). */
class MainActivity : Activity() {
    private var web: WebView? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        immersive()
        val wv = KioskWebView.create(this, Prefs.url(this))
        wv.setOnLongClickListener {
            startActivity(Intent(this, SettingsActivity::class.java)); true
        }
        web = wv
        setContentView(wv)
    }

    private fun immersive() {
        window.decorView.systemUiVisibility = (
            View.SYSTEM_UI_FLAG_FULLSCREEN or
            View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
            View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or
            View.SYSTEM_UI_FLAG_LAYOUT_STABLE)
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) immersive()
    }

    override fun onDestroy() {
        web?.let { it.loadUrl("about:blank"); it.destroy() }
        web = null
        super.onDestroy()
    }
}
