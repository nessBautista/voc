"""The host template keeps every door closed and grants the instance only its own needs."""

import base64
import json
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[4]
TEMPLATE = REPO / "docker/app/aws/host.yaml"
EXAMPLE = REPO / "docker/app/aws/host.parameters.example.json"


@pytest.fixture(scope="module")
def template():
    return yaml.safe_load(TEMPLATE.read_text())


def resource(template, kind):
    [(name, body)] = [
        item for item in template["Resources"].items() if item[1]["Type"] == kind
    ]
    return name, body


def test_security_group_has_no_inbound_rules_outside_a_demo_window(template):
    _, group = resource(template, "AWS::EC2::SecurityGroup")
    assert "SecurityGroupIngress" not in group["Properties"]
    assert {rule["ToPort"] for rule in group["Properties"]["SecurityGroupEgress"]} == {
        443,
        53,
        123,
    }
    _, demo = resource(template, "AWS::EC2::SecurityGroupIngress")
    assert demo["Condition"] == "DemoOpen"
    assert demo["Properties"]["FromPort"] == demo["Properties"]["ToPort"] == 8091
    assert "SourcePrefixListId" in demo["Properties"]
    assert "CidrIp" not in demo["Properties"]
    assert template["Parameters"]["DemoFrontDoor"]["Default"] == "false"
    assert template["Parameters"]["CloudFrontPrefixListId"]["Default"] == ""


def test_instance_requires_imdsv2_with_one_hop_and_encrypted_disks(template):
    _, launch = resource(template, "AWS::EC2::LaunchTemplate")
    data = launch["Properties"]["LaunchTemplateData"]
    assert data["MetadataOptions"]["HttpTokens"] == "required"
    assert data["MetadataOptions"]["HttpPutResponseHopLimit"] == 1
    assert data["BlockDeviceMappings"][0]["Ebs"]["Encrypted"] is True
    _, volume = resource(template, "AWS::EC2::Volume")
    assert volume["DeletionPolicy"] == volume["UpdateReplacePolicy"] == "Retain"
    assert volume["Properties"]["Encrypted"] is True
    assert volume["Properties"]["VolumeType"] == "gp3"
    _, instance = resource(template, "AWS::EC2::Instance")
    assert "KeyName" not in instance["Properties"]
    assert template["Parameters"]["InstanceType"]["Default"] == "t4g.medium"


def statements(template):
    _, role = resource(template, "AWS::IAM::Role")
    [policy] = role["Properties"]["Policies"]
    return role, {item["Sid"]: item for item in policy["PolicyDocument"]["Statement"]}


def test_host_role_is_scoped_to_its_own_resources(template):
    role, sids = statements(template)
    assert role["Properties"]["ManagedPolicyArns"] == [
        "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
    ]
    assert role["Properties"]["Path"] == "/voc/"
    for sid, statement in sids.items():
        if sid == "EcrLogin":
            assert statement["Action"] == "ecr:GetAuthorizationToken"
            continue
        assert statement["Resource"] != "*", sid
    text = json.dumps(sids["ReadPublicationsAndDeployments"]["Resource"])
    assert "/publications/*" in text and "/deployments/*" in text
    assert "backups" not in text
    assert json.dumps(sids["WriteBackups"]["Resource"]).count("/backups/*") == 1
    assert len(sids["ReadOwnSecrets"]["Resource"]) == 2
    assert all("Fn::ImportValue" in item for item in sids["ReadOwnSecrets"]["Resource"])
    assert sids["ListPublicationsAndDeployments"]["Condition"]["StringLike"][
        "s3:prefix"
    ] == [
        "publications/*",
        "deployments/*",
    ]


def user_data(template):
    _, instance = resource(template, "AWS::EC2::Instance")
    script, variables = instance["Properties"]["UserData"]["Fn::Base64"]["Fn::Sub"]
    return script, variables


def test_user_data_carries_no_secret_values_and_verifies_the_bundle(template):
    script, variables = user_data(template)
    assert "get-secret-value" not in script and "SecretString" not in script
    assert "sha256sum -c" in script
    assert "voc-host bootstrap" in script
    for line in script.splitlines():
        if "=" in line and line.startswith("VOC_"):
            key = line.split("=")[0]
            assert not key.endswith(("_SECRET", "_KEY", "_PASSWORD", "_TOKEN")), key
    assert set(variables) == {
        "RepositoryUri",
        "PublicationsBucket",
        "BackupBucket",
        "LogGroup",
        "WebAuthSecretArn",
        "ModelProviderSecretArn",
    }
    assert not any(
        "Secret" in name and "Arn" not in name for name in template["Parameters"]
    )


def test_demo_front_door_is_conditional_and_points_at_the_app_port(template):
    _, distribution = resource(template, "AWS::CloudFront::Distribution")
    assert distribution["Condition"] == "DemoOpen"
    config = distribution["Properties"]["DistributionConfig"]
    [origin] = config["Origins"]
    assert origin["CustomOriginConfig"] == {
        "HTTPPort": 8091,
        "OriginProtocolPolicy": "http-only",
        "OriginReadTimeout": 60,
        "OriginKeepaliveTimeout": 5,
    }
    assert config["PriceClass"] == "PriceClass_100"
    assert config["DefaultCacheBehavior"]["ViewerProtocolPolicy"] == "redirect-to-https"
    assert template["Outputs"]["DemoDomain"]["Condition"] == "DemoOpen"
    assert template["Parameters"]["PublicOrigin"]["Default"] == "http://localhost:8091"


def test_outputs_and_files_hold_no_identifiers_or_secrets(template):
    for name in template["Outputs"]:
        assert "Secret" not in name
    for path in (TEMPLATE, EXAMPLE):
        text = path.read_text()
        assert not re.search(r"\b\d{12}\b", text), path
        assert not re.search(r"AKIA[0-9A-Z]{16}|op://|/managed/", text), path
    example = {entry["ParameterKey"] for entry in json.loads(EXAMPLE.read_text())}
    assert example == set(template["Parameters"])


def test_user_data_decodes_to_a_strict_bash_script(template):
    script, _ = user_data(template)
    assert script.startswith("#!/bin/bash\nset -euo pipefail")
    # Fn::Sub leaves ${Name} for CloudFormation; the script itself must not need ${!...}.
    assert "${!" not in script
    assert base64.b64encode(script.encode()).decode()
