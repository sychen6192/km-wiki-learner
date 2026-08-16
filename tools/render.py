#!/usr/bin/env python3
"""Expand a prompt template into the plain text the agent receives.

Templates in prompts/ stay readable by keeping their moving parts inline:

    !`command`   run a shell command, substitute its output
    @path        inline a file's contents
    $ARGUMENTS   the arguments passed to this run ($1, $2… for individual ones)

The loop renders a template and hands the result to whatever agent CLI is
configured, so the prompt is portable text rather than one vendor's config
format. Rendering is also how you read the prompt before spending a token:

    python3 tools/render.py prompts/daily.md            preview, with headers
    python3 tools/render.py --raw prompts/daily.md      just the prompt text
    python3 tools/render.py prompts/learn.md "KV cache" with arguments
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# The rendered prompt carries whatever the vault contains — Japanese, Chinese,
# anything — so stdout must not be at the mercy of the console's codepage.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
SHELL_RE = re.compile(r"!`([^`]+)`")
FILE_RE = re.compile(r"(?m)^@([^\s]+)\s*$")


def split_frontmatter(text: str):
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---", 4)
    if end == -1:
        return "", text
    body = text[end + 4:]
    return text[4:end], body[1:] if body.startswith("\n") else body


def expand_shell(text: str) -> str:
    # A template that says `python3` gets whatever that name happens to mean on
    # this machine — on Windows it is usually a Store stub that prints nothing
    # and exits. The interpreter already running this file is the one that
    # works, so templates ask for `$KM_PYTHON` and get it.
    env = dict(os.environ)
    env.setdefault("KM_PYTHON", sys.executable)

    # Templates are written in POSIX shell — `date +%F`, `2>/dev/null || true`,
    # `${VAR:-default}`. `shell=True` means cmd.exe on Windows, which
    # understands none of it and would paste its own error messages into the
    # prompt where the vault scan should be. The loop already runs on bash, so
    # the templates get bash here too.
    shell = os.environ.get("KM_SHELL") or shutil.which("bash")

    def run(match):
        cmd = match.group(1)
        try:
            # Decode explicitly: these commands report on a vault full of
            # non-Latin titles, and the platform default would mangle them.
            done = subprocess.run(
                [shell, "-c", cmd] if shell else cmd, shell=not shell,
                cwd=REPO, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120, env=env,
            )
            return (done.stdout or done.stderr).rstrip("\n")
        except subprocess.TimeoutExpired:
            return f"[`{cmd}` timed out]"
    return SHELL_RE.sub(run, text)


def expand_files(text: str) -> str:
    def inline(match):
        rel = match.group(1)
        path = REPO / rel
        if not path.is_file():
            return f"[{rel} not found]"
        body = path.read_text(encoding="utf-8", errors="replace")
        return f"--- 8< --- {rel} --- 8< ---\n{body}--- 8< --- end {rel} --- 8< ---"
    return FILE_RE.sub(inline, text)


def render(template: Path, arguments: list) -> str:
    _, body = split_frontmatter(template.read_text(encoding="utf-8"))
    body = body.replace("$ARGUMENTS", " ".join(arguments))
    for i, value in enumerate(arguments, start=1):
        body = body.replace(f"${i}", value)
    return expand_files(expand_shell(body)).rstrip() + "\n"


def main(argv) -> int:
    raw = "--raw" in argv
    argv = [a for a in argv if a != "--raw"]
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2

    template = Path(argv[0])
    if not template.is_absolute():
        template = REPO / template
    if not template.is_file():
        print(f"template not found: {template}", file=sys.stderr)
        return 1

    text = render(template, argv[1:])
    if raw:
        sys.stdout.write(text)
        return 0

    rel = template.relative_to(REPO)
    args = " ".join(argv[1:])
    print("=" * 72)
    print(f"PROMPT SENT TO THE AGENT  ({rel}{f', $ARGUMENTS={args!r}' if args else ''})")
    print("=" * 72)
    sys.stdout.write(text)
    print("=" * 72)
    print("Plus, loaded by the agent itself: AGENTS.md, vault/_meta/Style Guide.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
