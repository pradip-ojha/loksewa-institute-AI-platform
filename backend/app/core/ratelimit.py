"""HTTP rate limiting (CLAUDE.md §22).

A single shared SlowAPI limiter, wired in `main.py`. Two key functions:
  • `client_ip_key`  — per source IP, for the unauthenticated login endpoint
    (brute-force / credential-stuffing guard).
  • `user_or_ip_key` — per authenticated user (the bearer token uniquely identifies the
    logged-in user for the access-token lifetime), falling back to IP. Used on the AI
    chat/stream endpoints to bound per-user model spend.

Default storage is in-process memory, which is correct for a single API process. For a
horizontally-scaled API, set `RATELIMIT_STORAGE_URI` (e.g. the Redis URL) so the limit is
shared across processes; we pass it through if present.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.core.config import settings


def client_ip_key(request: Request) -> str:
    return get_remote_address(request)


def user_or_ip_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        # The access token identifies the user for its lifetime; truncate to keep the
        # rate-limit key bounded. Falls back to IP for any unauthenticated reach.
        return auth[7:][:64]
    return get_remote_address(request)


# Optional shared storage (e.g. Redis) for multi-process deployments; defaults to memory.
_storage_uri = getattr(settings, "RATELIMIT_STORAGE_URI", "") or None

limiter = Limiter(key_func=user_or_ip_key, storage_uri=_storage_uri)

# Named limits referenced by route decorators, so the policy lives in one place.
LOGIN_LIMIT = "10/minute"
AI_CHAT_LIMIT = "30/minute"
