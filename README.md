# km-wiki-learner 🏛️

**一座自己會長大的個人維基。** 靈感來自 [Andrej Karpathy 的 llm-wiki idea file](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)，加上他沒有做的那一半：**每日自動迴圈**。每天清晨，一個 LLM 圖書館員（經由 [opencode](https://opencode.ai) headless 執行）掃描 Obsidian vault，消化你丟進來的主題與素材、沿著知識前緣生長、複習到期的筆記，然後把成果 commit 給你驗收。

> Karpathy：*「You never (or rarely) write the wiki yourself — the LLM writes and maintains all of it.」*
> 本專案補上的問句是：**那為什麼還要你來按 Enter？**

```
你（Obsidian / 手機）                每日迴圈（cron / systemd / GitHub Actions）
┌──────────────────┐               ┌─────────────────────────────────────────────┐
│ Inbox.md  出題    │──────────────▶│ extract ──▶ scan ──▶ 渲染 prompt ──▶ agent  │
│ Raw/      丟素材  │               │  (OCR)     (JSON)    plan→write→link→review  │
│ git diff  驗收    │◀──────────────│ lint ──▶ stats ──▶ git commit & push        │
└──────────────────┘               └─────────────────────────────────────────────┘
```

## 為什麼再做一個 llm-wiki？

Karpathy 的 idea file（2026-04）走紅後出現一批實作，我們逐一研究過（星數為 2026-08-14 快照，詳見 [docs/RESEARCH.md](docs/RESEARCH.md)）：

| 專案 | ⭐ | 定位 | 缺的那塊 |
|---|---|---|---|
| [karpathy/llm-wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) | ~5k | 原始 idea file：raw→wiki→schema、ingest/query/lint | 純想法，**無排程** — 全靠人類發起 |
| [nashsu/llm_wiki](https://github.com/nashsu/llm_wiki) | 16.3k | 桌面 GUI 實作 | 檔案監看觸發，沒新檔就不成長 |
| [SamurAIGPT/llm-wiki-agent](https://github.com/SamurAIGPT/llm-wiki-agent) | 3.4k | agent skill 版 | 明言「no automatic scheduling, watch folders, or cron」 |
| [garrytan/gbrain](https://github.com/garrytan/gbrain) | 28.4k | 真的有 24/7 迴圈 | 定位是 agent 記憶／CRM 基建，不是學習維基，安裝沉重 |
| [khoj-ai/khoj](https://github.com/khoj-ai/khoj) | 36.5k | AI 第二大腦 + 排程自動化 | 自動化產出寄到信箱，不回寫、不長 vault |
| [AsyncFuncAI/deepwiki-open](https://github.com/AsyncFuncAI/deepwiki-open) | 17.6k | 幫 code repo 生 wiki | 對象是程式碼庫，不是你的知識 |

**開放的缺口**（我們做的）：一個以 Obsidian vault 為家、**每天自主生長**的學習維基 — 附帶知識前緣驅動、spaced repetition、筆記成熟度晉升，以及「LLM 寫、程式驗、人類審」的三權分立。

## 六個差異化設計

1. **知識前緣（frontier）驅動生長** — dangling `[[wikilink]]` 不是錯誤，是維基的好奇心佇列。`scan` 依被引用次數排序，迴圈每天優先長出最被需要的頁面：維基沿著自己的無知邊界擴張。
2. **確定性外骨骼、LLM 內臟** — 掃描、lint、儀表板、git 全是 stdlib Python + bash（零依賴）；LLM 的輸出必須通過 `vault.py lint`（0 error）才會被 commit。壞筆記進不了 main。
3. **丟進去就好** — PDF、掃描件、手機拍的照片、`.docx` 直接放 `Raw/`，迴圈自動抽文字，掃描件走 OCR 或交給 vision 模型逐頁轉錄（`KM_VISION_MODEL`）。出題就在 `Inbox.md` 打勾選框。整個操作介面就是 Obsidian 本身，手機也能用。
4. **知識會留在你腦裡** — 每篇筆記自動配 flashcard（相容 [obsidian-spaced-repetition](https://github.com/st3v3nmw/obsidian-spaced-repetition)），且筆記本身有 `review_after` 排程：到期的筆記迴圈會回頭查證、更新、晉升（`seed → budding → evergreen`）。
5. **完全留痕、完全可攜** — 每天一個 commit + 一篇 `Daily/` 報告（做了什麼、為什麼、明日候選）。全部是 markdown 檔案，Karpathy 說的 "file over app"：沒有資料庫、沒有服務、換掉任何一個工具都活得下去。
6. **不綁任何一家 agent** — repo 裡沒有 opencode 專屬設定；prompt 是 `prompts/` 底下的純文字模板，執行器由 `KM_AGENT_CMD` 決定（`opencode run`、`claude -p`、`codex exec` 都行）。合約寫在 `AGENTS.md`，各家 CLI 都認得。

## 快速開始

```bash
# 1. 安裝 opencode 並設定模型（automation 請用 API key，不能用 Pro/Max OAuth）
curl -fsSL https://opencode.ai/install | bash
export ANTHROPIC_API_KEY=sk-ant-...        # 或 opencode auth login

# 2. 跑第一圈
git clone <this-repo> && cd km-wiki-learner
make daily                                  # = ./loop/daily.sh

# 3. 用 Obsidian 打開 vault/ 資料夾，看它長了什麼
```

跑不起來先 `make doctor`，它會逐項列出這台機器缺什麼。
單獨試工具鏈（不叫 LLM）：`make scan`、`make lint`、`make test`，或 `KM_SKIP_AGENT=1 make daily`。
隨選深潛一個主題：`make learn TOPIC="KV cache 為什麼省算力"`。

📖 **從零走一遍：[docs/WALKTHROUGH.md](docs/WALKTHROUGH.md)** — 以「準備日文 N4，把老師的講義和作業餵進去」為例的完整實作流程。
📘 **日常操作與疑難排解：[docs/USAGE.md](docs/USAGE.md)**
🔧 **要動這個 repo：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — 執行流、四個契約、環境變數，以及 Windows 上那些「名字解析到的東西跟你以為的不一樣」的陷阱。

### 讓它每天自己跑

擇一：

- **cron**：`make install-cron` （預設每天 05:30，可 `make install-cron TIME=06:00`）
- **systemd user timer**（筆電推薦，睡過頭會補跑）：`make install-systemd`
- **GitHub Actions**（免掛機）：repo Settings → Secrets 加 `ANTHROPIC_API_KEY`，把本 repo 推上 GitHub 即可 — [`.github/workflows/daily.yml`](.github/workflows/daily.yml) 每天 05:30（Asia/Taipei）自動跑並 push 成果；也可在 Actions 頁手動觸發並填 `topic` 做隨選深潛。可用 repo Variables `KM_MODEL`、`KM_MAX_ITEMS` 覆寫預設。

### 日常操作（全部在 Obsidian 裡）

| 你做 | 迴圈做 |
|---|---|
| 在 `Inbox.md` 加一行 `- [ ] 主題或問題或網址` | 研究 → 寫筆記 → 勾掉並附上 `→ [[筆記]]` |
| 剪網頁／丟 PDF 進 `vault/Raw/` | 寫成 `Sources/` 摘要、織進概念筆記（原始檔永不修改） |
| 什麼都不做 | 沿知識前緣長頁面、複習到期筆記、養大 seed |
| 刷 `Review/Flashcards.md` 的卡（裝 spaced-repetition 外掛） | 為每篇新筆記補卡 |
| 看 `git diff` / `Daily/` 報告驗收 | 每天一個 commit，一篇報告交代決策 |

## 設定與客製

- **語言**：預設繁體中文（標題與術語用英文）。改 `AGENTS.md` 裡的一行即可。
- **預算**：`KM_MAX_ITEMS`（預設 3 個工作項/天）控制成本上限。
- **模型**：`KM_MODEL=anthropic/claude-sonnet-4-5` 之類（`provider/model` 格式，`opencode models` 可列出）。
- **本機私有指令**：`loop/local/*.md`（gitignored）會注入每天的 prompt，優先級同 Inbox — 適合放只屬於這台機器的來源與偏好，範例見 [`loop/local.example.md`](loop/local.example.md)。
- **掃描件用 vision 讀**：`KM_VISION_MODEL=qwen3.8:27b make extract` — 密排、小字、多語混排的
  教材，OCR 吐的是像文字的雜訊，而下游模型會拿自己的先驗把雜訊補成一份看似合理的假摘要。
  vision 逐頁轉錄準得多，代價是一頁 1～4 分鐘（`KM_VISION_MAX_PAGES` 可先試幾頁）。
  連不上模型會自動退回 OCR。
- **接上外部來源（MCP）**：讓迴圈每天自己去某個系統抓新素材，含「從哪裡抓起」的游標機制 — 見 [docs/SOURCES.md](docs/SOURCES.md)。
- **換掉 opencode**：`KM_AGENT_CMD="claude -p" make daily` — prompt 是純文字，agent 只是執行器。
- **接自己的模型（不裝任何 agent CLI）**：`tools/agent.py` 是內建的極簡 tool-calling
  runner，直接打你自己的 endpoint，只用標準函式庫。模型需要支援 tool calling
  （純 completion 的模型寫不了檔案，也就長不了 vault）：
  ```bash
  KM_API_BASE=http://llm:11434 KM_MODEL=qwen3.6:35b-a3b \
    KM_AGENT_CMD="python tools/agent.py" ./loop/daily.sh
  ```
  （`KM_AGENT_CMD` 是原樣執行的字串，不會替你解析直譯器 — Windows 上要寫 `python`，
  `python3` 在那裡是個什麼都不做的空殼。）
  預設講 Ollama 的 `/api/chat`（這樣才能指定 `KM_NUM_CTX`，避免 context 被默默砍掉）；
  `KM_API_STYLE=openai` 可改用 OpenAI 相容端點。模型拿不到 shell，只能寫 `vault/` 底下的
  `.md`、跑 vault 工具；`Raw/` 唯讀，`locked: true` 與 `<!--SR:...-->` 有防呆擋著。
- **權限**：repo 內沒有任何 agent 設定檔，權限限制放你的全域設定（見 [WALKTHROUGH](docs/WALKTHROUGH.md) 的「安全邊界」）。git 寫入一律由外層腳本執行，agent 改不動歷史。
- **寫作規範**：`vault/_meta/Style Guide.md` 是憲法，`tools/vault.py lint` 是執法者。

## 專案結構

```
AGENTS.md                 agent 操作手冊與行為契約
prompts/daily.md          每日迴圈的 prompt 模板（scan、Inbox、素材清單直接注入）
prompts/learn|garden.md   隨選深潛、結構修復
vault/                    ← 用 Obsidian 打開這個資料夾
  Home.md  Inbox.md  Raw/  Notes/  Maps/  Sources/  Daily/  Review/  _meta/
tools/vault.py            scan / lint / stats / seed（stdlib，零依賴）
tools/extract.py          Raw 的 PDF／掃描件／docx → 文字（自動 OCR）
tools/render.py           把 prompt 模板展開成純文字
tests/                    工具鏈測試（python3 -m unittest）
loop/daily.sh             迴圈外骨骼：extract → scan → agent → lint → commit → push
scripts/doctor.sh         環境自檢
scripts/ systemd/         cron 與 systemd timer 安裝器
.github/workflows/daily.yml
docs/USAGE.md             使用手冊（從第一天到日常掌舵）
docs/ARCHITECTURE.md      這套怎麼跑的：執行流、契約、環境變數、Windows 陷阱
docs/SOURCES.md           接上外部素材來源（MCP）與游標機制
docs/DESIGN.md            設計決策紀錄
docs/RESEARCH.md          先行者研究快照
```

## 靈感與致謝

- Andrej Karpathy — [LLM Knowledge Bases](https://x.com/karpathy/status/2039805659525644595)、[llm-wiki idea file](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)、[讀書配 LLM 的習慣](https://x.com/karpathy/status/1990577951671509438)。本專案是那份 idea file 的「+每日迴圈」分支。
- Andy Matuschak 的 [Evergreen notes](https://notes.andymatuschak.org/Evergreen_notes)、Nick Milo 的 MOC、Maggie Appleton 的 [digital garden 成熟度](https://maggieappleton.com/garden-history)（🌱→🌿→🌳）。
- [opencode](https://opencode.ai) — 預設的 headless agent 執行層（可用 `KM_AGENT_CMD` 換掉）。

License: MIT
