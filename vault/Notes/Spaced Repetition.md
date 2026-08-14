---
status: budding
created: 2026-08-14
updated: 2026-08-14
tags: [concept, learning]
review_after: 2026-08-21
---

# Spaced Repetition

**間隔重複**：在你即將遺忘的時間點安排複習，用最少的複習次數對抗遺忘曲線（Ebbinghaus）。Anki、SuperMemo 是代表性實作；研究上這是效果最穩固的學習技術之一。

在這座 [[LLM-Native Wiki]] 裡，間隔重複以兩種形式存在：

1. **卡片層** — 迴圈為每篇新筆記產出問答卡，寫進 [[Flashcards]]，格式相容 obsidian-spaced-repetition 外掛（`問題::答案`），你在 Obsidian 裡就能刷卡。
2. **筆記層** — frontmatter 的 `review_after` 欄位是筆記自己的複習日。到期的筆記會出現在 `scan` 結果與 [[Home]] 儀表板，迴圈會回頭檢視它：內容還對嗎？有沒有新發展？該不該升級 `status`？複習完把日期往後推（間隔遞增：7 → 21 → 60 天）。

這是人機分工的關鍵一環：機器可以無限產出，但**你的腦袋才是最終要部署知識的生產環境** — 沒有複習排程的知識庫只是一座漂亮的倉庫。

## See also

- [[Evergreen Notes]] — 複習的對象
- [[Loop Engineering]] — 複習排程如何成為迴圈的工作來源
