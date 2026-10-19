"""Check packaging policy, relocated helper framing and release identity without Xcode."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "voc_package", Path(__file__).resolve().parents[1] / "scripts/package.py"
)
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


class PackagingTests(unittest.TestCase):
    def test_dependencies_ignore_executable_and_architecture_headers(self):
        output = """/path with spaces/VOCDesktopBridge:
/path with spaces/VOCDesktopBridge (architecture arm64):
    /System/Library/Frameworks/Foundation.framework/Versions/C/Foundation (compatibility version 300.0.0, current version 2676.0.0)
    /usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1351.0.0)
"""
        self.assertEqual(len(package.linked_libraries(output)), 2)
        for library in (
            "/Users/dev/DerivedData/libPrivate.dylib",
            "@rpath/libPrivate.dylib",
            "/opt/homebrew/lib/libPrivate.dylib",
        ):
            with (
                self.subTest(library=library),
                self.assertRaisesRegex(ValueError, "non-system"),
            ):
                package.linked_libraries(
                    f"helper:\n{library} (compatibility version 1.0.0, current version 1.0.0)\n"
                )
        with self.assertRaisesRegex(ValueError, "no recognizable"):
            package.linked_libraries("helper: not a Mach-O file")

    def test_compatibility_libraries_must_exist_inside_the_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp) / "App.app"
            library = app / "Contents/Frameworks/libswiftCompatibilitySpan.dylib"
            library.parent.mkdir(parents=True)
            dependency = "@rpath/libswiftCompatibilitySpan.dylib (compatibility version 0.0.0, current version 0.0.0)"
            with self.assertRaisesRegex(ValueError, "non-system"):
                package.linked_libraries(dependency, package.runtime_libraries(app))
            library.write_bytes(b"runtime")
            self.assertEqual(
                package.linked_libraries(dependency, package.runtime_libraries(app)),
                ["@rpath/libswiftCompatibilitySpan.dylib"],
            )
            # An included library cannot hide an absent transitive dependency.
            with self.assertRaisesRegex(ValueError, "non-system"):
                package.linked_libraries(
                    dependency.replace("CompatibilitySpan", "Missing"),
                    package.runtime_libraries(app),
                )
            library.unlink()
            outside = Path(temp) / "outside.dylib"
            outside.write_bytes(b"runtime")
            library.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "runtime entry"):
                package.runtime_libraries(app)

    def test_runtime_directory_rejects_unexpected_libraries_and_subdirectories(self):
        for name in ("libPrivate.dylib", "libswiftFake.txt", "nested"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                app = Path(temp)
                directory = app / "Contents/Frameworks"
                directory.mkdir(parents=True)
                if name == "nested":
                    (directory / name).mkdir()
                else:
                    (directory / name).write_bytes(b"unexpected")
                with self.assertRaisesRegex(ValueError, "runtime entry"):
                    package.runtime_libraries(app)

    def test_runpaths_must_work_after_relocation(self):
        def commands(paths):
            return "\n".join(
                f"Load command {i}\n cmd LC_RPATH\n cmdsize 48\n path {path} (offset 12)"
                for i, path in enumerate(paths)
            )

        bundled = "@executable_path/../Frameworks"
        self.assertEqual(package.check_runpaths(commands([bundled]), True), [bundled])
        package.check_runpaths(commands(["/usr/lib/swift", bundled]), True)
        with self.assertRaisesRegex(ValueError, "cannot resolve"):
            package.check_runpaths(commands(["/usr/lib/swift"]), True)
        for path in (
            "/Users/dev/DerivedData",
            "/Applications/Xcode.app/Contents/Developer/lib",
            "/opt/homebrew/lib",
            "@executable_path/../../..",
            "./Frameworks",
        ):
            with (
                self.subTest(path=path),
                self.assertRaisesRegex(ValueError, "search paths"),
            ):
                package.check_runpaths(commands([bundled, path]), True)
        # A dylib load-command name cannot masquerade as a runtime search path.
        self.assertEqual(
            package.check_runpaths(
                "cmd LC_LOAD_DYLIB\n name /usr/lib/libSystem.B.dylib (offset 24)"
            ),
            [],
        )

    def test_inventory_checks_helper_changes_extra_files_and_escaped_links(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp) / "App.app"
            helper = app / "Contents/Helpers/bridge"
            helper.parent.mkdir(parents=True)
            helper.write_bytes(b"original")
            helper.chmod(0o755)
            (helper.parent / "alias").symlink_to("bridge")
            base = {
                "bundle_id": "id",
                "version": "1",
                "build": "7",
                "minimum_macos": "15.0",
                "architecture": "arm64",
                "files": package.inventory(app),
            }
            package.compare_manifest(base, dict(base))
            helper.write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "files"):
                package.compare_manifest(
                    {**base, "files": package.inventory(app)}, base
                )
            helper.write_bytes(b"original")
            (app / "unexpected").write_bytes(b"extra")
            with self.assertRaisesRegex(ValueError, "files"):
                package.compare_manifest(
                    {**base, "files": package.inventory(app)}, base
                )
            (helper.parent / "outside").symlink_to(Path(temp) / "outside")
            with self.assertRaisesRegex(ValueError, "symlink"):
                package.inventory(app)

    def test_manifest_checks_version_and_both_executables(self):
        metadata = {
            "bundle_id": "id",
            "version": "1",
            "build": "7",
            "minimum_macos": "15.0",
            "architecture": "arm64",
            "files": {name: {"sha256": "abc"} for name in package.EXECUTABLES},
        }
        for key in metadata:
            with self.subTest(key=key), self.assertRaises(ValueError):
                package.compare_manifest({**metadata, key: "wrong"}, metadata)

    def test_build_inputs_include_new_sources_but_no_runtime_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Sources").mkdir()
            source = root / "Sources/one.swift"
            source.write_text("one")
            (root / ".runtime").mkdir()
            (root / ".runtime/private.swift").write_text("not an input")
            first = package.build_inputs(root)
            self.assertEqual(set(first), {"Sources/one.swift"})
            (root / "Sources/two.swift").write_text("two")
            self.assertNotEqual(first, package.build_inputs(root))

    def test_relocated_stdio_with_minimal_path_and_clean_eof(self):
        result = self.probe()
        self.assertEqual(result["tools"], ["voc_open", "voc_read_result"])
        self.assertEqual(result["eof_exit"], 0)

    def test_malformed_wrong_tools_and_failed_shutdown_are_rejected(self):
        for mode in ("bad_json", "wrong_tools", "bad_exit", "extra_output"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                self.probe(mode)

    def probe(self, mode="normal"):
        with tempfile.TemporaryDirectory(prefix="space in path ") as temp:
            helper = Path(temp) / "helper"
            helper.write_text(f"""#!{sys.executable}
import json,os,sys
assert os.getcwd()=="/"
assert os.environ["PATH"]=="/usr/bin:/bin:/usr/sbin:/sbin"
mode={json.dumps(mode)}
for line in sys.stdin:
    message=json.loads(line)
    if "id" not in message: continue
    if mode=="bad_json": print("invalid",flush=True); continue
    if message["method"]=="initialize":
        result={{"serverInfo":{{"name":"voc-desktop","version":"0.5.0"}},"protocolVersion":"2025-11-25","capabilities":{{"tools":{{"listChanged":False}}}}}}
    else:
        result={{"tools":[{{"name":"voc_open"}},{{"name":"wrong" if mode=="wrong_tools" else "voc_read_result","inputSchema":{{"properties":{{"work_id":{{}},"report_id":{{}},"offset":{{}}}}}}}}]}}
    print(json.dumps({{"jsonrpc":"2.0","id":message["id"],"result":result}}),flush=True)
if mode=="extra_output": print("unexpected",flush=True)
sys.exit(1 if mode=="bad_exit" else 0)
""")
            helper.chmod(0o755)
            return package.stdio_probe(helper)


if __name__ == "__main__":
    unittest.main()
