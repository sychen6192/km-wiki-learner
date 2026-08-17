#!/usr/bin/env python3
"""vault.py — deterministic toolkit for the km-wiki Obsidian vault.

The LLM writes prose; this script keeps the graph honest. Zero dependencies
beyond the Python 3.9+ standard library, so it runs anywhere the loop runs.

Subcommands:
  scan   Walk the vault and emit a machine-readable work report (JSON):
         stubs, dangling wikilinks (the "frontier"), orphans, due reviews,
         open inbox items. The daily agent plans its day from this.
  lint   Validate frontmatter and vault invariants. Exit 1 on errors so the
         loop can catch bad agent output before committing.
  stats  Regenerate the dashboard block in Home.md between the
         `<!-- km:stats -->` markers. Idempotent, safe to run repeatedly.
  seed   Create a new seed note from the template (used by agent and humans).

The vault contract these tools enforce is documented in AGENTS.md and
vault/_meta/Style Guide.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import unicodedata
from pathlib import Path

# Note titles come from the material, not from the machine's locale: a Windows
# console on a legacy ANSI codepage cannot print a Japanese title and would
# take the whole run down with it.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

VALID_STATUS = ("seed", "budding", "evergreen")
REQUIRED_KEYS = ("status", "created", "updated")
# Folders whose notes carry full frontmatter and participate in the graph.
GRAPH_DIRS = ("Notes", "Sources", "Maps")
# Immutable source layer (Karpathy's llm-wiki "raw/"): humans drop files here,
# the loop digests them into Sources/ notes. Never modified by the agent.
RAW_DIR = "Raw"
# Orphan detection only makes sense for content notes.
ORPHAN_DIRS = ("Notes", "Sources")
STATS_START = "<!-- km:stats:start -->"
STATS_END = "<!-- km:stats:end -->"
STALE_SEED_DAYS = 14
# A source older than this is flagged so notes built on it can date their claims
# instead of stating them as current fact.
STALE_SOURCE_DAYS = 365
UNKNOWN_DATE = "unknown"
# Ingestion watermarks ("where did I get to last time") for external sources.
# Lives under loop/state/, which is gitignored — private to each machine.
CURSOR_FILE = "loop/state/cursors.json"

WIKILINK_RE = re.compile(r"\[\[([^\]\|#\n]+)(?:#[^\]\|\n]*)?(?:\|[^\]\n]*)?\]\]")
# Generated dashboard content is not part of the knowledge graph — links inside
# the stats block must not feed back into the next scan.
STATS_BLOCK_RE = re.compile(r"<!-- km:stats:start -->.*?<!-- km:stats:end -->", re.S)
FENCED_CODE_RE = re.compile(r"^(```|~~~).*?^\1\s*$", re.M | re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
OBSIDIAN_COMMENT_RE = re.compile(r"%%.*?%%", re.S)
TASK_RE = re.compile(r"^\s*-\s*\[( |x|X)\]\s+(.*)$")


# ---------------------------------------------------------------------------
# Frontmatter (minimal YAML subset: scalars, inline lists, block lists)
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str):
    """Return (frontmatter_dict_or_None, body, error_or_None)."""
    if not text.startswith("---\n") and text.strip() != "---":
        return None, text, None
    end = text.find("\n---", 4)
    if end == -1:
        return None, text, "unterminated frontmatter block"
    raw = text[4:end]
    body = text[end + 4:]
    body = body[1:] if body.startswith("\n") else body
    fm: dict = {}
    error = None
    current_list_key = None
    for lineno, line in enumerate(raw.splitlines(), start=2):
        if not line.strip() or line.strip().startswith("#"):
            continue
        item = re.match(r"^\s+-\s*(.*)$", line)
        if item and current_list_key:
            fm[current_list_key].append(_scalar(item.group(1)))
            continue
        kv = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not kv:
            error = error or f"line {lineno}: cannot parse {line.strip()!r}"
            continue
        key, value = kv.group(1), kv.group(2).strip()
        current_list_key = None
        if value == "":
            fm[key] = []
            current_list_key = key
        elif value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            fm[key] = [_scalar(v) for v in inner.split(",") if v.strip()] if inner else []
        else:
            fm[key] = _scalar(value)
    return fm, body, error


def _scalar(value: str):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


# ---------------------------------------------------------------------------
# Vault model
# ---------------------------------------------------------------------------

class Note:
    def __init__(self, path: Path, vault: Path):
        self.path = path
        self.rel = path.relative_to(vault).as_posix()
        self.folder = self.rel.split("/")[0] if "/" in self.rel else ""
        self.title = path.stem
        text = path.read_text(encoding="utf-8")
        self.fm, self.body, self.fm_error = parse_frontmatter(text)
        self.aliases = self._fm_list("aliases")
        self.tags = self._fm_list("tags")
        self.links = extract_links(self.body)
        self.words = len(strip_markup(self.body).split())

    def _fm_list(self, key) -> list:
        v = (self.fm or {}).get(key, [])
        if isinstance(v, str):
            v = [v] if v else []
        return [str(x) for x in v]

    @property
    def status(self):
        return (self.fm or {}).get("status")

    def date(self, key):
        raw = (self.fm or {}).get(key)
        if not isinstance(raw, str) or not raw:
            return None
        try:
            return dt.date.fromisoformat(raw[:10])
        except ValueError:
            return None


def strip_markup(body: str) -> str:
    body = STATS_BLOCK_RE.sub(" ", body)
    body = FENCED_CODE_RE.sub(" ", body)
    body = OBSIDIAN_COMMENT_RE.sub(" ", body)
    body = INLINE_CODE_RE.sub(" ", body)
    body = re.sub(r"^#+\s.*$", " ", body, flags=re.M)
    return body


def extract_links(body: str) -> list:
    cleaned = STATS_BLOCK_RE.sub(" ", body)
    cleaned = INLINE_CODE_RE.sub(" ", OBSIDIAN_COMMENT_RE.sub(" ", FENCED_CODE_RE.sub(" ", cleaned)))
    seen, out = set(), []
    for m in WIKILINK_RE.finditer(cleaned):
        target = m.group(1).strip()
        if target and norm(target) not in seen:
            seen.add(norm(target))
            out.append(target)
    return out


def norm(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold().strip()


class Vault:
    def __init__(self, root: Path):
        self.root = root
        self.notes: list[Note] = []
        self.errors: list[str] = []
        self.meta_notes: list[Note] = []
        self.raw_files: list[str] = sorted(
            p.relative_to(root).as_posix()
            for p in (root / RAW_DIR).rglob("*")
            if p.is_file() and not p.name.startswith((".", "_"))
        ) if (root / RAW_DIR).is_dir() else []
        for path in sorted(root.rglob("*.md")):
            rel = path.relative_to(root).as_posix()
            if "/.obsidian/" in f"/{rel}":
                continue
            if rel.startswith(f"{RAW_DIR}/"):
                continue  # raw sources are opaque payloads, not notes
            if rel.startswith("_meta/"):
                # _meta pages (Style Guide etc.) are valid link targets but do
                # not participate in graph checks; templates are pure scaffolds.
                if not rel.startswith("_meta/Templates/"):
                    try:
                        self.meta_notes.append(Note(path, root))
                    except UnicodeDecodeError:
                        pass
                continue
            try:
                self.notes.append(Note(path, root))
            except UnicodeDecodeError:
                self.errors.append(f"{rel}: not valid UTF-8")
        self.by_name: dict = {}
        for note in self.notes + self.meta_notes:
            self.by_name.setdefault(norm(note.title), note)
            for alias in note.aliases:
                self.by_name.setdefault(norm(alias), note)
        # Raw files are valid wikilink targets by full name ([[paper.pdf]])
        # and by stem ([[paper]]), without being notes.
        self.raw_by_name: dict = {}
        for rel in self.raw_files:
            name = rel.split("/")[-1]
            self.raw_by_name.setdefault(norm(name), rel)
            self.raw_by_name.setdefault(norm(Path(name).stem), rel)

    def resolve(self, target: str):
        return self.by_name.get(norm(target))

    def resolve_raw(self, target: str):
        return self.raw_by_name.get(norm(target))

    def graph_notes(self):
        return [n for n in self.notes if n.folder in GRAPH_DIRS]

    def inbox_items(self):
        """Open (unchecked) task lines from Inbox.md."""
        inbox = self.root / "Inbox.md"
        items = []
        if inbox.exists():
            text = FENCED_CODE_RE.sub(" ", inbox.read_text(encoding="utf-8"))
            for line in text.splitlines():
                m = TASK_RE.match(line)
                if m and m.group(1) == " ":
                    items.append(m.group(2).strip())
        return items


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

def build_report(vault: Vault, today: dt.date) -> dict:
    dangling: dict = {}
    inbound: dict = {}
    ingested_raw: set = set()
    for note in vault.notes:
        for target in note.links:
            resolved = vault.resolve(target)
            if resolved is not None:
                inbound.setdefault(norm(resolved.title), 0)
                inbound[norm(resolved.title)] += 1
                continue
            raw = vault.resolve_raw(target)
            if raw is not None:
                # A raw file counts as ingested once a Sources/ note cites it.
                if note.folder == "Sources":
                    ingested_raw.add(raw)
                continue
            dangling.setdefault(target, []).append(note.rel)
    pending_raw = [rel for rel in vault.raw_files if rel not in ingested_raw]

    orphans = [
        n.rel for n in vault.notes
        if n.folder in ORPHAN_DIRS
        and inbound.get(norm(n.title), 0) == 0
        and not n.links
    ]

    stubs, stale_seeds, due_reviews = [], [], []
    for n in vault.graph_notes():
        is_stub = n.status == "seed" or n.words < 60
        if is_stub:
            stubs.append(n.rel)
            updated = n.date("updated") or n.date("created")
            if updated and (today - updated).days >= STALE_SEED_DAYS:
                stale_seeds.append(n.rel)
        ra = n.date("review_after")
        if ra and ra <= today:
            due_reviews.append({"note": n.rel, "review_after": ra.isoformat()})

    frontier = sorted(
        ({"target": t, "referenced_by": sorted(srcs), "inbound": len(srcs)}
         for t, srcs in dangling.items()),
        key=lambda d: (-d["inbound"], norm(d["target"])),
    )

    status_counts: dict = {s: 0 for s in VALID_STATUS}
    for n in vault.graph_notes():
        if n.status in status_counts:
            status_counts[n.status] += 1

    # Sources whose material predates the last year, or that never said when it
    # was written. Notes resting on these should date their claims rather than
    # assert them as current.
    stale_sources: dict = {}
    for n in vault.notes:
        if n.folder != "Sources":
            continue
        raw = str((n.fm or {}).get("source_date", "")).strip()
        if raw == UNKNOWN_DATE:
            stale_sources[n.rel] = "undated"
            continue
        dated = n.date("source_date")
        if dated and (today - dated).days > STALE_SOURCE_DAYS:
            stale_sources[n.rel] = f"{(today - dated).days // 365 or 1}y+"

    return {
        "generated": today.isoformat(),
        "totals": {
            "notes": len(vault.notes),
            "graph_notes": len(vault.graph_notes()),
            "by_status": status_counts,
            "dangling_links": len(frontier),
            "orphans": len(orphans),
            "due_reviews": len(due_reviews),
            "inbox_open": len(vault.inbox_items()),
            "pending_raw": len(pending_raw),
            "stale_sources": len(stale_sources),
        },
        "inbox": vault.inbox_items(),
        "pending_raw": pending_raw,
        "frontier": frontier,
        "stubs": sorted(stubs),
        "stale_seeds": sorted(stale_seeds),
        "stale_sources": stale_sources,
        "orphans": sorted(orphans),
        "due_reviews": sorted(due_reviews, key=lambda d: d["review_after"]),
    }


# ---------------------------------------------------------------------------
# lint
# ---------------------------------------------------------------------------

def lint(vault: Vault, today: dt.date, strict: bool):
    errors, warnings = list(vault.errors), []
    for n in vault.notes:
        if n.fm_error:
            errors.append(f"{n.rel}: {n.fm_error}")
        if n.folder not in GRAPH_DIRS:
            continue
        if n.fm is None:
            errors.append(f"{n.rel}: missing frontmatter")
            continue
        for key in REQUIRED_KEYS:
            if key not in n.fm:
                errors.append(f"{n.rel}: missing frontmatter key {key!r}")
        if "status" in n.fm and n.status not in VALID_STATUS:
            errors.append(f"{n.rel}: invalid status {n.status!r} (want one of {', '.join(VALID_STATUS)})")
        for key in ("created", "updated", "review_after", "source_date"):
            raw = n.fm.get(key)
            if isinstance(raw, str) and raw and raw != UNKNOWN_DATE and n.date(key) is None:
                errors.append(f"{n.rel}: {key} is not an ISO date: {raw!r}")
        # A digest that does not say how old its source is claims, by omission,
        # to be current — and a stale internal doc reads exactly like a live one.
        if n.folder == "Sources" and not str(n.fm.get("source_date", "")).strip():
            warnings.append(f"{n.rel}: no source_date (how old is the source? "
                            f"use {UNKNOWN_DATE} if it is genuinely undated)")

    report = build_report(vault, today)
    for rel, age in sorted(report["stale_sources"].items()):
        warnings.append(f"{rel}: source is {age} old — notes citing it should date their claims")
    for rel in report["orphans"]:
        warnings.append(f"{rel}: orphan (no inbound or outbound links)")
    for rel in report["stale_seeds"]:
        warnings.append(f"{rel}: seed untouched for {STALE_SEED_DAYS}+ days")
    home = vault.root / "Home.md"
    if home.exists() and STATS_START not in home.read_text(encoding="utf-8"):
        warnings.append("Home.md: stats markers missing; `vault.py stats` cannot update the dashboard")

    for e in errors:
        print(f"ERROR {e}")
    for w in warnings:
        print(f"WARN  {w}")
    failed = bool(errors) or (strict and bool(warnings))
    print(f"lint: {len(errors)} error(s), {len(warnings)} warning(s) "
          f"in {len(vault.notes)} note(s) -> {'FAIL' if failed else 'OK'}")
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# stats (Home.md dashboard)
# ---------------------------------------------------------------------------

def render_stats(vault: Vault, today: dt.date) -> str:
    r = build_report(vault, today)
    t = r["totals"]
    lines = [
        STATS_START,
        f"> [!info] 圖書館現況 · {r['generated']}",
        f"> 筆記 **{t['graph_notes']}** 篇 — "
        f"🌱 seed {t['by_status']['seed']} · 🌿 budding {t['by_status']['budding']} · "
        f"🌳 evergreen {t['by_status']['evergreen']}",
        f"> 待長出的連結（frontier）**{t['dangling_links']}** · "
        f"到期複習 **{t['due_reviews']}** · Inbox 待辦 **{t['inbox_open']}** · "
        f"Raw 待消化 **{t['pending_raw']}**",
        "",
    ]
    if r["frontier"]:
        lines.append("**知識前緣** — 已被引用但還不存在的頁面（明日候選）：")
        for f in r["frontier"][:10]:
            lines.append(f"- [[{f['target']}]] ×{f['inbound']}")
        lines.append("")
    if r["due_reviews"]:
        lines.append("**今日到期複習**：")
        for d in r["due_reviews"][:10]:
            name = Path(d["note"]).stem
            lines.append(f"- [[{name}]]（{d['review_after']}）")
        lines.append("")
    dailies = sorted(
        (n for n in vault.notes if n.folder == "Daily"),
        key=lambda n: n.title, reverse=True,
    )
    if dailies:
        recent = " · ".join(f"[[{n.title}]]" for n in dailies[:5])
        lines.append(f"**最近的每日報告**：{recent}")
        lines.append("")
    lines.append(STATS_END)
    return "\n".join(lines)


def update_home(vault: Vault, today: dt.date) -> int:
    home = vault.root / "Home.md"
    if not home.exists():
        print("stats: vault/Home.md not found", file=sys.stderr)
        return 1
    text = home.read_text(encoding="utf-8")
    start, end = text.find(STATS_START), text.find(STATS_END)
    if start == -1 or end == -1 or end < start:
        print("stats: km:stats markers not found in Home.md", file=sys.stderr)
        return 1
    new = text[:start] + render_stats(vault, today) + text[end + len(STATS_END):]
    if new != text:
        home.write_text(new, encoding="utf-8")
        print("stats: Home.md dashboard updated")
    else:
        print("stats: Home.md already up to date")
    return 0


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------

SEED_TEMPLATE = """---
status: seed
created: {date}
updated: {date}
tags: []
---

