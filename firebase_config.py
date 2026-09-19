"""Browser Firebase configuration only. No admin credentials or HERE key."""
import os


def public_firebase_config():
    emulators = os.environ.get("FIREBASE_USE_EMULATORS") == "1"
    return {
        "apiKey": "demo-api-key" if emulators else os.environ.get("FIREBASE_API_KEY", ""),
        "authDomain": "blyatbike.firebaseapp.com",
        "projectId": "demo-safer-ride" if emulators else "blyatbike",
        "storageBucket": "blyatbike.firebasestorage.app",
        "messagingSenderId": "797610387818",
        "appId": "1:797610387818:web:475832091e9add97e95d13",
        "useEmulators": emulators,
    }
