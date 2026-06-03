import os
import ssl
import sys

from kombu import Queue

# Make backend/app importable from the workers directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from celery import Celery
from app.core.config import settings

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

celery_app = Celery(
    "neurafix",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "workers.tasks.test_task",
        "workers.tasks.knowledge_tasks",
        "workers.tasks.mcq_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry=True,
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=None,  # retry forever
    task_default_queue="kvi_ai_default",
    task_queues=[
        Queue("kvi_ai_default"),
        Queue("kvi_ai_mcq"),
        Queue("kvi_ai_knowledge"),
        Queue("kvi_ai_subjective"),
        Queue("kvi_ai_video"),
        Queue("kvi_ai_skill"),
    ],
    # Upstash closes idle TCP connections — these settings keep the connection
    # alive and recover quickly when it drops.
    broker_transport_options={
        "socket_timeout": 5,
        "socket_connect_timeout": 10,
        "socket_keepalive": True,
        "socket_keepalive_options": {},
        "retry_on_timeout": True,
        "visibility_timeout": 18000,  # 5 hours — tasks won't re-queue mid-run
        # Proactively checks the connection every 25 s so a dropped idle
        # connection is healed before the next task submission, not during it.
        # Value must be below Upstash's idle-disconnect threshold (~10 min).
        "health_check_interval": 25,
        # Cap the pool so a single worker process can't exhaust Upstash's
        # per-plan connection quota (free = 100, pay-as-you-go = 1000).
        "max_connections": 10,
    },
    result_backend_transport_options={
        "socket_timeout": 5,
        "socket_connect_timeout": 10,
        "socket_keepalive": True,
        "retry_on_timeout": True,
        "health_check_interval": 25,
        "max_connections": 10,
    },
    redis_socket_timeout=5,
    redis_socket_connect_timeout=10,
    redis_socket_keepalive=True,
    redis_retry_on_timeout=True,
)

# Enable TLS for Upstash or any rediss:// broker
if settings.REDIS_URL.startswith("rediss://"):
    _ssl_opts = {"ssl_cert_reqs": ssl.CERT_NONE}
    celery_app.conf.update(
        broker_use_ssl=_ssl_opts,
        redis_backend_use_ssl=_ssl_opts,
    )
