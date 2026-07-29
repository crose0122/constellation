package com.constellation.kiosk

import android.app.Activity
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast

/** Set the Constellation server URL. Also the Dream's settings screen, so you
 *  can configure it from Settings → Screensaver → Constellation → gear. */
class SettingsActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val pad = (24 * resources.displayMetrics.density).toInt()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.parseColor("#010409"))
            setPadding(pad, pad, pad, pad)
            gravity = Gravity.CENTER_HORIZONTAL
        }
        root.addView(TextView(this).apply {
            text = "Constellation server address"
            setTextColor(Color.parseColor("#dfeefa")); textSize = 18f
        })
        val input = EditText(this).apply {
            setText(Prefs.url(this@SettingsActivity))
            setTextColor(Color.WHITE); setHintTextColor(Color.GRAY)
            layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT)
        }
        root.addView(input)
        root.addView(TextView(this).apply {
            text = "e.g. http://10.0.0.5:8484/?lite=1  (use ?lite=1 on low-end devices)"
            setTextColor(Color.parseColor("#7f9bb3")); textSize = 12f
        })
        root.addView(Button(this).apply {
            text = "Save"
            setOnClickListener {
                Prefs.setUrl(this@SettingsActivity, input.text.toString())
                Toast.makeText(this@SettingsActivity, "Saved", Toast.LENGTH_SHORT).show()
                finish()
            }
        })
        setContentView(root)
    }
}
