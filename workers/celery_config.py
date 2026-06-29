"""Single source of truth for Celery configuration.

Both the worker app (`workers/celery_app.py`) and the FastAPI sender app
(`backend/app/core/celery_client.py`) apply `build_common_conf()`, so their
queues / routing / serialization / transport can never drift apart. Config
drift between those two apps is exactly what caused tasks to silently land on
an unconsumed queue, so everything they must agree on lives here and ONLY here.

Worker-only concerns (task `include` list, beat schedule) stay in
`workers/celery_app.py`.
"""
import ssl

from kombu import Queue

# The complete set of queues. This is the ONLY place queue names are listed.
# The worker (started without -Q) consumes all of them; task_routes maps each
# task to one of them; the sender declares them so producers and consumers match.
QUEUES = (
    "kvi_ai_default",
    "kvi_ai_mcq",
    "kvi_ai_knowledge",
    "kvi_ai_subjective",
    "kvi_ai_video",
    "kvi_ai_skill",
)

DEFAULT_QUEUE = "kvi_ai_default"

# Route every task to its queue BY TASK NAME. With this in place a `send_task`
# that forgets `queue=` still routes correctly instead of vanishing into an
# unconsumed queue. Add a line here when a new task module lands.
TASK_ROUTES = {
    "workers.tasks.knowledge_tasks.*": {"queue": "kvi_ai_knowledge"},
    "workers.tasks.mcq_tasks.*": {"queue": "kvi_ai_mcq"},
    "workers.tasks.mcq_test_tasks.*": {"queue": "kvi_ai_mcq"},
    "workers.tasks.subjective_tasks.*": {"queue": "kvi_ai_subjective"},
    "workers.tasks.video_tasks.*": {"queue": "kvi_ai_video"},
    "workers.tasks.skill_tasks.*": {"queue": "kvi_ai_skill"},
    "workers.tasks.maintenance.*": {"queue": "kvi_ai_default"},
    "workers.tasks.keepalive.*": {"queue": "kvi_ai_default"},
    "workers.tasks.test_task.*": {"queue": "kvi_ai_default"},
    "workers.tasks.personalization_tasks.*": {"queue": "kvi_ai_default"},
}

# Transport options shared by broker and result backend.
_REDIS_TRANSPORT_OPTS = {
    "socket_timeout": 5,
    "socket_connect_timeout": 10,
    "socket_keepalive": True,
    # Empty dict → OS default keepalive timers. Upstash resets idle TLS conns
    # after ~30 min; health_check_interval + beat keepalive compensate.
    "socket_keepalive_options": {},
    "retry_on_timeout": True,
    # Ping the server when a connection has been idle this many seconds — keeps
    # Upstash from dropping the worker's consumer connection.
    "health_check_interval": 15,
    "max_connections": 10,
    # Re-deliver a task only after this long if it was never acked (acks_late).
    # 6 h is far longer than any real task, so an in-progress task is never
    # re-queued underneath itself.
    "visibility_timeout": 21600,
}


def build_common_conf(redis_url: str, *, task_timeout_seconds: int) -> dict:
    """Return the Celery conf dict shared by the worker and the sender apps.

    `redis_url` decides TLS (rediss:// → CERT_NONE for Upstash).
    `task_timeout_seconds` drives the hard time limits.
    """
    conf: dict = {
        # ── Serialization ─────────────────────────────────────────────────
        "task_serializer": "json",
        "accept_content": ["json"],
        "result_serializer": "json",
        "timezone": "UTC",
        "enable_utc": True,
        # ── Status / acks ────────────────────────────────────────────────
        "task_track_started": True,
        "task_acks_late": True,
        "worker_prefetch_multiplier": 1,
        # The app tracks status via the processing_jobs DB row and never reads
        # AsyncResult — so don't store per-task results in Upstash at all.
        "task_ignore_result": True,
        # ── Hard time limits (OS-level backstop to the in-task wait_for) ──
        # NOTE: these rely on signals/billiard that DO NOT fire under
        # --pool=solo on Windows. On Windows the effective timeout is the
        # asyncio.wait_for in run_task; the stuck-job reaper is the
        # cross-platform backstop. On Linux/prefork these also apply.
        "task_soft_time_limit": task_timeout_seconds,
        "task_time_limit": task_timeout_seconds + 120,
        # ── Reconnection ─────────────────────────────────────────────────
        "broker_connection_retry": True,
        "broker_connection_retry_on_startup": True,
        "broker_connection_max_retries": None,   # retry forever
        "broker_pool_limit": None,               # one connection per process
        # ── Queues + routing (the anti-drift core) ───────────────────────
        "task_default_queue": DEFAULT_QUEUE,
        "task_queues": [Queue(q) for q in QUEUES],
        "task_routes": dict(TASK_ROUTES),
        # ── Transport: broker + result backend ───────────────────────────
        "broker_transport_options": dict(_REDIS_TRANSPORT_OPTS),
        "result_backend_transport_options": dict(_REDIS_TRANSPORT_OPTS),
        "redis_socket_timeout": 5,
        "redis_socket_connect_timeout": 10,
        "redis_socket_keepalive": True,
        "redis_retry_on_timeout": True,
    }

    if redis_url.startswith("rediss://"):
        _ssl_opts = {"ssl_cert_reqs": ssl.CERT_NONE}
        conf["broker_use_ssl"] = _ssl_opts
        conf["redis_backend_use_ssl"] = _ssl_opts

    return conf
