-keepattributes *Annotation*,InnerClasses,EnclosingMethod,Signature,RuntimeVisibleAnnotations

# kotlinx.serialization — canonical keeps (generated serializers + companions).
-keepclassmembers class **$$serializer { *; }
-keep,includedescriptorclasses class **$$serializer { *; }
-keepclassmembers class ** {
    *** Companion;
}
-keepclasseswithmembers class ** {
    kotlinx.serialization.KSerializer serializer(...);
}
# Belt-and-braces: keep every @Serializable model this app declares and its serializer.
-keep @kotlinx.serialization.Serializable class app.azimuthphoto.mobile.** { *; }
-keep class app.azimuthphoto.mobile.**$$serializer { *; }
-keepclassmembers class app.azimuthphoto.mobile.** {
    *** Companion;
    kotlinx.serialization.KSerializer serializer(...);
}
-dontwarn kotlinx.serialization.**

-dontwarn okhttp3.**
-dontwarn okio.**
-dontwarn org.bouncycastle.**
-keep class org.bouncycastle.** { *; }
-dontwarn androidx.media3.**
-keep class androidx.media3.** { *; }
