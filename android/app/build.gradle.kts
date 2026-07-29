plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.constellation.kiosk"
    compileSdk = 34

    buildFeatures {
        buildConfig = true
    }

    defaultConfig {
        applicationId = "com.constellation.kiosk"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"

        // Bake in your server so the app needs no setup on first run:
        //   ./gradlew :app:assembleDebug -PconstellationUrl=http://10.0.0.5:8484/?lite=1
        // Left empty, the app opens Settings on first launch instead.
        val bakedUrl = (project.findProperty("constellationUrl") as String?).orEmpty()
        buildConfigField("String", "DEFAULT_URL", "\"$bakedUrl\"")
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
}
