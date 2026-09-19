import { initializeApp } from "https://www.gstatic.com/firebasejs/12.19.0/firebase-app.js";
import {
  getAuth,
  GoogleAuthProvider,
  signInWithPopup,
  signOut,
  onAuthStateChanged,
  connectAuthEmulator,
} from "https://www.gstatic.com/firebasejs/12.19.0/firebase-auth.js";
import * as firestore from "https://www.gstatic.com/firebasejs/12.19.0/firebase-firestore.js";
import { createBikeStore } from "./bike-store.js";

export async function connectFirebase() {
  const response = await fetch("/api/config");
  if (!response.ok)
    throw new Error("Account services are unavailable. Please try again.");
  const { firebase: config } = await response.json();
  if (!config?.apiKey)
    throw new Error(
      "Bike registration is not connected yet. Please try again later.",
    );
  const { useEmulators, ...webConfig } = config;
  const app = initializeApp(webConfig);
  const auth = getAuth(app),
    db = firestore.getFirestore(app);
  if (useEmulators) {
    if (!["localhost", "127.0.0.1"].includes(location.hostname))
      throw new Error("Local test mode is only available on localhost.");
    connectAuthEmulator(auth, "http://127.0.0.1:9099", {
      disableWarnings: true,
    });
    firestore.connectFirestoreEmulator(db, "127.0.0.1", 8080);
  }
  const provider = new GoogleAuthProvider();
  provider.setCustomParameters({ prompt: "select_account" });
  return {
    auth,
    store: createBikeStore({ db, auth, firestore }),
    useEmulators,
    signIn: () => signInWithPopup(auth, provider),
    signOut: () => signOut(auth),
    onAuth: (callback) => onAuthStateChanged(auth, callback),
  };
}

export function friendlyError(error) {
  const messages = {
    "auth/popup-closed-by-user":
      "Sign-in was cancelled. You can try again when ready.",
    "auth/cancelled-popup-request": "A sign-in window is already open.",
    "auth/popup-blocked":
      "Allow the Google sign-in popup in your browser, then try again.",
    "auth/unauthorized-domain":
      "Sign-in is not enabled for this website address yet.",
    "auth/operation-not-allowed":
      "Google sign-in is not enabled yet. Please try again later.",
    "auth/configuration-not-found":
      "Google sign-in is not configured yet. Please try again later.",
    "auth/network-request-failed": "Check your connection and try again.",
    "permission-denied":
      "This change is not allowed for your account, or registration is still being configured.",
    "failed-precondition":
      "The registration service is still being configured. Please try again later.",
    unavailable:
      "Account services are temporarily unavailable. Check your connection and try again.",
  };
  return (
    messages[error?.code] ||
    error?.message ||
    "Something went wrong. Please try again."
  );
}
