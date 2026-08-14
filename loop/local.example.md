# 本機補充指令（範例）

`loop/local/` 底下的所有 `.md` 會在每天的 `/daily` 執行時注入 prompt，
優先級等同 Inbox — 但**整個資料夾不進 git**（見 `.gitignore`），
適合放只屬於這台機器的來源與偏好。

啟用方式：

```bash
mkdir -p loop/local
cp loop/local.example.md loop/local/daily.md   # 然後編輯它
```

範例內容（自行改寫）：

- 用 opencode 全域設定裡掛載的 MCP 工具檢查有沒有新素材（RSS、書籤、
  任何你接上的資料來源）；值得留的寫成 `Sources/` 摘要並織進概念筆記。
- 本週特別關注：____（主題），相關內容優先於一般前緣。
- 筆記語言改為：____（不填則依 AGENTS.md 預設）。
