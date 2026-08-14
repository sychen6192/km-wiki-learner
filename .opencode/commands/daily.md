---
description: 每日迴圈 — 消化 Inbox/Raw、沿知識前緣生長、複習到期筆記、寫每日報告
agent: librarian
---

今天是 !`date +%F`。執行 km-wiki 每日迴圈。本次預算：最多 **!`echo "${KM_MAX_ITEMS:-3}"` 個工作項**（一篇新筆記、一次 Raw 消化、一次複習各算一項；相關的 Map 更新與複習卡不另計）。

## Vault 掃描結果（scan JSON）

```json
!`python3 tools/vault.py scan`
```

## Inbox 現況

@vault/Inbox.md

## 本機補充指令（若空白則略過）

!`cat loop/local/*.md 2>/dev/null || true`

## 執行步驟

1. **計畫**：依優先序（本機補充指令＝Inbox > Raw 待消化 > 知識前緣（inbound 高者先）> 到期複習 > 停滯 seed）從上面的資料挑出預算內的工作項。先讀相關的既有筆記與 Maps 再動工。
2. **研究**：需要外部資訊時上網查證；大量閱讀交給 @scholar 子代理，拿回帶來源的摘要再落筆。
3. **寫作**：
   - 新概念 → `Notes/`（可先 `python3 tools/vault.py seed "Title"` 再充實），依 Style Guide：frontmatter 齊全、≥2 出鏈、收進一張 Map、內文繁中。
   - Raw 素材 → 一篇 `Sources/` 摘要（模板見 `vault/_meta/Templates/Source.md`），以 wikilink 引用原始檔，並把觀念織進相關概念筆記。原始檔絕不修改。
   - 到期複習 → 查證內容是否仍正確、有無新發展；更新 `updated`、視品質調整 `status`、`review_after` 依 7 → 21 → 60 天遞推。
4. **收尾簿記**：
   - 完成的 Inbox 項目勾掉並在行尾加上 `→ [[筆記]]`。
   - 每篇新／大改筆記在 `vault/Review/Flashcards.md` 對應小節加 1–2 張卡（`問題::答案`；不動任何 `<!--SR:...-->` 註解）。
   - 寫 `vault/Daily/!`date +%F`.md`（模板：`vault/_meta/Templates/Daily Report.md`）：完成了什麼、決策理由、明日候選。
5. **自我驗收**：跑 `python3 tools/vault.py lint`，修到 0 error。不要執行 git commit／push，外層腳本會處理。

預算用完就停，剩下的想法寫進每日報告的「明日候選」。
