"""The Reports export bundle (Qt-free): one zip with README, reports.json and the snapshots."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile

_DATA = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = _DATA
os.environ["APPDATA"] = _DATA
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import report_export  # noqa: E402
import storage  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\nfake"
BANK_FA = "بانک"


class ReportExportTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.store = storage.ReportStore(os.path.join(self._dir.name, "vault.db"))
        self.out = os.path.join(self._dir.name, "out.zip")

    def test_module_has_no_qt_dependency_beyond_an_optional_version_probe(self):
        with open(report_export.__file__, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(text.count("from PyQt6"), 1)
        self.assertNotIn("import PyQt6", text)
        self.assertIn("except ImportError", text)

    def test_zip_holds_readme_reports_and_snapshots(self):
        self.store.add("bank", {"headword": "bank", "persian_equivalent": BANK_FA}, note="wrong sense",
                       snapshot=PNG, app_version="abc")
        self.store.add("break the ice", {"headword": "break the ice", "is_idiom": True})  # no snapshot
        count = report_export.write_bundle(self.out, self.store.all_full(), {"theme": "Light Mode", "tts": "secret"})
        self.assertEqual(count, 2)
        with zipfile.ZipFile(self.out) as z:
            self.assertIsNone(z.testzip())
            names = set(z.namelist())
            doc = json.loads(z.read("reports.json").decode("utf-8"))
            readme = z.read("README.md").decode("utf-8")
            first_snap = doc["reports"][0]["snapshot"]
            self.assertEqual(z.read(first_snap), PNG)
        self.assertEqual(names, {"README.md", "reports.json", first_snap})
        self.assertRegex(first_snap, r"^snapshots/\d{3}-bank\.png$")
        bank, idiom = doc["reports"]
        self.assertEqual((bank["term"], bank["kind"], bank["note"], bank["app_version"]), ("bank", "word", "wrong sense", "abc"))
        self.assertEqual(bank["payload"]["persian_equivalent"], BANK_FA)
        self.assertEqual((idiom["kind"], idiom["snapshot"]), ("idiom", None))
        env = doc["environment"]
        self.assertEqual(env["engine_version"], storage.lexical_engine.ENGINE_VERSION)
        self.assertEqual(env["settings"], {"theme": "Light Mode"})  # only known harmless settings
        for key in ("os", "python", "exported_at", "lexicon_db"):
            self.assertIn(key, env)
        self.assertEqual(doc["format"], report_export.BUNDLE_FORMAT)
        for needle in ("system-wide", "root cause", "reports.json", "snapshots/"):
            self.assertIn(needle, readme)

    def test_an_empty_export_is_still_a_valid_zip(self):
        self.assertEqual(report_export.write_bundle(self.out, []), 0)
        with zipfile.ZipFile(self.out) as z:
            self.assertEqual(json.loads(z.read("reports.json"))["reports"], [])

    def test_lexicon_meta_reads_build_statistics_and_survives_a_missing_or_broken_database(self):
        db = os.path.join(self._dir.name, "ocean_lexicon.db")
        self.assertEqual(report_export.lexicon_meta(db), {"path_exists": False})
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO meta VALUES ('kaikki_entries', '123'), ('built_at', '2026-11-01')")
        conn.commit()
        conn.close()
        info = report_export.lexicon_meta(db)
        self.assertEqual(info["meta"], {"kaikki_entries": "123", "built_at": "2026-11-01"})
        self.assertIn("size_mb", info)
        broken = os.path.join(self._dir.name, "broken.db")
        with open(broken, "wb") as f:
            f.write(b"not sqlite")
        self.assertEqual(report_export.lexicon_meta(broken)["meta"], {})

    def test_file_names_are_safe(self):
        self.assertEqual(report_export._slug("Break the ice!"), "break-the-ice")
        self.assertEqual(report_export._slug(BANK_FA), BANK_FA)
        self.assertEqual(report_export._slug("///"), "entry")
        self.assertTrue(report_export.default_filename().endswith(".zip"))


if __name__ == "__main__":
    unittest.main()
