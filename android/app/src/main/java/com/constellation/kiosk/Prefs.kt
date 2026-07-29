package com.constellation.kiosk

import android.content.Context

/** Where the Constellation server lives. Empty until either the build baked one
 *  in (-PconstellationUrl) or the user set one in Settings — every household's
 *  server is on its own LAN, so there is no sensible universal default. */
object Prefs {
    private const val FILE = "constellation"
    private const val KEY_URL = "url"
    val DEFAULT_URL: String = BuildConfig.DEFAULT_URL

    fun url(ctx: Context): String =
        ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .getString(KEY_URL, DEFAULT_URL) ?: DEFAULT_URL

    /** True when there is nothing to load yet — send the user to Settings. */
    fun isUnset(ctx: Context): Boolean = url(ctx).isBlank()

    fun setUrl(ctx: Context, url: String) {
        ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE)
            .edit().putString(KEY_URL, url.trim()).apply()
    }
}
