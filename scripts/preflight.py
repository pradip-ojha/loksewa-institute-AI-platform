"""Demo preflight — go/no-go check for every dependency the platform needs.

Run this immediately before a demo, and again after any reboot or network change:

    python scripts/preflight.py

It verifies (in order) env config, Postgres + migration head, Redis, Pinecone
(dimension 3072), R2, Azure OpenAI (reasoning + embeddings), Gemini vision,
FFmpeg/ffprobe on PATH, and the bundled Nepali fonts. Each line prints PASS/FAIL
with a short detail. Exit code is 0 only when every check passes, so this doubles
as a gate you can put in front of the run scripts.

No data is written anywhere — every check is a read/ping.
"""
import asyncio
import io
import os
import shutil
import subprocess
import sys
import time

# Make `app...` importable when run from the project root.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND = os.path.join(_ROOT, "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

ALEMBIC_HEAD = "013"  # latest migration revision id (alembic/versions/013_*.py)

_results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    tag = "PASS" if ok else "FAIL"
    line = f"[{tag}] {name}"
    if detail:
        line += f" - {detail}"
    print(line, flush=True)


def check(name: str, fn) -> None:
    """Run a sync check, recording PASS/FAIL and never raising out."""
    try:
        detail = fn() or ""
        record(name, True, detail)
    except Exception as exc:  # noqa: BLE001 — preflight must survive any failure
        record(name, False, f"{type(exc).__name__}: {exc}")


async def acheck(name: str, coro_factory) -> None:
    try:
        detail = await coro_factory() or ""
        record(name, True, detail)
    except Exception as exc:  # noqa: BLE001
        record(name, False, f"{type(exc).__name__}: {exc}")


# ── individual checks ────────────────────────────────────────────────────────────

def _check_env() -> str:
    from app.core.config import settings
    missing = settings.missing_required()
    if missing:
        raise RuntimeError(f"missing/placeholder: {', '.join(missing)}")
    return "all required settings present"


async def _check_postgres() -> str:
    from sqlalchemy import text
    from app.core.database import engine
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        rev = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
    if rev != ALEMBIC_HEAD:
        raise RuntimeError(f"alembic at '{rev}', expected head '{ALEMBIC_HEAD}' — run: alembic upgrade head")
    return f"connected, migrations at head ({rev})"


def _check_redis() -> str:
    import redis
    from app.core.config import settings
    url = settings.REDIS_URL
    kwargs = {"socket_timeout": 10}
    if url.startswith("rediss://"):
        kwargs["ssl_cert_reqs"] = None  # Upstash TLS; match Celery's relaxed verification
    client = redis.from_url(url, **kwargs)
    try:
        client.ping()
    finally:
        client.close()
    return "PING ok"


def _check_pinecone() -> str:
    from app.core.config import settings
    from app.integrations.pinecone_client import get_pinecone
    stats = get_pinecone()._get_index().describe_index_stats()
    dim = getattr(stats, "dimension", None)
    if dim is None and isinstance(stats, dict):
        dim = stats.get("dimension")
    if dim != settings.EMBEDDING_DIMENSIONS:
        raise RuntimeError(f"index dimension {dim} != expected {settings.EMBEDDING_DIMENSIONS}")
    return f"index reachable, dimension={dim}"


def _check_r2() -> str:
    from app.integrations.r2_client import get_r2
    r2 = get_r2()
    # head_bucket confirms credentials + bucket without reading any object.
    r2._client.head_bucket(Bucket=r2.bucket)
    return f"bucket '{r2.bucket}' reachable"


async def _check_azure_reasoning() -> str:
    from app.ai.model_router import get_provider
    res = await get_provider("text").generate_text("Reply with exactly: ok")
    txt = (res.get("text") or "").strip()
    return f"reasoning replied ({txt[:20]!r})"


async def _check_azure_embed() -> str:
    from app.core.config import settings
    from app.ai.model_router import get_provider
    vecs = await get_provider("text").embed(["preflight ping"])
    if not vecs or len(vecs[0]) != settings.EMBEDDING_DIMENSIONS:
        raise RuntimeError(f"got {len(vecs[0]) if vecs else 0} dims, expected {settings.EMBEDDING_DIMENSIONS}")
    return f"embedding ok ({len(vecs[0])} dims)"


async def _check_gemini_vision() -> str:
    from app.ai.model_router import get_provider
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (48, 48), "white").save(buf, format="PNG")
    res = await get_provider("vision").generate_with_image(
        "Return JSON: {\"text\": \"ok\"}.", buf.getvalue(), schema={},
    )
    if not isinstance(res, dict):
        raise RuntimeError("vision returned a non-dict response")
    return "vision call ok"


def _check_ffmpeg() -> str:
    missing = [b for b in ("ffmpeg", "ffprobe") if shutil.which(b) is None]
    if missing:
        raise RuntimeError(f"not on PATH: {', '.join(missing)} (Video Tutor needs these)")
    out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=15)
    first = (out.stdout or out.stderr or "").splitlines()[0] if (out.stdout or out.stderr) else "ffmpeg"
    return first.strip()[:60]


def _check_fonts() -> str:
    font_dir = os.path.join(_BACKEND, "app", "processing", "fonts")
    needed = [
        "NotoSansDevanagari-Regular.ttf", "NotoSansDevanagari-Bold.ttf",
        "NotoSans-Regular.ttf", "NotoSans-Bold.ttf",
    ]
    missing = [f for f in needed if not os.path.isfile(os.path.join(font_dir, f))]
    if missing:
        raise RuntimeError(f"missing in {font_dir}: {', '.join(missing)} (Nepali PDF text will be tofu)")
    return "all 4 Noto fonts present"


# ── runner ─────────────────────────────────────────────────────────────────────

async def main() -> int:
    print("=== NeuraFix demo preflight ===", flush=True)
    t0 = time.monotonic()

    # Local checks first — they fail fast and don't cost any API calls.
    check("env / required settings", _check_env)
    check("ffmpeg + ffprobe on PATH", _check_ffmpeg)
    check("bundled Nepali fonts", _check_fonts)

    # Network checks.
    await acheck("Postgres (Neon) + migration head", _check_postgres)
    check("Redis (Upstash)", _check_redis)
    check("Pinecone index (dim 3072)", _check_pinecone)
    check("Cloudflare R2 bucket", _check_r2)
    await acheck("Azure OpenAI — reasoning", _check_azure_reasoning)
    await acheck("Azure OpenAI — embeddings", _check_azure_embed)
    await acheck("Gemini — vision", _check_gemini_vision)

    # Release the DB engine cleanly.
    try:
        from app.core.database import engine
        await engine.dispose()
    except Exception:
        pass

    failed = [name for name, ok, _ in _results if not ok]
    elapsed = time.monotonic() - t0
    print("-" * 50, flush=True)
    if failed:
        print(f"RESULT: {len(failed)} FAILED ({elapsed:.1f}s) - do NOT start the demo until fixed:", flush=True)
        for name in failed:
            print(f"  - {name}", flush=True)
        return 1
    print(f"RESULT: all {len(_results)} checks passed ({elapsed:.1f}s). Good to go.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
