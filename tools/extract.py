#!/usr/bin/env python3
"""Turn whatever got dropped into Raw/ into text the agent can actually read.

You drop a PDF, a scanned homework photo, a .docx from your teacher. Models read
text, so something has to bridge that gap — and it should not be you. This runs
in the loop's preflight and leaves a plain-text rendering of every raw file under
loop/state/extracted/, keeping Raw/ itself untouched.

Extraction is idempotent: a file whose contents and recipe have not changed since
the last run is skipped, so re-running costs nothing.

    python3 tools/extract.py           extract anything new, print the report
    python3 tools/extract.py --list    print the last report without doing work

External tools are used when present and reported honestly when not:
  pdftotext, pdftoppm, pdfimages (poppler)   PDF text, page rasterising, scan detection
  tesseract                                  OCR for scans and images
Plain text and .docx need nothing beyond the standard library.

A scan can also be read by a vision model instead of OCR, which is the better
option when the page is dense or multilingual — OCR returns confident noise on
material like that, and noise is what a downstream model quietly invents around.
It is the only option for marked homework: OCR sees no colour and reads
handwriting badly, so the red-ink corrections, the part worth learning from,
never reach the agent. Set KM_VISION_MODEL and the pages go to a vision model;
leave it unset and nothing changes. OCR remains the fallback if the model cannot
be reached.

    KM_VISION_MODEL      anthropic/claude-opus-5-5 → Claude via the official SDK
                         (pip install anthropic; reads ANTHROPIC_API_KEY)
                         anything else, e.g. qwen3.8:27b → an Ollama server
                         unset → OCR, as before
    KM_VISION_EFFORT     Claude only: low | medium | high | xhigh | max; unset leaves
                         the model's own default (medium on claude-opus-5-5)
    KM_API_BASE          Ollama-compatible server (default http://localhost:11434)
    KM_VISION_MAX_PAGES  stop after N pages (0 = all); a page takes minutes
    KM_VISION_TIMEOUT    seconds per page (default 900)
    KM_EXTRACT_BUDGET_SEC  stop asking the vision model for new pages after this
                         many seconds (0 = no limit); the rest waits for the next
                         run, so a big backlog cannot starve the agent of its time

Every page a vision model reads is cached on its own, so a run that is killed,
times out or hits a failing page pays again only for the pages it did not get.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import NamedTuple

# A Windows console defaults to a legacy ANSI codepage (cp950 on a Traditional
# Chinese machine), which cannot encode a Japanese filename — printing the
# report would crash the whole extraction. The material decides the alphabet
# here, not the machine's locale.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "vault" / "Raw"
OUT = REPO / "loop" / "state" / "extracted"
MANIFEST = OUT / "manifest.json"
PAGE_CACHE = OUT / "pages"

TEXT_SUFFIXES = {".md", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml",
                 ".html", ".htm", ".org", ".rst", ".tex"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
# A PDF yielding less than this many characters is almost certainly scanned.
OCR_THRESHOLD = 80
OCR_PREFERENCE = ("jpn", "chi_tra", "chi_sim", "kor", "eng")
RASTER_DPI = 200
# An embedded image this large (about a phone photo of a page at 100 dpi) is a
# scanned page, not a figure.
SCAN_MIN_PIXELS = 1_000_000

# A KM_VISION_MODEL with this prefix goes to Anthropic's API instead of an
# Ollama server. The provider/model spelling is the one KM_MODEL already uses.
ANTHROPIC_PREFIX = "anthropic/"
# Claude fits every image into about 4784 visual tokens (28×28 px patches), or
# roughly 3.75 megapixels. A portrait A4 or B5 page with a 2200 px long edge
# fits that budget whole, so nothing is thrown away by a server-side resize and
# the upload stays small.
ANTHROPIC_LONG_EDGE = 2200
# The API takes up to 10 MB of base64 per image, which is about 7.5 MB of file.
ANTHROPIC_MAX_IMAGE_BYTES = 7_500_000
ANTHROPIC_MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                         ".png": "image/png", ".webp": "image/webp"}
# Models that accept server-side refusal fallbacks (`fallbacks: "default"`).
# Sending the parameter to any other model risks a 400 on every page.
ANTHROPIC_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5",
                             "claude-fable-5-1"}
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# Methods whose text cannot show handwriting or ink colour. The report says so
# next to each such file, because the agent cannot tell from the text alone
# that the red-ink marking it is looking for was never there to read.
HANDWRITING_BLIND = ("ocr", "pdftotext:scan")
TRUNCATED = "[轉錄到這裡被截斷：超過輸出長度上限]"
HANDWRITING_WARNING = "這份是掃描件但沒經過 vision：OCR 分不出顏色、也讀不好手寫，紅筆批改讀不到"

# Written against a model that had just invented a textbook's contents rather
# than admit it could not read the page. Every clause below is load-bearing:
# transcription only, an explicit way to say "unreadable", and no room to be
# helpful. A gap the model reports is recoverable; a gap it fills is not.
#
# The handwriting half exists for marked homework. What the student wrote and
# what the red pen changed it to is the whole lesson, and a transcriber that
# reads the page as print drops it silently. Ink colour is spelled out on every
# mark because it is the only evidence of who wrote what; the model is kept out
# of grading because deciding which option is right is exactly the step where
# its own Japanese would overwrite what is on the page.
VISION_PROMPT = """這是掃描或拍照的一頁教材或作業。請逐字轉錄你在圖片上「實際看到」的內容，保持原本的排列順序。

