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
from app.seeds.admin_seed import create_default_admin
from app.seeds.syllabus_seed import seed_syllabus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_default_admin()
    await seed_syllabus()
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


@app.get("/health")
async def health():
    return {"status": "ok", "service": "neurafix-api"}
