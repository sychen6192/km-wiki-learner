# 本機補充指令（範例）

`loop/local/` 底下的所有 `.md` 會在每天的 `daily` 執行時注入 prompt，
優先級等同 Inbox — 但**整個資料夾不進 git**（見 `.gitignore`），
適合放只屬於這台機器的來源、憑證範圍與偏好。

啟用：

```bash
mkdir -p loop/local
cp loop/local.example.md loop/local/daily.md   # 然後改成你自己的
```

---

## 範例一：偏好覆寫

```markdown
- 本週特別關注「分散式系統」，相關的前緣頁面優先於其他主題。
- 筆記語言改用英文。
- 每篇筆記都要附一個可執行的最小範例。
```

## 範例二：每天從外部來源抓素材（MCP）

完整說明見 [`docs/SOURCES.md`](../docs/SOURCES.md)。六個要素缺一不可：
**用哪個工具、從哪裡抓、從哪裡開始、抓多少、產出什麼、失敗怎麼辦**。

```markdown
## 每日外部素材

1. 用 `<工具全名>` 從 `<明確範圍：space / 路徑 / 專案 ID>` 開始，
   取出晚於游標 `<游標名稱>` 的項目，最多 `<N>` 筆；沒有新項目就跳過本節。
2. 每筆用 `<取全文的工具全名>` 取內容，寫一篇 `Sources/` 摘要：
   frontmatter `sources` 放連結，內文註明來源 ID 與更新日期。
3. 把觀念織進既有概念筆記；值得成頁的新概念留成 dangling link。
4. 處理完推進游標（取這批裡最新的位置，不是第一筆）：
   `python3 tools/vault.py cursor set <游標名稱> <新位置> --note "<最後處理的項目>"`
5. 來源連不上或無權限：在今天的每日報告註明並跳過，**不要臆造內容，也不要動游標**。
```

設定起點（決定「從哪裡抓起」）：

```bash
python3 tools/vault.py cursor set <游標名稱> <起點> --note "初始起點"
python3 tools/vault.py cursor list
```

工具全名不要用猜的，查法：

```bash
opencode run "列出你目前可用的工具名稱，一行一個，只輸出名稱本身"
```
