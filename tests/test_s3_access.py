"""Offline checks: refuse wrong/missing input before writing to S3."""

import io

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber
from click.testing import CliRunner

from src.mlops.check_s3 import CONTAINER_MARKER, HOST_MARKER, check_access, main

BUCKET = "voc-test-bucket"
HOST_KEY = "members/ness/tests/host-test/host.txt"


def response(payload):
    return {"Body": StreamingBody(io.BytesIO(payload), len(payload))}


@pytest.fixture
def session():
    # Synthetic credentials avoid all external credential discovery/network calls.
    real = boto3.Session(
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        region_name="us-east-2",
    )
    sts, s3 = real.client("sts"), real.client("s3")

    class Session:
        region_name = "us-east-2"

        def client(self, name, **kwargs):
            return {"sts": sts, "s3": s3}[name]

    with Stubber(sts) as identity, Stubber(s3) as objects:
        identity.add_response(
            "get_caller_identity",
            {
                "UserId": "test-user",
                "Account": "123456789012",
                "Arn": "arn:aws:iam::123456789012:user/test-user",
            },
        )
        yield Session(), identity, objects


def test_read_host_write_and_reload_container_marker(session):
    client, identity, objects = session
    objects.add_response(
        "get_object", response(HOST_MARKER), {"Bucket": BUCKET, "Key": HOST_KEY}
    )
    objects.add_response(
        "put_object",
        {},
        {
            "Bucket": BUCKET,
            "Key": ANY,
            "Body": CONTAINER_MARKER,
            "ContentType": "text/plain",
            "IfNoneMatch": "*",
        },
    )
    objects.add_response(
        "get_object", response(CONTAINER_MARKER), {"Bucket": BUCKET, "Key": ANY}
    )
    result = check_access(client, bucket=BUCKET, member="ness", host_key=HOST_KEY)
    assert result["status"] == "passed"
    assert result["container_marker"].startswith("members/ness/tests/")
    assert result["container_marker"].endswith("/container.txt")
    identity.assert_no_pending_responses()
    objects.assert_no_pending_responses()


def test_mismatched_host_marker_stops_before_upload_and_closes_body(session):
    client, _, objects = session
    data = io.BytesIO(b"wrong contents")
    objects.add_response("get_object", {"Body": StreamingBody(data, 14)})
    # There is deliberately no queued PutObject response: a write would fail.
    with pytest.raises(ValueError, match="contents did not match"):
        check_access(client, bucket=BUCKET, member="ness", host_key=HOST_KEY)
    assert data.closed
    objects.assert_no_pending_responses()


@pytest.mark.parametrize(
    "member,key",
    [
        ("../other", HOST_KEY),
        ("ness", "voc/datasets/latest.json"),
        ("ness", "members/other/tests/x/host.txt"),
    ],
)
def test_invalid_destination_rejected_before_network(member, key):
    with pytest.raises(ValueError):
        check_access(None, bucket=BUCKET, member=member, host_key=key)


def test_missing_container_credentials_has_actionable_error(monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    result = CliRunner().invoke(
        main,
        [
            "--bucket",
            BUCKET,
            "--region",
            "us-east-2",
            "--member",
            "ness",
            "--host-key",
            HOST_KEY,
        ],
    )
    assert result.exit_code == 1
    assert "just up-aws" in result.output
