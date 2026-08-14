# 🃏 Flashcards

#flashcards/km-wiki

> 相容 [obsidian-spaced-repetition](https://github.com/st3v3nmw/obsidian-spaced-repetition) 外掛：單行卡用 `問題::答案`，安裝外掛後按 🧠 圖示開始複習。
> 迴圈每天為新筆記追加卡片（附上來源筆記連結），依主題分節。

## Knowledge Engineering

[[LLM-Native Wiki]] 的核心分工是什麼？::LLM 寫內容、確定性工具管結構（lint／儀表板）、人類從 Inbox 掌舵。

[[Evergreen Notes]] 的四個特徵？::原子性、概念導向、稠密連結、持續改寫（seed → budding → evergreen）。

[[Loop Engineering]] 為什麼要求迴圈冪等？::每天的工作完全由 scan 的狀態決定，任何一圈失敗隔天重跑即可，不依賴模型記憶。

dangling link（知識前緣）在本維基扮演什麼角色？::它是維基的好奇心佇列 — 已被引用但還不存在的頁面，就是迴圈明天優先生長的方向。

[[Map of Content]] 對 LLM agent 的隱藏功能？::注意力路標 — agent 讀地圖就能掌握全庫結構，token 成本從 O(全庫) 降到 O(地圖)。

[[Zettelkasten]] 給本維基的核心原則？::連結即思考 — 寫下兩個概念的連結會逼你想清楚關係，所以 lint 要求每篇筆記至少兩條出鏈。
