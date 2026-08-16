今天是 !`date +%F`。執行 km-wiki 每日迴圈。

先讀 `AGENTS.md` 與 `vault/_meta/Style Guide.md`，那是這座 vault 的規範。
本次預算：最多 **!`echo "${KM_MAX_ITEMS:-3}"` 個工作項**（一篇新筆記、一份素材消化、一次複習各算一項；
順帶的 Map 更新與複習卡不另計）。預算用完就停，剩下的寫進報告的「明日候選」。

## Vault 現況（scan）

```json
!`"$KM_PYTHON" tools/vault.py scan`
```

## Inbox（人類出的題）

@vault/Inbox.md

## Raw 素材（已自動轉成文字，直接讀對應路徑即可）

```
!`"$KM_PYTHON" tools/extract.py --list`
```

## 本機補充指令（空白就略過）

!`cat loop/local/*.md 2>/dev/null || true`

## 執行步驟

1. **計畫** — 依優先序挑出預算內的工作項：
   本機補充指令＝Inbox ＞ Raw 待消化 ＞ 知識前緣（inbound 高者先）＞ 到期複習 ＞ 停滯 seed。
   動工前先讀相關的既有筆記與 Maps，新內容要接上既有的圖。
2. **研究** — 需要外部資訊就上網查證，優先一手來源。查不到就明說「不確定」，**絕不編造**。
3. **寫作**
   - 新概念 → `vault/Notes/`（可先 `"$KM_PYTHON" tools/vault.py seed "Title"` 再充實）。
   - Raw 素材 → 一篇 `vault/Sources/` 摘要（模板 `vault/_meta/Templates/Source.md`），
     以 wikilink 指回原始檔，並把內容裡的概念各自織進或長成概念筆記。**原始檔絕不修改**。
     素材若屬於一個還沒有 Map 的領域，就開一張新 Map 收納它。
   - 到期複習 → 查證是否仍正確、有無新發展；更新 `updated`、視品質調整 `status`、
     `review_after` 依 7 → 21 → 60 天遞推。
4. **收尾**
   - 完成的 Inbox 項目勾掉並在行尾加上 `→ [[筆記]]`。
   - 每篇新增或大改的筆記，在對應的 `vault/Review/` 複習檔補 1–2 張卡。
     **不要動任何 `<!--SR:...-->` 註解**，那是複習排程。
   - 寫 `vault/Daily/!`date +%F`.md`（模板 `vault/_meta/Templates/Daily Report.md`）：
     做了什麼、為什麼這樣選、明日候選。
5. **自我驗收** — 跑 `"$KM_PYTHON" tools/vault.py lint`，修到 0 error。
   **不要執行 git commit 或 push**，外層腳本會處理。
