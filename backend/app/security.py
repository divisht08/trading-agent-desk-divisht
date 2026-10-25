"""Dashboard sign-in and the two-step check that guards Automatic (real-money) mode.

Step 1: the dashboard password, typed again at the moment of switching.
Step 2: a 6-digit code from an authenticator app (TOTP, RFC 6238), set up once.
Codes cannot be reused, and five wrong attempts lock the switch for 15 minutes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import timedelta
from urllib.parse import quote

from .config import settings
from .store import Store, now_ist, ts

STEP = 30
DIGITS = 6
MAX_FAILS = 5
LOCK_MINUTES = 15


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return str(code).zfill(DIGITS)


def totp_now(secret: str, at: float | None = None) -> str:
    return _hotp(secret, int((at or time.time()) // STEP))


def verify_totp(secret: str, code: str, last_counter: int = -1, window: int = 1) -> int | None:
    """Return the matched time-step counter, or None. Rejects counters already used."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return None
    now = int(time.time() // STEP)
    for c in range(now - window, now + window + 1):
        if c > last_counter and hmac.compare_digest(_hotp(secret, c), code):
            return c
    return None


def otpauth_uri(secret: str, account: str = "dashboard") -> str:
    issuer = quote(settings.totp_issuer)
    return f"otpauth://totp/{issuer}:{quote(account)}?secret={secret}&issuer={issuer}&digits={DIGITS}&period={STEP}"


def password_ok(pw: str) -> bool:
    return bool(settings.dashboard_password) and hmac.compare_digest(pw or "", settings.dashboard_password)


class TwoStep:
    def __init__(self, store: Store):
        self.store = store

    @property
    def enabled(self) -> bool:
        return bool(self.store.get("totp_secret"))

    def locked_until(self) -> str | None:
        lock = self.store.get("twostep_lock")
        if lock and now_ist().isoformat() < lock:
            return lock
        return None

    def _fail(self) -> None:
        n = int(self.store.get("twostep_fails", 0)) + 1
        self.store.set("twostep_fails", n)
        if n >= MAX_FAILS:
            self.store.set("twostep_lock", (now_ist() + timedelta(minutes=LOCK_MINUTES)).isoformat())
            self.store.set("twostep_fails", 0)

    def start_setup(self, password: str) -> dict:
        if not password_ok(password):
            raise PermissionError("Password is wrong (or DASHBOARD_PASSWORD is not set)")
        if self.enabled:
            raise PermissionError("An authenticator is already set up. Remove it first with a current code.")
        secret = new_secret()
        self.store.set("totp_pending", {"secret": secret, "ts": ts()})
        return {"secret": secret, "uri": otpauth_uri(secret)}

    def confirm_setup(self, code: str) -> None:
        pend = self.store.get("totp_pending")
        if not pend:
            raise PermissionError("Start the setup first")
        c = verify_totp(pend["secret"], code)
        if c is None:
            raise PermissionError("That code didn't match. Check the phone's clock and try the newest code.")
        self.store.set("totp_secret", pend["secret"])
        self.store.set("totp_last", c)
        self.store.set("totp_pending", None)
        self.store.event(None, "security", "done", "Authenticator app set up for two-step verification")

    def remove(self, password: str, code: str) -> None:
        self.check(password, code)
        self.store.set("totp_secret", None)
        self.store.event(None, "security", "done", "Authenticator app removed")

    def check(self, password: str, code: str) -> None:
        """Raise PermissionError unless both steps pass."""
        if self.locked_until():
            raise PermissionError(f"Too many wrong attempts. Try again after {self.locked_until()[11:16]}.")
        if not self.enabled:
            raise PermissionError("Set up the authenticator app first (Controls, Two-step verification)")
        if not password_ok(password):
            self._fail()
            raise PermissionError("Password is wrong")
        c = verify_totp(self.store.get("totp_secret"), code, int(self.store.get("totp_last", -1)))
        if c is None:
            self._fail()
            raise PermissionError("Authenticator code is wrong or already used")
        self.store.set("totp_last", c)
        self.store.set("twostep_fails", 0)


def session_token() -> str:
    return hmac.new(settings.secret_key.encode(), b"dashboard:" + settings.dashboard_password.encode(),
                    hashlib.sha256).hexdigest()
