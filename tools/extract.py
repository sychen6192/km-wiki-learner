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
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
    # Too little text: this is a scan, so rasterise the pages and OCR them.
    if not (have("pdftoppm") and have("tesseract")):
        raise RuntimeError("PDF 沒有文字層（掃描件），需要 pdftoppm + tesseract 才能 OCR")
    langs = ocr_languages()
    with tempfile.TemporaryDirectory() as tmp:
        run(["pdftoppm", "-r", "200", "-png", str(path), f"{tmp}/page"])
        pages = sorted(Path(tmp).glob("page*.png"))
        if not pages:
            raise RuntimeError("PDF 無法轉成圖片")
        chunks = []
        for page in pages:
            got = run(["tesseract", str(page), "-", "-l", langs])
            chunks.append(got.stdout)
    return "\n\n".join(chunks), f"ocr:{langs}"


def from_image(path: Path):
    if not have("tesseract"):
        raise RuntimeError("需要 tesseract 才能辨識圖片文字（macOS: brew install tesseract tesseract-lang）")
    langs = ocr_languages()
    done = run(["tesseract", str(path), "-", "-l", langs])
    return done.stdout, f"ocr:{langs}"


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
        if target.exists() and target.stat().st_mtime >= path.stat().st_mtime:
            entries[rel] = {
                "status": "ok",
                "text": target.relative_to(REPO).as_posix(),
                "method": "cached",
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
            "chars": len(text),
        }

    MANIFEST.write_text(json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(report(entries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