印刷的字：
1. 只寫圖片上真的有的字。看不清楚的地方寫 [不清楚]，**絕對不要猜、不要推測**。
2. 不要總結、不要解釋、不要補充任何背景知識或你認為應該出現的內容。
   你自己加的說明（例如側欄、頁首）一律用［］括起來，不要和頁面上的字混在一起。
3. 日文照圖片上的字形寫，**不要換成繁體中文字形**（来 不要寫成 來，気 不要寫成 氣）。
   印刷的標音（ふりがな）照抄在該詞後面的括號內；字太小看不清楚就只寫漢字，
   **不要用你的日文知識推測讀音**。
4. 表格或多欄排版就逐列轉錄，欄位之間用 | 分隔。譯文欄（中／英／韓／越）也要轉錄。

手寫的字與批改記號（作業最重要的資訊在這裡，一筆都不能漏）：
5. 先看整頁有沒有顏色。如果整頁是黑白或灰階（印刷和筆跡都沒有任何彩色），
   在最前面寫一行〔整頁為灰階影像，分不出筆的顏色〕，之後的手寫一律寫「顏色不確定」——
   灰階掃描裡的紅筆看起來跟黑筆一樣，寫成「黑」會把批改抹掉。筆跡如果明顯有深淺兩種，
   就加註「較深」或「較淺」，例如〔手寫·顏色不確定（較淺）：2〕；只描述深淺，不要因此判斷是紅筆。
6. 每一筆手寫都放進〔〕並寫出筆的顏色，例如〔手寫·黑：2〕、〔手寫·紅：3〕。
   鉛筆、藍筆照實寫；分不出顏色就寫〔手寫·顏色不確定：…〕。
   字形不確定時寫最接近的字，加問號和簡短描述，例如〔手寫·黑：1？（只有一條直線）〕；
   像數字又像記號（像 2 又像勾、像 0 又像圈）就把兩種讀法都寫出來。
7. 被劃掉的筆跡要註明，也寫是什麼顏色劃的，例如〔手寫·黑：2，被紅筆劃掉〕。
   一條線橫穿或斜穿過手寫的字就是劃掉：把底下的字和劃線分開寫，
   不要合起來讀成另一個字或記號（被劃掉的 1 不要讀成 7 或叉）。
8. 圈、勾、叉、底線等記號也要寫，並寫出它標在哪個字或哪一題上，
   例如〔紅筆畫了一個 ✓〕、〔黑筆圈住題號「①」〕；有延伸出去的尾巴就寫出延伸到哪裡。
   只描述形狀，不要寫成「對」「錯」——同一個記號在不同老師手裡意思相反。
9. 寫在空白處的作答單獨放一行，緊接在它所屬題目的題幹之後、選項之前，
   不要黏在某個選項的行首。不要從位置推論它選的是哪一個選項，照筆跡寫。
10. 你是在轉錄，不是在批改：**不要判斷哪個答案才對**，也不要用你的知識「修正」任何筆跡。
    紅筆寫什麼就記什麼，就算你覺得它寫錯了。

11. 這頁只要有任何作答或批改的筆跡，最後加上這一段，每一題一列，沒有作答的寫「—」：

## 本頁作答紀錄
| 題號 | 原本的作答（非紅筆） | 紅筆寫的 | 其他記號 |
|---|---|---|---|

被劃掉的作答照樣寫在「原本的作答」並註明（被紅筆劃掉）；圈、勾、叉寫在「其他記號」。
灰階頁的手寫全部寫在「原本的作答」並加（顏色不確定），「紅筆寫的」一欄寫「無法分辨」。
彩色頁整頁都沒有紅筆時，在表格上方寫一行「本頁沒有紅筆筆跡」。
這張表只是把上面已經轉錄的筆跡整理成一題一列，不能出現上面沒有的內容。
交出前數一數整頁有幾處紅筆筆跡（數字、劃線、勾、圈各算一處），確認每一處都已寫進上面的轉錄。

