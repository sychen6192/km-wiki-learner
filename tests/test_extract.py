"""Tests for tools/extract.py's vision path — run with `python3 -m unittest discover tests`.

Marked homework is only worth anything if the red ink survives extraction, so
these pin down the Claude backend's request, how it fails, and the cache that
keeps a paid model from re-reading the same pages every day. None of them need
network access or an API key; the one that drives the real SDK is skipped when
the SDK is not installed.
"""

import base64
import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import extract  # noqa: E402

CLAUDE = "anthropic/claude-opus-5-5"


def message(text="〔手寫·紅：4〕", stop_reason="end_turn"):
    return types.SimpleNamespace(stop_reason=stop_reason, content=[
        types.SimpleNamespace(type="thinking", thinking=""),
        types.SimpleNamespace(type="text", text=text),
    ])


def fake_anthropic(outcomes, api_key="sk-ant-test"):
    """A stand-in for the SDK: records every request, answers from `outcomes`.

    Each outcome is a message, the name of an SDK exception to raise, or any
    other exception instance (what a dropped stream looks like: not an SDK type).
    """
    calls = []
    sdk = types.ModuleType("anthropic")

    class AnthropicError(Exception):
        pass

    class APIStatusError(AnthropicError):
        pass

    sdk.AnthropicError = AnthropicError
    for name in ("AuthenticationError", "PermissionDeniedError", "NotFoundError",
                 "RateLimitError", "BadRequestError"):
        setattr(sdk, name, type(name, (APIStatusError,), {}))

    class Stream:
        def __init__(self, result):
            self.result = result

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_final_message(self):
            return self.result

    class Messages:
        def stream(self, **request):
            calls.append(request)
            outcome = outcomes[min(len(calls), len(outcomes)) - 1]
            if isinstance(outcome, Exception):
                raise outcome
            if isinstance(outcome, str):
                raise getattr(sdk, outcome)(f"fake {outcome}")
            return Stream(outcome)

    class Anthropic:
        def __init__(self, timeout=None):
            self.api_key, self.auth_token, self.credentials = api_key, None, None
            self.beta = types.SimpleNamespace(messages=Messages())

    sdk.Anthropic = Anthropic
    return sdk, calls


class ClaudeFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.pages = []
        for n in (1, 2, 3):
            page = self.dir / f"page-{n}.jpg"
            page.write_bytes(b"\xff\xd8 fake jpeg %d" % n)
            self.pages.append(page)
        env = mock.patch.dict(os.environ, {"KM_VISION_MODEL": CLAUDE})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("KM_VISION_EFFORT", None)
        os.environ.pop("KM_VISION_MAX_PAGES", None)
        for name, value in (("PAGE_CACHE", self.dir / "pages"), ("_deadline", None)):
            patcher = mock.patch.object(extract, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def use(self, *outcomes, **kw):
        sdk, calls = fake_anthropic(list(outcomes), **kw)
        patcher = mock.patch.dict(sys.modules, {"anthropic": sdk})
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def quietly(self, fn, *args):
        with contextlib.redirect_stderr(io.StringIO()):
            return fn(*args)


class TestClaudeRequest(ClaudeFixture):
    def test_page_goes_first_then_the_transcription_prompt(self):
        calls = self.use(message("〔手寫·黑：1，被紅筆劃掉〕〔手寫·紅：4〕"))
        text = extract.ask_vision(self.pages[0])
        self.assertEqual(text, "〔手寫·黑：1，被紅筆劃掉〕〔手寫·紅：4〕")   # thinking skipped
        request = calls[0]
        self.assertEqual(request["model"], "claude-opus-5-5")   # provider prefix stripped
        self.assertNotIn("output_config", request)   # model default unless asked
        self.assertEqual(request["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", request["betas"])
        image, prompt = request["messages"][0]["content"]
        self.assertEqual(image["type"], "image")
        self.assertEqual(image["source"]["media_type"], "image/jpeg")
        self.assertEqual(base64.b64decode(image["source"]["data"]), self.pages[0].read_bytes())
        self.assertEqual(prompt, {"type": "text", "text": extract.VISION_PROMPT})

    def test_prompt_asks_for_ink_colour_and_forbids_grading(self):
        self.assertIn("紅", extract.VISION_PROMPT)
        self.assertIn("劃掉", extract.VISION_PROMPT)
        self.assertIn("不要判斷哪個答案才對", extract.VISION_PROMPT)

    def test_effort_is_configurable(self):
        calls = self.use(message())
        with mock.patch.dict(os.environ, {"KM_VISION_EFFORT": "high"}):
            extract.ask_vision(self.pages[0])
        self.assertEqual(calls[0]["output_config"], {"effort": "high"})

    def test_fallbacks_only_go_to_models_that_take_them(self):
        calls = self.use(message())
        with mock.patch.dict(os.environ, {"KM_VISION_MODEL": "anthropic/claude-haiku-4-5"}):
            extract.ask_vision(self.pages[0])
        self.assertNotIn("fallbacks", calls[0])
        self.assertNotIn("betas", calls[0])

    def test_truncated_page_is_marked_as_truncated(self):
        self.use(message("問題1", stop_reason="max_tokens"))
        self.assertIn("被截斷", extract.ask_vision(self.pages[0]))

    def test_oversized_photo_is_refused_before_upload(self):
        calls = self.use(message())
        big = self.dir / "photo.jpg"
        big.write_bytes(b"\0" * (extract.ANTHROPIC_MAX_IMAGE_BYTES + 1))
        with self.assertRaises(extract.VisionError) as ctx:
            extract.ask_vision(big)
        self.assertFalse(ctx.exception.fatal)
        self.assertEqual(calls, [])

    def test_unsupported_image_type_is_a_page_failure(self):
        tif = self.dir / "scan.tif"
        tif.write_bytes(b"II*\0")
        self.use(message())
        with self.assertRaises(extract.VisionError) as ctx:
            extract.ask_vision(tif)
        self.assertFalse(ctx.exception.fatal)


class TestClaudeFailures(ClaudeFixture):
    def test_missing_sdk_falls_back_without_trying_every_page(self):
        with mock.patch.dict(sys.modules, {"anthropic": None}):
            with self.assertRaises(extract.VisionError) as ctx:
                self.quietly(extract.vision_pages, self.pages)
        self.assertTrue(ctx.exception.fatal)
        self.assertIn("pip install anthropic", str(ctx.exception))

    def test_missing_key_is_reported_before_any_request(self):
        calls = self.use(message(), api_key=None)
        with self.assertRaises(extract.VisionError) as ctx:
            self.quietly(extract.vision_pages, self.pages)
        self.assertTrue(ctx.exception.fatal)
        self.assertIn("ANTHROPIC_API_KEY", str(ctx.exception))
        self.assertEqual(calls, [])

    def test_rejected_key_stops_after_the_first_page(self):
        calls = self.use("AuthenticationError")
        with self.assertRaises(extract.VisionError):
            self.quietly(extract.vision_pages, self.pages)
        self.assertEqual(len(calls), 1)

    def test_when_every_page_fails_the_report_says_why(self):
        self.use("BadRequestError")
        with self.assertRaises(RuntimeError) as ctx:
            self.quietly(extract.vision_pages, self.pages)
        self.assertIn("fake BadRequestError", str(ctx.exception))

    def test_unknown_effort_level_is_reported_once(self):
        calls = self.use(message())
        with mock.patch.dict(os.environ, {"KM_VISION_EFFORT": "extreme"}):
            with self.assertRaises(extract.VisionError) as ctx:
                self.quietly(extract.vision_pages, self.pages)
        self.assertTrue(ctx.exception.fatal)
        self.assertEqual(calls, [])

    def test_one_bad_page_does_not_cost_the_others(self):
        self.use(message("第一頁"), "RateLimitError", message(stop_reason="refusal"))
        result = self.quietly(extract.vision_pages, self.pages)
        self.assertIn("第一頁", result.text)
        self.assertEqual(result.text.count("[這頁沒讀到"), 2)
        self.assertEqual(result.method, f"vision:{CLAUDE}（2/3 頁失敗）")
        # The rate limit may clear by tomorrow; the refusal will not.
        self.assertIn("1 頁暫時讀不到", result.retry)

    def test_a_refusal_alone_is_final(self):
        self.use(message("一"), message(stop_reason="refusal"), message("三"))
        result = self.quietly(extract.vision_pages, self.pages)
        self.assertEqual(result.retry, "")

    def test_a_stream_that_drops_costs_one_page(self):
        class RemoteProtocolError(Exception):     # httpx's, which the SDK lets through
            pass
        self.use(message("一"), RemoteProtocolError("peer closed connection"), message("三"))
        result = self.quietly(extract.vision_pages, self.pages)
        self.assertIn("一", result.text)
        self.assertIn("三", result.text)
        self.assertIn("RemoteProtocolError", result.text)
        self.assertTrue(result.retry)

    def test_failed_vision_prefers_the_scanners_text_layer(self):
        self.use("AuthenticationError")
        result = self.quietly(extract.read_pages, self.pages, "スキャナーの文字層")
        self.assertEqual(result.text, "スキャナーの文字層")
        self.assertEqual(result.method, "pdftotext:scan")
        self.assertIn("vision 失敗", result.retry)

    def test_failed_vision_falls_back_to_ocr_and_says_why(self):
        self.use(api_key=None)
        ocr = types.SimpleNamespace(stdout="OCR の文字")
        with mock.patch.object(extract, "have", return_value=True), \
                mock.patch.object(extract, "ocr_languages", return_value="jpn"), \
                mock.patch.object(extract, "run", return_value=ocr):
            result = self.quietly(extract.read_pages, self.pages[:1])
        self.assertEqual(result.method, "ocr:jpn")
        self.assertIn("ANTHROPIC_API_KEY", result.retry)


class TestPageCache(ClaudeFixture):
    """Pages are paid for once, whatever happened to the rest of the file."""

    def test_only_the_page_that_failed_is_asked_for_again(self):
        self.use(message("一"), "RateLimitError", message("三"))
        self.quietly(extract.vision_pages, self.pages, "source-sha")
        calls = self.use(message("二，這次讀到了"))
        result = self.quietly(extract.vision_pages, self.pages, "source-sha")
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.retry, "")
        for text in ("一", "二，這次讀到了", "三"):
            self.assertIn(text, result.text)

    def test_raising_the_page_limit_pays_only_for_new_pages(self):
        self.use(message("一"))
        with mock.patch.dict(os.environ, {"KM_VISION_MAX_PAGES": "1"}):
            self.quietly(extract.vision_pages, self.pages, "source-sha")
        calls = self.use(message("後面"))
        self.quietly(extract.vision_pages, self.pages, "source-sha")
        self.assertEqual(len(calls), 2)

    def test_rewording_the_prompt_reads_pages_again(self):
        self.use(message("一"))
        self.quietly(extract.vision_pages, self.pages, "source-sha")
        calls = self.use(message("新"))
        with mock.patch.object(extract, "VISION_PROMPT", extract.VISION_PROMPT + "。"):
            self.quietly(extract.vision_pages, self.pages, "source-sha")
        self.assertEqual(len(calls), 3)

    def test_a_complete_read_says_so(self):
        self.use(message("頁"))
        self.assertTrue(self.quietly(extract.vision_pages, self.pages, "s").complete)
        self.use(message("頁"), message("後半", stop_reason="max_tokens"), message("頁"))
        self.assertFalse(self.quietly(extract.vision_pages, self.pages, "t").complete)
        self.use(message("頁"))
        with mock.patch.dict(os.environ, {"KM_VISION_MAX_PAGES": "2"}):
            self.assertFalse(self.quietly(extract.vision_pages, self.pages, "u").complete)

    def test_broken_credentials_after_cached_pages_keep_those_pages(self):
        extract.write_atomically(extract.cached_page("source-sha", 1), "第一頁（之前讀過）")
        self.use(message(), api_key=None)
        result = self.quietly(extract.vision_pages, self.pages, "source-sha")
        self.assertIn("第一頁（之前讀過）", result.text)
        self.assertEqual(result.text.count("[這頁還沒讀"), 2)
        self.assertTrue(result.retry)

    def test_a_lasting_failure_on_every_page_is_final(self):
        self.use(message(stop_reason="refusal"))
        ocr = types.SimpleNamespace(stdout="OCR の文字")
        with mock.patch.object(extract, "have", return_value=True), \
                mock.patch.object(extract, "ocr_languages", return_value="jpn"), \
                mock.patch.object(extract, "run", return_value=ocr):
            result = self.quietly(extract.read_pages, self.pages[:1], "", "source-sha")
        self.assertEqual(result.retry, "")           # asking again tomorrow costs money for nothing
        self.assertIn("拒絕", result.note)

    def test_no_fallback_is_attempted_when_its_result_would_be_thrown_away(self):
        self.use("AuthenticationError")
        with mock.patch.object(extract, "run") as ocr:
            with self.assertRaises(RuntimeError) as ctx:
                self.quietly(extract.read_pages, self.pages, "スキャナーの文字層", "s", True)
        ocr.assert_not_called()
        self.assertIn("保留先前的 vision 轉錄", str(ctx.exception))

    def test_a_cache_write_that_fails_does_not_cost_the_page(self):
        self.use(message("讀到了"))
        with mock.patch.object(extract, "write_atomically", side_effect=PermissionError("locked")):
            result = self.quietly(extract.vision_pages, self.pages[:1], "source-sha")
        self.assertIn("讀到了", result.text)

    def test_out_of_time_defers_unread_pages_instead_of_failing(self):
        extract.write_atomically(extract.cached_page("source-sha", 1), "第一頁（之前讀過）")
        calls = self.use(message())
        with mock.patch.object(extract, "_deadline", 0.0):
            result = self.quietly(extract.vision_pages, self.pages, "source-sha")
            with self.assertRaises(extract.OutOfTime):
                self.quietly(extract.vision_pages, self.pages, "other-file")
        self.assertEqual(calls, [])
        self.assertIn("第一頁（之前讀過）", result.text)
        self.assertEqual(result.text.count("[這頁還沒讀"), 2)
        self.assertIn("延後", result.retry)


class TestAtomicWrite(unittest.TestCase):
    def test_a_briefly_locked_file_is_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "manifest.json"
            real = os.replace
            failures = iter([PermissionError("in use"), PermissionError("in use")])

            def flaky(src, dst):
                failure = next(failures, None)
                if failure:
                    raise failure
                real(src, dst)
            with mock.patch.object(extract.os, "replace", side_effect=flaky), \
                    mock.patch.object(extract.time, "sleep"):
                extract.write_atomically(target, "{}")
            self.assertEqual(target.read_text(encoding="utf-8"), "{}")


class TestCacheAndReport(unittest.TestCase):
    def test_nothing_marked_for_retry_is_a_cache_hit(self):
        recipe = "vision|x"
        self.assertTrue(extract.reusable({"status": "ok", "recipe": recipe}, recipe))
        self.assertFalse(extract.reusable({"status": "ok", "recipe": recipe, "retry": True}, recipe))
        self.assertFalse(extract.reusable({"status": "ok", "recipe": "ocr|eng|200"}, recipe))

    def test_rewording_the_prompt_invalidates_vision_caches(self):
        with mock.patch.dict(os.environ, {"KM_VISION_MODEL": CLAUDE}):
            before = extract.extraction_recipe()
            with mock.patch.object(extract, "VISION_PROMPT", extract.VISION_PROMPT + "。"):
                self.assertNotEqual(extract.extraction_recipe(), before)

    def test_report_says_when_red_ink_could_not_be_read(self):
        text = extract.report({
            "Raw/ocr.jpg": {"status": "ok", "text": "a.txt", "method": "ocr:jpn", "chars": 9},
            "Raw/layer.pdf": {"status": "ok", "text": "b.txt", "method": "pdftotext:scan",
                              "chars": 9, "note": "vision 失敗（沒有憑證）"},
            "Raw/vision.pdf": {"status": "ok", "text": "c.txt", "method": f"vision:{CLAUDE}",
                               "chars": 9},
            "Raw/born-digital.pdf": {"status": "ok", "text": "d.txt", "method": "pdftotext",
                                     "chars": 9},
        })
        lines = {line.split(" — ")[0]: line for line in text.splitlines()}
        self.assertIn(extract.HANDWRITING_WARNING, lines["Raw/ocr.jpg"])
        self.assertIn(extract.HANDWRITING_WARNING, lines["Raw/layer.pdf"])
        self.assertIn("沒有憑證", lines["Raw/layer.pdf"])
        self.assertNotIn(extract.HANDWRITING_WARNING, lines["Raw/vision.pdf"])
        self.assertNotIn(extract.HANDWRITING_WARNING, lines["Raw/born-digital.pdf"])

    def test_report_says_when_a_file_waits_for_the_next_run(self):
        text = extract.report({"Raw/big.pdf": {"status": "deferred", "text": None,
                                               "method": None, "chars": 0}})
        self.assertIn("⏳", text)
        self.assertIn("下一圈", text)


class TestFreshCheckout(unittest.TestCase):
    """CI checks the repo out fresh every run, which resets every mtime."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        repo = Path(self._tmp.name)
        raw = repo / "vault" / "Raw"
        raw.mkdir(parents=True)
        self.raw = raw
        self.source = raw / "homework.jpg"
        self.source.write_bytes(b"\xff\xd8 page")
        out = repo / "loop" / "state" / "extracted"
        for name, value in (("REPO", repo), ("RAW", raw), ("OUT", out),
                            ("MANIFEST", out / "manifest.json"), ("PAGE_CACHE", out / "pages"),
                            ("ocr_languages", lambda: "jpn")):
            patcher = mock.patch.object(extract, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (mock.patch.dict(os.environ, {"KM_VISION_MODEL": CLAUDE}),
                        mock.patch.object(extract, "extract", side_effect=self.read)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.reads = 0
        self.order = []
        self.manifest = out / "manifest.json"
        self.target = out / "homework.jpg.txt"

    def read(self, path, source="", keep_previous=False):
        self.reads += 1
        self.order.append(path.name)
        return extract.Extraction(f"read #{self.reads}", f"vision:{CLAUDE}", complete=True)

    def vision_down(self, path, source="", keep_previous=False):
        """What read_pages does when vision fails: OCR, unless there is a
        vision transcript to keep — then it gives up instead."""
        if keep_previous:
            raise RuntimeError("vision 失敗（API 掛了），保留先前的 vision 轉錄")
        return extract.Extraction("OCR noise", "ocr:jpn", "vision 失敗（API 掛了）")

    def new_prompt(self):
        return mock.patch.object(extract, "VISION_PROMPT", extract.VISION_PROMPT + "。")

    def run_extract(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            extract.main([])
        self.report = out.getvalue()
        return json.loads(self.manifest.read_text(encoding="utf-8"))

    def test_same_bytes_are_not_read_twice_even_with_a_new_mtime(self):
        self.run_extract()
        future = self.source.stat().st_mtime + 3600
        os.utime(self.source, (future, future))      # what a fresh checkout does
        entry = self.run_extract()["Raw/homework.jpg"]
        self.assertEqual(self.reads, 1)
        self.assertTrue(entry["cached"])

    def test_changed_bytes_are_read_again(self):
        self.run_extract()
        self.source.write_bytes(b"\xff\xd8 page, redone")
        self.run_extract()
        self.assertEqual(self.reads, 2)

    def test_a_result_marked_for_retry_is_redone_next_run(self):
        with mock.patch.object(extract, "extract", return_value=extract.Extraction(
                "ocr", "ocr:jpn", "vision 失敗（沒有憑證）")):
            entry = self.run_extract()["Raw/homework.jpg"]
        self.assertTrue(entry["retry"])
        self.run_extract()
        self.assertEqual(self.reads, 1)

    def test_a_run_killed_halfway_keeps_the_files_it_finished(self):
        (self.raw / "second.jpg").write_bytes(b"\xff\xd8 second")

        def dies_on_second(path, source="", keep_previous=False):
            if path.name == "second.jpg":
                raise KeyboardInterrupt
            return self.read(path)
        with mock.patch.object(extract, "extract", side_effect=dies_on_second):
            with self.assertRaises(KeyboardInterrupt):
                self.run_extract()
        self.run_extract()
        self.assertEqual(self.reads, 2)      # homework once, second once — not homework twice

    def test_a_run_without_vision_keeps_the_vision_transcript(self):
        self.run_extract()
        with mock.patch.dict(os.environ, {"KM_VISION_MODEL": ""}):
            entry = self.run_extract()["Raw/homework.jpg"]
        self.assertEqual(self.reads, 1)
        self.assertEqual(entry["method"], f"vision:{CLAUDE}")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "read #1")

    def test_failed_vision_does_not_overwrite_an_older_transcript(self):
        self.run_extract()
        ocr = extract.Extraction("OCR noise", "ocr:jpn", "vision 失敗（API 掛了）")
        with self.new_prompt(), mock.patch.object(extract, "extract", return_value=ocr):
            entry = self.run_extract()["Raw/homework.jpg"]
        self.assertEqual(self.target.read_text(encoding="utf-8"), "read #1")
        self.assertEqual(entry["method"], f"vision:{CLAUDE}")
        self.assertTrue(entry["retry"])

    def test_the_transcript_survives_day_after_day_of_failures(self):
        self.run_extract()
        with self.new_prompt(), mock.patch.object(extract, "extract", side_effect=self.vision_down):
            for _ in range(3):
                entry = self.run_extract()["Raw/homework.jpg"]
        self.assertEqual(self.target.read_text(encoding="utf-8"), "read #1")
        self.assertEqual(entry["method"], f"vision:{CLAUDE}")

    def test_a_partial_reread_does_not_replace_a_complete_transcript(self):
        self.run_extract()
        partial = extract.Extraction("一頁＋[這頁還沒讀]", f"vision:{CLAUDE}（2 頁延後）",
                                     "2 頁因時間預算延後，下一圈補讀")
        with self.new_prompt(), mock.patch.object(extract, "extract", return_value=partial):
            entry = self.run_extract()["Raw/homework.jpg"]
        self.assertEqual(self.target.read_text(encoding="utf-8"), "read #1")
        self.assertTrue(entry["retry"])
        # …and a run without vision after that still keeps it, retry flag or not.
        with mock.patch.dict(os.environ, {"KM_VISION_MODEL": ""}):
            entry = self.run_extract()["Raw/homework.jpg"]
        self.assertEqual(self.target.read_text(encoding="utf-8"), "read #1")
        self.assertTrue(entry["retry"])

    def test_new_material_is_read_before_rereads(self):
        self.run_extract()
        (self.raw / "a-new-homework.jpg").write_bytes(b"\xff\xd8 new")    # sorts first anyway
        (self.raw / "zz-new-homework.jpg").write_bytes(b"\xff\xd8 newer")
        self.order.clear()
        with self.new_prompt():
            self.run_extract()
        self.assertEqual(self.order, ["a-new-homework.jpg", "zz-new-homework.jpg", "homework.jpg"])

    def test_a_failed_retry_stays_marked_for_retry(self):
        with mock.patch.object(extract, "extract", return_value=extract.Extraction(
                "ocr", "ocr:jpn", "vision 失敗")):
            self.run_extract()
        with mock.patch.object(extract, "extract", side_effect=RuntimeError("tesseract 不見了")):
            entry = self.run_extract()["Raw/homework.jpg"]
        self.assertTrue(entry["retry"])
        self.assertIn("sha256", entry)
        self.run_extract()
        self.assertEqual(self.reads, 1)

    def test_out_of_time_defers_the_file(self):
        with mock.patch.object(extract, "extract", side_effect=extract.OutOfTime):
            entry = self.run_extract()["Raw/homework.jpg"]
        self.assertEqual(entry["status"], "deferred")
        self.assertIn("⏳", self.report)
        self.run_extract()
        self.assertEqual(self.reads, 1)

    def test_the_budget_never_holds_back_files_that_need_no_vision(self):
        with mock.patch.object(extract, "out_of_time", return_value=True):
            entry = self.run_extract()["Raw/homework.jpg"]
        self.assertEqual(entry["status"], "ok")     # the time limit is for vision pages only


LISTING = """page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi size ratio
--------------------------------------------------------------------------------------------
{rows}"""


class TestScanDetection(unittest.TestCase):
    """A phone scan with an embedded OCR layer still has its red ink only in the image."""

    def tools(self, pages, rows):
        def run(cmd, **kw):
            if cmd[0] == "pdfinfo":
                return types.SimpleNamespace(stdout=f"Producer: x\nPages:          {pages}\n")
            return types.SimpleNamespace(stdout=LISTING.format(rows="\n".join(rows)))
        return mock.patch.multiple(extract, run=mock.DEFAULT, have=mock.DEFAULT), run

    def detect(self, pages, rows):
        patcher, run = self.tools(pages, rows)
        with patcher as mocked:
            mocked["run"].side_effect = run
            mocked["have"].return_value = True
            return extract.looks_scanned(Path("x.pdf"))

    def test_page_sized_photo_on_every_page_is_a_scan(self):
        rows = [f"   {p}     {p - 1} image    1101  1488  icc     3   8  jpeg   yes  5  0  180  180 158K 3.3%"
                for p in range(1, 7)]
        self.assertTrue(self.detect(6, rows))

    def test_a_figure_or_two_is_not_a_scan(self):
        rows = ["   3     0 image     640   480  rgb     3   8  jpeg   no  9  0  96  96 40K 2%",
                "   7     1 image    1200  1000  rgb     3   8  jpeg   no  12 0  96  96 90K 2%"]
        self.assertFalse(self.detect(10, rows))

    def test_scan_with_text_layer_goes_to_vision_when_configured(self):
        layer = types.SimpleNamespace(stdout="スキャナーが付けた文字層" * 10, returncode=0, stderr="")
        vision = extract.Extraction("〔手寫·紅：3〕", f"vision:{CLAUDE}")
        with mock.patch.object(extract, "have", return_value=True), \
                mock.patch.object(extract, "run", return_value=layer), \
                mock.patch.object(extract, "looks_scanned", return_value=True), \
                mock.patch.object(extract, "raster_pages", return_value=[Path("p1.jpg")]), \
                mock.patch.object(extract, "vision_pages", return_value=vision):
            with mock.patch.dict(os.environ, {"KM_VISION_MODEL": CLAUDE}):
                self.assertEqual(extract.from_pdf(Path("hw.pdf")), vision)
            with mock.patch.dict(os.environ, {"KM_VISION_MODEL": ""}):
                self.assertEqual(extract.from_pdf(Path("hw.pdf")).method, "pdftotext:scan")


def sdk_installed():
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


@unittest.skipUnless(sdk_installed(), "anthropic SDK not installed")
class TestClaudeOverTheWire(unittest.TestCase):
    """Drive the real SDK against a local stand-in for the Messages API."""

    REPLY = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": "〔手寫·紅：4〕"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn",
                                                                "stop_sequence": None},
                           "usage": {"output_tokens": 5}}),
        ("message_stop", {"type": "message_stop"}),
    ]

    def setUp(self):
        seen = self.seen = {}
        reply = self.REPLY

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                seen["path"] = self.path
                seen["headers"] = dict(self.headers)
                seen["body"] = json.loads(self.rfile.read(length))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for event, data in reply:
                    self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        env = mock.patch.dict(os.environ, {
            "KM_VISION_MODEL": CLAUDE, "ANTHROPIC_API_KEY": "sk-ant-test",
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}",
            "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
        env.start()
        self.addCleanup(env.stop)
        for name in ("ANTHROPIC_AUTH_TOKEN", "KM_VISION_EFFORT"):
            os.environ.pop(name, None)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.page = Path(tmp.name) / "page-1.jpg"
        self.page.write_bytes(b"\xff\xd8 fake jpeg")

    def test_request_and_reply_round_trip_through_the_sdk(self):
        self.assertEqual(extract.ask_vision(self.page), "〔手寫·紅：4〕")
        body, headers = self.seen["body"], {k.lower(): v for k, v in self.seen["headers"].items()}
        self.assertTrue(self.seen["path"].startswith("/v1/messages"))
        self.assertIn("server-side-fallback-2026-07-01", headers["anthropic-beta"])
        self.assertEqual(headers["x-api-key"], "sk-ant-test")
        self.assertEqual(body["model"], "claude-opus-5-5")
        self.assertEqual(body["fallbacks"], "default")
        self.assertNotIn("output_config", body)
        self.assertTrue(body["stream"])
        self.assertEqual(body["messages"][0]["content"][0]["source"]["media_type"], "image/jpeg")


    def test_a_stream_that_drops_mid_reply_is_a_page_failure(self):
        """The SDK does not wrap errors raised while reading the stream."""
        class Dropping(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                event, data = TestClaudeOverTheWire.REPLY[0]
                chunk = f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)   # mid-reply, no final chunk

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Dropping)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with mock.patch.dict(os.environ, {
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}"}):
            with self.assertRaises(extract.VisionError) as ctx:
                extract.ask_vision(self.page)
        self.assertFalse(ctx.exception.fatal)
        self.assertTrue(ctx.exception.transient)


if __name__ == "__main__":
    unittest.main()
