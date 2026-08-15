# 🏛️ km-wiki

一座**自己會長大的個人維基**。每天清晨，一個 LLM 圖書館員（經由 [opencode](https://opencode.ai) 執行）掃描這座 vault：讀取你丟進 [[Inbox]] 的主題、沿著「已被引用但還不存在」的連結探索知識前緣、複習到期的筆記，然後把學到的東西寫成互相連結的 evergreen 筆記。

你負責掌舵，維基負責生長。

## 導覽

- 📥 [[Inbox]] — 把想學的主題、問題、連結丟進來（手機上用 Obsidian 編輯即可）
- 📦 `Raw/` — 丟網頁剪藏、論文、檔案進來，迴圈會消化成 `Sources/` 摘要（說明見 `Raw/_about.md`）
- 🗾 [[日本語 N4]] — JLPT N4 學習地圖（文法、單字、課程講義）
- 🗺️ [[Knowledge Engineering]] — 這座維基自身的方法論
- 🃏 複習卡：[[日本語 N4 複習]] · [[Flashcards]]（相容 obsidian-spaced-repetition）
- 📓 每日報告在 `Daily/`，維基每天學了什麼、為什麼，全部留痕

<!-- km:stats:start -->
> [!info] 圖書館現況 · 2026-08-15
> 筆記 **9** 篇 — 🌱 seed 0 · 🌿 budding 5 · 🌳 evergreen 4
> 待長出的連結（frontier）**23** · 到期複習 **0** · Inbox 待辦 **5** · Raw 待消化 **0**

**知識前緣** — 已被引用但還不存在的頁面（明日候選）：
- [[Digital Garden]] ×5
- [[Agent Harness]] ×4
- [[Andrej Karpathy]] ×3
- [[たほうがいい]] ×3
- [[てはいけない]] ×3
- [[てもいい]] ×2
- [[て形]] ×2
- [[ない形]] ×2
- [[N4 漢字]] ×1
- [[あげる もらう くれる]] ×1

**最近的每日報告**：[[2026-08-14]]

<!-- km:stats:end -->

## 運作方式

```
你（Inbox / Obsidian）─┐
                        ├─▶ 每日迴圈：scan → plan → research → write → link → review → report
知識前緣（dangling links）┘        （opencode run --command daily，由 cron / GitHub Actions 觸發）
```

結構由 `tools/vault.py` 以確定性程式維護（連結健檢、儀表板、種子筆記）；內容由 LLM 撰寫、由 lint 把關。詳細規範見 [[Style Guide]]。
