"""The foundation template creates only private, retained, secret-free resources."""

import json
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[4]
TEMPLATE = REPO / "docker/app/aws/foundation.yaml"
EXAMPLE = REPO / "docker/app/aws/foundation.parameters.example.json"


@pytest.fixture(scope="module")
def template():
    # Long-form intrinsic functions keep the file readable by a plain parser.
    return yaml.safe_load(TEMPLATE.read_text())


def resources(template, kind):
    return {
        name: body
        for name, body in template["Resources"].items()
        if body["Type"] == kind
    }


def test_template_defines_exactly_the_foundation_resources(template):
    kinds = sorted(body["Type"] for body in template["Resources"].values())
    assert kinds == [
        "AWS::ECR::Repository",
        "AWS::IAM::User",
        "AWS::Logs::LogGroup",
        "AWS::S3::Bucket",
        "AWS::S3::Bucket",
        "AWS::S3::Bucket",
        "AWS::S3::BucketPolicy",
        "AWS::S3::BucketPolicy",
        "AWS::S3::BucketPolicy",
        "AWS::SecretsManager::Secret",
        "AWS::SecretsManager::Secret",
    ]


def test_data_bearing_resources_are_retained_on_delete_and_replace(template):
    for name, body in template["Resources"].items():
        if body["Type"] == "AWS::S3::BucketPolicy":
            continue
        assert body.get("DeletionPolicy") == "Retain", name
        assert body.get("UpdateReplacePolicy") == "Retain", name


