# Constellation for Android

A tiny native Android app that shows your Constellation photo display full-screen —
as an **always-on kiosk** and as a **system screensaver** (Daydream). No paid kiosk
app required. Built for cheap Android TV boxes (the ONN box, Chromecast-with-Google-TV,
Fire TV) and tablets.

It's just a full-screen WebView pointed at your Constellation server on the LAN —
the display, the ambient loop, and the wall all render exactly as they do in a browser.

## Two ways it runs

- **Kiosk (launch the app):** opens full-screen, hides the system bars, and keeps the
  screen awake. Good for a dedicated photo frame. **Long-press** anywhere to open
  Settings and change the server address.
- **Screensaver (Daydream):** on the box, go to **Settings → Display → Screensaver**
  (Android TV: *Settings → System → Screensaver / Ambient*), pick **Constellation**,
  and set "When to start" to *While charging / Always*. It now shows the loop whenever
  the box goes idle — a real screensaver, so any button press dismisses it.

## Configure the server

Default address is `http://10.0.0.5:8484/?lite=1`. Change it in Settings
(long-press in kiosk mode, or the gear on the Screensaver entry). Use the `?lite=1`
suffix on low-powered boxes — it drops the display to a lighter render path.

## Install (sideload)

Debug builds are unsigned-for-store but debug-signed, so they sideload fine:

1. Enable **Developer options → Install unknown apps** (or *Apps from unknown sources*).
2. Copy the `.apk` to the device (USB stick, `adb install Constellation-1.0-debug.apk`,
   or a download link) and open it.

## Build it yourself

Requires JDK 17 and the Android SDK (platform 34, build-tools 34.0.0). Point
`local.properties` at your SDK (`sdk.dir=...`), then:

```bash
cd android
./gradlew :app:assembleDebug
# → app/build/outputs/apk/debug/app-debug.apk
```

- `minSdk 26` (Android 8.0) · `targetSdk 34` · Kotlin · no third-party UI deps.
- Cleartext HTTP is allowed (`usesCleartextTraffic`) because the server is a plain-HTTP
  box on your own LAN.

## What's inside

| File | Role |
|------|------|
| `MainActivity.kt` | kiosk activity — immersive full-screen, keep-screen-on |
| `ConstellationDream.kt` | the screensaver (DreamService) |
| `KioskWebView.kt` | shared WebView setup (JS, autoplay, black bg) |
| `SettingsActivity.kt` | edit the server URL |
| `Prefs.kt` | stores the URL |
