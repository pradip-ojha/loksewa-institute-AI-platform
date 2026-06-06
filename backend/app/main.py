import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.modules.auth.router import router as auth_router
from app.modules.users.router import router as users_router
from app.modules.syllabus.router import router as syllabus_router
from app.modules.files.router import router as files_router
from app.modules.jobs.router import router as jobs_router
from app.modules.knowledge.router import router as knowledge_router
from app.modules.mcq.router import router as mcq_router
from app.modules.mcq_tests.router import router as mcq_tests_router
from app.modules.subjective.router import router as subjective_router
from app.modules.video.router import router as video_router
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
    title="NeuraFix AI API",
    description="AI learning platform API for Kirtipur Valley Institute",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

app.include_router(auth_router, prefix="/api")
app.include_router(users_router, prefix="/api")
app.include_router(syllabus_router, prefix="/api")
app.include_router(files_router, prefix="/api")
app.include_router(jobs_router, prefix="/api")
app.include_router(knowledge_router, prefix="/api")
app.include_router(mcq_router, prefix="/api")
app.include_router(mcq_tests_router, prefix="/api")
app.include_router(subjective_router, prefix="/api")
app.include_router(video_router, prefix="/api")
app.include_router(skill_router, prefix="/api")
app.include_router(dashboard_router, prefix="/api")
app.include_router(analytics_router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "neurafix-api"}
