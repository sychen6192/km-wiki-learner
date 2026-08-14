# 先行者研究（2026-08-14 快照）

開工前的盡職調查：Karpathy 原始想法的一手出處、既有高星實作各自做到哪裡、
我們選擇站上去的缺口。星數為 GitHub API 當日值；部分站點被研究環境擋掉的，
引文來自搜尋摘要（已標註）。

## Karpathy 的原始想法（一手來源）

- **LLM Knowledge Bases**（2026-04-02，[tweet](https://x.com/karpathy/status/2039805659525644595)）：
  「using LLMs to build personal knowledge bases for various topics of research interest…
  the LLM has been pretty good about auto-maintaining index files and brief summaries…
  I like to have it render markdown files for me… all of which I then view again in **Obsidian**.」
- **llm-wiki idea file**（2026-04-04，[gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)，~5k stars）：
  - 三層：`raw/`（「immutable — the LLM reads from them but never modifies them」）→
    `wiki/`（「The LLM owns this layer entirely」）→ schema（規範與工作流）。
  - 三操作：**ingest**（一份來源可能觸及 10–15 頁）、**query**（帶引用回答）、
    **lint**（「contradictions between pages, stale claims, orphan pages with no inbound links」）。
  - 特殊檔：`index.md`（目錄）+ `log.md`（append-only 時間軸）。
  - 「You never (or rarely) write the wiki yourself — the LLM writes and maintains all of it.」
  - 「This is an idea file, it is designed to be copy pasted to your own LLM Agent
    (e.g. OpenAI Codex, Claude Code, OpenCode / Pi, or etc.)」
  - **關鍵：通篇沒有排程／自動迴圈 — 所有操作由人類發起。**（本專案的切入點）
- **Farzapedia 讚語**（2026-04-04，[tweet](https://x.com/karpathy/status/2040572272944324650)）：
  好記憶的三美德 — 明確（看得到 AI 知道什麼）、可攜（檔案在你機器上）、"file over app"。
- 相關脈絡：[reading with LLMs 習慣](https://x.com/karpathy/status/1990577951671509438)（2025-11）、
  [llm-council](https://github.com/karpathy/llm-council)（24.0k⭐）、
  [nanochat](https://github.com/karpathy/nanochat)（57.2k⭐，"ramp to knowledge" 學習哲學）、
  [學習不該被短影音化](https://x.com/karpathy/status/1756380066580455557)（"the primary feeling should be that of effort"）。

## 高星實作對照

| 專案 | ⭐ | 型態 | 強項 | 相對本專案的缺口 |
|---|---|---|---|---|
| [nashsu/llm_wiki](https://github.com/nashsu/llm_wiki) | 16,333 | 桌面 App | GUI、多供應商、vault 相容 Obsidian、來源資料夾監看 | FS 觸發非排程；沒新檔就不長 |
| [SamurAIGPT/llm-wiki-agent](https://github.com/SamurAIGPT/llm-wiki-agent) | 3,363 | agent skill | entity/concept 頁、矛盾標記、`[[wikilinks]]` | README 明言無 scheduling/cron |
| [kytmanov/obsidian-llm-wiki-local](https://github.com/kytmanov/obsidian-llm-wiki-local) | 802 | 本地 Ollama 管線 | 100% 本地、frontmatter status/confidence | `olw run` 手動或 FS watch；無排程；~100 來源上限 |
| [iBlinkQ/llm-wiki-obsidian-blink](https://github.com/iBlinkQ/llm-wiki-obsidian-blink) | 615 | vault 模板 | 現成 raw/wiki/TheSchema/index/log 結構 | 手動 ingest/query/lint |
| [garrytan/gbrain](https://github.com/garrytan/gbrain) | 28,415 | agent 記憶基建 | **真 24/7 cron 迴圈**（夜間 enrich/dedupe）、wikilinks | 定位是記憶／CRM（人脈公司），非學習維基；重基建 |
| [khoj-ai/khoj](https://github.com/khoj-ai/khoj) | 36,490 | AI 第二大腦 | Obsidian 外掛 + **排程 automations** | automation 寄 newsletter/通知，不回寫 vault |
| [AsyncFuncAI/deepwiki-open](https://github.com/AsyncFuncAI/deepwiki-open) | 17,648 | repo→wiki | 一鍵生 codebase 文件+圖 | 對象是程式碼庫；generate-then-ask |
| [danielmiessler/Fabric](https://github.com/danielmiessler/Fabric) | 43,436 | prompt 模式庫 | extract_wisdom 等管線元件 | 無持久 wiki、無狀態、無迴圈 |
| [logancyang/obsidian-copilot](https://github.com/logancyang/obsidian-copilot) | 7,559 | Obsidian 內 chat | 最成熟的 in-app AI UX | 人主動問；不自主維護筆記 |
| [reorproject/reor](https://github.com/reorproject/reor) | 8,572 | 本地 AI PKM | 早期願景 | **已 archived** |

工具鏈借力（非競品）：[quartz](https://github.com/jackyzha0/quartz)（13.0k，之後可做發佈層）、
[obsidian-spaced-repetition](https://github.com/st3v3nmw/obsidian-spaced-repetition)（2.5k，我們的卡片消費端）、
[obsidian-git](https://github.com/Vinzent03/obsidian-git)（11.8k，人類端同步；行動端不穩，故 git 主責放在迴圈側）、
[obsidian-smart-connections](https://github.com/brianpetro/obsidian-smart-connections)（5.4k，語意連結建議）。

**結論**：截至調查日，「以 Obsidian vault 為家、每天自主生長、帶複習排程與成熟度晉升的學習維基」
沒有現成占位者 — gbrain 與 khoj 是最接近的鄰居，各缺一半。此即本專案定位。

## 採用的社群慣例

- **Evergreen notes**（[Andy Matuschak](https://notes.andymatuschak.org/Evergreen_notes)）：原子、概念導向、稠密連結、關聯優於階層。
- **成熟度**（[Maggie Appleton](https://maggieappleton.com/garden-history)）：seedling→budding→evergreen；本專案取 `seed/budding/evergreen`。
- **MOC**（Nick Milo LYT）：主題導覽頁 + Home 進入點；重疊、非互斥。
- **spaced-repetition 卡片語法**（插件 README 驗證）：單行 `問題::答案`、反向 `:::`、多行以獨行 `?` 分隔、
  牌組 `#flashcards/子牌組`；插件寫入的 `<!--SR:!YYYY-MM-DD,interval,ease-->` 排程註解**絕不可改動**。
- **frontmatter**：官方保留鍵為複數 `tags`/`aliases`/`cssclasses`；自訂鍵（`status` 等）為社群慣例。

## 可信度備註

多數非 GitHub 網站被研究環境的 egress proxy 擋掉，該部分引文取自搜尋摘要（截斷處以 … 標示）；
星數為 API 精確值，但 2026 年病毒式增長的 repo 建議只取相對量級。未能獨立驗證：
Factory AutoWiki 細節、tweet 精確瀏覽數、Farzapedia tweet 全文後半。