如果整頁都無法辨識，只回覆：[這頁讀不到]"""


class Extraction(NamedTuple):
    """What an extractor read, how, and — when the result is not final — why.

    `retry` is set when the text is worse than it should be for a reason that
    may go away: vision fell back to OCR, a page failed on a transient error, or
    the time budget ran out mid-file. Such a result is used today and redone on
    the next run, instead of being served as the answer forever.
    """
    text: str
    method: str
    retry: str = ""
    note: str = ""          # worth reporting, but nothing to retry
    complete: bool = False  # a vision transcript with every page read in full


class OutOfTime(Exception):
    """The extraction time budget ran out before this file got any page read."""


class VisionError(RuntimeError):
    """A page the vision model could not read.

    `fatal` means no page will be: the credentials, the SDK or the model name
    are wrong. Asking for the remaining pages would only repeat the failure, so
    the run falls back at once instead of after one doomed call per page.

    `transient` means the same page might well succeed next time (a dropped
    connection, an overloaded server); a refusal or an oversized photo will not.
    """

    def __init__(self, message: str, fatal: bool = False, transient: bool = True):
        super().__init__(message)
        self.fatal = fatal
        self.transient = transient


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def run(cmd, **kw) -> subprocess.CompletedProcess:
    # pdftotext and tesseract emit UTF-8. Letting Python guess from the locale
    # instead throws away the Japanese it just spent a minute recognising.
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600, **kw)


@contextlib.contextmanager
def staged_for_external_tools(path: Path):
    """Hand pdftotext/tesseract a path they can actually open.

    On Windows they resolve arguments through the legacy ANSI codepage, so a
    file the human named `N4語彙マスター_6.pdf` arrives mangled and they refuse
    it — a failure that looks exactly like a PDF with no text layer, sending
    the report off blaming the wrong thing. Copying the bytes under an ASCII
    name sidesteps the whole question, and is a no-op for names already fine.
    """
    if str(path).isascii():
        yield path
        return
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / f"staged{path.suffix.lower()}"
        shutil.copyfile(path, staged)
        yield staged


def vision_model() -> str:
    return os.environ.get("KM_VISION_MODEL", "").strip()


def uses_claude() -> bool:
    return vision_model().startswith(ANTHROPIC_PREFIX)


def vision_effort() -> str:
    # Unset means the model's own default. Sending a level to a model that has
    # no effort control (Sonnet 4.5, Haiku 4.5) is a 400 on every page.
    return os.environ.get("KM_VISION_EFFORT", "").strip()


def extraction_recipe() -> str:
    """Everything that changes the output if it changes.

    Cached text is only a hit if it was produced the same way. Keying the cache
    on the source file alone means raising KM_VISION_MAX_PAGES, moving to a
    better model, bumping the DPI or rewording the transcription prompt all
    return the previous answer and report success — the most confusing possible
    outcome, because the command the human just ran did nothing and said it
    worked.
    """
    if vision_model():
        return f"{page_recipe()}|pages{os.environ.get('KM_VISION_MAX_PAGES', '0')}"
    return "|".join(["ocr", ocr_languages(), os.environ.get("KM_RASTER_DPI", str(RASTER_DPI))])


def page_recipe() -> str:
    """What decides how a single page reads under vision — the page cache key.

    The page limit is left out on purpose: raising it should only pay for the
    pages not read yet.
    """
    prompt = hashlib.sha256(VISION_PROMPT.encode("utf-8")).hexdigest()[:8]
    if uses_claude():
        return "|".join(["vision", vision_model(), f"edge{ANTHROPIC_LONG_EDGE}",
                         vision_effort() or "default", prompt])
    return "|".join(["vision", vision_model(),
                     os.environ.get("KM_RASTER_DPI", str(RASTER_DPI)), prompt])


def fingerprint(path: Path) -> str:
    """Identify a source by its bytes rather than its mtime.

    A fresh git checkout — every CI run — stamps every file with the checkout
    time, so by mtime every source looks newer than its cached text and gets
    extracted again. With a paid vision model that is the same bill every day.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reusable(entry: dict, recipe: str) -> bool:
    """Would re-running now produce what is already cached?

    Matching the recipe alone is not enough, because the recipe records what
    was *asked for*. A vision run that fell back to OCR is stored under the
    vision recipe holding OCR output, and calling that a hit would freeze one
    server hiccup into the permanent answer — the next run matches, skips the
    work, and the noise never leaves. So nothing marked for retry is a hit.
    """
    return (entry.get("status") == "ok" and entry.get("recipe") == recipe
            and not entry.get("retry"))


def vision_on_disk(entry: dict) -> bool:
    """Does the text on disk for this entry come from a vision model?

    Asked regardless of whether the entry is due for a retry: a transcript that
    is waiting to be redone still sees the red ink, and is still worth more
    than anything OCR could put in its place.
    """
    return entry.get("status") == "ok" and str(entry.get("method", "")).startswith("vision")


