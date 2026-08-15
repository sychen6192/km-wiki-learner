# 從零開始：用日本語 N4 走一遍

以「把老師的講義和作業餵進去，讓維基幫我準備 N4」為例，走完整個流程。
換成任何領域都一樣，只是換一張 Map。

---

## 步驟 0：先讓 opencode 在 repo 裡跑得起來

如果 `opencode run "說 hi"` 在家目錄正常、在 repo 裡失敗，而且 repo 裡莫名多出
`node_modules/`、`package.json` —— 原因是 opencode 看到專案層級的 `opencode.json`
就會在那個目錄 bootstrap 一套相依套件沙箱，bootstrap 失敗就噴 `UnknownError`。

先確認：

```bash
cd ~/你的/km-wiki-learner
rm -rf node_modules package.json package-lock.json bun.lock*
mv opencode.json /tmp/
opencode run "說 hi"; echo "exit=$?"
```

**成功了** → 就是它。把權限設定搬到你的全域設定（那裡本來就跑得動），
編輯 `~/.config/opencode/opencode.json`：

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

然後 repo 裡不要再放 `opencode.json`，並在 `loop/daily.sh` 的 opencode 指令加上
`--agent librarian`（原本靠設定檔的 `default_agent` 指定）。

**還是失敗** → 那是 `.opencode/` 目錄的問題，你的版本可能只認單數目錄名：

```bash
cp -r .opencode/agents .opencode/agent
cp -r .opencode/commands .opencode/command
```

確認能跑之後再往下。

---

## 步驟 1：打開 vault

Obsidian → 開啟資料夾作為儲存庫 → 選 `vault/`。

裝兩個社群外掛（都不是必要，但差很多）：

- **Spaced Repetition** —— 刷複習卡，語言學習的重點就在這
- **Git** —— 手機／多台電腦同步用

打開 [[Home]] 會看到儀表板，[[日本語 N4]] 是 N4 的地圖。裡面大部分連結是灰色的
（頁面還不存在）—— 那不是壞掉，那是**知識前緣**，迴圈會照被引用次數優先長出來。

---

## 步驟 2：把素材餵進去

素材放 `vault/Raw/`，agent 只讀不改。**但 agent 讀的是文字**，二進位檔要先轉。

**Google Docs**（老師的講義）：
在文件裡 `檔案 → 下載 → Markdown (.md)`，把檔案丟進 `vault/Raw/`。
（`純文字 (.txt)` 也可以，Markdown 會保留標題結構，agent 更好讀。）

**PDF 作業**：

```bash
# 需要 poppler-utils；macOS: brew install poppler
pdftotext -layout ~/Downloads/第3課_宿題.pdf "vault/Raw/2026-08-15 第3課 作業.txt"

# 一整批
for f in ~/Downloads/*.pdf; do
  pdftotext -layout "$f" "vault/Raw/$(basename "${f%.pdf}").txt"
done
```

**掃描件或手機拍的作業**（`pdftotext` 出來是空的，代表沒有文字層）：

```bash
tesseract 作業.jpg "vault/Raw/2026-08-15 第3課 作業" -l jpn
```

> 檔名就是 agent 的第一個線索。`2026-08-15 第3課 講義.txt` 比 `scan001.txt` 好用太多。

確認它看到了：

```bash
make scan | grep -A5 pending_raw
```

---

## 步驟 3：出題

編輯 `vault/Inbox.md`，一行一個。給 N4 的好寫法：

```markdown
- [ ] 消化 Raw 裡的第3課講義，把教到的文法點各寫一頁，收進 [[日本語 N4]]
- [ ] 作業我第 4、7 題錯了，講解為什麼，並補進對應文法頁的「容易搞混的鄰居」
- [ ] [[て形]] 三類動詞變化整理成一張表，含例外
- [ ] 比較 [[なければならない]] 和 [[なくてはいけない]]，考試會怎麼考？
```

比起「教我 N4 文法」，**指名到具體文法點或具體錯題**得到的品質高得多。
錯題尤其有價值 —— 那是你真正的弱點，不是課本的平均難度。

---

## 步驟 4：跑

```bash
make test                      # 工具鏈自檢，不花錢
KM_SKIP_AGENT=1 make daily     # 空跑，只驗流程
KM_MAX_ITEMS=5 make daily      # 真的跑（語言學習素材多，預算開大一點）
```

第一次建議 `KM_MAX_ITEMS=1` 跑一輪看它的風格對不對，滿意再開大。

---

## 步驟 5：驗收

```bash
cat "vault/Daily/$(date +%F).md"     # 它今天做了什麼、為什麼、明天想做什麼
git diff HEAD~1 -- vault/            # 實際寫了哪些字
tail -40 "loop/logs/$(date +%F).log" # 過程
```

在 Obsidian 裡打開 [[日本語 N4]]，剛長出來的文法頁會從灰色變成可點。
按 graph view 可以看到文法點之間連起來的樣子。

不滿意的話有三個施力點：

| 想改 | 改哪裡 |
|---|---|
| 筆記的寫法、結構 | `vault/_meta/Style Guide.md` 的「語言學習筆記」那節 |
| 方向、優先順序 | `vault/Inbox.md` 寫更具體的題目 |
| 某頁不准它再動 | 該筆記 frontmatter 加 `locked: true` |

寫壞了就 `git revert` —— 全部是純文字，沒有資料庫會壞。

---

## 步驟 6：刷卡（這步最重要）

Obsidian 左側點 🧠 圖示（Spaced Repetition 外掛）→ 選 `flashcards/japanese` 牌組 → 開始複習。

卡片格式：文法用 `問題::答案`，單字用 `日文:::中文`（三個冒號＝日→中、中→日都會考）。
迴圈每消化一份講義就往 [[日本語 N4 複習]] 補卡。

> ⚠️ 卡片後面會出現 `<!--SR:!2026-09-01,25,249-->` 這種註解，那是外掛記錄的複習排程。
> **絕對不要手動改或刪**，改了等於清空你的複習進度。Style Guide 也明令 agent 不准碰。

---

## 步驟 7：讓它每天自己跑

```bash
make install-cron TIME=06:00     # 或 make install-systemd（筆電推薦，錯過會補跑）
```

之後的節奏就變成：

| 你（每天 2 分鐘） | 它（每天清晨） |
|---|---|
| 上完課，講義丟 `Raw/` | 消化成 `Sources/` 摘要，文法點各自成頁 |
| 錯題寫進 `Inbox.md` | 針對錯題深入講解，補進對應文法頁 |
| 通勤時刷卡 | 為新內容補卡、複習到期的舊筆記 |
| 什麼都不做 | 沿前緣自己長（那 20 個灰色文法點會慢慢變綠） |

考前一週把 `KM_MAX_ITEMS` 開大，讓它把剩下的前緣一次補完。

---

## 隨選深潛

不想等明天清晨：

```bash
make learn TOPIC="て形的三類動詞變化，整理成表格加例外"
```

會立刻研究、寫成筆記、補卡、記進今天的每日報告。
