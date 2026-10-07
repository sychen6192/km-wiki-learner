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
    KM_VISION_EFFORT     Claude only: low | medium | high | xhigh | max (default medium)
    KM_API_BASE          Ollama-compatible server (default http://localhost:11434)
    KM_VISION_MAX_PAGES  stop after N pages (0 = all); a page takes minutes
    KM_VISION_TIMEOUT    seconds per page (default 900)
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

# Methods whose text cannot show handwriting or ink colour. The report says so
# next to each such file, because the agent cannot tell from the text alone
# that the red-ink marking it is looking for was never there to read.
HANDWRITING_BLIND = ("ocr", "pdftotext:scan")
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
3. 日文保留漢字與假名原樣。標音（ふりがな）字很小，**只有在你確實看得清楚時**
   才寫在該詞後面的括號內；看不清楚就只寫漢字，**不要用你的日文知識推測讀音**。
4. 表格或多欄排版就逐列轉錄，欄位之間用 | 分隔。譯文欄（中／英／韓／越）也要轉錄。

手寫的字與批改記號（作業最重要的資訊在這裡，一筆都不能漏）：
5. 每一筆手寫都放進〔〕並寫出筆的顏色，例如〔手寫·黑：2〕、〔手寫·紅：3〕。
   鉛筆、藍筆照實寫；分不出顏色就寫〔手寫·顏色不確定：…〕。
6. 被劃掉的筆跡要註明，也寫是什麼顏色劃的，例如〔手寫·黑：2，被紅筆劃掉〕。
7. 圈、勾、叉、底線等記號也要寫，並寫出它標在哪個字或哪一題上，
   例如〔紅筆打勾〕、〔黑筆圈住「〇〇」〕。
8. 手寫內容寫在它所屬的那一題、那一行，不要集中到最後。
9. 你是在轉錄，不是在批改：**不要判斷哪個答案才對**，也不要用你的知識「修正」任何筆跡。
   紅筆寫什麼就記什麼，就算你覺得它寫錯了。

10. 這頁只要有任何作答或批改的筆跡，最後加上這一段，每一題一列，沒有作答的寫「—」：

## 本頁作答紀錄
| 題號 | 原本的作答（非紅筆） | 紅筆寫的 | 其他記號 |
|---|---|---|---|

這張表只是把上面已經轉錄的筆跡整理成一題一列，不能出現上面沒有的內容。

如果整頁都無法辨識，只回覆：[這頁讀不到]"""


class Extraction(NamedTuple):
    """What an extractor read, how, and — if a vision run fell back to something
    worse — why. A fallback is kept but not trusted as final: the next run tries
    again instead of serving the worse text forever."""
    text: str
    method: str
    fallback: str = ""


class VisionError(RuntimeError):
    """A page the vision model could not read.

    `fatal` means no page will be: the credentials, the SDK or the model name
    are wrong. Asking for the remaining pages would only repeat the failure, so
    the run falls back at once instead of after one doomed call per page.
    """

    def __init__(self, message: str, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


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
    return os.environ.get("KM_VISION_EFFORT", "").strip() or "medium"


def extraction_recipe() -> str:
    """Everything that changes the output if it changes.

    Cached text is only a hit if it was produced the same way. Keying the cache
    on the source file alone means raising KM_VISION_MAX_PAGES, moving to a
    better model, bumping the DPI or rewording the transcription prompt all
    return the previous answer and report success — the most confusing possible
    outcome, because the command the human just ran did nothing and said it
    worked.
    """
    dpi = os.environ.get("KM_RASTER_DPI", str(RASTER_DPI))
    if vision_model():
        prompt = hashlib.sha256(VISION_PROMPT.encode("utf-8")).hexdigest()[:8]
        if uses_claude():
            return "|".join(["vision", vision_model(), f"edge{ANTHROPIC_LONG_EDGE}",
                             vision_effort(), os.environ.get("KM_VISION_MAX_PAGES", "0"),
                             prompt])
        return "|".join(["vision", vision_model(), dpi,
                         os.environ.get("KM_VISION_MAX_PAGES", "0"), prompt])
    return "|".join(["ocr", ocr_languages(), dpi])


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
    work, and the noise never leaves. So a fallback is never a hit.
    """
    return (entry.get("status") == "ok" and entry.get("recipe") == recipe
            and not entry.get("fallback"))


def ask_vision(image: Path) -> str:
    """Transcribe one page image with the configured vision model."""
    return ask_claude(image) if uses_claude() else ask_ollama(image)


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
    media_type = ANTHROPIC_MEDIA_TYPES.get(image.suffix.lower())
    if media_type is None:
        raise VisionError(f"Claude 不收 {image.suffix} 圖檔，只收 JPEG、PNG、WebP")
    data = image.read_bytes()
    if len(data) > ANTHROPIC_MAX_IMAGE_BYTES:
        raise VisionError(f"圖檔 {len(data) / 1e6:.1f} MB，超過 Claude 單張約 7.5 MB 的上限，"
                          f"請先縮小再放進 Raw/")

    client = anthropic.Anthropic(timeout=float(os.environ.get("KM_VISION_TIMEOUT", "900")))
    # Without credentials the SDK raises a bare TypeError at request time.
    # Asking first turns that into a reason the report can show.
    if not (client.api_key or client.auth_token or getattr(client, "credentials", None)):
        raise VisionError("沒有 Anthropic 憑證：設定 ANTHROPIC_API_KEY（GitHub Actions 要加在 "
                          "repo 的 Settings → Secrets）", fatal=True)
    model = vision_model()[len(ANTHROPIC_PREFIX):]
    request = {
        "model": model,
        "max_tokens": 32000,
        "output_config": {"effort": vision_effort()},
        "messages": [{"role": "user", "content": [
            # Image before the instructions: Claude reads it better that way.
            {"type": "image", "source": {
                "type": "base64", "media_type": media_type,
                "data": base64.standard_b64encode(data).decode("ascii")}},
            {"type": "text", "text": VISION_PROMPT},
        ]}],
    }
    if model in ANTHROPIC_FALLBACK_MODELS:
        # A safety decline re-runs on the model Anthropic recommends for its
        # category, inside the same call, instead of losing the page.
        request.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    try:
        with client.beta.messages.stream(**request) as stream:
            message = stream.get_final_message()
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
            anthropic.NotFoundError) as exc:
        raise VisionError(f"Claude 拒絕了請求（{type(exc).__name__}）："
                          f"檢查 ANTHROPIC_API_KEY 與 KM_VISION_MODEL", fatal=True) from exc
    except anthropic.AnthropicError as exc:
        raise VisionError(f"Claude 呼叫失敗：{exc}") from exc
    except TypeError as exc:
        # The only TypeError this call raises is an SDK that predates one of the
        # parameters above. Every page would fail the same way.
        raise VisionError(f"anthropic 套件太舊（{exc}）：pip install -U anthropic",
                          fatal=True) from exc

    if message.stop_reason == "refusal":
        raise VisionError("Claude 拒絕轉錄這一頁")
    text = "".join(block.text for block in message.content if block.type == "text").strip()
    if message.stop_reason == "max_tokens":
        # Keep what was read, but never let a cut-off page pass for a whole one.
        text += "\n[轉錄到這裡被截斷：超過輸出長度上限]"
    return text


