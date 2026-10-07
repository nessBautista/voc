# Project configuration

This directory holds VOC's configuration values. The Python code that reads and validates them lives under `src/`.

## Files

| File | Purpose |
| --- | --- |
| `storage.toml` | Dataset source, artifact destination, and S3 prefixes |
| `aws.local.env.example` | Template for AWS keys or 1Password references |
| `aws.local.env` | Your private credentials file, copied from the example |
| `collector.toml` | Collection sources and update rules; added in a later lesson |
| `preparation.toml` | Rules for preparing the workable dataset; added in a later lesson |

## Local settings and credentials

The root `.env` holds your bucket, region, member ID, storage modes, ports, and notebook login token.

Both authentication modes use `config/aws.local.env`. Set `VOC_AWS_AUTH_MODE` in `.env` to match its contents:

- `env-file`: actual AWS credentials.
- `1password`: `op://...` references resolved at startup.

Keep `.env` and `aws.local.env` private; both are ignored by Git. Put no real credentials in `.example` files.

## How configuration is loaded

`just up` passes settings and credentials into the container. The storage loader in `src/voc/storage/settings.py` combines values in this order:

```text
Built-in defaults → storage.toml → nonempty environment variables → explicit destination override
```

Later values override earlier ones. The loader returns a `StorageSettings` object; the explicit destination override affects only personal artifacts.

After editing TOML, load the settings again. After changing container environment settings, run `just up` again. Generated databases and caches belong in `data/`, not here.

## Optional OpenRouter access

To request new topic interpretations, add `OPENROUTER_API_KEY` to the ignored
`config/aws.local.env`, as a key in env-file mode or an `op://...` reference in
1Password mode. `just up` passes the resolved value to the notebook services.
Leave it empty to view cached demo answers. Do not put keys in notebook cells.

For a running container, `scripts/load-openrouter-key.py` accepts the key on
standard input and saves it privately inside the container. A host-side example:

```bash
op read 'op://VAULT/ITEM/FIELD' | docker compose -f compose.yaml exec -T workspace python scripts/load-openrouter-key.py
```

This does not send a model request. The temporary key disappears when the
container is removed, so use the private credential file for future startups.
