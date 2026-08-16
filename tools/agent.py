#!/usr/bin/env python3
"""agent.py — run the km-wiki prompt against your own model, no vendor CLI.

The loop hands a prompt to whatever `KM_AGENT_CMD` names and expects that
process to *write files into the vault*. A bare chat completion cannot do that,
so this is a small tool-calling agent: it gives the model a handful of
file operations, runs them, feeds the results back, and stops when the model
says it is done.

    KM_AGENT_CMD="python3 tools/agent.py" ./loop/daily.sh

Talks to Ollama's native /api/chat by default, which is the only way to pin the
context window — a model served at 4k tokens would silently drop most of the
prompt. Set KM_API_STYLE=openai for an OpenAI-compatible endpoint instead
(any provider), at the cost of that control.

    KM_API_BASE    http://llm:11434            server root
    KM_API_STYLE   ollama | openai             wire format (default: ollama)
    KM_API_KEY     sent as a bearer token if set
    KM_MODEL       model name, or pass --model
    KM_NUM_CTX     context window to request (default 32768, ollama style only)
    KM_MAX_STEPS   tool-call budget before giving up (default 60)

The model gets no shell. It reads and writes files under the repo, and may run
the vault toolkit's own subcommands — nothing else. Raw/ is read-only, matching
the contract in AGENTS.md, and spaced-repetition scheduling comments are
protected from being dropped on a rewrite.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
VAULT = REPO / "vault"
RAW = VAULT / "Raw"
SR_COMMENT = re.compile(r"<!--SR:.*?-->", re.S)
# Subcommands the model may run. Everything else — git, rm, arbitrary shell —
# is deliberately absent: the exoskeleton owns those, not the librarian.
VAULT_COMMANDS = ("scan", "lint", "stats", "seed")
MAX_READ_CHARS = 200_000


# The task prompt is written for a general agent and tells it to go and verify
# things online. This runner has no network tool, so an instruction to browse is
# an instruction to invent: a real run cited a jlpt.jp URL it could not possibly
# have opened. The runner is the only thing that knows what it can actually do,
# so it says so up front.
CAPABILITIES = """## 你這次的實際能力（先讀這段，它覆蓋下面任何相衝突的指示）

你**只有**這些工具：`list_dir`、`read_file`、`write_file`、`edit_file`、`vault`。

- **你沒有網路，也沒有 shell。** 下面的指示若叫你「上網查證」，你做不到。
- 因此 `sources:` 只能填**你這次真的用 `read_file` 讀過的 repo 內路徑**。
  絕對不要寫網址——你無法開啟它，寫上去就是編造來源。
- 教材讀不到、或你不確定某個說法時，就在筆記裡明說「不確定」或直接略過。
  留一個誠實的缺口是可以的；用你的先驗把它補起來不行。