def read_pages(pages: list, text_layer: str = "") -> Extraction:
    """Turn page images into text — vision when configured, OCR otherwise.

    `text_layer` is what pdftotext found on a scan that carries one (phone
    scanner apps OCR the page and embed it). It is the fallback of choice when
    vision fails: the app's OCR is usually better than ours and costs nothing.
    """
    fallback = ""
    if vision_model():
        try:
            return vision_pages(pages)
        except (urllib.error.URLError, OSError, KeyError, ValueError, RuntimeError) as exc:
            # Losing the material because a server blinked would be worse than
            # reading it badly, so fall through and say so.
            fallback = f"vision 失敗（{exc}）"
            print(f"    {fallback}，改用{'PDF 內嵌的文字層' if text_layer else ' OCR'}",
                  file=sys.stderr)
            if text_layer:
                return Extraction(text_layer, "pdftotext:scan", fallback)
    if not have("tesseract"):
        if fallback:
            raise RuntimeError(f"{fallback}，而且沒有 tesseract 可以退回 OCR")
        raise RuntimeError("掃描件需要 tesseract 才能 OCR，或設 KM_VISION_MODEL 用視覺模型讀")
    langs = ocr_languages()
    chunks = [run(["tesseract", str(page), "-", "-l", langs]).stdout for page in pages]
    return Extraction("\n\n".join(chunks), f"ocr:{langs}", fallback)


