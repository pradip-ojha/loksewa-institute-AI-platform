import io
import logging
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings
from app.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)


class R2Client:
    def __init__(self):
        # Prefer the explicitly configured endpoint; fall back to the standard
        # account-scoped R2 endpoint only when one isn't provided.
        endpoint = settings.R2_PUBLIC_OR_ENDPOINT_URL or (
            f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
        )
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )
        self.bucket = settings.R2_BUCKET_NAME

    def upload_fileobj(self, key: str, file_obj: io.IOBase, content_type: str) -> None:
        try:
            self._client.upload_fileobj(
                file_obj,
                self.bucket,
                key,
                ExtraArgs={"ContentType": content_type},
            )
        except (BotoCoreError, ClientError) as exc:
            raise ExternalServiceError("r2", f"upload failed for {key}: {exc}") from exc

    def get_signed_url(self, key: str, expires_in: int = 3600) -> str:
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        except (BotoCoreError, ClientError) as exc:
            raise ExternalServiceError("r2", f"could not sign url for {key}: {exc}") from exc

    def download_fileobj(self, key: str) -> bytes:
        buf = io.BytesIO()
        try:
            self._client.download_fileobj(self.bucket, key, buf)
        except (BotoCoreError, ClientError) as exc:
            raise ExternalServiceError("r2", f"download failed for {key}: {exc}") from exc
        return buf.getvalue()

    def delete_object(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            # Deletion failures are logged but not fatal to the caller's workflow.
            logger.warning("R2 delete failed for %s: %s", key, exc)


@lru_cache
def get_r2() -> R2Client:
    return R2Client()
