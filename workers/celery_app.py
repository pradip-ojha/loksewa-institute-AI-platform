import os
import sys

# Make backend/app importable from the workers directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from celery import Celery
from app.core.config import settings
from workers.celery_config import build_common_conf

# Register ALL models with SQLAlchemy's metadata so foreign key relationships
# (e.g. knowledge_documents.created_by → users.id) can be resolved at runtime.
# Without these imports the worker process never loads the User model and
# SQLAlchemy raises NoReferencedTableError on first DB commit.
import app.modules.users.models          # noqa: F401
import app.modules.syllabus.models       # noqa: F401
import app.modules.files.models          # noqa: F401
import app.modules.jobs.models           # noqa: F401
import app.modules.knowledge.models      # noqa: F401
import app.modules.ai_audit.models       # noqa: F401
import app.modules.mcq.models            # noqa: F401
import app.modules.skill_layer.models   # noqa: F401

celery_app = Celery(
    "neurafix",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "workers.tasks.keepalive",
        "workers.tasks.maintenance",
        "workers.tasks.test_task",
        "workers.tasks.knowledge_tasks",
        "workers.tasks.mcq_tasks",
        "workers.tasks.skill_tasks",
    ],
)

# Import the shared runtime so its worker_shutdown signal handler (engine dispose)
# is registered regardless of task import order.
import workers.runtime  # noqa: E402,F401

# Shared queues / routing / transport / TLS — identical to the FastAPI sender
# app so they can never drift (drift is what silently misroutes tasks).
celery_app.conf.update(
    **build_common_conf(
        settings.REDIS_URL,
        task_timeout_seconds=settings.TASK_TIMEOUT_SECONDS,
    )
)

# ── Worker-only: beat schedule ────────────────────────────────────────────────
# Upstash forcibly resets idle TLS connections after ~30 min. A lightweight ping
# every 4 minutes keeps the connection warm; the reaper every 2 minutes fails any
# job that got stuck (never picked up, or running past the hard timeout).
# Start with   celery -A workers.celery_app.celery_app worker -B
# or a separate beat:  celery -A workers.celery_app.celery_app beat
celery_app.conf.beat_schedule = {
    "upstash-keepalive": {
        "task": "workers.tasks.keepalive.ping",
        "schedule": 240.0,              # every 4 minutes
    },
    "reap-stale-jobs": {
        "task": "workers.tasks.maintenance.reap_stale_jobs",
        "schedule": 120.0,              # every 2 minutes
    },
}