def vision_pages(pages: list) -> Extraction:
    limit = int(os.environ.get("KM_VISION_MAX_PAGES", "0")) or len(pages)
    used = pages[:limit]
    chunks = []
    failures = 0
    for number, page in enumerate(used, start=1):
        print(f"    vision 第 {number}/{len(used)} 頁…", file=sys.stderr)
        try:
            body = ask_vision(page).strip()
        except (urllib.error.URLError, OSError, KeyError, ValueError, VisionError) as exc:
            if getattr(exc, "fatal", False):
                raise
            # One bad page must not discard the good ones. At minutes apiece,
            # restarting a whole book because the server hiccuped on page 18 is
            # an hour thrown away — and the gap is named, so nothing downstream
            # mistakes a missing page for a blank one.
            failures += 1
            body = f"[這頁沒讀到：{exc}]"
            print(f"      第 {number} 頁失敗：{exc}", file=sys.stderr)
        chunks.append(f"--- page {number} ---\n{body}")
    if failures == len(used):
        raise RuntimeError(f"vision 每一頁都失敗（共 {failures} 頁）")
    if len(used) < len(pages):
        # Saying so matters: a silent stop reads downstream as "this is the
        # whole document", and the rest of the book quietly stops existing.
        chunks.append(f"[只轉錄了前 {len(used)} 頁，全檔共 {len(pages)} 頁。"
                      f"調高 KM_VISION_MAX_PAGES 可讀更多]")
    method = f"vision:{vision_model()}"
    if failures:
        method += f"（{failures}/{len(used)} 頁失敗）"
    return Extraction("\n\n".join(chunks), method)


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


def from_pdf(path: Path) -> Extraction:
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
        return read_pages(pages, text_layer)


def from_image(path: Path) -> Extraction:
    return read_pages([path])


def extract(path: Path) -> Extraction:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return from_docx(path)  # stdlib zipfile, so any filename opens fine
    if suffix == ".pdf" or suffix in IMAGE_SUFFIXES:
        reader = from_pdf if suffix == ".pdf" else from_image
        with staged_for_external_tools(path) as usable:
            return reader(usable)
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
        else:
            lines.append(f"{rel} — ⚠️ 無法讀取：{e['note']}")
    return "\n".join(lines)


def main(argv) -> int:
    if "--list" in argv:
        entries = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
        print(report(entries))
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    previous = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    recipe = extraction_recipe()
    entries: dict = {}
    for path in raw_files():
        rel = path.relative_to(REPO / "vault").as_posix()
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
        if target.exists() and unchanged and reusable(before, recipe):
            entries[rel] = {
                "status": "ok",
                "text": target.relative_to(REPO).as_posix(),
                # Keep the method that actually produced the text. Overwriting
                # it with "cached" loses the only record of how the file was
                # read, and the run after that can no longer tell OCR output
                # from a vision transcript.
                "method": before["method"],
                "recipe": recipe,
                "sha256": digest,
                "cached": True,
                "chars": len(target.read_text(encoding="utf-8", errors="replace")),
            }
            continue

        try:
            result = extract(path)
        except Exception as exc:  # noqa: BLE001 — every failure is reportable, not fatal
            entry = {"status": "failed", "text": None, "method": None,
                     "chars": 0, "note": str(exc)}
            # Text extracted earlier is still on disk and still readable. A
            # missing tool today must not retract material that was already
            # read, or the prompt starts telling the agent the source is
            # unreadable while a good transcript sits next to it.
            if target.exists() and before.get("status") == "ok":
                entry = {
                    "status": "ok",
                    "text": target.relative_to(REPO).as_posix(),
                    "method": before["method"],
                    "recipe": before.get("recipe", ""),
                    "cached": True,
                    "chars": len(target.read_text(encoding="utf-8", errors="replace")),
                    "note": f"沿用先前的抽取結果（這次重抽失敗：{exc}）",
                }
            entries[rel] = entry
            continue

        if not result.text.strip():
            entries[rel] = {"status": "failed", "text": None, "method": result.method,
                            "chars": 0, "note": "抽出來是空的（可能是空白頁或辨識失敗）"}
            continue

        # newline="" disables the platform translation `write_text` would apply.
        # Without it Windows rewrites every \n as \r\n, so the file no longer
        # holds the text that was extracted, the reported size stops matching
        # what a later read returns, and the same PDF yields different bytes on
        # different machines.
        with target.open("w", encoding="utf-8", newline="") as handle:
            handle.write(result.text)
        entries[rel] = {
            "status": "ok",
            "text": target.relative_to(REPO).as_posix(),
            "method": result.method,
            "recipe": recipe,
            "sha256": digest,
            "cached": False,
            "chars": len(result.text),
        }
        if result.fallback:
            entries[rel].update(fallback=True, note=result.fallback)

    MANIFEST.write_text(json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(report(entries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
