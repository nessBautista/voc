#!/usr/bin/env python3
"""Build and verify a private Release ZIP; never install or replace an application."""

import argparse
import hashlib
import json
import os
import plistlib
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "VOCDataState.app"
BUNDLE_ID = "dev.voc.data-state"
EXECUTABLES = ("Contents/MacOS/VOCDataState", "Contents/Helpers/VOCDesktopBridge")
TOOL_NAMES = ["voc_open", "voc_read_result"]


def run(*args):
    """Capture diagnostics without printing child environments or credentials."""
    result = subprocess.run(
        list(map(str, args)), check=True, capture_output=True, text=True, timeout=60
    )
    return result.stdout


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def linked_libraries(output, bundled_libraries=()):
    # Architecture and executable headings are not load-command dependencies.
    libraries = [
        line.rsplit(" (compatibility version ", 1)[0].strip()
        for line in output.splitlines()
        if " (compatibility version " in line
    ]
    if not libraries:
        raise ValueError("otool returned no recognizable native dependencies.")
    for library in libraries:
        if library.startswith(("/System/Library/", "/usr/lib/")):
            continue
        # Only exact names of verified bundled Swift libraries may use @rpath.
        if library.startswith("@rpath/") and library[7:] in bundled_libraries:
            continue
        raise ValueError(f"Unexpected non-system dependency: {library}")
    return libraries


def runtime_libraries(app):
    """Accept a flat Swift runtime directory; audit every library, not just Span."""
    directory = app / "Contents/Frameworks"
    libraries = {}
    if not directory.exists():
        return libraries
    for path in directory.iterdir():
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.name.startswith("libswift")
            or path.suffix != ".dylib"
        ):
            raise ValueError(f"Unexpected bundled runtime entry: {path.name}")
        libraries[path.name] = path
    return libraries


def check_runpaths(output, require_bundle=False):
    """Reject developer search paths even when they happen to work on this Mac."""
    paths = []
    command = None
    for line in output.splitlines():
        value = line.strip()
        if value.startswith("cmd "):
            command = value[4:]
        elif command == "LC_RPATH" and value.startswith("path "):
            paths.append(value[5:].rsplit(" (offset ", 1)[0])
    allowed = {"@executable_path/../Frameworks", "/usr/lib/swift"}
    if any(path not in allowed for path in paths):
        raise ValueError(f"Unexpected runtime search paths: {paths}")
    if require_bundle and "@executable_path/../Frameworks" not in paths:
        raise ValueError("Executable cannot resolve its bundled Swift runtime.")
    return paths


def embed_swift_runtime(app):
    """Stage compatibility libraries using the selected Xcode, then sign inside out."""
    frameworks = app / "Contents/Frameworks"
    frameworks.mkdir(exist_ok=True)
    arguments = [
        "/usr/bin/xcrun",
        "swift-stdlib-tool",
        "--copy",
        "--platform",
        "macosx",
        "--destination",
        frameworks,
    ]
    for relative in EXECUTABLES:
        arguments.extend(["--scan-executable", app / relative])
    # This also follows transitive Swift dependencies. Missing libraries fail audit.
    run(*arguments)
    for library in runtime_libraries(app).values():
        architectures = run("/usr/bin/lipo", "-archs", library).split()
        if "arm64" not in architectures:
            raise ValueError(f"Swift runtime has no arm64 slice: {library.name}")
        if architectures != ["arm64"]:
            thin = library.with_suffix(".arm64")
            run("/usr/bin/lipo", library, "-thin", "arm64", "-output", thin)
            thin.replace(library)
        run("/usr/bin/codesign", "--force", "--sign", "-", library)
    # The helper already has its build signature. Seal the containing app last.
    run(
        "/usr/bin/codesign",
        "--force",
        "--sign",
        "-",
        "--options",
        "runtime",
        "--entitlements",
        ROOT / "Resources/VOCDataState.entitlements",
        app,
    )


