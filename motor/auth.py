"""Two ways in, never mixed.

- Page (`/`, forms under `/page/...`): one password, a signed session cookie
  for ~30 days. Same scheme as the hub (`SecondBrain-new/server/auth.py`),
  separate password and cookie.
- Providers (`/<provider>`, `/<provider>/<action>`): only
  `Authorization: Bearer <consumer token>`. The cookie does not open them, so
  a page in the browser can never be tricked into calling a provider.

Stdlib only: scrypt for the password, HMAC-SHA256 for the cookie. The hash
has no `$` in it (`scrypt:N:r:p:salt:hash`) so it sits in `.env` as is.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time

COOKIE_NAME = "motor_session"
SESSION_TTL = 30 * 24 * 3600

MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return f"scrypt:{_SCRYPT_N}:{_SCRYPT_R}:{_SCRYPT_P}:{_b64(salt)}:{_b64(digest)}"


def valid_hash(stored: str) -> bool:
    return stored.startswith("scrypt:") and stored.count(":") == 5


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split(":")
        if scheme != "scrypt":
            return False
        expected = _unb64(digest)
        actual = hashlib.scrypt(
            password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
            maxmem=64 * 1024 * 1024, dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _sign(secret: bytes, payload: str) -> str:
    return _b64(hmac.new(secret, payload.encode(), hashlib.sha256).digest())


def make_session(secret: bytes, now: float | None = None) -> str:
    expires = int((now if now is not None else time.time()) + SESSION_TTL)
    payload = f"v1.{expires}"
    return f"{payload}.{_sign(secret, payload)}"


def session_valid(secret: bytes, value: str | None, now: float | None = None) -> bool:
    if not value:
        return False
    payload, _, signature = value.rpartition(".")
    if not hmac.compare_digest(signature.encode(), _sign(secret, payload).encode()):
        return False
    version, _, expires = payload.partition(".")
    if version != "v1" or not expires.isdigit():
        return False
    return int(expires) > (now if now is not None else time.time())


def bearer(header: str | None) -> str | None:
    scheme, _, token = (header or "").partition(" ")
    token = token.strip()
    return token if scheme.lower() == "bearer" and token else None


class LoginThrottle:
    """Consecutive failed logins; one user, in memory (a restart resets it)."""

    def __init__(self, max_failures: int = MAX_FAILURES, lockout: float = LOCKOUT_SECONDS):
        self.max_failures = max_failures
        self.lockout = lockout
        self._failures = 0
        self._locked_until = 0.0
        self._lock = threading.Lock()

    def retry_after(self, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        with self._lock:
            return max(0, int(self._locked_until - now + 0.999))

    def failed(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        with self._lock:
            self._failures += 1
            if self._failures >= self.max_failures:
                self._locked_until = now + self.lockout
                self._failures = 0

    def succeeded(self) -> None:
        with self._lock:
            self._failures = 0
            self._locked_until = 0.0