> [!note] 種子筆記
> 這一頁還沒長出來。{reason}

## 想知道什麼

- 這是什麼？為什麼重要？
- 它和 vault 裡的哪些概念相連？
"""


def seed(vault_root: Path, title: str, reason: str, today: dt.date) -> int:
    safe = re.sub(r'[\\/:*?"<>|]', "-", title).strip()
    if not safe:
        print("seed: empty title", file=sys.stderr)
        return 1
    path = vault_root / "Notes" / f"{safe}.md"
    if path.exists() or Vault(vault_root).resolve(title):
        print(f"seed: {title!r} already exists, skipping")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    why = f"（{reason}）" if reason else ""
    path.write_text(SEED_TEMPLATE.format(date=today.isoformat(), reason=why), encoding="utf-8")
    print(f"seed: created {path.relative_to(vault_root.parent)}")
    return 0


# ---------------------------------------------------------------------------
# cursor (ingestion watermarks for external sources)
# ---------------------------------------------------------------------------

def cursor(args, repo_root: Path, today: dt.date) -> int:
    """Track how far each external source has been ingested.

    The daily prompt injects `cursor list` so the agent knows where to resume;
    after ingesting it calls `cursor set <name> <value>` to move the watermark.
    Values are opaque strings — an ISO timestamp, a document ID, a page token.
    """
    path = Path(args.state) if args.state else repo_root / CURSOR_FILE
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"cursor: {path} is corrupt, starting fresh", file=sys.stderr)

    if args.action == "list":
        if not data:
            print("(no cursors set — treat every source as a first run)")
            return 0
        for name in sorted(data):
            entry = data[name] if isinstance(data[name], dict) else {"value": data[name]}
            line = f"{name}: {entry.get('value', '')}"
            if entry.get("note"):
                line += f"  # {entry['note']}"
            if entry.get("updated"):
                line += f"  (updated {entry['updated']})"
            print(line)
        return 0

    if not args.name:
        print(f"cursor: {args.action} requires a cursor name", file=sys.stderr)
        return 2

    if args.action == "get":
        entry = data.get(args.name)
        if isinstance(entry, dict):
            print(entry.get("value", ""))
        elif entry is not None:
            print(entry)
        else:
            print(args.default)
        return 0

    if args.action == "set":
        if args.value is None:
            print("cursor: set requires a value", file=sys.stderr)
            return 2
        data[args.name] = {
            "value": args.value,
            "note": args.note,
            "updated": today.isoformat(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"cursor: {args.name} -> {args.value}")
        return 0
    return 2


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vault", default=None, help="vault directory (default: <repo>/vault)")
    parser.add_argument("--today", default=None, help="override today's date (ISO, for tests)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_scan = sub.add_parser("scan", help="emit work-report JSON")
    p_scan.add_argument("--out", default=None, help="write JSON to file instead of stdout")
    p_lint = sub.add_parser("lint", help="validate the vault; exit 1 on errors")
    p_lint.add_argument("--strict", action="store_true", help="warnings also fail")
    sub.add_parser("stats", help="refresh the Home.md dashboard block")
    p_seed = sub.add_parser("seed", help="create a seed note")
    p_seed.add_argument("title")
    p_seed.add_argument("--reason", default="", help="why this seed exists (e.g. 'linked from X')")
    p_cursor = sub.add_parser("cursor", help="read/write ingestion watermarks for external sources")
    p_cursor.add_argument("action", choices=["list", "get", "set"])
    p_cursor.add_argument("name", nargs="?", help="cursor name, e.g. the source's short id")
    p_cursor.add_argument("value", nargs="?", help="new watermark: timestamp, doc id, page token…")
    p_cursor.add_argument("--default", default="", help="printed by `get` when the cursor is unset")
    p_cursor.add_argument("--note", default="", help="human-readable hint stored alongside the value")
    p_cursor.add_argument("--state", default=None, help="override the cursor file path")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent
    today_arg = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    if args.cmd == "cursor":
        return cursor(args, repo_root, today_arg)

    root = Path(args.vault) if args.vault else repo_root / "vault"
    if not root.is_dir():
        print(f"vault directory not found: {root}", file=sys.stderr)
        return 2
    today = today_arg

    if args.cmd == "seed":
        return seed(root, args.title, args.reason, today)

    vault = Vault(root)
    if args.cmd == "scan":
        payload = json.dumps(build_report(vault, today), ensure_ascii=False, indent=2, sort_keys=True)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(payload + "\n", encoding="utf-8")
            print(f"scan: wrote {args.out}")
        else:
            print(payload)
        return 0
    if args.cmd == "lint":
        return lint(vault, today, args.strict)
    if args.cmd == "stats":
        return update_home(vault, today)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
