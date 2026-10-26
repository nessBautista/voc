# Project configuration

Follow [the installation guide](../docs/setup-guide.md) for macOS or Windows setup. These files configure the main `voc-main` workspace; the standalone `notebooks/demos/` environment keeps its independent configuration.

## Settings versus secrets

| File | Responsibility |
| --- | --- |
| `workspace.toml` | Shared host ports, authentication mode, credential-file selection |
| `workspace.local.toml` | Ignored installation-specific workspace overrides |
| `storage.toml` | Safe local/personal storage defaults and namespace prefixes |
| `storage.local.toml` | Ignored member, bucket, region, or storage-mode overrides |
| `collector.toml` | Collection sources and update rules |
| `preparation.toml` | Workable-dataset preparation rules |
| Root `.env` | Private `JUPYTER_TOKEN` only |
| `aws.local.env` | Private AWS/OpenRouter keys or 1Password references |
| `aws.local.env.example` | Empty credential template; never real keys |

Setup creates the local override files, notebook token, and selected credential file. Edit only the values you need in local TOML; omitted values continue to inherit tracked defaults. New installations select local artifacts and personal datasets, with no AWS bucket or region.

`auth_mode = "env-file"` loads literal keys without 1Password. `auth_mode = "1password"` uses `op run` during `just up`. Both select `credential_file` in workspace TOML. Custom credential files must remain inside the checkout, outside `data/`, and end in `.env`; keep them ignored by Git. Setup rejects symlinks rather than modifying their targets.

Keep dotenv secrets on one line. Single-quote literal dollar signs so Compose cannot interpolate the host environment. Neither loader executes dotenv files as shell code. The selected credential file accepts `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, and optional `OPENROUTER_API_KEY`; leave them empty for local work. Root `.env` does not hold AWS credentials or configuration.

Local TOMLs and `.env` files are excluded from the Docker build context. Resolved credentials still enter the running workspace's environment; users with control of Docker can inspect that environment. Do not print or share Compose configuration containing secrets.

## Resolution and entrypoints

`scripts/workspace.sh` runs a stdlib Python configuration reader in a disposable Docker container. No host Python installation is required. It merges `workspace.toml` then optional `workspace.local.toml`, validates the result, and passes ports plus the selected storage region to Compose as runtime environment variables. It does not generate another persistent configuration copy.

All `just` commands use that bridge. It pins the project to `voc-main`, the repository bind mount, and the Compose file. Inherited project names, credentials, and legacy storage/port overrides cannot select the reference workspace. Only `up` resolves credentials; management and notebook commands do not require a 1Password login.

The Python storage loader in `src/voc/storage/shared/settings.py` resolves:

```text
Built-in defaults → config/storage.toml → config/storage.local.toml
                  → nonempty runtime environment → explicit destination argument
```

For standalone Python/CLI execution, existing runtime overrides remain available: `AWS_BUCKET`, `AWS_REGION`, `VOC_MEMBER_ID`, `VOC_EMBEDDINGS_PREFIX`, `VOC_DATASET_SOURCE`, and `VOC_ARTIFACT_DESTINATION`. They are not settings to put back into `.env`. Empty runtime values do not erase TOML values; set an empty string in local TOML to clear a configured bucket or region.

An explicit `load_storage(path)` / CLI `--config` / `VOC_STORAGE_CONFIG` selects one required file, without the automatic local overlay. The default files are optional for standalone library consumers. Explicit destination overrides affect personal artifacts, not dataset source. Changing storage modes does not move SQLite databases or previous artifacts.

After editing configuration or credentials, run `just up` again. Direct `docker compose` bypasses the TOML bridge and is not the supported workspace launcher. Direct Python consumers reload TOML when they call `load_storage()`.

## Updating an existing installation

Run your platform setup again. It transfers recognized nonsecret assignments from old `.env` files into local TOML, preserves the notebook token and selected credential file, and leaves a private `.env.before-toml` recovery copy. It preserves an existing S3 selection, so review the destination before starting.

Conflicting local overrides, unknown assignments, unsupported interpolation, invalid paths, or a different Compose project name stop migration before writes. Matching partial migrations can be retried. The unused `VOC_DASHBOARD_PORT` has no replacement. Migration does not touch `data/`, notebooks, or AWS.

## Verification boundary

The configuration refactor passed 159 focused offline tests covering setup, migration, storage precedence, launch validation, notebook creation, and storage inspection. A disposable-checkout smoke exercised the real setup and launch scripts with Docker Compose 5.6.1's configuration renderer: literal credentials, a simulated `op run` injection, custom paths/ports, inherited-environment isolation, and management without credentials. The real `voc storage-info` command consumed local TOML without creating runtime data.

No Docker engine or 1Password vault was available in that verification environment. Actual service startup, vault authorization, and Windows filesystem/runtime behavior still require the host checkpoints in the installation guide.
