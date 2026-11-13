"""Sessions, request guards and response headers.

Threats handled here:
  spoofed identity      signed, expiring, HttpOnly session cookie; the role comes from
                        the session, never from a request parameter
  cross-site requests   SameSite=Strict cookie, a required custom header and an Origin
                        check on every state-changing request; no CORS
  brute force / abuse   per-client rate limits, tighter on sign-in and writes
  oversized input       request bodies above a few kilobytes are refused
  script injection      strict Content-Security-Policy; no inline or third-party scripts
  clickjacking          frame-ancestors 'none'
  data at rest in caches  API responses are marked no-store
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import defaultdict, deque

from . import config

COOKIE = "mrs_session"
CSRF_HEADER = b"x-requested-with"
MAX_BODY = 8 * 1024
MAX_BATCH = 256 * 1024     # a batch of up to 200 payment events

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'")
BASE_HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
]


# ---------------------------------------------------------------- sessions
class Sessions:
    def __init__(self, secret: bytes, ttl: int = config.SESSION_HOURS * 3600):
        self.secret, self.ttl = secret, ttl

    def _sig(self, body: str) -> str:
        return hmac.new(self.secret, body.encode(), hashlib.sha256).hexdigest()

    def issue(self, role: str) -> str:
        body = f"{role}.{int(time.time()) + self.ttl}.{secrets.token_hex(8)}"
        return f"{body}.{self._sig(body)}"

    def read(self, value: str | None) -> str | None:
        """Return the role if the cookie is genuine and unexpired."""
        if not value or value.count(".") != 3:
            return None
        body, sig = value.rsplit(".", 1)
        if not hmac.compare_digest(sig, self._sig(body)):
            return None
        role, exp, _ = body.split(".")
        if role not in config.ROLES or not exp.isdigit() or int(exp) < time.time():
            return None
        return role


def code_ok(given: str | None) -> bool:
    """Constant-time check of the optional access code."""
    if not config.ACCESS_CODE:
        return True
    return hmac.compare_digest((given or "").encode(), config.ACCESS_CODE.encode())


# ---------------------------------------------------------------- rate limits
class RateLimiter:
    """Sliding-window counter per (client, bucket), kept in memory."""

    def __init__(self):
        self.hits: dict[tuple, deque] = defaultdict(deque)
        self.lock = threading.Lock()
        self._swept = time.monotonic()

    def allow(self, client: str, bucket: str, limit: int, window: float) -> float:
        """Record a hit. Returns 0 if allowed, else seconds until the next one is."""
        now = time.monotonic()
        with self.lock:
            q = self.hits[(client, bucket)]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return max(1.0, window - (now - q[0]))
            q.append(now)
            if now - self._swept > 300:  # forget clients that have gone quiet
                self._swept = now
                for k in [k for k, v in self.hits.items() if not v or now - v[-1] > 900]:
                    del self.hits[k]
        return 0.0

    def reset(self) -> None:
        with self.lock:
            self.hits.clear()


LIMITS = {
    "read": (config.RATE_READ, 60.0),      # all API traffic
    "write": (config.RATE_WRITE, 60.0),    # decisions and demo controls
    "signin": (config.RATE_SIGNIN, 300.0),  # sign-in attempts
    "ingest": (config.RATE_INGEST, 60.0),   # batches of payment events from a bank
}
limiter = RateLimiter()


def client_ip(scope) -> str:
    headers = dict(scope.get("headers") or [])
    if config.TRUST_PROXY:
        real = headers.get(b"x-real-ip")
        if real:
            return real.decode("latin-1").strip()
        fwd = headers.get(b"x-forwarded-for")
        if fwd:  # the right-most entry is the one our own proxy added
            return fwd.decode("latin-1").split(",")[-1].strip()
    client = scope.get("client")
    return client[0] if client else "unknown"


def is_https(scope) -> bool:
    headers = dict(scope.get("headers") or [])
    return scope.get("scheme") == "https" or headers.get(b"x-forwarded-proto", b"").startswith(b"https")


# ---------------------------------------------------------------- middleware
class SecurityMiddleware:
    """Pure ASGI middleware: guards requests on the way in, adds headers on the way out."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path, method = scope["path"], scope["method"]
        headers = dict(scope["headers"])
        is_api = path.startswith("/api/")
        extra = list(BASE_HEADERS)
        if is_https(scope):
            extra.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
        if is_api:
            extra.append((b"cache-control", b"no-store"))
        elif path.startswith("/static/vendor/"):
            extra.append((b"cache-control", b"public, max-age=604800"))
        else:
            extra.append((b"cache-control", b"no-cache"))

        async def reject(status: int, detail: str, more: list | None = None):
            body = json.dumps({"detail": detail}).encode()
            await send({"type": "http.response.start", "status": status,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode())] + extra + (more or [])})
            await send({"type": "http.response.body", "body": body})

        if is_api and path not in ("/api/health", "/api/ready"):
            ip = client_ip(scope)
            write = method not in ("GET", "HEAD", "OPTIONS")
            ingest = path == "/api/ingest/transactions"
            bucket = "signin" if (write and path == "/api/session" and method == "POST") else \
                     "ingest" if (write and ingest) else "write" if write else "read"
            wait = limiter.allow(ip, bucket, *LIMITS[bucket])
            if wait:
                return await reject(429, "Too many requests. Please wait a moment and try again.",
                                    [(b"retry-after", str(int(wait)).encode())])
            if write:
                # A browser cannot attach a custom header to a cross-site request, so either custom
                # header proves the request is not a forgery riding on someone's session cookie.
                if headers.get(CSRF_HEADER) != b"MuleRingShield" and not headers.get(b"x-ingest-key"):
                    return await reject(403, "Request refused: missing anti-forgery header.")
                origin = headers.get(b"origin")
                if origin:
                    host = headers.get(b"host", b"")
                    if origin.split(b"://", 1)[-1] != host:
                        return await reject(403, "Request refused: cross-site origin.")
                length = headers.get(b"content-length")
                if length is None and headers.get(b"transfer-encoding"):
                    return await reject(411, "A Content-Length header is required.")
                if length is not None and (not length.isdigit() or int(length) > (MAX_BATCH if ingest else MAX_BODY)):
                    return await reject(413, "Request body too large.")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                names = {k for k, _ in extra}
                message["headers"] = [(k, v) for k, v in message.get("headers", []) if k not in names] + extra
            await send(message)

        await self.app(scope, receive, send_with_headers)
