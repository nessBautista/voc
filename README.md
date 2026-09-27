hola


## Checking S3 access from Docker

This checkpoint supplies AWS credentials to the container and tests a small
object. Pipelines still use their existing artifact stores.

On your host, install AWS CLI and 1Password CLI, enable desktop CLI
integration, and complete the host upload/readback check. From the repository
root, create your personal reference file:

```bash
cp -n config/aws.refs.env.example config/aws.refs.env
```

`config/aws.refs.env` is excluded from Git and Docker builds; the `.example`
file is tracked. The existing `/workspace` bind mount still makes the reference
file visible inside the running container. It contains references, not keys.
On Windows, these shell commands and the current Justfile require a POSIX shell
(such as Git Bash); native PowerShell execution has not been validated. Each
member keeps their own values in this file.

Edit that file. In 1Password, use **Copy Secret Reference** for the access key ID
and secret access key fields. Paste the `op://...` references, not the secret
values. Fill in your bucket, region code, member ID, and the key of the host
marker containing `VOC S3 access check` followed by a newline. If you use a
section in your item, the copied reference includes that section.

Keep your existing repository `.env` settings (storage mode, seed and Jupyter).
The host reference file supplies only the AWS checkpoint values. You can select
another reference file with `VOC_AWS_REFS_FILE`.

From the host, run:

```bash
just up-aws
just s3-check
```

`up-aws` resolves references through `op run`, validates that credentials are
present, and applies `compose.aws-credentials.yaml` when creating the container.
It builds with the updated lock and reuses your current bind directory or named
volume. `s3-check` runs `src/mlops/check_s3.py` inside that container: it checks
identity, reads the host marker, and writes/reads a unique container marker.
Expect JSON with `"status": "passed"`; compare its `caller_arn` with your host
identity. Diagnostic objects remain under `members/<id>/tests/`.

`just shell`, `just down`, `just status`, and `just logs` still work without
1Password authentication. Use `just up-aws` on subsequent starts to provide AWS
access. Ordinary `just up` starts without this override, for local work.

Credentials are injected at runtime, never into image build arguments. Docker
retains them in container metadata: do not share expanded Compose/environment
or Docker inspection output. Expiring credentials require a fresh credential
session and container recreation with `just up-aws`; injection does not refresh
them automatically. Direct field references do not perform a plugin's role
assumption or MFA flow, so compare host and container identities.