def ask_vision(image: Path) -> str:
    """Transcribe one page image with the configured vision model.

    Whatever goes wrong comes back as a VisionError, so one failing page is
    always one failing page — never an exception that takes the file with it.
    """
    if uses_claude():
        return ask_claude(image)
    try:
        return ask_ollama(image)
    except Exception as exc:  # noqa: BLE001 — URLError, IncompleteRead, bad JSON, …
        raise VisionError(f"Ollama 呼叫失敗：{exc}") from exc


def ask_ollama(image: Path) -> str:
    base = os.environ.get("KM_API_BASE", "http://localhost:11434").rstrip("/")
    payload = {
        "model": vision_model(),
        "stream": False,
        "options": {"num_ctx": int(os.environ.get("KM_NUM_CTX", "32768"))},
        "messages": [{
            "role": "user",
            "content": VISION_PROMPT,
            "images": [base64.b64encode(image.read_bytes()).decode()],
        }],
    }
    request = urllib.request.Request(
        f"{base}/api/chat", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    key = os.environ.get("KM_API_KEY")
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    timeout = int(os.environ.get("KM_VISION_TIMEOUT", "900"))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))["message"]["content"]


def ask_claude(image: Path) -> str:
    """Transcribe one page image with Claude, through the official SDK.

    The SDK is imported here rather than at the top: like tesseract, it is only
    needed by someone who chose this backend, and its absence is a reason to
    report, not a crash for everyone else.
    """
    try:
        import anthropic
    except ImportError:
        raise VisionError("KM_VISION_MODEL 指定了 Claude，但沒有安裝 anthropic 套件"
                          "（pip install anthropic）", fatal=True) from None
    if vision_effort() and vision_effort() not in EFFORT_LEVELS:
        raise VisionError(f"KM_VISION_EFFORT={vision_effort()} 不認得，只能是 "
                          f"{'／'.join(EFFORT_LEVELS)}，或不設", fatal=True)
    media_type = ANTHROPIC_MEDIA_TYPES.get(image.suffix.lower())
    if media_type is None:
        raise VisionError(f"Claude 不收 {image.suffix} 圖檔，只收 JPEG、PNG、WebP",
                          transient=False)
    data = image.read_bytes()
    if len(data) > ANTHROPIC_MAX_IMAGE_BYTES:
        raise VisionError(f"圖檔 {len(data) / 1e6:.1f} MB，超過 Claude 單張約 7.5 MB 的上限，"
                          f"請先縮小再放進 Raw/", transient=False)
    model = vision_model()[len(ANTHROPIC_PREFIX):]
    request = {
        "model": model,
        "max_tokens": 32000,
        "messages": [{"role": "user", "content": [
            # Image before the instructions: Claude reads it better that way.
            {"type": "image", "source": {
                "type": "base64", "media_type": media_type,
                "data": base64.standard_b64encode(data).decode("ascii")}},
            {"type": "text", "text": VISION_PROMPT},
        ]}],
    }
    if vision_effort():
        request["output_config"] = {"effort": vision_effort()}
    if model in ANTHROPIC_FALLBACK_MODELS:
        # A safety decline re-runs on the model Anthropic recommends for its
        # category, inside the same call, instead of losing the page.
        request.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    rejected = tuple(getattr(anthropic, name) for name in (
        "AuthenticationError", "PermissionDeniedError", "NotFoundError", "CredentialsError")
        if hasattr(anthropic, name))
    try:
        client = anthropic.Anthropic(timeout=float(os.environ.get("KM_VISION_TIMEOUT", "900")))
        # Without credentials the SDK raises a bare TypeError at request time.
        # Asking first turns that into a reason the report can show.
        if not (client.api_key or client.auth_token or getattr(client, "credentials", None)):
            raise VisionError("沒有 Anthropic 憑證：設定 ANTHROPIC_API_KEY（GitHub Actions 要加在 "
                              "repo 的 Settings → Secrets）", fatal=True)
        with client.beta.messages.stream(**request) as stream:
            message = stream.get_final_message()
    except VisionError:
        raise
    except rejected as exc:
        raise VisionError(f"Claude 拒絕了請求（{type(exc).__name__}）："
                          f"檢查 ANTHROPIC_API_KEY 與 KM_VISION_MODEL", fatal=True) from exc
    except TypeError as exc:
        # Credentials are checked above, so a TypeError here is an SDK that
        # predates one of the parameters. Every page would fail the same way.
        raise VisionError(f"anthropic 套件太舊（{exc}）：pip install -U anthropic",
                          fatal=True) from exc
    except Exception as exc:  # noqa: BLE001
        # Includes what the SDK does not wrap: a connection that drops while the
        # reply is streaming arrives as a raw httpx error. It costs this page,
        # not the file.
        raise VisionError(f"Claude 呼叫失敗：{type(exc).__name__}: {exc}") from exc

    if message.stop_reason == "refusal":
        raise VisionError("Claude 拒絕轉錄這一頁", transient=False)
    text = "".join(block.text for block in message.content if block.type == "text").strip()
    if message.stop_reason == "max_tokens":
        # Keep what was read, but never let a cut-off page pass for a whole one.
        text += f"\n{TRUNCATED}"
    return text


