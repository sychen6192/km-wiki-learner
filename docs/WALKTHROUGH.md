# 從零開始：以準備日文 N4 為例

把老師的講義和作業丟進去，讓維基自己長。換成任何領域都一樣，流程不變。

---

## 1. 跑起來

```bash
git clone <this-repo> && cd km-wiki-learner

curl -fsSL https://opencode.ai/install | bash    # 還沒裝的話
export PATH="$HOME/.opencode/bin:$PATH"
export ANTHROPIC_API_KEY=sk-ant-...              # 或 opencode auth login

make doctor                  # 檢查這台機器缺什麼
make test                    # 工具鏈自檢，不花錢
KM_SKIP_AGENT=1 make daily   # 空跑，只驗流程不叫 LLM
```

repo 裡沒有任何 opencode 專案設定檔，所以它不會在你的目錄裡 bootstrap `node_modules`
那些東西。權限限制放你自己的全域設定（下面「安全邊界」一節）。

用 Obsidian「開啟資料夾作為儲存庫」選 `vault/`。建議裝兩個外掛：
**Spaced Repetition**（刷卡）和 **Git**（手機同步）。

---

## 2. 把素材丟進去 —— 直接丟，不用轉檔

```bash
cp ~/Downloads/第3課_講義.pdf   vault/Raw/
cp ~/Downloads/宿題_batch1.pdf  vault/Raw/
cp ~/Desktop/黑板照片.jpg       vault/Raw/
```

PDF、掃描件、手機拍的照片、`.docx` 都直接丟。迴圈的 preflight 會自動處理：

| 你丟的 | 它怎麼讀 |
|---|---|
| `.md` `.txt` 等純文字 | 直接讀 |
| 有文字層的 PDF | `pdftotext` 抽文字 |
| 掃描 PDF、照片 | 自動偵測沒有文字層 → 轉圖 → OCR |
| `.docx` | 標準函式庫直接解 |

想先確認它讀到什麼：

```bash
make extract
```

會逐檔列出抽了多少字，或明確告訴你缺什麼工具。OCR 需要 `tesseract`
（macOS: `brew install tesseract tesseract-lang`；Debian: `apt install tesseract-ocr tesseract-ocr-jpn`），
PDF 需要 `poppler`。裝了就自動用，沒裝它會直說，不會默默略過。

**Google Docs** 沒有檔案可丟，在文件裡 `檔案 → 下載 → Markdown (.md)` 再丟進 `vault/Raw/`。

> 檔名是 agent 的第一個線索。`2026-08-16 第3課 講義.pdf` 比 `scan001.pdf` 有用得多。

---

## 3. 出題（選用）

`vault/Inbox.md` 一行一個。**不寫也可以** —— Raw 裡有素材它就知道要做什麼。
寫了只是讓它更聚焦：

```markdown
- [ ] 消化 Raw 裡的第3課講義，把教到的文法點各寫一頁，開一張 [[日本語 N4]] 地圖
- [ ] 作業第 4、7 題我錯了，講解為什麼
```

第二行是**加分不是義務**：作業如果已經批改過，它自己看得出來哪裡錯；
你講只是省它猜的功夫。

那個 `[[日本語 N4]]` 是還不存在的頁面 —— 這正是機制：**灰色連結就是待辦**，
迴圈會照被引用次數把它們長出來。你不需要先手寫任何知識點。

---

## 4. 跑第一圈

```bash
KM_MAX_ITEMS=1 make daily    # 先做一件事就好，看它的風格對不對
KM_MAX_ITEMS=5 make daily    # 滿意了再開大
```

`KM_MAX_ITEMS` 是**每天最多處理幾件事**的預算（預設 3）。存在的理由是控制花費，
以及避免它一個晚上寫出 300 頁半生不熟的東西。一份講義的消化算一件事，
即使它從裡面拆出五個文法點。

想先看它會收到什麼指令再決定要不要跑：

```bash
make prompt          # 印出這次要送出去的完整 prompt，一個 token 都不花
```

---

## 5. 驗收

```bash
cat "vault/Daily/$(date +%F).md"     # 它做了什麼、為什麼、明天想做什麼
git diff HEAD~1 -- vault/            # 實際寫了哪些字
tail -40 "loop/logs/$(date +%F).log" # 過程
```

在 Obsidian 打開 [[Home]] 看儀表板，或按 graph view 看新長出來的連結。

不滿意有三個施力點：

| 想改 | 改哪裡 |
|---|---|
| 筆記的寫法、結構 | `vault/_meta/Style Guide.md`（憲法，agent 每天讀） |
| 它每天送出去的指令 | `prompts/daily.md` |
| 方向與優先順序 | `vault/Inbox.md` |
| 某頁不准它再動 | 該筆記 frontmatter 加 `locked: true` |

寫壞了 `git revert` —— 全部是純文字，沒有資料庫會壞。

---

## 6. 刷卡

Obsidian 左側 🧠 圖示 → 選牌組 → 開始複習。
文法卡 `問題::答案`，單字卡 `日文:::中文`（三個冒號＝雙向都考）。

> ⚠️ 卡片後面的 `<!--SR:!2026-09-01,25,249-->` 是外掛的複習排程紀錄。
> **不要手動改或刪**，改了等於清空進度。Style Guide 也明令 agent 不准碰。

---

## 7. 讓它每天自己跑

```bash
make install-cron TIME=06:00     # 或 make install-systemd（筆電推薦，錯過會補跑）
```

之後的節奏：

| 你（每天 2 分鐘） | 它（每天清晨） |
|---|---|
| 上完課，講義丟 `Raw/` | 轉文字 → 寫成 `Sources/` 摘要 → 文法點各自成頁 |
| 錯題寫進 `Inbox.md`（選用） | 針對錯題深入講解 |
| 通勤時刷卡 | 為新內容補卡、複習到期的舊筆記 |
| 什麼都不做 | 沿知識前緣自己長 |

考前一週把 `KM_MAX_ITEMS` 開大，讓它把剩下的前緣補完。

---

## 安全邊界

repo 裡沒有設定檔，所以權限限制放在你的全域設定
`~/.config/opencode/opencode.json`：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "permission": {
    "bash": {
      "*": "allow",
      "git commit*": "deny",
      "git push*": "deny",
      "rm *": "deny",
      "sudo *": "deny"
    }
  }
}
```

git 的寫入一律由 `loop/daily.sh` 執行，agent 只負責寫檔案 —— 這樣它改不動歷史，
你永遠能用 diff 驗收。

---

## 換掉 opencode

prompt 是純文字，agent 只是執行器：

```bash
KM_AGENT_CMD="claude -p" make daily
KM_AGENT_CMD="codex exec" make daily
```

只要那個 CLI 接受「一段文字當參數」就能用。預設是 `opencode run --auto` ——
`--auto` 不能省，headless 執行時沒有人可以按同意，少了它每個寫檔都會被自動拒絕。
換成別家 CLI 時記得帶上對應的免互動旗標。
