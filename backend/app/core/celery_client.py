"""Celery app used by FastAPI to *send* tasks (it never runs them).

It applies the exact same shared config as the worker via `build_common_conf`,
so producer and consumer always agree on queues, routing, serialization and
transport. It deliberately does NOT import the worker task modules — the web
process stays light; tasks are dispatched by name through `send_task`, and
`task_routes` resolves the queue from the task name.
"""
import sys
from functools import lru_cache
from pathlib import Path

from celery import Celery

from app.core.config import settings

# Make the top-level `workers` package importable from the backend process so
# the single shared config module can be reused (avoids a second, drifting copy).
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from workers.celery_config import build_common_conf  # noqa: E402


@lru_cache
def get_celery() -> Celery:
    # No result backend: FastAPI never reads task results (it polls the DB job
    # row), so the web tier holds no result-backend connection to Upstash.
    app = Celery(broker=settings.REDIS_URL)
    app.conf.update(
        **build_common_conf(
            settings.REDIS_URL,
            task_timeout_seconds=settings.TASK_TIMEOUT_SECONDS,
        )
    )
    return app
