package com.constellation.kiosk

import android.content.Context

/** Where the Constellation server lives. Defaults to the common LAN address in
 *  lite mode; editable in Settings so each household can point it at their box. */
object Prefs {
    private const val FILE = "constellation"
    private const val KEY_URL = "url"
    const val DEFAULT_URL = "http://10.0.0.5:8484/?lite=1"

    fun url(ctx: Context): String =
        ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .getString(KEY_URL, DEFAULT_URL) ?: DEFAULT_URL

    fun setUrl(ctx: Context, url: String) {
        ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .edit().putString(KEY_URL, url.trim()).apply()
    }
}
