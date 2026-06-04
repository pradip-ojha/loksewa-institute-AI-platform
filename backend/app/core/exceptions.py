from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppException(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message


class ExternalServiceError(Exception):
    """Raised when a managed external service (Azure OpenAI, Pinecone, R2, Redis)
    fails in a way that is transient or outside our control. Carries a short,
    safe message suitable for surfacing to a job's error_message."""

    def __init__(self, service: str, message: str):
        self.service = service
        self.message = message
        super().__init__(f"{service}: {message}")


class AIResponseError(ExternalServiceError):
    """Raised when the AI provider returns a response we cannot use:
    empty choices, or a body that is not valid JSON when JSON was required.
    This is NOT retried as a transient error — the call completed but the
    content was unusable."""

    def __init__(self, message: str):
        super().__init__("azure_openai", message)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        import logging
        logging.getLogger("neurafix").exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "An unexpected error occurred."}},
        )
