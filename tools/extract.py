#!/usr/bin/env python3
"""Turn whatever got dropped into Raw/ into text the agent can actually read.

You drop a PDF, a scanned homework photo, a .docx from your teacher. Models read
text, so something has to bridge that gap — and it should not be you. This runs
in the loop's preflight and leaves a plain-text rendering of every raw file under
loop/state/extracted/, keeping Raw/ itself untouched.

Extraction is idempotent: a file whose text is newer than the source is skipped,
so re-running costs nothing.

    python3 tools/extract.py           extract anything new, print the report
    python3 tools/extract.py --list    print the last report without doing work

External tools are used when present and reported honestly when not:
  pdftotext, pdftoppm (poppler)   PDF text and page rasterising
  tesseract                       OCR for scans and images
Plain text and .docx need nothing beyond the standard library.

A scan can also be read by a vision model instead of OCR, which is the better
option when the page is dense or multilingual — OCR returns confident noise on
material like that, and noise is what a downstream model quietly invents around.
Set KM_VISION_MODEL and the pages go to KM_API_BASE as images; leave it unset
and nothing changes. OCR remains the fallback if the model cannot be reached.

    KM_VISION_MODEL      e.g. qwen3.8:27b — unset means OCR, as before
    KM_API_BASE          Ollama-compatible server (default http://localhost:11434)
    KM_VISION_MAX_PAGES  stop after N pages (0 = all); a page takes minutes
    KM_VISION_TIMEOUT    seconds per page (default 900)
"""

from __future__ import annotations

import base64
import contextlib
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

# Written against a model that had just invented a textbook's contents rather
# than admit it could not read the page. Every clause below is load-bearing:
# transcription only, an explicit way to say "unreadable", and no room to be
# helpful. A gap the model reports is recoverable; a gap it fills is not.
VISION_PROMPT = """這是掃描的教材頁面。請逐字轉錄你在圖片上「實際看到」的內容，保持原本的排列順序。

規則：
1. 只寫圖片上真的有的字。看不清楚的地方寫 [不清楚]，**絕對不要猜、不要推測**。
2. 不要總結、不要解釋、不要補充任何背景知識或你認為應該出現的內容。
3. 日文保留漢字與假名原樣。標音（ふりがな）字很小，**只有在你確實看得清楚時**
   才寫在該詞後面的括號內；看不清楚就只寫漢字，**不要用你的日文知識推測讀音**。
4. 表格或多欄排版就逐列轉錄，欄位之間用 | 分隔。譯文欄（中／英／韓／越）也要轉錄。
5. 這是轉錄工作，不是理解工作。你的輸出應該只包含頁面上的文字。

如果整頁都無法辨識，只回覆：[這頁讀不到]"""


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


def extraction_recipe() -> str:
    """Everything that changes the output if it changes.

    Cached text is only a hit if it was produced the same way. Keying the cache
    on the source file's mtime alone means raising KM_VISION_MAX_PAGES, moving
    to a better model or bumping the DPI all return the previous answer and
    report success — the most confusing possible outcome, because the command
    the human just ran did nothing and said it worked.
    """
    dpi = os.environ.get("KM_RASTER_DPI", str(RASTER_DPI))
    if vision_model():
        return "|".join(["vision", vision_model(), dpi,
                         os.environ.get("KM_VISION_MAX_PAGES", "0")])
    return "|".join(["ocr", ocr_languages(), dpi])


def ask_vision(image: Path) -> str:
    """Transcribe one page image with the configured vision model."""
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


def read_pages(pages: list):
    """Turn page images into text — vision when configured, OCR otherwise."""
    if vision_model():
        try:
            return vision_pages(pages)
        except (urllib.error.URLError, OSError, KeyError, ValueError, RuntimeError) as exc:
            # Losing the material because a server blinked would be worse than
            # reading it badly, so fall through to OCR and say so in the method.
            print(f"    vision 失敗（{exc}），改用 OCR", file=sys.stderr)
    if not have("tesseract"):
        raise RuntimeError("掃描件需要 tesseract 才能 OCR，或設 KM_VISION_MODEL 用視覺模型讀")
    langs = ocr_languages()
    chunks = [run(["tesseract", str(page), "-", "-l", langs]).stdout for page in pages]
    return "\n\n".join(chunks), f"ocr:{langs}"


def vision_pages(pages: list):
    limit = int(os.environ.get("KM_VISION_MAX_PAGES", "0")) or len(pages)
    used = pages[:limit]
    chunks = []
    failures = 0
    for number, page in enumerate(used, start=1):
        print(f"    vision 第 {number}/{len(used)} 頁…", file=sys.stderr)
        try:
            body = ask_vision(page).strip()
        except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
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
    return "\n\n".join(chunks), method


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


# ---------------------------------------------------------------------------
# Extractors — each returns (text, method) or raises RuntimeError with a reason
# ---------------------------------------------------------------------------

def from_docx(path: Path):
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    return text, "docx"


def from_pdf(path: Path):
    if not have("pdftotext"):
        raise RuntimeError("需要 pdftotext（macOS: brew install poppler / Debian: apt install poppler-utils）")
    done = run(["pdftotext", "-layout", str(path), "-"])
    text = done.stdout
    if len(text.strip()) >= OCR_THRESHOLD:
        return text, "pdftotext"
    # No text does not always mean "scanned". A damaged or unreadable file also
    # comes back empty, and quietly OCRing it would bury the real reason.
    if not text.strip() and done.returncode != 0:
        raise RuntimeError(f"pdftotext 讀不了這個檔：{done.stderr.strip()[:200] or '未知錯誤'}")
    # Too little text: this is a scan, so the page images are the whole content.
    if not have("pdftoppm"):
        raise RuntimeError("PDF 沒有文字層（掃描件），需要 pdftoppm 才能把頁面轉成圖片")
    dpi = os.environ.get("KM_RASTER_DPI", str(RASTER_DPI))
    with tempfile.TemporaryDirectory() as tmp:
        run(["pdftoppm", "-r", dpi, "-png", str(path), f"{tmp}/page"])
        pages = sorted(Path(tmp).glob("page*.png"))
        if not pages:
            raise RuntimeError("PDF 無法轉成圖片")
        return read_pages(pages)


def from_image(path: Path):
    return read_pages([path])


def extract(path: Path):
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
            lines.append(f"{rel} — 已抽出文字：{e['text']}（{e['method']}，{e['chars']} 字）")
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
        if (target.exists() and target.stat().st_mtime >= path.stat().st_mtime
                and previous.get(rel, {}).get("recipe") == recipe):
            entries[rel] = {
                "status": "ok",
                "text": target.relative_to(REPO).as_posix(),
                "method": "cached",
                "recipe": recipe,
                "chars": len(target.read_text(encoding="utf-8", errors="replace")),
            }
            continue

        try:
            text, method = extract(path)
        except Exception as exc:  # noqa: BLE001 — every failure is reportable, not fatal
            entries[rel] = {"status": "failed", "text": None, "method": None,
                            "chars": 0, "note": str(exc)}
            continue

        if not text.strip():
            entries[rel] = {"status": "failed", "text": None, "method": method,
                            "chars": 0, "note": "抽出來是空的（可能是空白頁或辨識失敗）"}
            continue

        target.write_text(text, encoding="utf-8")
        entries[rel] = {
            "status": "ok",
            "text": target.relative_to(REPO).as_posix(),
            "method": method,
            "recipe": recipe,
            "chars": len(text),
        }

    MANIFEST.write_text(json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(report(entries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
