#!/usr/bin/env bash
# Check that this machine can actually run the loop, and say what to do about
# anything that is missing. Run this first when something does not work.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
problems=0

ok()   { printf '  ✅ %s\n' "$*"; }
warn() { printf '  ⚠️  %s\n' "$*"; }
bad()  { printf '  ❌ %s\n' "$*"; problems=$((problems + 1)); }

echo "必要條件"
if command -v python3 >/dev/null 2>&1; then
    ok "python3 — $(python3 --version 2>&1)"
else
    bad "python3 沒有安裝（工具鏈全靠它）"
fi
if command -v git >/dev/null 2>&1; then
    ok "git — $(git --version | awk '{print $3}')"
else
    bad "git 沒有安裝"
fi

AGENT_CMD="${KM_AGENT_CMD:-opencode run --auto}"
AGENT_BIN="${AGENT_CMD%% *}"
if command -v "$AGENT_BIN" >/dev/null 2>&1; then
    ok "agent CLI — $AGENT_BIN（完整指令：$AGENT_CMD）"
else
    bad "找不到 $AGENT_BIN。裝 opencode：curl -fsSL https://opencode.ai/install | bash"
    bad "  裝完記得：export PATH=\"\$HOME/.opencode/bin:\$PATH\""
fi
if [[ "$AGENT_BIN" == "opencode" && "$AGENT_CMD" != *"--auto"* ]]; then
    bad "KM_AGENT_CMD 少了 --auto，headless 執行時所有寫檔都會被自動拒絕"
fi
if [[ -n "${KM_MODEL:-}" ]]; then
    ok "KM_MODEL — $KM_MODEL"
else
    warn "沒設 KM_MODEL，會用 agent 的預設模型（可能不是你想要的那個）"
fi

if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    ok "ANTHROPIC_API_KEY 已設定（${ANTHROPIC_API_KEY:0:7}…）"
elif [[ -f "$HOME/.local/share/opencode/auth.json" ]]; then
    ok "opencode 已登入（auth.json 存在）"
else
    bad "沒有模型憑證。export ANTHROPIC_API_KEY=… 或跑 opencode auth login"
fi

echo
echo "選用（缺了會降級，不會壞）"
if command -v timeout >/dev/null 2>&1; then
    ok "timeout — agent 跑太久會被中止"
elif command -v gtimeout >/dev/null 2>&1; then
    ok "gtimeout — agent 跑太久會被中止"
else
    warn "沒有 timeout／gtimeout，agent 會無限期執行（macOS: brew install coreutils）"
fi
if command -v pdftotext >/dev/null 2>&1; then
    ok "pdftotext — 讀得了 PDF"
else
    warn "沒有 pdftotext，PDF 讀不了（macOS: brew install poppler / Debian: apt install poppler-utils）"
fi
if command -v tesseract >/dev/null 2>&1; then
    langs="$(tesseract --list-langs 2>/dev/null | tail -n +2 | tr '\n' ' ')"
    ok "tesseract — 掃描件可 OCR，已裝語言：${langs:-未知}"
else
    warn "沒有 tesseract，掃描件／照片無法辨識（macOS: brew install tesseract tesseract-lang）"
fi

echo
echo "專案狀態"
if [[ -d vault ]]; then
    ok "vault/ 存在"
    if python3 tools/vault.py lint >/dev/null 2>&1; then
        ok "vault lint 通過"
    else
        bad "vault lint 失敗，跑 make lint 看細節"
    fi
else
    bad "找不到 vault/，你在對的目錄嗎？"
fi
if [[ -d loop/state/lock ]]; then
    owner="$(cat loop/state/lock/pid 2>/dev/null || echo unknown)"
    if kill -0 "$owner" 2>/dev/null; then
        warn "目前有一圈正在跑（pid $owner）"
    else
        warn "有殘留的鎖（pid $owner 已不存在），下次執行會自動回收"
    fi
else
    ok "沒有殘留的鎖"
fi

echo
if (( problems == 0 )); then
    echo "可以跑了：KM_MAX_ITEMS=1 make daily"
else
    echo "有 $problems 項必要條件沒滿足，先解決上面標 ❌ 的項目。"
    exit 1
fi
