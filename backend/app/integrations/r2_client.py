import io
from functools import lru_cache

import boto3
from botocore.config import Config

from app.core.config import settings


class R2Client:
    def __init__(self):
        self._client = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )
        self.bucket = settings.R2_BUCKET_NAME

    def upload_fileobj(self, key: str, file_obj: io.IOBase, content_type: str) -> None:
        self._client.upload_fileobj(
            file_obj,
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )

    def get_signed_url(self, key: str, expires_in: int = 3600) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def download_fileobj(self, key: str) -> bytes:
        buf = __import__("io").BytesIO()
        self._client.download_fileobj(self.bucket, key, buf)
        return buf.getvalue()

    def delete_object(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)


@lru_cache
def get_r2() -> R2Client:
    return R2Client()