def read_pages(pages: list, text_layer: str = "", source: str = "",
               keep_previous: bool = False) -> Extraction:
    """Turn page images into text — vision when configured, OCR otherwise.

    `text_layer` is what pdftotext found on a scan that carries one (phone
    scanner apps OCR the page and embed it). It is the fallback of choice when
    vision fails: the app's OCR is usually better than ours and costs nothing.
    `source` identifies the file the pages came from, for the page cache.
    `keep_previous` says a vision transcript of this file already exists; if
    vision fails now, any fallback would be thrown away, so none is attempted.
    """
    retry = note = ""
    if vision_model():
        try:
            return vision_pages(pages, source)
        except OutOfTime:
            raise
        except Exception as exc:  # noqa: BLE001 — whatever it was, the material must survive
            reason = f"vision 失敗（{exc}）"
            if keep_previous:
                raise RuntimeError(f"{reason}，保留先前的 vision 轉錄") from exc
            if out_of_time():
                raise OutOfTime() from exc
            # Losing the material because a server blinked would be worse than
            # reading it badly, so fall through and say so. A refusal or an
            # oversized photo will fail the same way tomorrow, so that fallback
            # is final; anything else is retried.
            lasting = isinstance(exc, VisionError) and not exc.transient and not exc.fatal
            retry, note = ("", reason) if lasting else (reason, "")
            print(f"    {reason}，改用{'PDF 內嵌的文字層' if text_layer else ' OCR'}",
                  file=sys.stderr)
            if text_layer:
                return Extraction(text_layer, "pdftotext:scan", retry, note)
    if not have("tesseract"):
        if retry or note:
            raise RuntimeError(f"{retry or note}，而且沒有 tesseract 可以退回 OCR")
        raise RuntimeError("掃描件需要 tesseract 才能 OCR，或設 KM_VISION_MODEL 用視覺模型讀")
    langs = ocr_languages()
    chunks = [run(["tesseract", str(page), "-", "-l", langs]).stdout for page in pages]
    return Extraction("\n\n".join(chunks), f"ocr:{langs}", retry, note)


_deadline: float | None = None


def out_of_time() -> bool:
    return _deadline is not None and time.monotonic() > _deadline


def cached_page(source: str, number: int) -> Path | None:
    if not source:
        return None
    key = hashlib.sha256(f"{source}|{number}|{page_recipe()}".encode("utf-8")).hexdigest()
    return PAGE_CACHE / f"{key[:40]}.txt"