- 引用教材時，例句要是你在抽出的文字裡真的看得到的那幾句。"""


class ToolError(Exception):
    """A tool refused the call. The message goes back to the model verbatim."""


# --- path safety -------------------------------------------------------------

def resolve(rel: str, *, for_write: bool) -> Path:
    """Resolve a model-supplied path, or refuse it.

    The model is told to work in the vault, but "told" is not a guarantee, so
    every path is checked rather than trusted.
    """
    if not rel or not str(rel).strip():
        raise ToolError("path 不能是空的")
    path = (REPO / str(rel).strip()).resolve()
    try:
        path.relative_to(REPO)
    except ValueError:
        raise ToolError(f"拒絕：{rel} 在 repo 之外") from None
    if ".git" in path.parts:
        raise ToolError("拒絕：不能碰 .git")
    if for_write:
        # Reads range over the whole repo — AGENTS.md, the prompts, the
        # extracted material. Writes do not: the vault is the only place the
        # librarian is allowed to leave anything. A model that drops
        # `Notes/Foo.md` at the repo root has written a note that no longer
        # exists as far as scan and lint are concerned, and the wikilink
        # pointing at it silently becomes a dangling one, so say where it goes.
        if path != VAULT and VAULT not in path.parents:
            suggestion = f"，你要的可能是 vault/{str(rel).lstrip('/')}"
            raise ToolError(f"拒絕：只能寫在 vault/ 底下（收到 {rel}）{suggestion}")
        if path == RAW or RAW in path.parents:
            raise ToolError(f"拒絕：Raw/ 是不可變素材層，只能讀（{rel}）")
        # Markdown only. `.json` and extensionless were allowed for no stated
        # reason while the refusal below claimed otherwise — and between them
        # they put vault/.obsidian/*.json within reach of the librarian.
        if path.suffix.lower() != ".md":
            raise ToolError(f"拒絕：只能寫 .md（收到 {rel}）")
    return path


def guard_overwrite(path: Path, new_text: str) -> None:
    """Refuse rewrites that would destroy things the human owns.

    A note marked `locked: true` is off limits, and spaced-repetition schedules
    live in `<!--SR:...-->` comments the plugin wrote — losing them silently
    resets real review history, so a rewrite that drops them is rejected rather
    than committed and discovered weeks later.
    """
    if not path.exists():
        return
    old = path.read_text(encoding="utf-8", errors="replace")
    if re.search(r"(?m)^locked:\s*true\s*$", old):
        raise ToolError(f"拒絕：{path.relative_to(REPO)} 標了 locked: true")
    lost = set(SR_COMMENT.findall(old)) - set(SR_COMMENT.findall(new_text))
    if lost:
        raise ToolError(
            f"拒絕：這次改寫會刪掉 {len(lost)} 個 <!--SR:...--> 複習排程註解。"
            "保留原本的註解再寫一次。"
        )


# --- tools -------------------------------------------------------------------

def nearby(rel: str) -> str:
    """Suggest the path the model probably meant.

    It guesses the root in both directions — `vault/AGENTS.md` for a file at
    the repo root, `Notes/Foo.md` for one inside the vault — and a bare "not
    found" gives it nothing to correct with. Observed: a run asked for
    `vault/AGENTS.md` twice, was refused twice, and gave up having written
    nothing. Naming the real path costs one glob and it recovers next turn.
    """
    name = Path(str(rel)).name
    if not name:
        return ""
    found = [p.relative_to(REPO).as_posix()
             for p in REPO.glob(f"**/{name}")
             if p.is_file() and ".git" not in p.parts][:3]
    return f"（也許你要的是：{'、'.join(found)}）" if found else ""


def tool_list_dir(path: str = "vault") -> str:
    target = resolve(path, for_write=False)
    if not target.is_dir():
        raise ToolError(f"{path} 不是資料夾")
    entries = []
    for item in sorted(target.iterdir()):
        if item.name.startswith("."):
            continue
        entries.append(f"{item.relative_to(REPO).as_posix()}{'/' if item.is_dir() else ''}")
    return "\n".join(entries) or "(空的)"


def tool_read_file(path: str) -> str:
    target = resolve(path, for_write=False)
    if not target.is_file():
        raise ToolError(f"找不到檔案：{path}{nearby(path)}")
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_READ_CHARS:
        return text[:MAX_READ_CHARS] + f"\n\n[...截斷，全長 {len(text)} 字]"
    return text


def tool_write_file(path: str, content: str) -> str:
    target = resolve(path, for_write=True)
    guard_overwrite(target, content)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not content.endswith("\n"):
        content += "\n"
    target.write_text(content, encoding="utf-8")
    return f"已寫入 {target.relative_to(REPO).as_posix()}（{len(content)} 字）"


def tool_edit_file(path: str, old_text: str, new_text: str) -> str:
    target = resolve(path, for_write=True)
    if not target.is_file():
        raise ToolError(f"找不到檔案：{path}")
    body = target.read_text(encoding="utf-8", errors="replace")
    hits = body.count(old_text)
    if hits == 0:
        raise ToolError(f"在 {path} 找不到要取代的文字")
    if hits > 1:
        raise ToolError(f"要取代的文字在 {path} 出現 {hits} 次，請給更長的唯一片段")
    updated = body.replace(old_text, new_text)
    guard_overwrite(target, updated)
    target.write_text(updated, encoding="utf-8")
    return f"已修改 {target.relative_to(REPO).as_posix()}"


def tool_vault(command: str, argument: str = "") -> str:
    if command not in VAULT_COMMANDS:
        raise ToolError(f"只能跑 {', '.join(VAULT_COMMANDS)}（收到 {command}）")
    cmd = [os.environ.get("KM_PYTHON") or sys.executable, "tools/vault.py", command]
    if argument:
        cmd.append(argument)
    done = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    return (done.stdout + done.stderr).strip() or "(沒有輸出)"


TOOLS = {
    "list_dir": tool_list_dir,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "vault": tool_vault,
}

SCHEMA = [
    {"type": "function", "function": {
        "name": "list_dir", "description": "列出資料夾內容（相對 repo 根目錄）",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "例如 vault/Notes"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "讀取一個檔案的完整內容",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file", "description": "建立或覆寫一個 .md 檔",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "edit_file", "description": "把檔案裡一段唯一的文字換成新的（增量編輯優先用這個）",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old_text": {"type": "string"},
            "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}}},
    {"type": "function", "function": {
        "name": "vault", "description": "執行 vault 工具：scan（工作清單）、lint（驗收）、stats（儀表板）、seed（建種子頁，argument 給標題）",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "enum": list(VAULT_COMMANDS)},
            "argument": {"type": "string"}}, "required": ["command"]}}},
]


# --- model plumbing ----------------------------------------------------------

def post(url: str, payload: dict, timeout: int) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    key = os.environ.get("KM_API_KEY")
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise SystemExit(f"agent: 模型回了 HTTP {exc.code} — {detail}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"agent: 連不上 {url} — {exc.reason}") from None


def chat(base: str, style: str, model: str, messages: list, timeout: int) -> dict:
    """One round trip. Returns {content, tool_calls} in a shape we control."""
    if style == "openai":
        data = post(f"{base}/v1/chat/completions", {
            "model": model, "messages": messages, "tools": SCHEMA, "stream": False,
        }, timeout)
        message = data["choices"][0]["message"]
    else:
        data = post(f"{base}/api/chat", {
            "model": model, "messages": messages, "tools": SCHEMA, "stream": False,
            "options": {"num_ctx": int(os.environ.get("KM_NUM_CTX", "32768"))},
        }, timeout)
        message = data["message"]

    calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function", {})
        arguments = function.get("arguments")
        # The OpenAI wire format sends arguments as a JSON string; Ollama's
        # native one sends a real object. Accept whichever turns up.
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"__unparsable__": arguments}
        calls.append({"id": call.get("id", ""), "name": function.get("name", ""),
                      "arguments": arguments or {}})
    return {"raw": message, "content": message.get("content") or "", "tool_calls": calls}


def main(argv) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="the rendered prompt text")
    parser.add_argument("--model", default=os.environ.get("KM_MODEL", ""))
    parser.add_argument("--max-steps", type=int,
                        default=int(os.environ.get("KM_MAX_STEPS", "60")))
    args = parser.parse_args(argv)

    base = os.environ.get("KM_API_BASE", "http://localhost:11434").rstrip("/")
    style = os.environ.get("KM_API_STYLE", "ollama").lower()
    timeout = int(os.environ.get("KM_HTTP_TIMEOUT", "900"))
    if not args.model:
        raise SystemExit("agent: 要指定模型（--model 或 KM_MODEL）")

    print(f"agent: {args.model} @ {base} ({style} 格式)", file=sys.stderr)
    messages = [{"role": "user", "content": CAPABILITIES + "\n\n---\n\n" + args.prompt}]
    used = 0

    while used < args.max_steps:
        reply = chat(base, style, args.model, messages, timeout)
        messages.append(reply["raw"])

        if not reply["tool_calls"]:
            if reply["content"].strip():
                print(reply["content"])
            print(f"agent: 完成，用了 {used} 次工具呼叫", file=sys.stderr)
            return 0

        for call in reply["tool_calls"]:
            used += 1
            name, arguments = call["name"], call["arguments"]
            try:
                if name not in TOOLS:
                    raise ToolError(f"沒有這個工具：{name}")
                result = TOOLS[name](**arguments)
                status = "ok"
            except ToolError as exc:
                result, status = str(exc), "拒絕"
            except TypeError as exc:
                result, status = f"參數不對：{exc}", "錯誤"
            except Exception as exc:  # noqa: BLE001 — the model should see it and adapt
                result, status = f"{type(exc).__name__}: {exc}", "錯誤"
            preview = str(arguments)[:90].replace("\n", " ")
            print(f"  [{used}] {name}({preview}) -> {status}", file=sys.stderr)
            messages.append({"role": "tool", "tool_name": name,
                             "tool_call_id": call["id"], "content": str(result)})

    print(f"agent: 用完 {args.max_steps} 次工具預算就停了，做到哪算哪", file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        # Ctrl-C is an answer, not a crash. A traceback here buries the loop's
        # own "interrupted" message under forty lines of urllib internals.
        print("\nagent: 中斷，已寫入的檔案留在工作區", file=sys.stderr)
        raise SystemExit(130) from None
