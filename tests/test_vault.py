"""Tests for tools/vault.py — run with `python3 -m unittest discover tests`."""

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import vault as vt  # noqa: E402

TODAY = dt.date(2026, 8, 14)


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def note(status="evergreen", body="", extra="", created="2026-08-01", updated="2026-08-01"):
    return (
        f"---\nstatus: {status}\ncreated: {created}\nupdated: {updated}\n"
        f"tags: [test]\n{extra}---\n\n{body}\n"
    )


class VaultFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "vault"
        (self.root / "Notes").mkdir(parents=True)
        self.addCleanup(self._tmp.cleanup)

    def scan(self):
        return vt.build_report(vt.Vault(self.root), TODAY)


class TestFrontmatter(unittest.TestCase):
    def test_scalars_lists_and_body(self):
        fm, body, err = vt.parse_frontmatter(
            "---\nstatus: seed\ntags: [a, b]\naliases:\n  - Foo\n  - Bar\n---\nBody here\n"
        )
        self.assertIsNone(err)
        self.assertEqual(fm["status"], "seed")
        self.assertEqual(fm["tags"], ["a", "b"])
        self.assertEqual(fm["aliases"], ["Foo", "Bar"])
        self.assertEqual(body, "Body here\n")

    def test_no_frontmatter(self):
        fm, body, err = vt.parse_frontmatter("just text")
        self.assertIsNone(fm)
        self.assertEqual(body, "just text")
        self.assertIsNone(err)

    def test_unterminated(self):
        fm, _, err = vt.parse_frontmatter("---\nstatus: seed\n")
        self.assertIsNone(fm)
        self.assertIn("unterminated", err)


class TestLinks(unittest.TestCase):
    def test_aliases_headings_and_code_are_handled(self):
        body = (
            "See [[Alpha]] and [[Beta|the beta note]] and [[Gamma#part]].\n"
            "```\n[[NotALink]]\n```\n"
            "Inline `[[AlsoNot]]` and %%[[HiddenToo]]%% done. [[Alpha]] again.\n"
        )
        self.assertEqual(vt.extract_links(body), ["Alpha", "Beta", "Gamma"])

    def test_unicode_targets(self):
        self.assertEqual(vt.extract_links("連到 [[知識工程]] 一頁"), ["知識工程"])


class TestScan(VaultFixture):
    def test_dangling_orphans_stubs_reviews(self):
        write(self.root, "Notes/Alpha.md",
              note(body="Links to [[Beta]] and [[Missing Page]]. " + "word " * 80))
        write(self.root, "Notes/Beta.md",
              note(status="seed", body="Tiny.", extra="review_after: 2026-08-10\n"))
        write(self.root, "Notes/Loner.md", note(body="No links at all. " + "word " * 80))
        write(self.root, "Inbox.md",
              "- [ ] learn about transformers\n- [x] done thing\n"
              "```\n- [ ] example inside code fence, not a real todo\n```\n")

        r = self.scan()
        self.assertEqual([f["target"] for f in r["frontier"]], ["Missing Page"])
        self.assertEqual(r["frontier"][0]["referenced_by"], ["Notes/Alpha.md"])
        self.assertEqual(r["orphans"], ["Notes/Loner.md"])
        self.assertEqual(r["stubs"], ["Notes/Beta.md"])
        self.assertEqual(r["due_reviews"][0]["note"], "Notes/Beta.md")
        self.assertEqual(r["inbox"], ["learn about transformers"])
        self.assertEqual(r["totals"]["by_status"], {"seed": 1, "budding": 0, "evergreen": 2})

    def test_alias_resolution_prevents_false_dangling(self):
        write(self.root, "Notes/Large Language Model.md",
              note(extra="aliases: [LLM]\n", body="Long body. " + "word " * 80))
        write(self.root, "Notes/User.md", note(body="I love [[LLM]] tech. " + "word " * 80))
        self.assertEqual(self.scan()["frontier"], [])

    def test_meta_and_templates_are_ignored(self):
        write(self.root, "_meta/Templates/Note.md", "[[Fake Link {{title}}]]")
        write(self.root, "Notes/Real.md", note(body="Body. " + "word " * 80))
        self.assertEqual(self.scan()["frontier"], [])

    def test_raw_layer_pending_and_ingested(self):
        write(self.root, "Raw/paper.pdf", "binary-ish")
        write(self.root, "Raw/clip.md", "a web clipping, no frontmatter needed")
        write(self.root, "Raw/_about.md", "explainer, not a source")
        r = self.scan()
        self.assertEqual(r["pending_raw"], ["Raw/clip.md", "Raw/paper.pdf"])
        # linking from Notes/ is not ingestion; a Sources/ digest is
        write(self.root, "Notes/Mention.md", note(body="see [[clip]] [[Digest]] " + "word " * 80))
        self.assertEqual(self.scan()["pending_raw"], ["Raw/clip.md", "Raw/paper.pdf"])
        write(self.root, "Sources/Digest.md",
              note(body="Digest of [[clip]] and [[paper.pdf]]. [[Mention]] " + "word " * 80))
        r = self.scan()
        self.assertEqual(r["pending_raw"], [])
        self.assertEqual(r["totals"]["pending_raw"], 0)
        # raw links are never dangling
        self.assertEqual(r["frontier"], [])

    def test_meta_pages_are_valid_targets_but_templates_are_not(self):
        write(self.root, "_meta/Style Guide.md", "# rules, no frontmatter needed")
        write(self.root, "_meta/Templates/Source.md", "template body")
        write(self.root, "Notes/Real.md",
              note(body="See [[Style Guide]] and [[Source]]. " + "word " * 80))
        r = self.scan()
        self.assertEqual([f["target"] for f in r["frontier"]], ["Source"])
        # _meta pages never show up as notes/orphans themselves
        self.assertNotIn("_meta/Style Guide.md", r["orphans"])
        self.assertEqual(r["totals"]["notes"], 1)


