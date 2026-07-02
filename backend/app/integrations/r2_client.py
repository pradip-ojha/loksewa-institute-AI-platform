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
        # Bounded connect/read timeouts + a small retry so a slow/stalled R2 call can
        # never block a worker's event loop indefinitely (spec §7 #1). Callers that run
        # inside the async loop offload these (blocking) S3 calls via asyncio.to_thread.
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            config=Config(
                signature_version="s3v4",
                connect_timeout=10,
                read_timeout=30,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
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

    def list_all_keys(self, prefix: str = "") -> list[str]:
        """Page through every object key in the bucket (optionally under a prefix)."""
        keys: list[str] = []
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                keys.extend(obj["Key"] for obj in page.get("Contents", []))
        except (BotoCoreError, ClientError) as exc:
            raise ExternalServiceError("r2", f"list failed: {exc}") from exc
        return keys

    def delete_objects(self, keys: list[str]) -> int:
        """Bulk-delete keys (S3 DeleteObjects, ≤1000 per call). Returns the count deleted."""
        deleted = 0
        for i in range(0, len(keys), 1000):
            batch = keys[i : i + 1000]
            try:
                self._client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
                )
                deleted += len(batch)
            except (BotoCoreError, ClientError) as exc:
                raise ExternalServiceError("r2", f"bulk delete failed: {exc}") from exc
        return deleted


@lru_cache
def get_r2() -> R2Client:
    return R2Client()