def inventory(app):
    """Include every shipped file and reject links that escape the application."""
    app = app.resolve()
    entries = {}
    for path in sorted(app.rglob("*")):
        name = path.relative_to(app).as_posix()
        if path.is_symlink():
            if not path.resolve().is_relative_to(app) or not path.exists():
                raise ValueError(f"External or broken bundle symlink: {name}")
            entries[name] = {"symlink": os.readlink(path)}
        elif path.is_file():
            entries[name] = {
                "sha256": digest(path),
                "byte_length": path.stat().st_size,
                "executable": bool(path.stat().st_mode & 0o111),
            }
    return entries


def compare_manifest(observed, expected):
    """Match the release rather than merely accepting any correctly signed bundle."""
    for key in (
        "bundle_id",
        "version",
        "build",
        "minimum_macos",
        "architecture",
        "files",
    ):
        if observed[key] != expected[key]:
            raise ValueError(f"Installed bundle differs from release manifest: {key}")


def stdio_probe(helper):
    """Only initialize and list tools; never open VOC or read a saved work."""
    # Hide developer tool locations. The native helper must stand on its own.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in ("HOME", "TMPDIR", "USER", "LOGNAME", "LANG")
    }
    environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    with tempfile.TemporaryFile() as errors:
        child = subprocess.Popen(
            [str(helper), "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errors,
            cwd="/",
            env=environment,
            bufsize=0,
        )
        selector = selectors.DefaultSelector()
        selector.register(child.stdout, selectors.EVENT_READ)
        buffered = bytearray()

        def send(message):
            child.stdin.write(json.dumps(message).encode() + b"\n")
            child.stdin.flush()

        def receive(identity):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if b"\n" in buffered:
                    line, _, remainder = buffered.partition(b"\n")
                    buffered[:] = remainder
                    response = json.loads(line)
                    if response.get("id") != identity or "error" in response:
                        raise ValueError(
                            "Unexpected MCP response during package verification."
                        )
                    return response["result"]
                events = selector.select(max(0, deadline - time.monotonic()))
                if not events:
                    break
                data = os.read(child.stdout.fileno(), 65536)
                if not data:
                    raise ValueError("The bundled MCP exited before replying.")
                buffered.extend(data)
                if len(buffered) > 128 * 1024:
                    raise ValueError("The MCP discovery response exceeded its bound.")
            raise ValueError("The bundled MCP response timed out.")

        try:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "voc-installed-probe", "version": "1"},
                    },
                }
            )
            initialized = receive(1)
            if (
                initialized["serverInfo"]["name"] != "voc-desktop"
                or "tools" not in initialized["capabilities"]
                or initialized["protocolVersion"] != "2025-11-25"
            ):
                raise ValueError(
                    "Unexpected MCP server identity, version or capabilities."
                )
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            listed = receive(2)
            if [tool["name"] for tool in listed["tools"]] != TOOL_NAMES:
                raise ValueError("The installed helper advertised unexpected tools.")
            properties = listed["tools"][1]["inputSchema"]["properties"]
            if not {"work_id", "report_id", "offset"}.issubset(properties):
                raise ValueError("The installed helper lacks report retrieval.")
            child.stdin.close()
            if child.wait(timeout=5) != 0:
                raise ValueError("The MCP helper failed during clean EOF shutdown.")
            if buffered or child.stdout.read():
                raise ValueError("Unexpected extra output on the protocol channel.")
            return {
                "server": initialized["serverInfo"],
                "protocol": initialized["protocolVersion"],
                "tools": TOOL_NAMES,
                "eof_exit": 0,
            }
        finally:
            selector.close()
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            child.stdout.close()
            if not child.stdin.closed:
                child.stdin.close()