def write_atomically(path: Path, text: str) -> None:
    """Replace a file in one step, so a killed run never leaves half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    # newline="" for the same reason as the extracted text below: the bytes
    # must be the text, on every platform.
    with partial.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    for attempt in range(5):
        try:
            os.replace(partial, path)
            return
        except PermissionError:
            # Windows refuses to replace a file another process has open — a
            # sync client uploading it, a virus scanner reading it. It lets go
            # within moments.
            if attempt == 4:
                raise
            time.sleep(0.2 * (attempt + 1))


def vision_pages(pages: list, source: str = "") -> Extraction:
    limit = int(os.environ.get("KM_VISION_MAX_PAGES", "0")) or len(pages)
    used = pages[:limit]
    chunks = []
    read = failures = transient = deferred = 0
    first_error = None
    stopped = None
    truncated = False
    for number, page in enumerate(used, start=1):
        if stopped is not None:
            deferred += 1
            chunks.append(f"--- page {number} ---\n[這頁還沒讀：{stopped}]")
            continue
        cache = cached_page(source, number)
        if cache is not None and cache.exists():
            # Paid for on an earlier run — a killed run, or one where another
            # page failed. Only the missing pages cost anything.
            body = cache.read_text(encoding="utf-8")
            truncated = truncated or TRUNCATED in body
            chunks.append(f"--- page {number} ---\n{body}")
            read += 1
            continue
        if out_of_time():
            deferred += 1
            chunks.append(f"--- page {number} ---\n[這頁還沒讀：這次抽取的時間預算用完，下一圈會接著讀]")
            continue
        print(f"    vision 第 {number}/{len(used)} 頁…", file=sys.stderr)
        try:
            body = ask_vision(page).strip()
        except (VisionError, OSError) as exc:
            if getattr(exc, "fatal", False):
                if not read:
                    raise
                # Pages already read (most likely from the cache) are worth
                # keeping; the rest wait for whatever broke to be fixed.
                stopped = exc
                deferred += 1
                chunks.append(f"--- page {number} ---\n[這頁還沒讀：{exc}]")
                continue
            # One bad page must not discard the good ones. At minutes apiece,
            # restarting a whole book because the server hiccuped on page 18 is
            # an hour thrown away — and the gap is named, so nothing downstream
            # mistakes a missing page for a blank one.
            failures += 1
            transient += getattr(exc, "transient", True)
            first_error = first_error or exc
            body = f"[這頁沒讀到：{exc}]"
            print(f"      第 {number} 頁失敗：{exc}", file=sys.stderr)
        else:
            read += 1
            truncated = truncated or TRUNCATED in body
            if cache is not None:
                try:
                    write_atomically(cache, body)
                except OSError as exc:
                    # The page is read and in hand; failing to cache it must
                    # not cost the run.
                    print(f"      第 {number} 頁快取寫不進去：{exc}", file=sys.stderr)
        chunks.append(f"--- page {number} ---\n{body}")
    if not read:
        if deferred:
            raise OutOfTime()
        # The reason is the useful part: a misconfiguration fails every page
        # the same way, and "all pages failed" alone sends nobody anywhere.
        raise VisionError(f"vision 每一頁都失敗（共 {failures} 頁），第一頁的錯誤：{first_error}",
                          transient=bool(transient))
    if len(used) < len(pages):
        # Saying so matters: a silent stop reads downstream as "this is the
        # whole document", and the rest of the book quietly stops existing.
        chunks.append(f"[只轉錄了前 {len(used)} 頁，全檔共 {len(pages)} 頁。"
                      f"調高 KM_VISION_MAX_PAGES 可讀更多]")
    method = f"vision:{vision_model()}"
    if failures:
        method += f"（{failures}/{len(used)} 頁失敗）"
    if deferred:
        method += f"（{deferred} 頁延後）"
    # A refusal or an oversized photo will fail the same way tomorrow; a dropped
    # connection or an unread page will not, so those make the result temporary.
    retry = "、".join(part for part in (
        f"{transient} 頁暫時讀不到" if transient else "",
        f"{deferred} 頁因時間預算延後" if deferred else "") if part)
    complete = not (failures or deferred or truncated or len(used) < len(pages))
    return Extraction("\n\n".join(chunks), method, f"{retry}，下一圈補讀" if retry else "",
                      complete=complete)


def ocr_languages() -> str:
    """Pick OCR languages from what tesseract actually has installed."""
    override = os.environ.get("KM_OCR_LANG")
    if override:
        return override
    try:
        done = run(["tesseract", "--list-langs"])
    except (OSError, subprocess.SubprocessError):
        return "eng"
    installed = {line.strip() for line in done.stdout.splitlines()[1:]}
    chosen = [lang for lang in OCR_PREFERENCE if lang in installed]
    return "+".join(chosen) if chosen else "eng"


def looks_scanned(path: Path) -> bool:
    """Is this PDF a scan that happens to carry a text layer?

    Phone scanner apps OCR every page and embed the result, so pdftotext finds
    plenty of text and the file passes for a born-digital PDF — while the
    handwriting and red-ink marking on it live only in the page images. A
    page-sized photo on at least half the pages is the tell.
    """
    if not (have("pdfimages") and have("pdfinfo")):
        return False
    try:
        info = run(["pdfinfo", str(path)]).stdout
        listing = run(["pdfimages", "-list", str(path)]).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    match = re.search(r"^Pages:\s+(\d+)", info, re.MULTILINE)
    if not match:
        return False
    scanned_pages = set()
    for line in listing.splitlines()[2:]:     # two header lines
        cols = line.split()
        if len(cols) > 4 and cols[0].isdigit() and cols[3].isdigit() and cols[4].isdigit():
            if int(cols[3]) * int(cols[4]) >= SCAN_MIN_PIXELS:
                scanned_pages.add(int(cols[0]))
    return len(scanned_pages) * 2 >= int(match.group(1)) > 0


# ---------------------------------------------------------------------------
# Extractors — each returns an Extraction or raises RuntimeError with a reason
# ---------------------------------------------------------------------------

def from_docx(path: Path) -> Extraction:
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    return Extraction(text, "docx")


def raster_pages(path: Path, tmp: str) -> list:
    """Render every page of a PDF to an image file."""
    if vision_model() and uses_claude():
        # JPEG keeps a colour page far below the API's per-image limit, which a
        # PNG of a noisy scan can exceed; at quality 92 red ink stays red.
        cmd = ["pdftoppm", "-jpeg", "-jpegopt", "quality=92",
               "-scale-to", str(ANTHROPIC_LONG_EDGE)]
    else:
        cmd = ["pdftoppm", "-r", os.environ.get("KM_RASTER_DPI", str(RASTER_DPI)), "-png"]
    run(cmd + [str(path), f"{tmp}/page"])
    return sorted(Path(tmp).glob("page*"))


def from_pdf(path: Path, source: str = "", keep_previous: bool = False) -> Extraction:
    if not have("pdftotext"):
        raise RuntimeError("需要 pdftotext（macOS: brew install poppler / Debian: apt install poppler-utils）")
    done = run(["pdftotext", "-layout", str(path), "-"])
    text = done.stdout
    text_layer = text if len(text.strip()) >= OCR_THRESHOLD else ""
    if text_layer and not looks_scanned(path):
        return Extraction(text_layer, "pdftotext")
    if text_layer and not (vision_model() and have("pdftoppm")):
        # A scanner app's OCR is as good as ours and free, but just as blind to
        # handwriting — which the method name has to admit.
        return Extraction(text_layer, "pdftotext:scan")
    # No text does not always mean "scanned". A damaged or unreadable file also
    # comes back empty, and quietly OCRing it would bury the real reason.
    if not text.strip() and done.returncode != 0:
        raise RuntimeError(f"pdftotext 讀不了這個檔：{done.stderr.strip()[:200] or '未知錯誤'}")
    # Too little text, or a scan to be read properly: the page images are the
    # whole content.
    if not have("pdftoppm"):
        raise RuntimeError("PDF 沒有文字層（掃描件），需要 pdftoppm 才能把頁面轉成圖片")
    with tempfile.TemporaryDirectory() as tmp:
        pages = raster_pages(path, tmp)
        if not pages:
            raise RuntimeError("PDF 無法轉成圖片")
        return read_pages(pages, text_layer, source, keep_previous)


def from_image(path: Path, source: str = "", keep_previous: bool = False) -> Extraction:
    return read_pages([path], source=source, keep_previous=keep_previous)


def extract(path: Path, source: str = "", keep_previous: bool = False) -> Extraction:
    """Read one Raw file. `source` (its content hash) keys the page cache;
    `keep_previous` means a vision transcript exists that a fallback must not
    replace."""
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return from_docx(path)  # stdlib zipfile, so any filename opens fine
    if suffix == ".pdf" or suffix in IMAGE_SUFFIXES:
        reader = from_pdf if suffix == ".pdf" else from_image
        with staged_for_external_tools(path) as usable:
            return reader(usable, source, keep_previous)
    raise RuntimeError(f"還不支援 {suffix} 格式，請自行轉成文字後再放進 Raw/")


# ---------------------------------------------------------------------------

def raw_files():
    if not RAW.is_dir():
        return []
    return sorted(
        p for p in RAW.rglob("*")
        if p.is_file() and not p.name.startswith((".", "_"))
    )


def report(entries: dict) -> str:
    if not entries:
        return "Raw/ 沒有素材。把講義、作業、剪藏丟進 vault/Raw/ 就會出現在這裡。"
    lines = []
    for rel in sorted(entries):
        e = entries[rel]
        if e["status"] == "text":
            lines.append(f"{rel} — 純文字，直接讀原檔（{e['chars']} 字）")
        elif e["status"] == "ok":
            how = e["method"] + ("，快取" if e.get("cached") else "")
            notes = [e["note"]] if e.get("note") else []
            if str(e["method"]).startswith(HANDWRITING_BLIND):
                notes.append(HANDWRITING_WARNING)
            note = "".join(f"　※ {n}" for n in notes)
            lines.append(f"{rel} — 已抽出文字：{e['text']}（{how}，{e['chars']} 字）{note}")
        elif e["status"] == "deferred":
            lines.append(f"{rel} — ⏳ 這次沒輪到：抽取的時間預算用完，下一圈繼續（現在還沒有文字可讀）")
        else:
            lines.append(f"{rel} — ⚠️ 無法讀取：{e['note']}")
    return "\n".join(lines)


def main(argv) -> int:
    global _deadline
    if "--list" in argv:
        entries = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
        print(report(entries))
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    previous = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    recipe = extraction_recipe()
    budget = int(os.environ.get("KM_EXTRACT_BUDGET_SEC", "0") or 0)
    _deadline = time.monotonic() + budget if budget > 0 else None
    entries: dict = {}

    def save(final: bool = False) -> None:
        # Written after every file, not once at the end: a run killed halfway
        # (Ctrl-C, a CI time limit) must not forget the files it finished and
        # send them to a paid model again. Files not reached yet keep their old
        # entries until the run gets to them.
        merged = dict(entries) if final else {**{k: v for k, v in previous.items()
                                                 if k not in entries}, **entries}
        try:
            write_atomically(MANIFEST, json.dumps(merged, ensure_ascii=False, indent=2,
                                                  sort_keys=True) + "\n")
        except OSError as exc:
            print(f"km-wiki: 清單寫不進去（{exc}），下一個檔案再試", file=sys.stderr)

    def rel_of(path: Path) -> str:
        return path.relative_to(REPO / "vault").as_posix()

    # Material with no readable text yet goes first, so a long re-read (after a
    # prompt change, say) never keeps today's homework waiting behind it.
    for path in sorted(raw_files(), key=lambda p: previous.get(rel_of(p), {}).get("status") == "ok"):
        rel = rel_of(path)
        if path.suffix.lower() in TEXT_SUFFIXES or path.suffix == "":
            entries[rel] = {
                "status": "text",
                "text": rel,
                "method": "plain",
                "chars": len(path.read_text(encoding="utf-8", errors="replace")),
            }
            continue

        target = OUT / (path.relative_to(RAW).as_posix().replace("/", "__") + ".txt")
        digest = fingerprint(path)
        before = previous.get(rel, {})
        # Manifests written before fingerprints existed only have mtime to go on.
        unchanged = (before.get("sha256") == digest if "sha256" in before
                     else target.exists() and target.stat().st_mtime >= path.stat().st_mtime)
        kept = dict(before, text=target.relative_to(REPO).as_posix(), sha256=digest,
                    cached=True, chars=len(target.read_text(encoding="utf-8", errors="replace"))
                    if target.exists() else 0)
        kept.pop("note", None)
        if target.exists() and unchanged and reusable(before, recipe):
            # Keep the method that actually produced the text. Overwriting it
            # with "cached" loses the only record of how the file was read, and
            # the run after that can no longer tell OCR output from a vision
            # transcript.
            entries[rel] = kept
            save()
            continue
        # A vision transcript of exactly these bytes. Whatever happens below,
        # OCR or a half-finished re-read must not take its place: OCR cannot
        # see the red ink, and a gap is worse than yesterday's full page.
        protected = target.exists() and unchanged and vision_on_disk(before)
        if protected and not vision_model():
            # A run without vision — a scheduled job missing the variable, a
            # laptop without the key — keeps it, retry flag and all.
            entries[rel] = dict(kept, note="沒有設定 vision，沿用先前的 vision 轉錄")
            save()
            continue

        try:
            result = extract(path, digest, keep_previous=protected)
        except OutOfTime:
            entries[rel] = (dict(kept, sha256=before.get("sha256"), retry=True,
                                 note="這次沒時間重抽，沿用先前的結果")
                            if target.exists() and before.get("status") == "ok" else
                            {"status": "deferred", "text": None, "method": None, "chars": 0})
            save()
            continue
        except Exception as exc:  # noqa: BLE001 — every failure is reportable, not fatal
            entry = {"status": "failed", "text": None, "method": None,
                     "chars": 0, "note": str(exc)}
            # Text extracted earlier is still on disk and still readable. A
            # missing tool today must not retract material that was already
            # read, or the prompt starts telling the agent the source is
            # unreadable while a good transcript sits next to it. Its hash and
            # retry flag stay those of the text on disk, so the next run judges
            # it by what it actually is.
            if target.exists() and before.get("status") == "ok":
                entry = dict(before, text=target.relative_to(REPO).as_posix(), cached=True,
                             chars=len(target.read_text(encoding="utf-8", errors="replace")),
                             note=f"沿用先前的抽取結果（這次重抽失敗：{exc}）")
            entries[rel] = entry
            save()
            continue

        worse = (not result.text.strip() or str(result.method).startswith(HANDWRITING_BLIND)
                 or (result.retry and before.get("complete")))
        if protected and worse:
            # Today's pages are in the page cache already; the next run puts
            # the full new transcript together without paying for them again.
            entries[rel] = dict(kept, retry=True,
                                note=f"保留先前的 vision 轉錄（這次{result.retry or result.note or '讀不完整'}）")
            save()
            continue

        if not result.text.strip():
            entries[rel] = {"status": "failed", "text": None, "method": result.method,
                            "chars": 0, "note": "抽出來是空的（可能是空白頁或辨識失敗）"}
            save()
            continue

        # newline="" disables the platform translation `write_text` would apply.
        # Without it Windows rewrites every \n as \r\n, so the file no longer
        # holds the text that was extracted, the reported size stops matching
        # what a later read returns, and the same PDF yields different bytes on
        # different machines.
        write_atomically(target, result.text)
        entries[rel] = {
            "status": "ok",
            "text": target.relative_to(REPO).as_posix(),
            "method": result.method,
            "recipe": recipe,
            "sha256": digest,
            "cached": False,
            "chars": len(result.text),
            "complete": result.complete,
        }
        if result.retry:
            entries[rel].update(retry=True, note=result.retry)
        elif result.note:
            entries[rel]["note"] = result.note
        save()

    save(final=True)
    print(report(entries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
