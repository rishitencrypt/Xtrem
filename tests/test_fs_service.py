import os
import tempfile
import unittest
from pathlib import Path

from services.fs_service import Workspace, WorkspaceError


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.ws = Workspace(self.tmp / "ws")

    def tearDown(self):
        self._tmp.cleanup()

    def test_root_created(self):
        self.assertTrue((self.tmp / "ws").is_dir())

    def test_rejects_traversal_and_absolute_outside(self):
        for bad in ("../outside.txt", "sub/../../x", str(self.tmp / "other")):
            with self.assertRaises(WorkspaceError):
                self.ws.resolve(bad)

    def test_rejects_symlink_escape(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "s.txt").write_text("secret")
        os.symlink(outside, self.ws.root / "link")
        with self.assertRaises(WorkspaceError):
            self.ws.read_text("link/s.txt")
        with self.assertRaises(WorkspaceError):
            self.ws.create_file("link", "x.txt", "no")
        self.assertFalse((outside / "x.txt").exists())

    def test_deleting_symlink_keeps_target(self):
        target = self.tmp / "keep.txt"
        target.write_text("data")
        os.symlink(target, self.ws.root / "flink")
        self.ws.delete("flink")
        self.assertFalse((self.ws.root / "flink").is_symlink())
        self.assertEqual(target.read_text(), "data")

    def test_create_is_verified_and_reports_real_path(self):
        rep = self.ws.create_file_at("essays/politics.txt", "line one\nline two\n")
        self.assertEqual(rep.path, self.ws.root / "essays" / "politics.txt")
        self.assertEqual(rep.path.read_text(), "line one\nline two\n")
        self.assertEqual(rep.size, len("line one\nline two\n"))

    def test_create_never_overwrites(self):
        self.ws.create_file(None, "a.txt", "one")
        with self.assertRaises(WorkspaceError):
            self.ws.create_file(None, "a.txt", "two")
        self.assertEqual(self.ws.read_text("a.txt"), "one")

    def test_conflict_rename(self):
        self.ws.create_file(None, "a.txt", "one")
        rep = self.ws.create_file(None, "a.txt", "two", on_conflict="rename")
        self.assertTrue(rep.renamed)
        self.assertEqual(rep.path.name, "a (2).txt")
        self.assertEqual(self.ws.read_text("a.txt"), "one")
        self.assertEqual(self.ws.create_file(None, "a.txt", "3", on_conflict="rename").path.name, "a (3).txt")

    def test_extension_and_json_validation(self):
        with self.assertRaises(WorkspaceError):
            self.ws.create_file(None, "evil.sh", "rm -rf /")
        with self.assertRaises(WorkspaceError):
            self.ws.create_file(None, "x.json", "{not json")
        self.ws.create_file(None, "ok.json", '{"a": 1}')
        self.ws.create_file(None, "t.csv", "a,b\n1,2\n")

    def test_write_and_append(self):
        self.ws.create_file(None, "n.md", "# Title")
        self.ws.write_text("n.md", "# New")
        self.assertEqual(self.ws.read_text("n.md"), "# New")
        self.ws.append_text("n.md", "more")
        self.assertEqual(self.ws.read_text("n.md"), "# New\nmore")

    def test_unicode_roundtrip(self):
        rep = self.ws.create_file(None, "u.txt", "änd – “quotes” ✓")
        self.assertEqual(rep.path.read_text(encoding="utf-8"), "änd – “quotes” ✓")

    def test_rename_move(self):
        f = self.ws.create_file(None, "a.txt", "x").path
        folder = self.ws.make_folder(None, "dir")
        f2 = self.ws.rename(f, "b.txt")
        moved = self.ws.move(f2, folder)
        self.assertEqual(moved.read_text(), "x")
        self.assertFalse(f2.exists())

    def test_move_rules(self):
        a = self.ws.make_folder(None, "a")
        b = self.ws.make_folder(a, "b")
        with self.assertRaises(WorkspaceError):
            self.ws.move(a, b)
        f = self.ws.create_file(None, "f.txt", "x").path
        with self.assertRaises(WorkspaceError):
            self.ws.move(f, self.tmp)
        self.ws.create_file(a, "f.txt", "other")
        with self.assertRaises(WorkspaceError):
            self.ws.move(f, a)  # conflict: refuses instead of overwriting
        self.assertEqual((a / "f.txt").read_text(), "other")

    def test_delete_rules(self):
        d = self.ws.make_folder(None, "d")
        self.ws.create_file(d, "x.txt", "x")
        with self.assertRaises(WorkspaceError):
            self.ws.delete(d)
        self.assertTrue(d.exists())
        self.assertEqual(self.ws.count_tree(d), 1)
        self.ws.delete(d, recursive=True)
        self.assertFalse(d.exists())
        with self.assertRaises(WorkspaceError):
            self.ws.delete(self.ws.root, recursive=True)

    def test_invalid_names(self):
        for bad in ("", ".", "..", "a/b", "a\\b"):
            with self.assertRaises(WorkspaceError):
                self.ws.create_file(None, bad, "")

    def test_search_name_and_content(self):
        d = self.ws.make_folder(None, "docs")
        self.ws.create_file(d, "Report.txt", "Quarterly revenue grew.\nSecond line.")
        self.assertEqual([p.name for p in self.ws.search("report")], ["Report.txt"])
        hits = self.ws.search_content("REVENUE")
        self.assertEqual((hits[0][0].name, hits[0][1]), ("Report.txt", 1))
        self.assertEqual(self.ws.search_content("absent"), [])

    def test_read_limits(self):
        (self.ws.root / "big.txt").write_bytes(b"a" * 1_100_000)
        with self.assertRaises(WorkspaceError):
            self.ws.read_text("big.txt")
        (self.ws.root / "bin.txt").write_bytes(b"\xff\xfe\x00")
        with self.assertRaises(WorkspaceError):
            self.ws.read_text("bin.txt")


if __name__ == "__main__":
    unittest.main()