def check_bundle(app):
    """Check GUI and helper, then use only side-effect-free discovery commands."""
    app = app.expanduser().resolve()
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if (
        info.get("CFBundleIdentifier") != BUNDLE_ID
        or info.get("CFBundleExecutable") != "VOCDataState"
    ):
        raise ValueError("Not a VOC Data State application.")
    if "voc-data-state" not in [
        scheme
        for item in info.get("CFBundleURLTypes", [])
        for scheme in item.get("CFBundleURLSchemes", [])
    ]:
        raise ValueError("The installed bundle lacks its handoff URL scheme.")
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", app)
    files = inventory(app)
    libraries = {}
    runpaths = {}
    runtimes = runtime_libraries(app)
    binaries = [
        *EXECUTABLES,
        *[path.relative_to(app).as_posix() for path in runtimes.values()],
    ]
    for relative in binaries:
        binary = app / relative
        if relative not in files or (
            relative in EXECUTABLES and not files[relative].get("executable")
        ):
            raise ValueError(f"Missing bundled executable: {relative}")
        run("/usr/bin/codesign", "--verify", "--strict", binary)
        if run("/usr/bin/lipo", "-archs", binary).split() != ["arm64"]:
            raise ValueError(f"This private release requires arm64: {relative}")
        runpaths[relative] = check_runpaths(
            run("/usr/bin/otool", "-l", binary),
            require_bundle=bool(runtimes) and relative in EXECUTABLES,
        )
        libraries[relative] = linked_libraries(
            run("/usr/bin/otool", "-L", binary), runtimes
        )
    helper = app / EXECUTABLES[1]
    configuration = json.loads(run(helper, "--claude-config"))
    if configuration != {
        "mcpServers": {"voc-desktop": {"command": str(helper), "args": ["--stdio"]}}
    }:
        raise ValueError("Claude configuration does not identify this copied helper.")
    return {
        "bundle_id": BUNDLE_ID,
        "version": info["CFBundleShortVersionString"],
        "build": info["CFBundleVersion"],
        "minimum_macos": info["LSMinimumSystemVersion"],
        "architecture": "arm64",
        "files": files,
        "native_dependencies": libraries,
        "runtime_search_paths": runpaths,
        "mcp_probe": stdio_probe(helper),
    }


START_HERE = """# VOC · Estado de los datos — demo privada

Este paquete arm64 contiene la GUI y su helper MCP. macOS 15 o posterior.
Es un build privado con firma ad hoc, no una distribución notarizada ni para App Store.
No incluye datos, claves, configuración privada, Xcode, Python ni Node como dependencias de ejecución.
El servidor VOC debe estar disponible; el MCP local no lo inicia.

1. Detén el listener mcp-http con Ctrl+C, si está ejecutándose. Cierra VOC y Claude con Cmd+Q.
2. Copia VOCDataState.app a /Applications. Conserva una copia de cualquier instalación previa antes de reemplazarla.
   VOCDesktop.app del prototipo es otra aplicación: no la reemplaces.
3. Abre VOCDataState.app, elige el servidor de la demo (http://127.0.0.1:8091) y conecta.
4. Ejecuta en Terminal /Applications/VOCDataState.app/Contents/Helpers/VOCDesktopBridge --claude-config.
   En Claude → Settings → Developer → Edit Config, incorpora solo la entrada voc-desktop al JSON existente.
   Conserva otras integraciones; no pongas tokens en la entrada stdio. Reabre Claude y confirma voc-desktop running.
5. Cierra solo VOC con Cmd+Q. Pide a Claude abrir VOC con una pregunta sobre Google Play; conserva work_id.
   VOC aparece al frente y maximizado. Continúa dentro de VOC con «¿y de App Store?». Recarga para comprobar el historial.
   Una pregunta nueva desde Claude sustituye el trabajo anterior sin pulsar nada; su conversación se conserva.
6. Opcional: pulsa Preparar resultado para el agente y vuelve a Claude (Cmd+Tab) para recuperar el trabajo mediante voc_read_result.
   El receptor fija report_id, lee hasta eof y verifica bytes y SHA-256. El guardado Markdown en el Mac es opcional.

Las preguntas admitidas cubren fuentes, conteos, cobertura, actualización y estado informado de embeddings.
El modo fixture selecciona consultas sin un modelo; no identifica los datos como sintéticos.
VOC no mueve ninguna otra ventana ni necesita el permiso de Accesibilidad.
Si aparece un aviso de macOS sobre esta copia privada, revisa su procedencia. No desactives Gatekeeper.

La aplicación usa el estado existente en ~/Library/Application Support/VOCDataState.
Eliminar la app no borra ese historial. El paquete no modifica Claude, Keychain ni el servidor al instalarse.
Para verificación técnica opcional con Python 3.9+: python3 verify-installed.py /Applications/VOCDataState.app --manifest manifest.json.
Esta comprobación valida ambos ejecutables y el protocolo sin abrir la GUI ni enviar preguntas.
Las instrucciones HTTP de Argo y su token separado pertenecen al entorno de desarrollo, no a la demo con Claude stdio.
"""


