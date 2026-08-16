今天是 !`date +%F`。主編指定現在深入學習：**$ARGUMENTS**

先讀 `AGENTS.md` 與 `vault/_meta/Style Guide.md`。

## Vault 現況（避免重複造頁）

```json
!`python3 tools/vault.py scan`
```

## 要做的事

1. 深入研究這個主題：定義、核心機制、常見誤解、與既有筆記的關係。優先一手來源，
   重要論斷交叉驗證；查不到的明說「不確定」。
2. 產出（依 Style Guide）：
   - 一篇核心概念筆記（`vault/Notes/`，budding 以上品質；已存在同名筆記就深化它）。
   - 研究中用到的重要來源，各寫一篇 `vault/Sources/` 摘要並互相連結。
   - 收進最合適的 Map（沒有就開一張）；值得成頁的相關概念留成 dangling link，
     交給之後的迴圈長出來。
   - 對應的 `vault/Review/` 複習檔補 1–3 張卡；筆記設 `review_after`（+7 天）。
3. 在 `vault/Daily/!`date +%F`.md` 追加一節「隨選學習」記錄成果（檔案不存在就建立）。
4. 跑 `python3 tools/vault.py lint` 修到 0 error。**不要 git commit 或 push。**
