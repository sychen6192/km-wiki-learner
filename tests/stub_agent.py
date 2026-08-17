#!/usr/bin/env python3
"""A stand-in for the agent, so the loop can be tested without an LLM.

Every bug this project has actually shipped lived in loop/daily.sh — a missing
--auto, a Linux-only flock, an interrupt that committed anyway. Those are only
reachable by running the loop, and running the loop meant spending tokens on a
real model, so nobody ran it. This plays the librarian's part instead: it checks
the prompt it was handed really contains the day's work, then writes the kind of
output a good run produces.

    KM_AGENT_CMD="python3 tests/stub_agent.py" ./loop/daily.sh

Behaviour is switched by env var so a test can ask for a specific failure:
    KM_STUB_MODE=ok        write a note and a daily report (default)
    KM_STUB_MODE=silent    write nothing, exit 0 — the "did nothing" run
    KM_STUB_MODE=crash     exit 1 having written nothing
    KM_STUB_MODE=bad-note  write a note with invalid frontmatter, to exercise lint
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REQUIRED_IN_PROMPT = (
    '"frontier"',          # the vault scan really ran
    "Inbox",               # @vault/Inbox.md really got inlined
    "AGENTS.md",           # the contract is pointed at
    "lint",                # the run is told to check its own work
)


def main(argv) -> int:
    mode = os.environ.get("KM_STUB_MODE", "ok")
    prompt = argv[0] if argv else ""

    # The loop passes the rendered prompt as a single argument. If that ever
    # breaks, every run silently becomes a no-op, so fail loudly here instead.
    if len(prompt) < 500:
        print(f"stub: prompt looks empty or truncated ({len(prompt)} chars)", file=sys.stderr)
        return 2
    missing = [m for m in REQUIRED_IN_PROMPT if m not in prompt]
    if missing:
        print(f"stub: prompt is missing {missing}", file=sys.stderr)
        return 2
    if "!`" in prompt or "\n@vault" in prompt:
        print("stub: prompt still contains unexpanded template markers", file=sys.stderr)
        return 2
    budget = os.environ.get("KM_MAX_ITEMS", "")
    if budget and f"最多 **{budget} 個工作項" not in prompt:
        print(f"stub: prompt does not carry the budget KM_MAX_ITEMS={budget}", file=sys.stderr)
        return 2
    print(f"stub: prompt ok ({len(prompt)} chars), mode={mode}")

    if mode == "crash":
        print("stub: pretending to fail", file=sys.stderr)
        return 1
    if mode == "silent":
        return 0

    today = dt.date.today().isoformat()
    note = REPO / "vault" / "Notes" / "Stub Test Note.md"
    if mode == "bad-note":
        note.write_text("---\nstatus: banana\ncreated: whenever\n---\n\nbroken\n", encoding="utf-8")
        return 0

    note.write_text(
        f"---\nstatus: budding\ncreated: {today}\nupdated: {today}\ntags: [test]\n---\n\n"
        "# Stub Test Note\n\n**Stub Test Note** 是整合測試留下的痕跡，用來證明迴圈真的"
        "把 agent 的產出接了起來。它連向 [[Loop Engineering]] 與 [[LLM-Native Wiki]]，"
        f"所以不會被判成孤兒。{'字 ' * 60}\n",
        encoding="utf-8",
    )
    (REPO / "vault" / "Daily" / f"{today}.md").write_text(
        f"# {today} 每日報告\n\n> 一句話：整合測試寫了一篇存根筆記。\n\n"
        "## 完成\n\n- 新筆記：[[Stub Test Note]]\n",
        encoding="utf-8",
    )
    print("stub: wrote one note and a daily report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
