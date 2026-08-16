# 使用手冊

從零到每天自動運轉，以及之後怎麼掌舵。

## 心智模型：三個角色

| 角色 | 負責 | 你會碰到的東西 |
|---|---|---|
| **你（主編）** | 意圖與品味 | `Inbox.md` 出題、`Raw/` 丟素材、`git diff` 驗收 |
| **圖書館員（LLM）** | 研究與寫作 | 每天產出的筆記、複習卡、`Daily/` 報告 |
| **工具（程式）** | 結構與紀律 | `vault.py` 的 scan/lint/stats、`daily.sh`、git |

一句話：**你出題，它做功課，程式當監考官。** 你永遠不必自己寫維基，
但每天都看得懂它做了什麼、為什麼 —— 因為每圈都留下 commit 和報告。

## 第一天

```bash
# 1. 安裝 opencode 與模型憑證（automation 要用 API key）
curl -fsSL https://opencode.ai/install | bash
export PATH="$HOME/.opencode/bin:$PATH"
export ANTHROPIC_API_KEY=sk-ant-...

# 2. 先不叫 LLM，確認工具鏈是好的
git clone <this-repo> && cd km-wiki-learner
make test                    # 工具鏈測試
KM_SKIP_AGENT=1 make daily   # 空跑一圈：scan → lint → commit

# 3. 用 Obsidian「開啟資料夾作為儲存庫」，選 vault/
#    建議裝兩個外掛：spaced-repetition（刷卡）、git（手機同步）

# 4. 在 vault/Inbox.md 寫下你真正想學的三件事，然後跑第一圈真的
make daily
```

第一圈跑完，讀 `vault/Daily/<今天>.md` —— 它會告訴你挑了什麼、為什麼、
明天想做什麼。不滿意就調整 Inbox 或 `vault/_meta/Style Guide.md`，明天再看。

## 讓它每天自己跑

擇一，然後就可以忘掉它：

```bash
make install-cron              # 一般機器，預設 05:30
make install-cron TIME=07:00   # 換時間
make install-systemd           # 筆電推薦：關機錯過的會補跑
```

或完全不掛機 —— 用 GitHub Actions：把 repo 推上 GitHub，
Settings → Secrets 加 `ANTHROPIC_API_KEY`，`.github/workflows/daily.yml`
會每天 05:30（台北時間）自己跑並 push 成果。**注意：排程只有在
預設分支上才會生效**，所以要 merge 進 main。

## 日常節奏

| 你花 30 秒做 | 迴圈隔天做 |
|---|---|
| `Inbox.md` 加一行 `- [ ] 為什麼 KV cache 能省算力？` | 研究、寫成筆記、勾掉並附上 `→ [[筆記]]` |
| 剪一篇網頁 / 丟 PDF 進 `vault/Raw/` | 寫成 `Sources/` 摘要，把觀念織進概念筆記 |
| 什麼都不做 | 沿知識前緣長新頁、複習到期筆記、養大 seed |
| 在 Obsidian 刷複習卡 | 為新筆記補卡 |
| 掃一眼 `git diff` | 每天一個 commit、一篇報告 |

**手機也能用**：Obsidian 手機版打開同一個 vault（用 obsidian-git 或
iCloud/Syncthing 同步），通勤時丟題目、剪網頁、刷卡；生長在伺服器端發生。

## 四個指令

平常只會用到 `daily`（自動）和 `learn`（想立刻學）。

```bash
make daily                        # 每日迴圈（cron 跑的就是這個）
make learn TOPIC="Raft 共識演算法"  # 現在就深潛一個主題
make prompt                       # 印出這次要送出去的 prompt，不花 token
make extract                      # 只把 Raw 的素材轉成文字
```

三個 prompt 模板都在 `prompts/`：`daily`（每日）、`learn`（隨選）、`garden`（只修結構）。
`daily` 內建：lint 失敗會自動叫一次 `garden` 修。

## 怎麼下好 Inbox 指令

Inbox 是你唯一的方向盤，寫法決定產出品質：

```markdown
- [ ] 為什麼 KV cache 能省算力？代價是什麼？        ← 好：有具體問題，逼出取捨分析
- [ ] https://arxiv.org/abs/xxxx 這篇的核心貢獻     ← 好：指定來源，可查證
- [ ] 深化 [[Spaced Repetition]]，補上 Anki 的實際演算法  ← 好：指定既有筆記與缺口
- [ ] 機器學習                                      ← 差：太大，會得到一篇空泛概論
- [ ] 整理我的想法                                  ← 差：沒有可查證的目標
```

原則：**一行一個可回答的問題**。想要一個領域的全景，就要求它「開一張 Map
並列出該領域的核心概念」，而不是要它「介紹某某領域」。

## 驗收與掌舵

```bash
git log --oneline -7                    # 這週長了什麼
git diff HEAD~1 -- vault/               # 昨天改了哪些字
cat vault/Daily/$(date +%F).md          # 今天的決策理由
make scan | head -40                    # 現在的待辦與知識前緣
```

三種常見的掌舵動作：

- **內容不合胃口** → 改 `vault/_meta/Style Guide.md`（那是憲法，agent 每天讀）。
- **方向跑偏** → 在 Inbox 加明確題目；Inbox 永遠優先於自主探索。
- **某頁不准動** → 該筆記 frontmatter 加 `locked: true`，agent 就不會碰。

寫壞了就 `git revert` —— 全部是純文字檔，沒有資料庫可以壞。

## 旋鈕

| 想調整 | 怎麼做 |
|---|---|
| 每天做多少（成本） | `KM_MAX_ITEMS=5 make daily`，或 GitHub repo variable |
| 用哪個模型 | `KM_MODEL=anthropic/claude-sonnet-4-5`（`opencode models` 可列出） |
| 換掉 opencode | `KM_AGENT_CMD="claude -p"`，prompt 是純文字，任何 CLI agent 都能接 |
| 筆記語言 | 改 `AGENTS.md` 裡語言那一行 |
| 複習間隔 | 改 `AGENTS.md` 的 7 → 21 → 60 天 |
| 接外部來源（MCP） | 見 [SOURCES.md](SOURCES.md) |
| 本機專屬指令 | `loop/local/*.md`（不進 git），見 `loop/local.example.md` |

## 疑難排解

| 症狀 | 原因與解法 |
|---|---|
| `opencode not found` | 沒裝或 PATH 沒帶到 `$HOME/.opencode/bin`（cron 的 PATH 很乾淨，安裝腳本已代入） |
| 迴圈跑了但沒 commit | 沒有 vault 變更就不會 commit（正常）；看 `loop/logs/<日期>.log` |
| 卡住不動、log 有 `auto-rejecting` | 某個權限沒開，在全域 `~/.config/opencode/opencode.json` 加 `allow` |
| lint 一直有 error | 看 `make lint` 指的那幾行；`daily` 已內建一次自動修復 |
| 想重跑今天 | 直接再跑一次 `make daily`；迴圈冪等，工作由現況決定 |
| 兩個迴圈同時跑 | 不會 —— flock 會讓第二個直接退出 |
| 複習進度不見了 | 檢查是否有人改動 `<!--SR:...-->` 註解（Style Guide 明令禁止） |

日誌都在 `loop/logs/`，狀態在 `loop/state/`，兩者都不進 git。