def build_inputs(root):
    # Do not traverse .runtime, private releases or Xcode dependency caches.
    paths = [
        *root.glob("Sources/**/*.swift"),
        *root.glob("Tests/*.swift"),
        *root.glob("Resources/*"),
        *root.glob("scripts/*.py"),
        root / "VOCDataState.xcodeproj/project.pbxproj",
        root / "VOCDataState.xcodeproj/xcshareddata/xcschemes/VOCDataState.xcscheme",
        root
        / "VOCDataState.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved",
    ]
    return {
        p.relative_to(root).as_posix(): digest(p) for p in sorted(paths) if p.is_file()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Run packaging on the Mac with Xcode and Python 3.9 or later.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")  # noqa: UP017 -- Host Python 3.9 is supported.
    output = (args.output or ROOT / ".runtime/releases" / stamp).expanduser().resolve()
    if output.exists():
        parser.error("Output already exists; no release is overwritten.")
    # A fresh host build directory cannot overwrite a running development executable.
    derived = Path.home() / "Library/Developer/VOCDataState-Package" / stamp
    before = build_inputs(ROOT)
    subprocess.run(
        [
            "xcodebuild",
            "build",
            "-project",
            str(ROOT / "VOCDataState.xcodeproj"),
            "-scheme",
            "VOCDataState",
            "-configuration",
            "Release",
            "-destination",
            "platform=macOS,arch=arm64",
            "-derivedDataPath",
            str(derived),
            "-onlyUsePackageVersionsFromResolvedFile",
            "ARCHS=arm64",
            "ONLY_ACTIVE_ARCH=YES",
            "CODE_SIGN_IDENTITY=-",
        ],
        check=True,
    )
    if before != build_inputs(ROOT):
        raise ValueError(
            "Build inputs changed during packaging; retry from a stable checkout."
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".voc-package-", dir=output.parent) as temp:
        stage = Path(temp)
        payload = stage / "VOCDataState-private"
        payload.mkdir()
        app = payload / APP_NAME
        run("/usr/bin/ditto", derived / "Build/Products/Release" / APP_NAME, app)
        embed_swift_runtime(app)
        verified = check_bundle(app)
        manifest = {
            **verified,
            "schema_version": "voc-desktop-release-v1",
            "distribution": "private; ad-hoc signed; not notarized",
            "created_utc": stamp,
            "git_revision": run("git", "-C", ROOT, "rev-parse", "HEAD").strip(),
            "working_tree_dirty": bool(
                run("git", "-C", ROOT, "status", "--porcelain").strip()
            ),
            "xcode": run("xcodebuild", "-version").strip(),
            "build_macos": run("/usr/bin/sw_vers", "-productVersion").strip(),
            "source_sha256": before,
        }
        (payload / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (payload / "START-HERE.md").write_text(START_HERE)
        for name in ("package.py", "verify-installed.py"):
            shutil.copy2(ROOT / "scripts" / name, payload / name)
        archive = stage / "VOCDataState-private-arm64.zip"
        run(
            "/usr/bin/ditto",
            "-c",
            "-k",
            "--sequesterRsrc",
            "--keepParent",
            payload,
            archive,
        )
        extracted = stage / "verify"
        run("/usr/bin/ditto", "-x", "-k", archive, extracted)
        compare_manifest(check_bundle(extracted / payload.name / APP_NAME), verified)
        release = stage / "release"
        release.mkdir()
        shutil.move(archive, release / archive.name)
        (release / "SHA256SUMS").write_text(
            f"{digest(release / archive.name)}  {archive.name}\n"
        )
        shutil.copy2(payload / "manifest.json", release / "manifest.json")
        release.rename(output)
    print(f"Private package verified: {output}")
    print(
        "Close VOC, Claude and the optional HTTP listener before installing. See START-HERE.md inside the ZIP."
    )


if __name__ == "__main__":
    main()
