"""S3 object storage for evidence. Uploads go straight from the browser through presigned URLs."""

import math
from collections.abc import Iterator
from functools import cache
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import settings

MB = 1024 * 1024
MAX_PARTS = 10_000  # S3's limit per multipart upload
URL_TTL_SECONDS = 6 * 3600


def _client(endpoint: str | None) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


@cache
def client() -> Any:
    return _client(settings.s3_endpoint_url)


@cache
def presigner() -> Any:
    return _client(settings.s3_public_endpoint_url or settings.s3_endpoint_url)


def part_size(size: int) -> int:
    return max(settings.s3_part_size_mb * MB, math.ceil(size / MAX_PARTS))


def start_upload(key: str) -> str:
    response = client().create_multipart_upload(
        Bucket=settings.s3_bucket, Key=key, ContentType="application/octet-stream"
    )
    return response["UploadId"]


def part_urls(key: str, upload_id: str, count: int) -> list[str]:
    return [
        presigner().generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": settings.s3_bucket,
                "Key": key,
                "UploadId": upload_id,
                "PartNumber": number,
            },
            ExpiresIn=URL_TTL_SECONDS,
        )
        for number in range(1, count + 1)
    ]


def finish_upload(key: str, upload_id: str) -> int:
    """Complete a multipart upload from the parts S3 holds; return the object's size."""
    parts: list[dict[str, Any]] = []
    marker = 0
    while True:
        page = client().list_parts(
            Bucket=settings.s3_bucket, Key=key, UploadId=upload_id, PartNumberMarker=marker
        )
        parts += [{"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in page.get("Parts", [])]
        if not page.get("IsTruncated"):
            break
        marker = page["NextPartNumberMarker"]
    if not parts:
        raise ValueError("no parts were uploaded")
    client().complete_multipart_upload(
        Bucket=settings.s3_bucket, Key=key, UploadId=upload_id, MultipartUpload={"Parts": parts}
    )
    return client().head_object(Bucket=settings.s3_bucket, Key=key)["ContentLength"]


def abort_upload(key: str, upload_id: str) -> None:
    client().abort_multipart_upload(Bucket=settings.s3_bucket, Key=key, UploadId=upload_id)


def put(key: str, data: bytes) -> None:
    client().put_object(
        Bucket=settings.s3_bucket, Key=key, Body=data, ContentType="application/octet-stream"
    )


def stream(key: str) -> Iterator[bytes]:
    body = client().get_object(Bucket=settings.s3_bucket, Key=key)["Body"]
    yield from body.iter_chunks(MB)


def delete(key: str) -> None:
    client().delete_object(Bucket=settings.s3_bucket, Key=key)


def ensure_bucket() -> None:
    try:
        client().head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        client().create_bucket(Bucket=settings.s3_bucket)
