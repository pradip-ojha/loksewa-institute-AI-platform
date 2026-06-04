from workers.celery_app import celery_app


@celery_app.task(
    name="workers.tasks.keepalive.ping",
    queue="kvi_ai_default",
    ignore_result=True,
)
def ping() -> None:
    """Sent every 4 minutes by Celery beat to keep the Upstash TLS connection alive."""
    pass
