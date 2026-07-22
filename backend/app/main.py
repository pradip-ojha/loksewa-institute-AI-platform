import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.ratelimit import limiter
from app.modules.auth.router import router as auth_router
from app.modules.users.router import router as users_router
from app.modules.exams.router import router as exams_router
from app.modules.syllabus.router import router as syllabus_router
from app.modules.files.router import router as files_router
from app.modules.jobs.router import router as jobs_router
from app.modules.knowledge.router import router as knowledge_router
from app.modules.mcq.router import router as mcq_router
from app.modules.mcq_tests.router import router as mcq_tests_router
from app.modules.subjective.router import router as subjective_router
from app.modules.video.router import router as video_router
from app.modules.tutor.router import router as tutor_router
from app.modules.skill_layer.router import router as skill_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.analytics.router import router as analytics_router
from app.seeds.admin_seed import create_default_admin
from app.seeds.syllabus_seed import seed_syllabus
from app.modules.skill_layer.service import seed_default_skills

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("neurafix")


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = settings.missing_required()
    if missing:
        logger.error(
            "Missing or placeholder required settings: %s. "
            "Features depending on these will fail until they are configured in backend/.env.",
            ", ".join(missing),
        )

    # FAIL CLOSED in production: never run publicly on a known/placeholder secret or the
    # weak default admin password. In development these are warnings (above); in
    # production they abort startup so the misconfiguration is caught at deploy time.
    if settings.ENVIRONMENT.lower() == "production":
        problems: list[str] = []
        if not settings.JWT_SECRET or settings.JWT_SECRET == "change-this-secret":
            problems.append("JWT_SECRET is unset or the placeholder value")
        if settings.DEFAULT_ADMIN_PASSWORD in ("", "Admin@123"):
            problems.append("DEFAULT_ADMIN_PASSWORD is unset or the weak default")
        if missing:
            problems.append(f"required settings still unset: {', '.join(missing)}")
        if problems:
            raise RuntimeError(
                "Refusing to start in production with insecure configuration: "
                + "; ".join(problems)
            )

    # Run seeds one at a time so a failure names the exact step instead of a
    # cryptic stack trace at startup.
    seeds = (
        ("admin account", create_default_admin),
        ("syllabus", seed_syllabus),
        ("default skills", seed_default_skills),
    )
    for name, fn in seeds:
        try:
            await fn()
        except Exception:
            logger.exception("Startup seed failed: %s", name)
            raise
    yield


app = FastAPI(
    title="NeuraFix Loksewa API",
    description="AI-powered Loksewa exam preparation platform API",
    version="1.0.0",
    lifespan=lifespan,
)

# ── Rate limiting (CLAUDE.md §22) ─────────────────────────────────────────────
# The limiter is shared via app.state; SlowAPIMiddleware enforces the per-route
# @limiter.limit decorators (login 10/min/IP, AI chat 30/min/user).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline hardening headers on every response. The API serves JSON (not HTML), so
    a strict CSP + nosniff + framing/Referrer controls cost nothing and block MIME
    sniffing / clickjacking / referrer leakage. HSTS is sent so browsers pin HTTPS once
    the app is served over TLS (harmless over plain HTTP during local dev)."""

    # Swagger UI / ReDoc load their bundle + inline init from a CDN, so the strict
    # JSON-API CSP (default-src 'none') would render the interactive docs blank. These
    # HTML doc routes get a docs-friendly CSP instead; every other (JSON) response keeps
    # the strict policy.
    _DOCS_PATHS = ("/docs", "/redoc")
    _DOCS_CSP = (
        "default-src 'none'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data: https://cdn.jsdelivr.net https://fastapi.tiangolo.com; "
        "font-src 'self' https://cdn.jsdelivr.net; "
        "connect-src 'self'; frame-ancestors 'none'"
    )

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path in self._DOCS_PATHS:
            response.headers["Content-Security-Policy"] = self._DOCS_CSP
        else:
            response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL],
    allow_credentials=True,
    # Pin to the methods/headers the SPA actually uses rather than credentialed wildcards.
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

register_exception_handlers(app)

app.include_router(auth_router, prefix="/api")
app.include_router(users_router, prefix="/api")
app.include_router(exams_router, prefix="/api")
app.include_router(syllabus_router, prefix="/api")
app.include_router(files_router, prefix="/api")
app.include_router(jobs_router, prefix="/api")
app.include_router(knowledge_router, prefix="/api")
app.include_router(mcq_router, prefix="/api")
app.include_router(mcq_tests_router, prefix="/api")
app.include_router(subjective_router, prefix="/api")
app.include_router(video_router, prefix="/api")
app.include_router(tutor_router, prefix="/api")
app.include_router(skill_router, prefix="/api")
app.include_router(dashboard_router, prefix="/api")
app.include_router(analytics_router, prefix="/api")


@app.get("/health")
async def health():
    """Liveness — the process is up. Always 200 (does not touch dependencies)."""
    return {"status": "ok", "service": "neurafix-api"}


@app.get("/health/ready")
async def health_ready():
    """Readiness — quick reachability probe of the core dependencies (DB, Redis,
    Pinecone). Returns per-dependency status and 200 only if all are reachable,
    503 otherwise. Cheap pings only (no AI calls); safe to hit during a demo to
    confirm the backend can actually do work."""
    import asyncio

    from fastapi.responses import JSONResponse

    async def _db() -> None:
        from sqlalchemy import text
        from app.core.database import engine
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    def _redis() -> None:
        import redis
        url = settings.REDIS_URL
        kwargs = {"socket_timeout": 8}
        if url.startswith("rediss://"):
            kwargs["ssl_cert_reqs"] = None
        client = redis.from_url(url, **kwargs)
        try:
            client.ping()
        finally:
            client.close()

    def _pinecone() -> None:
        from app.integrations.pinecone_client import get_pinecone
        get_pinecone()._get_index().describe_index_stats()

    async def _probe(name, fn):
        try:
            await (fn() if asyncio.iscoroutinefunction(fn) else asyncio.to_thread(fn))
            return name, {"ok": True}
        except Exception as exc:  # noqa: BLE001
            # Log the detail server-side, but NEVER return it: this endpoint is
            # unauthenticated, and dependency error strings leak host/driver/connection
            # internals useful for reconnaissance.
            logger.warning("Readiness probe %s failed: %s: %s", name, type(exc).__name__, exc)
            return name, {"ok": False}

    results = await asyncio.gather(
        _probe("database", _db),
        _probe("redis", _redis),
        _probe("pinecone", _pinecone),
    )
    deps = {name: status for name, status in results}
    all_ok = all(s["ok"] for s in deps.values())
    body = {"status": "ready" if all_ok else "degraded", "dependencies": deps}
    return JSONResponse(body, status_code=200 if all_ok else 503)
