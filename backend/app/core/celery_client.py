import ssl
from functools import lru_cache

from celery import Celery

from app.core.config import settings


@lru_cache
def get_celery() -> Celery:
    app = Celery(broker=settings.REDIS_URL, backend=settings.REDIS_URL)
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        broker_connection_retry_on_startup=True,
        broker_transport_options={
            "socket_timeout": 5,
            "socket_connect_timeout": 10,
            "socket_keepalive": True,
            "socket_keepalive_options": {},
            "retry_on_timeout": True,
            "visibility_timeout": 18000,
        },
        redis_socket_timeout=5,
        redis_socket_connect_timeout=10,
        redis_socket_keepalive=True,
        redis_retry_on_timeout=True,
    )
    if settings.REDIS_URL.startswith("rediss://"):
        _ssl = {"ssl_cert_reqs": ssl.CERT_NONE}
        app.conf.update(
            broker_use_ssl=_ssl,
            redis_backend_use_ssl=_ssl,
        )
    return app
