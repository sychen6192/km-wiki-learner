# 設計文件

開發方法：brainstorm → 先行者研究（[RESEARCH.md](RESEARCH.md)）→ 設計 → 實作 → 測試。
本文件記錄「為什麼長這樣」，供未來的人（或 agent）修改系統時參考。

## 核心主張

Karpathy 的 llm-wiki 模式（raw → wiki → schema，操作 ingest / query / lint）解決了「誰來寫維基」；
本專案解決「誰來按 Enter」。設計成三權分立：

| 角色 | 承擔 | 實體 |
|---|---|---|
| 人類主編 | 意圖與品味 | `Inbox.md`、`Raw/`、`loop/local/`、git diff 驗收 |
| LLM 圖書館員 | 認知勞動 | `AGENTS.md` 的信條 + `prompts/` 的任務模板 |
| 確定性工具 | 結構與紀律 | `tools/vault.py`、`loop/daily.sh`、git |

原則：**LLM 永遠不做程式能做的事**。掃描、驗收、儀表板、排程、commit 都是普通程式碼 —
可測試、可重跑、不燒 token、不會幻覺。LLM 只負責研究與寫作，且輸出必須通過 lint。

## 每日迴圈

```
preflight   git pull → extract.py（Raw/ 的 PDF／掃描件／docx → loop/state/extracted/）
              → vault.py scan → loop/state/scan.json
agent       tools/render.py 把 prompts/daily.md 展開成純文字（!`shell` 插值、
              @file 引用），再交給 KM_AGENT_CMD 指定的 CLI（預設 opencode run）
postflight  vault.py stats → vault.py lint
              └─ lint 失敗 → 一次 garden 修復 pass → 再 lint（仍失敗就照樣 commit，
                 讓人類在 diff 裡看到問題 — 絕不弄丟工作）
commit      git add vault && commit "wiki(daily): DATE — <每日報告第一句>" → push（退避重試）
```

**冪等**：迴圈不依賴模型記憶與上次執行狀態；今天要做什麼完全由 scan 的現況決定。
任何一圈失敗，明天重跑即可。lockfile（flock）防重入；`timeout` 防吊死。

## 工作來源與優先序

1. `loop/local/*.md` 本機私有指令（gitignored；與 Inbox 同級）
2. `Inbox.md` 未勾選項 — 人類明示意圖
3. `pending_raw` — 人類丟檔案 = 強意圖；消化契約：**存在一篇 `Sources/` 筆記 wikilink 引用該檔** 才算完成
4. 知識前緣 — dangling links 依 inbound 次數
5. 到期複習（`review_after` ≤ today）— 間隔 7 → 21 → 60 天遞推
6. 停滯 seed（14 天未動）

預算 `KM_MAX_ITEMS`（預設 3）封頂成本；沒做完的寫進每日報告「明日候選」。

## scan JSON 合約（節錄）

```json
{
  "generated": "2026-08-14",
  "totals": {"notes": 0, "graph_notes": 0, "by_status": {}, "dangling_links": 0,
              "orphans": 0, "due_reviews": 0, "inbox_open": 0, "pending_raw": 0},
  "inbox": ["未勾選項目文字"],
  "pending_raw": ["Raw/檔名"],
  "frontier": [{"target": "頁名", "referenced_by": ["Notes/A.md"], "inbound": 2}],
  "stubs": [], "stale_seeds": [], "orphans": [],
  "due_reviews": [{"note": "Notes/X.md", "review_after": "2026-08-10"}]
}
```

## 關鍵決策記錄

- **dangling link 是 feature 不是 error** — lint 不擋它；它是生長機制。防氾濫的是「引用即背書」的寫作規範＋預算。
- **stats 區塊的連結不算圖的邊** — `Home.md` 儀表板由 `stats` 生成、含 wikilink，掃描時剝除
  （否則儀表板會把自己列的前緣「餵」回下一次掃描，數字自我膨脹 — 已用測試釘住）。
- **`_meta/` 可被連結、不參與健檢**；`_meta/Templates/` 連當連結目標都不算（模板裡是假連結）。
- **`Raw/` 檔案是合法連結目標**（`[[paper.pdf]]`、`[[剪藏標題]]`），但不是筆記；agent 只讀不寫。
- **frontmatter 用 stdlib 自寫的 YAML 子集解析**（scalar／inline list／block list）— 換取零依賴；
  Style Guide 明定只用這個子集。
- **git 只屬於外骨骼** — commit/push 只在 `daily.sh` 裡發生，prompt 三處明令 agent 不得執行；
  硬性權限（deny git 寫入）放使用者的全域 agent 設定，repo 不帶任何供應商設定檔。
- **不綁 agent 供應商** — 專案層級的 opencode 設定會讓它在 repo 裡 bootstrap 相依沙箱
  （`node_modules/`），而且 schema 隨版本變動、舊版直接開不起來。改成「純文字 prompt +
  可替換執行器」後，這兩個問題一起消失，也換得 `claude -p` 之類的可攜性。
- **素材轉檔屬於工具層** — 要求人類先把 PDF 轉成文字是把工具的限制轉嫁給使用者；
  extract 在 preflight 自動處理，掃描件自動 OCR，缺工具時明確報告而非默默略過。
- **模型不寫死** — `KM_MODEL` / opencode 設定決定；repo 不綁供應商。
- **語言是一行設定** — 預設繁中在 `AGENTS.md`，不散落各處。

## 失敗模式與對策

| 失敗 | 對策 |
|---|---|
| agent 寫出壞 frontmatter／亂連結 | lint 抓到 → garden 修復 pass → 仍失敗照 commit，人類看 diff |
| agent 跑到一半死掉 | timeout + 「WARN 繼續」；postflight 照樣驗收殘局；明天重來 |
| 兩圈重疊（cron + 手動） | flock 直接退出第二圈 |
| 網路斷 | pull/push 失敗只 WARN，工作先 commit 在本地 |
| 複習進度被清掉 | 鐵律：不動 `<!--SR:...-->`（Style Guide + 三個 command 都寫明） |
| 成本失控 | 每圈預算 + 一天一圈 + timeout |

## 未來方向（刻意不做在 v1）

- `quartz` 發佈層：把 vault 出版成 digital garden 網站
- 週報 command（`weekly.md`）：彙整一週學習、重排 Maps
- frontier 打分升級：除 inbound 外納入「與 Inbox 主題的相關度」
- 多 vault／多語言 profile
