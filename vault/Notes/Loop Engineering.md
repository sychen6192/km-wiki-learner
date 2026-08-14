---
status: evergreen
created: 2026-08-14
updated: 2026-08-14
tags: [concept, agents, automation]
---

# Loop Engineering

**Loop engineering** 是把 LLM agent 從「一次性對話」升級成「**每天自動運轉的系統**」的工程方法。單次 prompt 的品質天花板取決於模型；但一個設計良好的迴圈，品質會隨天數複利成長 — 因為每一圈的輸出（筆記、連結、狀態檔）都是下一圈的輸入。

本維基的日更迴圈是教科書式範例：

```
scan → plan → research → write → link → review → report → commit
```

可靠迴圈的四條設計原則：

1. **確定性外骨骼**：排程（cron）、掃描（`vault.py scan`）、驗收（`vault.py lint`）、提交（git）都是普通程式碼；LLM 只負責中段的認知工作。這就是 [[Agent Harness]] 的分層。
2. **狀態外置**：迴圈不依賴模型記憶。今天要做什麼，完全由 `scan` 產出的 JSON（[[Inbox]] 待辦、dangling links、到期複習）決定 — 任何一圈失敗，明天重跑即可，**冪等**。
3. **預算封頂**：每圈最多處理 N 個工作項，成本可預測，避免 agent 一夜寫出三百頁垃圾。
4. **留痕**：每圈寫一篇 `Daily/` 報告 + 一個 git commit，人類用 diff 監督機器。

和 [[Spaced Repetition]] 相扣的巧思：複習排程本身就是迴圈的工作來源之一，維基因此「學新的」與「顧舊的」並行。

## See also

- [[LLM-Native Wiki]] — 這個迴圈存在的目的
- [[Evergreen Notes]] — 迴圈每天產出的最小單位
