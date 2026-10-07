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

    Each outcome is a message, or the name of an SDK exception to raise.
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
        self.assertEqual(request["output_config"], {"effort": "medium"})
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

    def test_one_bad_page_does_not_cost_the_others(self):
        self.use(message("第一頁"), "RateLimitError", message(stop_reason="refusal"))
        result = self.quietly(extract.vision_pages, self.pages)
        self.assertIn("第一頁", result.text)
        self.assertEqual(result.text.count("[這頁沒讀到"), 2)
        self.assertEqual(result.method, f"vision:{CLAUDE}（2/3 頁失敗）")
        self.assertEqual(result.fallback, "")

    def test_failed_vision_prefers_the_scanners_text_layer(self):
        self.use("AuthenticationError")
        result = self.quietly(extract.read_pages, self.pages, "スキャナーの文字層")
        self.assertEqual(result.text, "スキャナーの文字層")
        self.assertEqual(result.method, "pdftotext:scan")
        self.assertIn("vision 失敗", result.fallback)

    def test_failed_vision_falls_back_to_ocr_and_says_why(self):
        self.use(api_key=None)
        ocr = types.SimpleNamespace(stdout="OCR の文字")
        with mock.patch.object(extract, "have", return_value=True), \
                mock.patch.object(extract, "ocr_languages", return_value="jpn"), \
                mock.patch.object(extract, "run", return_value=ocr):
            result = self.quietly(extract.read_pages, self.pages[:1])
        self.assertEqual(result.method, "ocr:jpn")
        self.assertIn("ANTHROPIC_API_KEY", result.fallback)


class TestCacheAndReport(unittest.TestCase):
    def test_a_fallback_is_never_a_cache_hit(self):
        recipe = "vision|x"
        self.assertTrue(extract.reusable({"status": "ok", "recipe": recipe}, recipe))
        self.assertFalse(extract.reusable({"status": "ok", "recipe": recipe, "fallback": True}, recipe))
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


class TestFreshCheckout(unittest.TestCase):
    """CI checks the repo out fresh every run, which resets every mtime."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        repo = Path(self._tmp.name)
        raw = repo / "vault" / "Raw"
        raw.mkdir(parents=True)
        self.source = raw / "homework.jpg"
        self.source.write_bytes(b"\xff\xd8 page")
        out = repo / "loop" / "state" / "extracted"
        for name, value in (("REPO", repo), ("RAW", raw), ("OUT", out),
                            ("MANIFEST", out / "manifest.json")):
            patcher = mock.patch.object(extract, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (mock.patch.dict(os.environ, {"KM_VISION_MODEL": CLAUDE}),
                        mock.patch.object(extract, "extract", side_effect=self.read)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.reads = 0
        self.manifest = out / "manifest.json"

    def read(self, path):
        self.reads += 1
        return extract.Extraction(f"read #{self.reads}", f"vision:{CLAUDE}")

    def run_extract(self):
        with contextlib.redirect_stdout(io.StringIO()):
            extract.main([])
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

    def test_a_fallback_is_retried_next_run(self):
        with mock.patch.object(extract, "extract", return_value=extract.Extraction(
                "ocr", "ocr:jpn", "vision 失敗（沒有憑證）")):
            entry = self.run_extract()["Raw/homework.jpg"]
        self.assertTrue(entry["fallback"])
        self.run_extract()
        self.assertEqual(self.reads, 1)


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
        self.page = Path(tempfile.mkdtemp()) / "page-1.jpg"
        self.page.write_bytes(b"\xff\xd8 fake jpeg")

    def test_request_and_reply_round_trip_through_the_sdk(self):
        self.assertEqual(extract.ask_vision(self.page), "〔手寫·紅：4〕")
        body, headers = self.seen["body"], {k.lower(): v for k, v in self.seen["headers"].items()}
        self.assertTrue(self.seen["path"].startswith("/v1/messages"))
        self.assertIn("server-side-fallback-2026-07-01", headers["anthropic-beta"])
        self.assertEqual(headers["x-api-key"], "sk-ant-test")
        self.assertEqual(body["model"], "claude-opus-5-5")
        self.assertEqual(body["fallbacks"], "default")
        self.assertEqual(body["output_config"], {"effort": "medium"})
        self.assertTrue(body["stream"])
        self.assertEqual(body["messages"][0]["content"][0]["source"]["media_type"], "image/jpeg")


if __name__ == "__main__":
    unittest.main()
