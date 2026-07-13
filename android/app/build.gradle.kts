plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.serialization")
}

android {
    namespace = "app.azimuthphoto.mobile"
    compileSdk = 35

    defaultConfig {
        applicationId = "app.azimuthphoto.mobile"
        minSdk = 33
        targetSdk = 35
        versionCode = 3
        versionName = "1.0"
        vectorDrawables { useSupportLibrary = true }
    }

    signingConfigs {
        create("release") {
            storeFile = file("C:/Users/smast/.azimuth/release.keystore")
            storePassword = System.getenv("AZIMUTH_KEYSTORE_PASSWORD") ?: "azimuth-local"
            keyAlias = "azimuth"
            keyPassword = System.getenv("AZIMUTH_KEYSTORE_PASSWORD") ?: "azimuth-local"
        }
    }

    buildTypes {
        debug { isMinifyEnabled = false }
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            signingConfig = signingConfigs.getByName("release")
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    buildFeatures {
        compose = true
        buildConfig = true
    }
    composeOptions { kotlinCompilerExtensionVersion = "1.5.14" }

    packaging { resources { excludes += "/META-INF/{AL2.0,LGPL2.1}" } }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.09.02")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.6")
    implementation("androidx.navigation:navigation-compose:2.8.1")
    implementation("androidx.core:core-splashscreen:1.0.1")

    implementation("io.coil-kt:coil-compose:2.7.0")
    implementation("io.coil-kt:coil-video:2.7.0")

    implementation("androidx.work:work-runtime-ktx:2.9.1")
    implementation("androidx.datastore:datastore-preferences:1.1.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.6.3")
    implementation("org.bouncycastle:bcprov-jdk18on:1.78.1")
    implementation("androidx.exifinterface:exifinterface:1.3.7")
    implementation("androidx.media3:media3-exoplayer:1.4.1")
    implementation("androidx.media3:media3-ui:1.4.1")

    testImplementation("junit:junit:4.13.2")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
}
