---
description: 補齊複習卡 — 為最近變動的筆記產出缺少的 flashcards
agent: librarian
---

最近 7 天有變動的筆記：

```
!`git log --since="7 days ago" --name-only --pretty=format: -- vault/Notes vault/Sources vault/Maps | sort -u | grep -v '^$' || echo "(無)"`
```

1. 逐篇檢查 `vault/Review/Flashcards.md` 是否已有對應卡片；缺的補上 1–2 張（`問題::答案`，考理解不考背誦，問題帶 `[[筆記]]` 連結，放進對應主題小節）。
2. 既有卡片一律不改寫、不刪除，**尤其不動 `<!--SR:...-->` 註解**。
3. 完成後 `python3 tools/vault.py lint` 確認 0 error。不要執行 git commit／push。
