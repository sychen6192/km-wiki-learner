---
status: budding
created: 2026-08-14
updated: 2026-08-14
tags: [concept, note-taking]
aliases: [MOC]
---

# Map of Content

**Map of Content（MOC）** 是 Obsidian 社群（Nick Milo 的 Linking Your Thinking）發展出的組織模式：不用資料夾階層硬性分類，而是用「**一頁充滿連結的導覽筆記**」替一個主題畫地圖。同一篇筆記可以被多張地圖引用 — 分類是視角，不是位置。

本維基的規則：

- `Maps/` 底下一個領域一張地圖，例如 [[Knowledge Engineering]]。
- 每篇 `Notes/` 至少被一張 Map 收錄，否則遲早變成孤兒（`vault.py scan` 會抓出來）。
- Map 除了羅列，還負責標出「**待探索**」區 — 也就是知識前緣，讓 [[Loop Engineering]] 的迴圈知道下一步往哪長。
- 當一張 Map 超過大約三十條連結，就該分裂出子地圖。

對 [[LLM-Native Wiki]] 而言，MOC 還有一個隱藏功能：它是 LLM 的**注意力路標**。Agent 每天不必重讀整座 vault，讀地圖就能掌握結構，token 成本從 O(全庫) 降到 O(地圖)。

## See also

- [[Evergreen Notes]] — 地圖上的節點
- [[Zettelkasten]] — 另一支「連結優先於分類」的傳統
