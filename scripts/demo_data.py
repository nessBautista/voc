"""Install the pinned demo publication from S3; serving always reads local data."""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from uuid import UUID

import boto3
import duckdb
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from voc.publications.store import (
    MAX_MANIFEST_BYTES,
    activate_bundle,
    current_publication,
    validate_bundle,
)


def load_selection(path):
    selection = json.loads(Path(path).read_text(encoding="utf-8"))
    if set(selection) != {"bucket", "region", "publication_id", "manifest_sha256"}:
        raise ValueError("Configuracion de publicacion incompleta o desconocida.")
    if selection["region"] != "us-east-2":
        raise ValueError("La Region del proyecto debe ser us-east-2.")
    identifier = selection["publication_id"]
    if str(UUID(identifier)) != identifier:
        raise ValueError("La publicacion debe tener un UUID canonico.")
    if not re.fullmatch(r"[a-f0-9]{64}", selection["manifest_sha256"]):
        raise ValueError("SHA-256 del manifiesto invalido.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", selection["bucket"]):
        raise ValueError("Nombre de bucket invalido.")
    return selection


def check_bundle(bundle, selection):
    payload = (Path(bundle) / "manifest.json").read_bytes()
    if hashlib.sha256(payload).hexdigest() != selection["manifest_sha256"]:
        raise ValueError("El manifiesto no coincide con la version aprobada.")
    manifest = validate_bundle(bundle)
    if manifest["publication_id"] != selection["publication_id"]:
        raise ValueError("La identidad de la publicacion no coincide.")
    if manifest["schema_version"] != "voc-data-state-v2":
        raise ValueError("El demo requiere una publicacion con analisis v2.")
    return manifest


def verify(selection, catalog, state):
    active = current_publication(state)
    if (
        active["publication_id"] != selection["publication_id"]
        or active["manifest_sha256"] != selection["manifest_sha256"]
    ):
        raise ValueError("Los datos locales no son la version acordada. Ejecuta sync.")
    check_bundle(Path(catalog) / active["publication_id"], selection)
    return active


def s3_client(selection):
    key = os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
    if not key or not secret:
        raise ValueError(
            "Completa las credenciales AWS de lectura en config/demo.local.env."
        )
    # Explicit credentials/region: no profiles, login, metadata service or custom endpoints.
    session = boto3.Session(
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        aws_session_token=os.environ.get("AWS_SESSION_TOKEN", "").strip() or None,
        region_name=selection["region"],
    )
    return session.client(
        "s3",
        config=Config(
            signature_version="s3v4",
            connect_timeout=10,
            read_timeout=60,
            retries={"mode": "standard", "total_max_attempts": 3},
            ignore_configured_endpoint_urls=True,
        ),
    )


def sync(selection, catalog, state, *, client=None):
    # Fast offline replay also works after the reader's credentials expire.
    try:
        return verify(selection, catalog, state)
    except LookupError:
        pass
    except ValueError:
        # Another publication or damaged local data: download and validate first.
        # activate_bundle rejects overwriting an existing UUID with different bytes.
        pass
    owned_client = client is None
    if owned_client:
        client = s3_client(selection)
    try:
        with tempfile.TemporaryDirectory(prefix="voc-demo-") as directory:
            bundle = Path(directory)
            prefix = f"publications/{selection['publication_id']}/"
            response = client.get_object(
                Bucket=selection["bucket"], Key=prefix + "manifest.json"
            )
            with response["Body"] as body:
                payload = body.read(MAX_MANIFEST_BYTES + 1)
            if len(payload) > MAX_MANIFEST_BYTES:
                raise ValueError("El manifiesto excede el limite permitido.")
            if hashlib.sha256(payload).hexdigest() != selection["manifest_sha256"]:
                raise ValueError(
                    "El manifiesto de S3 no coincide con la version aprobada."
                )
            manifest = json.loads(payload)
            if manifest["publication_id"] != selection["publication_id"]:
                raise ValueError("La identidad de la publicacion no coincide.")
            # Bound temporary storage; this demo's approved database is about 61 MB.
            if not 0 < manifest["database"]["size_bytes"] <= 256 * 1024 * 1024:
                raise ValueError(
                    "Tamano de publicacion fuera del limite del demo (256 MiB)."
                )
            (bundle / "manifest.json").write_bytes(payload)
            client.download_file(
                selection["bucket"],
                prefix + "publication.duckdb",
                str(bundle / "publication.duckdb"),
                Config=TransferConfig(use_threads=False),
            )
            check_bundle(bundle, selection)
            return activate_bundle(bundle, catalog, state)
    finally:
        if owned_client:
            client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("sync", "verify"))
    parser.add_argument(
        "--selection", type=Path, default=Path("/tools/publication.json")
    )
    parser.add_argument("--catalog", type=Path, default=Path("/app/catalog"))
    parser.add_argument("--state", type=Path, default=Path("/app/state"))
    args = parser.parse_args(argv)
    try:
        selection = load_selection(args.selection)
        result = (sync if args.action == "sync" else verify)(
            selection, args.catalog, args.state
        )
    except ClientError as error:
        # Never print request headers, credentials, provider responses or URLs.
        code = error.response.get("Error", {}).get("Code", "Unknown")
        if code in {
            "ExpiredToken",
            "InvalidAccessKeyId",
            "SignatureDoesNotMatch",
            "InvalidToken",
        }:
            print(
                "Credenciales AWS invalidas o vencidas. Solicita renovacion al responsable.",
                file=sys.stderr,
            )
        elif code in {"AccessDenied", "403", "404", "NoSuchKey", "NoSuchBucket"}:
            print(
                "S3 no permite leer esta publicacion o no existe. Consulta al responsable.",
                file=sys.stderr,
            )
        else:
            print(
                "No se pudo descargar la publicacion de S3. Revisa conexion y acceso.",
                file=sys.stderr,
            )
        return 1
    except BotoCoreError:
        print(
            "No se pudo conectar con S3. Revisa conexion y credenciales.",
            file=sys.stderr,
        )
        return 1
    except (ValueError, LookupError, OSError, TypeError, duckdb.Error) as error:
        print(f"No se pudo preparar el demo: {error}", file=sys.stderr)
        return 1
    print(f"Publicacion lista: {result['publication_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
