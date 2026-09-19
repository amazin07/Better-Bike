"""Server-only Firebase access. ADC in deployment; opt-in CLI login for local dev."""
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
from functools import lru_cache

import firebase_admin
from firebase_admin import auth, credentials, firestore
from google.auth.credentials import AnonymousCredentials, Credentials


class CliCredentials(Credentials):
    def refresh(self, request):
        cli = shutil.which("firebase")
        if not cli:
            raise RuntimeError("Firebase CLI is not installed.")
        result = subprocess.run(
            ["node", str(Path(__file__).parent / "scripts/firebase-server-token.cjs"), cli],
            capture_output=True, text=True, timeout=30, check=False,
            env={**os.environ, "NODE_NO_WARNINGS": "1"},
        )
        if result.returncode:
            raise RuntimeError("Firebase CLI login is unavailable. Run firebase login.")
        data = json.loads(result.stdout)
        self.token = data["access_token"]
        self.expiry = dt.datetime.fromtimestamp(data["expires_at"] / 1000, dt.timezone.utc).replace(tzinfo=None)


class CredentialAdapter(credentials.Base):
    def __init__(self, credential):
        self.credential = credential

    def get_credential(self):
        return self.credential


@lru_cache(maxsize=1)
def server_app():
    local = os.getenv("FIREBASE_USE_EMULATORS") == "1"
    if local:
        os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
        os.environ.setdefault("FIREBASE_AUTH_EMULATOR_HOST", "127.0.0.1:9099")
        credential = CredentialAdapter(AnonymousCredentials())
    else:
        if os.getenv("FIRESTORE_EMULATOR_HOST") or os.getenv("FIREBASE_AUTH_EMULATOR_HOST"):
            raise RuntimeError("Emulator hosts require FIREBASE_USE_EMULATORS=1.")
        credential = (CredentialAdapter(CliCredentials())
                      if os.getenv("FIREBASE_USE_CLI_CREDENTIALS") == "1"
                      else credentials.ApplicationDefault())
    return firebase_admin.initialize_app(credential, {
        "projectId": "demo-bikebetter" if local else "blyatbike",
    }, name="rental-payments")


def verify_user(token):
    claims = auth.verify_id_token(token, app=server_app(), check_revoked=True)
    if claims.get("email_verified") is not True:
        raise ValueError("A verified account is required.")
    return claims["uid"]


class FirestoreRepository:
    def __init__(self, db=None):
        self._db = db

    @property
    def db(self):
        if self._db is None:
            self._db = firestore.client(app=server_app())
        return self._db

    def run(self, callback):
        db = self.db

        @firestore.transactional
        def execute(transaction):
            def read(collection, identifier):
                return db.collection(collection).document(identifier).get(transaction=transaction).to_dict()

            def write(collection, identifier, value):
                transaction.set(db.collection(collection).document(identifier), value)

            return callback(read, write)

        return execute(db.transaction())


def verified_email(uid):
    """Only the verified Firebase identity supplies Connect's required contact email."""
    from firebase_admin import auth
    user = auth.get_user(uid, app=server_app())
    if not user.email_verified or not user.email:
        raise ValueError('A verified email is required')
    return user.email
