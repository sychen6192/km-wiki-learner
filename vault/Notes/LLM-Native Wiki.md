---
status: evergreen
created: 2026-08-14
updated: 2026-08-14
tags: [concept, llm, knowledge-management]
aliases: [llm-wiki]
---

# LLM-Native Wiki

**LLM-native wiki** 指的是一種反轉傳統分工的知識庫：LLM 負責撰寫與維護內容，人類負責出題與品味把關。[[Andrej Karpathy]] 多次描繪過這個方向 — 與其把 LLM 當成查資料的聊天視窗，不如讓它替你生成一座為你量身打造、持續更新的百科。

傳統 wiki（Wikipedia、公司 Confluence）的瓶頸從來不是儲存，而是**寫作與維護的人力**：頁面過時、連結斷裂、沒人想寫存根頁。LLM 恰好把這三件事的邊際成本壓到趨近於零，於是設計重心從「怎麼說服人來寫」變成「**怎麼引導機器寫得對**」：

- **人類掌舵**：從 [[Inbox]] 丟入主題與問題，決定維基往哪裡長。
- **結構交給確定性工具**：連結健檢、孤兒偵測、儀表板由程式碼維護，LLM 的輸出必須通過 lint 才算數（見 [[Agent Harness]]）。
- **生長交給迴圈**：dangling link 就是維基的好奇心，[[Loop Engineering]] 讓它每天沿著知識前緣長一圈。
- **內容以 [[Evergreen Notes]] 為目標形態**，並用 [[Spaced Repetition]] 讓人腦跟上機器的產出。

一句話：**wiki 是活的，editor 是機器，主編是你。**

## See also

- [[Digital Garden]] — 人類手工時代的同型嘗試
- [[Map of Content]] — 這座維基的導覽層
