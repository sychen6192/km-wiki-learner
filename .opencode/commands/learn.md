---
description: 立刻深入研究一個主題並寫成筆記（用法：opencode run --command learn "主題或問題"）
agent: librarian
---

主編指定現在深入學習：**$ARGUMENTS**

今天是 !`date +%F`。

1. 先掃一眼現況，避免重複造頁：

```json
!`python3 tools/vault.py scan`
```

2. 把主題交給 @scholar 深入研究（定義、重點、相關概念、來源、信心缺口）。
3. 依 Style Guide 產出：
   - 一篇核心概念筆記（`Notes/`，budding 以上品質；若已存在同名筆記則深化它）。
   - 研究中出現的重要一手來源，各寫一篇 `Sources/` 摘要並互相連結。
   - 收進最合適的 Map（必要時開新 Map）；值得成頁的相關概念留成 dangling link 給日後的迴圈。
   - `vault/Review/Flashcards.md` 加 1–3 張複習卡；筆記設 `review_after`（+7 天）。
4. 在今天的 `vault/Daily/!`date +%F`.md` 追加一節「隨選學習」記錄成果（檔案不存在就建立）。
5. `python3 tools/vault.py lint` 修到 0 error。不要執行 git commit／push。
