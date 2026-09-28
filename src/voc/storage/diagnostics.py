"""Explicit connectivity check: only writes a unique personal test object."""
import uuid
from .settings import load_storage

def check_s3(client=None):
    import boto3
    settings = load_storage(destination="s3")
    client = client or boto3.client("s3", region_name=settings.region)
    key = f"{settings.members_prefix}/{settings.member_id}/{settings.installation_id}/tests/{uuid.uuid4()}/marker.txt"
    payload = b"VOC rebuild connectivity check\n"
    client.put_object(Bucket=settings.bucket, Key=key, Body=payload, IfNoneMatch="*")
    body = client.get_object(Bucket=settings.bucket, Key=key)["Body"]
    try:
        if body.read(len(payload) + 1) != payload:
            raise ValueError("S3 round trip differed")
    finally:
        body.close()
    return {"status": "passed", "key": key, "bucket": settings.bucket}
