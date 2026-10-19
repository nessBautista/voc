"""Check web-revision evidence without Docker or macOS."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "web_reload", Path(__file__).with_name("check_web_reload.py")
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class WebReloadTests(unittest.TestCase):
    def test_two_revisions_preserve_the_application_and_show_distinct_markers(self):
        original = '<html><body><div id="root"></div><script src="/assets/app.js"></script></body></html>'
        a = probe.render_revision(original, "A")
        b = probe.render_revision(original, "B")
        self.assertNotEqual(a, b)
        for page, revision in ((a, "A"), (b, "B")):
            self.assertIn(f"versión {revision}", page)
            self.assertIn('<div id="root"></div>', page)
            self.assertIn('src="/assets/app.js"', page)
            self.assertEqual(page.count("<aside"), 1)
        with self.assertRaises(ValueError):
            probe.render_revision("invalid html", "A")
        with self.assertRaises(ValueError):
            probe.render_revision(original, "C")

    def test_either_native_binary_changing_invalidates_web_only_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp)
            for name in probe.EXECUTABLES:
                file = app / name
                file.parent.mkdir(parents=True)
                file.write_bytes(b"native")
            config = {
                "installed_app": str(app),
                "executable_sha256": probe.binary_hashes(app),
            }
            probe.assert_same_binaries(config)
            for name in probe.EXECUTABLES:
                (app / name).write_bytes(b"rebuilt")
                with self.assertRaisesRegex(ValueError, "changed"):
                    probe.assert_same_binaries(config)
                (app / name).write_bytes(b"native")


if __name__ == "__main__":
    unittest.main()
