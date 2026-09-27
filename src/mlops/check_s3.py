"""Verify container access using diagnostic objects, without changing storage."""

import json
import os
import re
import uuid

import boto3
import click
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

HOST_MARKER = b"VOC S3 access check\n"
CONTAINER_MARKER = b"VOC container S3 access check\n"


def read_marker(s3, bucket, key, expected):
    """Bound the download and close the HTTP stream, including on mismatch."""
    body = s3.get_object(Bucket=bucket, Key=key)["Body"]
    try:
        if body.read(len(expected) + 1) != expected:
            raise ValueError("S3 marker contents did not match; stopping the check")
    finally:
        body.close()


def check_access(session, *, bucket, member, host_key):
    """Read the host marker, then write/read a unique personal test object."""
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", member):
        raise ValueError("VOC_MEMBER_ID must contain only letters, digits, _ or -")
    if not bucket or "/" in bucket or ":" in bucket:
        raise ValueError("AWS_BUCKET must be a bucket name, without s3:// or a path")
    prefix = f"members/{member}/tests/"
    if not host_key.startswith(prefix) or not host_key.endswith("/host.txt"):
        raise ValueError("VOC_SMOKE_KEY must name your members/<id>/tests/.../host.txt")

    # Keep connectivity failures bounded rather than waiting on long SDK retries.
    options = Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 2})
    identity = session.client("sts", config=options).get_caller_identity()
    s3 = session.client("s3", config=options)
    read_marker(s3, bucket, host_key, HOST_MARKER)

    # Only write after the original host marker has been verified.
    key = f"{prefix}{uuid.uuid4()}/container.txt"
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=CONTAINER_MARKER,
        ContentType="text/plain",
        IfNoneMatch="*",
    )
    read_marker(s3, bucket, key, CONTAINER_MARKER)
    return {
        "status": "passed",
        "caller_arn": identity["Arn"],
        "bucket": bucket,
        "region": session.region_name,
        "host_marker": host_key,
        "container_marker": key,
    }


@click.command()
@click.option("--bucket", envvar="AWS_BUCKET", required=True)
@click.option("--region", envvar="AWS_REGION", required=True)
@click.option("--member", envvar="VOC_MEMBER_ID", required=True)
@click.option("--host-key", envvar="VOC_SMOKE_KEY", required=True)
def main(bucket, region, member, host_key):
    """Check STS identity and S3 read/write access from the container."""
    # This checkpoint specifically verifies the environment injection path.
    if not all(
        os.environ.get(k) for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
    ):
        raise click.ClickException(
            "Missing injected credentials. Start with just up-aws."
        )
    try:
        result = check_access(
            boto3.Session(region_name=region),
            bucket=bucket,
            member=member,
            host_key=host_key,
        )
    except ClientError as error:
        # Print the AWS error code, not credential-bearing request/debug details.
        code = error.response.get("Error", {}).get("Code", "Unknown")
        raise click.ClickException(
            f"AWS {error.operation_name} failed ({code})."
        ) from error
    except BotoCoreError as error:
        raise click.ClickException(
            f"AWS connection/credential error ({type(error).__name__}). "
            "Check region, connectivity and credential expiry."
        ) from error
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