def test_buckets_block_public_access_encrypt_and_version(template):
    buckets = resources(template, "AWS::S3::Bucket")
    assert set(buckets) == {"PublicationsBucket", "BackupBucket", "OperatorBucket"}
    policies = {
        body["Properties"]["Bucket"]["Ref"]
        for body in resources(template, "AWS::S3::BucketPolicy").values()
    }
    assert policies == set(buckets)
    for name, body in buckets.items():
        properties = body["Properties"]
        assert all(properties["PublicAccessBlockConfiguration"].values()), name
        assert properties["OwnershipControls"]["Rules"] == [
            {"ObjectOwnership": "BucketOwnerEnforced"}
        ]
        rule = properties["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]
        assert rule["ServerSideEncryptionByDefault"]["SSEAlgorithm"] == "AES256"
        assert properties["VersioningConfiguration"] == {"Status": "Enabled"}


def test_bucket_policies_only_deny_plain_http(template):
    for name, body in resources(template, "AWS::S3::BucketPolicy").items():
        statements = body["Properties"]["PolicyDocument"]["Statement"]
        assert len(statements) == 1, name
        statement = statements[0]
        assert statement["Effect"] == "Deny" and statement["Principal"] == "*"
        assert statement["Condition"] == {"Bool": {"aws:SecureTransport": "false"}}
        assert len(statement["Resource"]) == 2


def test_backup_bucket_documents_its_retention(template):
    rules = template["Resources"]["BackupBucket"]["Properties"][
        "LifecycleConfiguration"
    ]["Rules"]
    expiry = next(rule for rule in rules if rule["Id"] == "expire-backups")
    assert expiry["Prefix"] == "backups/"
    assert expiry["ExpirationInDays"] == {"Ref": "BackupRetentionDays"}
    assert expiry["NoncurrentVersionExpiration"] == {
        "NoncurrentDays": {"Ref": "BackupRetentionDays"}
    }
    assert any(rule.get("ExpiredObjectDeleteMarker") for rule in rules)


def test_operator_bucket_expires_old_versions_but_keeps_current_objects(template):
    rules = template["Resources"]["OperatorBucket"]["Properties"][
        "LifecycleConfiguration"
    ]["Rules"]
    noncurrent = next(
        rule for rule in rules if rule["Id"] == "expire-noncurrent-versions"
    )
    assert noncurrent["NoncurrentVersionExpiration"] == {"NoncurrentDays": 30}
    assert not any("ExpirationInDays" in rule for rule in rules)
    assert any(rule.get("ExpiredObjectDeleteMarker") for rule in rules)


def test_operator_user_reaches_only_its_bucket_and_holds_no_key(template):
    [user] = resources(template, "AWS::IAM::User").values()
    assert user["Properties"]["UserName"]["Fn::Sub"] == "voc-${Environment}-operator"
    [policy] = user["Properties"]["Policies"]
    statements = policy["PolicyDocument"]["Statement"]
    for statement in statements:
        assert statement["Effect"] == "Allow"
        assert "OperatorBucket" in json.dumps(statement["Resource"]), statement["Sid"]
    deletes = [s for s in statements if "s3:DeleteObject" in json.dumps(s["Action"])]
    assert [s["Resource"]["Fn::Sub"] for s in deletes] == [
        "${OperatorBucket.Arn}/members/*"
    ]
    assert not any("s3:*" in json.dumps(s["Action"]) for s in statements)
    assert "AWS::IAM::AccessKey" not in {
        body["Type"] for body in template["Resources"].values()
    }
    for output_name, output in template["Outputs"].items():
        assert "SecretAccessKey" not in json.dumps(output), output_name


def test_repository_is_immutable_scanned_and_pruned(template):
    [(name, body)] = resources(template, "AWS::ECR::Repository").items()
    properties = body["Properties"]
    assert properties["ImageTagMutability"] == "IMMUTABLE"
    assert properties["ImageScanningConfiguration"] == {"ScanOnPush": True}
    assert properties["EmptyOnDelete"] is False
    policy = json.loads(
        properties["LifecyclePolicy"]["LifecyclePolicyText"]["Fn::Sub"].replace(
            "${ReleaseImagesToKeep}", "5"
        )
    )
    tagged, untagged = policy["rules"]
    assert tagged["selection"]["tagStatus"] == "tagged"
    assert tagged["selection"]["countType"] == "imageCountMoreThan"
    assert untagged["selection"]["tagStatus"] == "untagged"
    assert untagged["selection"]["countType"] == "sinceImagePushed"
    assert template["Parameters"]["ReleaseImagesToKeep"]["MinValue"] >= 2


def test_log_group_retention_is_a_parameter(template):
    [(name, body)] = resources(template, "AWS::Logs::LogGroup").items()
    assert body["Properties"]["RetentionInDays"] == {"Ref": "LogRetentionDays"}
    assert template["Parameters"]["LogRetentionDays"]["Default"] == 14


def test_secrets_are_created_empty_and_only_their_arns_are_exported(template):
    secrets = resources(template, "AWS::SecretsManager::Secret")
    assert set(secrets) == {"WebAuthSecret", "ModelProviderSecret"}
    for name, body in secrets.items():
        assert "SecretString" not in body["Properties"], name
        assert "GenerateSecretString" not in body["Properties"], name
    for name, output in template["Outputs"].items():
        value = json.dumps(output["Value"])
        assert "SecretString" not in value and "SecretValue" not in value, name
        assert output["Export"]["Name"]["Fn::Sub"].startswith("voc-${Environment}-")


def test_template_and_example_contain_no_identifiers_or_credentials():
    for path in (TEMPLATE, EXAMPLE):
        text = path.read_text()
        assert not re.search(r"\b\d{12}\b", text), path
        assert not re.search(r"AKIA[0-9A-Z]{16}|op://|/managed/", text), path
    example = json.loads(EXAMPLE.read_text())
    assert {entry["ParameterKey"] for entry in example} == {
        "Environment",
        "LogRetentionDays",
        "BackupRetentionDays",
        "ReleaseImagesToKeep",
    }


def test_every_named_resource_lives_under_the_environment_label(template):
    for name, body in template["Resources"].items():
        properties = body["Properties"]
        label = (
            properties.get("RepositoryName")
            or properties.get("BucketName")
            or properties.get("LogGroupName")
            or properties.get("UserName")
            or properties.get("Name")
        )
        if label is not None:
            assert "${Environment}" in label["Fn::Sub"], name
