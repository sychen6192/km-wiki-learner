---
status: budding
created: 2026-08-16
updated: 2026-08-16
tags: [LLM, inference, optimization]
aliases: [KV 快取, Key-Value Cache]
sources:
  - "（未經查證）本頁由無網路能力的本機模型寫成，原先列的網址它並未開啟過，已移除。內容待人工或有網路的 agent 覆核。"
review_after: 2026-09-06
---

**KV cache** 是解碼（decoding）階段加速 Transformer 推理的關鍵機制，透過儲存先前 token 的注意力鍵（Key）與值（Value）張量，避免在 autoregressive 生成時重複計算。

在不使用 KV cache 的情況下，模型每次預測下一個 token 必須重新掃描整個歷史序列，使自注意力運算複雜度隨序列長度呈平方增長；引入 KV cache 後，已計算過的 Key/Value 被暫存於顯存，當前步驟僅需對新 token 進行投影並拼接，將每步生成延遲從 $O(N^2)$ 降至接近 $O(1)$。此機制以空間換取時間，是現代 LLM 達成分數推理（streaming inference）與高吞吐量的基石。

其代價在於序列增長時記憶體佔用呈線性擴張。長上下文（如 128k+ token）或高併發請求容易觸發 VRAM OOM；實務上通常搭配 [[paged attention]]、[[KV quantization]] 或 [[sliding window]] 進行壓縮與分頁管理。需留意 KV cache 僅適用於推理階段，訓練時因需保留完整梯度路徑且支援 token 級并行，一律不使用快取。
