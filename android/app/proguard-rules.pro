-keepattributes *Annotation*,InnerClasses,EnclosingMethod

-keep class **$$serializer { *; }
-keepclassmembers class ** {
    *** Companion;
}
-keepclasseswithmembers class ** {
    kotlinx.serialization.KSerializer serializer(...);
}

-dontwarn okhttp3.**
-dontwarn okio.**
-dontwarn org.bouncycastle.**
-keep class org.bouncycastle.** { *; }
-dontwarn androidx.media3.**
-keep class androidx.media3.** { *; }
