"""Bounded S3 operations under one configured root."""
import base64
import hashlib
import json
import re
from contextlib import closing
from botocore.exceptions import ClientError
from .settings import _prefix
from voc.datasets.manifest import MAX_FILE_BYTES, MAX_JSON_BYTES, canonical_id, file_hash, json_bytes

class PublicationConflict(RuntimeError):
    """Another release won; an old retry must not replace it."""



def _missing(error):
    return error.response["Error"]["Code"] in ("NoSuchKey", "404", "NotFound")



class ReleaseObjects:
    """Bounded S3 operations; all keys stay under one explicitly selected root."""

    def __init__(self, client, bucket, prefix):
        self.client, self.bucket = client, bucket
        self.prefix = _prefix(prefix, "dataset prefix")

    def key(self, relative):
        return self.prefix + "/" + _prefix(relative, "object key")

    def get_json(self, relative):
        try:
            response = self.client.get_object(
                Bucket=self.bucket, Key=self.key(relative)
            )
        except ClientError as error:
            if _missing(error):
                return None, None
            raise
        with closing(response["Body"]) as stream:
            data = stream.read(MAX_JSON_BYTES + 1)
        if len(data) > MAX_JSON_BYTES:
            raise ValueError("Shared metadata exceeds size limit")
        return json.loads(data), response["ETag"]

    def latest(self):
        value, etag = self.get_json("latest.json")
        if value is not None:
            release = canonical_id(value["release_id"])
            if (
                value.get("format_version") != 1
                or value["manifest_key"] != self.key(f"releases/{release}.json")
                or not re.fullmatch(r"[0-9a-f]{64}", value["manifest_sha256"])
            ):
                raise ValueError("Invalid shared latest pointer")
        return value, etag

    def verify_file(self, relative, digest, size):
        try:
            response = self.client.get_object(
                Bucket=self.bucket, Key=self.key(relative)
            )
        except ClientError as error:
            if _missing(error):
                return False
            raise
        h, count = hashlib.sha256(), 0
        with closing(response["Body"]) as stream:
            while block := stream.read(1024**2):
                count += len(block)
                if count > size:
                    raise ValueError("Existing shared object size differs: " + relative)
                h.update(block)
        if count != size or h.hexdigest() != digest:
            raise ValueError("Existing shared object checksum differs: " + relative)
        return True

    def put_immutable(self, relative, path):
        size, digest = path.stat().st_size, file_hash(path)
        if size > MAX_FILE_BYTES:
            raise ValueError("Shared export exceeds the initial 1 GiB upload limit")
        if self.verify_file(relative, digest, size):
            return
        try:
            with path.open("rb") as stream:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=self.key(relative),
                    Body=stream,
                    IfNoneMatch="*",
                    ContentLength=size,
                    ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode(),
                )
        except ClientError as error:
            if error.response["Error"]["Code"] != "PreconditionFailed":
                raise
        if not self.verify_file(relative, digest, size):
            raise ValueError("Shared upload is missing: " + relative)

    def promote(self, pointer, expected_etag):
        current, _ = self.latest()
        if current == pointer:
            return  # The previous successful response may have been lost.
        condition = (
            {"IfMatch": expected_etag} if expected_etag else {"IfNoneMatch": "*"}
        )
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=self.key("latest.json"),
                Body=json_bytes(pointer),
                ContentType="application/json",
                **condition,
            )
        except ClientError as error:
            if error.response["Error"]["Code"] in (
                "PreconditionFailed",
                "ConditionalRequestConflict",
                "NoSuchKey",
                "404",
            ):
                current, _ = self.latest()
                if current == pointer:
                    return
                raise PublicationConflict(
                    "Shared latest changed. This saved attempt cannot replace it; "
                    "review the newer release before choosing a new run to share."
                ) from error
            raise