class TestLint(VaultFixture):
    def test_errors_fail_and_clean_passes(self):
        write(self.root, "Notes/Bad.md", "---\nstatus: banana\ncreated: nope\n---\nx\n")
        self.assertEqual(vt.lint(vt.Vault(self.root), TODAY, strict=False), 1)

        write(self.root, "Notes/Bad.md", note(body="Fine now. [[Good]] " + "word " * 80))
        write(self.root, "Notes/Good.md", note(body="Links back [[Bad]]. " + "word " * 80))
        self.assertEqual(vt.lint(vt.Vault(self.root), TODAY, strict=False), 0)

    def test_strict_promotes_warnings(self):
        write(self.root, "Notes/Loner.md", note(body="No links. " + "word " * 80))
        self.assertEqual(vt.lint(vt.Vault(self.root), TODAY, strict=False), 0)
        self.assertEqual(vt.lint(vt.Vault(self.root), TODAY, strict=True), 1)


class TestStats(VaultFixture):
    def test_dashboard_updates_and_is_idempotent(self):
        write(self.root, "Home.md",
              f"# Home\n\n{vt.STATS_START}\nold\n{vt.STATS_END}\n\ntail\n")
        write(self.root, "Notes/Alpha.md", note(body="[[Frontier Idea]] " + "word " * 80))
        write(self.root, "Daily/2026-08-13.md", "report\n")

        self.assertEqual(vt.update_home(vt.Vault(self.root), TODAY), 0)
        text = (self.root / "Home.md").read_text(encoding="utf-8")
        self.assertIn("[[Frontier Idea]]", text)
        self.assertIn("[[2026-08-13]]", text)
        self.assertIn("tail", text)
        self.assertNotIn("old\n", text)

        before = text
        self.assertEqual(vt.update_home(vt.Vault(self.root), TODAY), 0)
        self.assertEqual((self.root / "Home.md").read_text(encoding="utf-8"), before)


class TestSeed(VaultFixture):
    def test_seed_creates_once(self):
        self.assertEqual(vt.seed(self.root, "New Topic", "linked from Alpha", TODAY), 0)
        path = self.root / "Notes" / "New Topic.md"
        self.assertTrue(path.exists())
        fm, _, err = vt.parse_frontmatter(path.read_text(encoding="utf-8"))
        self.assertIsNone(err)
        self.assertEqual(fm["status"], "seed")
        self.assertEqual(fm["created"], "2026-08-14")
        # second call is a no-op
        self.assertEqual(vt.seed(self.root, "New Topic", "", TODAY), 0)

    def test_scan_json_roundtrip(self):
        write(self.root, "Notes/Alpha.md", note(body="Body [[Beta]]. " + "word " * 80))
        payload = json.dumps(vt.build_report(vt.Vault(self.root), TODAY), ensure_ascii=False)
        self.assertEqual(json.loads(payload)["totals"]["dangling_links"], 1)


class TestCursor(unittest.TestCase):
    """Cursors are how the loop remembers where each external source got to."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = str(Path(self._tmp.name) / "state" / "cursors.json")
        self.addCleanup(self._tmp.cleanup)

    def run_cursor(self, *argv):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = vt.main(["--today", "2026-08-14", "cursor", *argv, "--state", self.state])
        return code, buf.getvalue().strip()

    def test_unset_get_returns_default(self):
        code, out = self.run_cursor("get", "acme", "--default", "2026-01-01")
        self.assertEqual(code, 0)
        self.assertEqual(out, "2026-01-01")

    def test_set_then_get_roundtrip(self):
        self.assertEqual(self.run_cursor("set", "acme", "DOC-42", "--note", "last synced")[0], 0)
        self.assertEqual(self.run_cursor("get", "acme")[1], "DOC-42")
        # a later run advances the watermark
        self.run_cursor("set", "acme", "DOC-99")
        self.assertEqual(self.run_cursor("get", "acme")[1], "DOC-99")
        stored = json.loads(Path(self.state).read_text(encoding="utf-8"))
        self.assertEqual(stored["acme"]["updated"], "2026-08-14")

    def test_list_is_prompt_friendly(self):
        self.assertIn("no cursors set", self.run_cursor("list")[1])
        self.run_cursor("set", "acme", "2026-08-13T00:00:00Z", "--note", "nightly sync")
        code, out = self.run_cursor("list")
        self.assertEqual(code, 0)
        self.assertIn("acme: 2026-08-13T00:00:00Z", out)
        self.assertIn("nightly sync", out)

    def test_corrupt_file_does_not_crash(self):
        Path(self.state).parent.mkdir(parents=True, exist_ok=True)
        Path(self.state).write_text("{not json", encoding="utf-8")
        self.assertEqual(self.run_cursor("get", "acme", "--default", "x")[1], "x")
        self.assertEqual(self.run_cursor("set", "acme", "ok")[0], 0)
        self.assertEqual(self.run_cursor("get", "acme")[1], "ok")

    def test_missing_args_are_rejected(self):
        self.assertEqual(self.run_cursor("get")[0], 2)
        self.assertEqual(self.run_cursor("set", "acme")[0], 2)


if __name__ == "__main__":
    unittest.main()
