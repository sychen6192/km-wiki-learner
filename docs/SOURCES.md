# 接上外部素材來源（MCP）

迴圈預設的素材來自 `Inbox.md`、`Raw/` 與知識前緣。若你有外部系統 —— 內部文件庫、
issue tracker、RSS、書籤、任何 MCP server —— 可以讓迴圈每天自己去抓。

本文件是**通用作法**。你自己的來源設定（server 位址、token、要抓哪個範圍）
應該放在**不進版本控制**的兩個地方：

| 放哪裡 | 放什麼 | 進 git 嗎 |
|---|---|---|
| `~/.config/opencode/opencode.json`（全域） | MCP server 掛載、憑證、權限 | ❌ 不在 repo 內 |
| `loop/local/*.md`（本機） | 抓取指令：範圍、產出契約、頻率 | ❌ `.gitignore` 已排除 |
| `loop/state/cursors.json`（本機） | 抓到哪裡了（游標） | ❌ `.gitignore` 已排除 |

專案的 `opencode.json` 完全不需要知道你接了什麼來源。

## 1. 掛載 MCP server（全域設定）

編輯 `~/.config/opencode/opencode.json`：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "acme-docs": {
      "type": "local",
      "command": ["npx", "-y", "acme-docs-mcp"],
      "environment": { "ACME_TOKEN": "{env:ACME_TOKEN}" },
      "enabled": true
    }
  },
  "permission": {
    "acme-docs*": "allow"
  }
}
```

遠端 server 用 `"type": "remote"` 搭配 `"url"` 與 `"headers"`。
`{env:VAR}` 會在載入時替換，所以 token 留在環境變數而不是設定檔裡。

**權限那一行是必要的**：迴圈跑 `opencode run --auto`，`--auto` 只自動核可
「沒有被明確 deny」的權限；把該來源的工具設成 `allow` 才不會每天卡在等待核可。
權限鍵支援萬用字元，通常是 `<server 名稱>*`。

## 2. 查出**精確的工具名稱**（不要猜）

指令寫得精準的前提是知道工具真名。最可靠的查法是直接問跑起來的 agent：

```bash
opencode run "列出你目前可用的工具名稱，一行一個，只輸出名稱本身，不要任何說明"
```

把輸出裡屬於該 server 的名稱抄下來（通常長得像 `acme-docs_search`、
`acme-docs_get_document`）。之後在指令裡直接寫全名，agent 就不必猜。

## 3. 寫抓取指令（`loop/local/*.md`）

```bash
mkdir -p loop/local
cp loop/local.example.md loop/local/daily.md
```

一份好的抓取指令要回答六個問題。缺任何一項，agent 就會自由發揮：

| 要素 | 為什麼 | 寫法 |
|---|---|---|
| **用哪個工具** | 避免它挑錯或亂試 | 寫工具全名 |
| **從哪裡抓**（範圍） | 界定 space / 專案 / 資料夾 | 寫明確 ID 或路徑，不要寫「相關的文件」 |
| **從哪裡開始**（游標） | 每天增量、不重工 | 見下一節 |
| **抓多少** | 成本與品質上限 | 「最多 N 筆」 |
| **產出什麼** | 否則它只會摘要給你看就結束 | 「每筆寫一篇 `Sources/` 筆記，記錄來源 ID，並織進概念筆記」 |
| **失敗怎麼辦** | 防幻覺 | 「連不上就在報告註明並跳過，不要臆造」 |

範例骨架：

```markdown
## 每日外部素材

1. 用 `acme-docs_search` 從 space `ENG` 底下、路徑 `/architecture` 開始，
   取出更新時間晚於游標 `acme` 的文件，最多 5 筆（沒有就跳過本節）。
2. 每筆用 `acme-docs_get_document` 取全文，寫一篇 `Sources/` 摘要：
   標題用原文件標題，frontmatter `sources` 放文件連結，內文註明文件 ID 與更新日期。
3. 把觀念織進既有概念筆記；值得成頁的新概念留成 dangling link 給後續迴圈。
4. 全部處理完，用最新一筆的更新時間推進游標：
   `python3 tools/vault.py cursor set acme <ISO 時間> --note "<最後一份文件 ID>"`
5. 來源連不上或沒有權限：在今天的每日報告註明，跳過，不要動游標，不要臆造內容。
```

## 4. 游標 —— 「從哪裡抓起」

游標是每個來源的水位線，存在 `loop/state/cursors.json`（本機、不進 git）。
每天的 prompt 會自動注入 `cursor list` 的結果，agent 因此知道要從哪裡續抓。

```bash
# 設定起點（第一次執行前，決定「從哪裡抓起」）
python3 tools/vault.py cursor set acme 2026-08-01T00:00:00Z --note "初始起點"

# 看目前所有來源的水位
python3 tools/vault.py cursor list

# 讀單一游標（未設定時回傳 --default 的值）
python3 tools/vault.py cursor get acme --default 2026-01-01T00:00:00Z
```

值是**不透明字串**：ISO 時間、文件 ID、page token 都可以 —— 只要你的指令和
來源系統對得起來即可。agent 有權限執行 `python3 tools/vault.py*`，所以它自己
就能推進游標；`loop/local/` 的指令要明確要求它在處理完後推進。

想重抓一段：把游標往回設即可。想從頭來過：刪掉 `loop/state/cursors.json`。

## 5. 驗證

先用最小預算跑一圈，看它到底做了什麼：

```bash
KM_MAX_ITEMS=1 make daily
tail -50 loop/logs/$(date +%F).log
git diff HEAD~1 -- vault/Sources    # 它寫出來的摘要
python3 tools/vault.py cursor list  # 游標有沒有前進
```

三個常見症狀：

- **它沒去抓** → 工具名寫錯，或 `loop/local/` 底下沒有 `.md` 檔（確認路徑是
  `loop/local/daily.md`，不是 `loop/local.example.md`）。
- **每天卡住不動** → 權限沒設 `allow`，`--auto` 把它擋掉了；看 log 裡的
  `permission requested ... auto-rejecting`。
- **每天重抓同一批** → 指令沒要求推進游標，或推進的值取錯（要取這批裡**最新**
  的位置，不是第一筆）。
