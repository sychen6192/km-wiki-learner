# 這套怎麼跑的

寫給要動這個 repo 的人。`docs/USAGE.md` 講「怎麼用」，這篇講「為什麼長這樣」。

---

## 心智模型：三權分立

整份設計就這一張表，其他都是細節：

| 角色 | 由誰做 | 檔案 |
|---|---|---|
| **立法** | 人類 | `AGENTS.md`、`vault/_meta/Style Guide.md` |
| **行政** | LLM | 寫筆記、連結、排複習 |
| **司法** | 確定性程式 | `tools/vault.py lint` |

LLM 只負責**寫檔案**。git、排程、驗收全部是 bash 與零依賴 Python。
所以 agent 改不動歷史，你永遠能用 `git diff` 驗收——這是整個專案敢讓它每天自動跑的唯一理由。

---

## 執行流：`loop/daily.sh`

```
① 解析 Python        實跑測試而非查 PATH → export KM_PYTHON
② 取鎖              loop/state/lock，持有者每 30s touch 心跳檔
③ git pull --rebase  KM_NO_PULL=1 可跳過
④ extract.py        Raw/* → loop/state/extracted/*.txt
⑤ vault.py scan     → loop/state/scan.json（工作清單）
⑥ render.py         prompts/daily.md ＋ scan ＋ Inbox → 一段純文字
⑦ $KM_AGENT_CMD     把那段文字當 argv 丟出去；agent 寫檔進 vault/
⑧ vault.py stats    重算 Home.md 儀表板
⑨ vault.py lint     0 error 才算過；失敗叫 agent 修一輪（prompts/garden.md）
⑩ git add -A vault && commit && push
```

失敗的一圈**照樣 commit**——丟掉 agent 的半成品比留著更糟——但 commit message
會標 `[agent 未完成]`，不會讀起來像成功。

---

## 資料流

```
vault/Raw/*.pdf ──extract.py──▶ loop/state/extracted/*.txt
                                          │
vault/Inbox.md ───────────────────────────┤
vault/**/*.md ──vault.py scan──▶ scan.json │
                                          ▼
                                     render.py
                                          ▼
                                  一段純文字 prompt
                                          ▼
                                    $KM_AGENT_CMD
                                          ▼
                                    vault/**/*.md  ←── lint 把關
```

`loop/state/` 與 `loop/logs/` 都 gitignored：那是快取與日誌，不是資料。
真正的資料只有 `vault/` 裡的 markdown。整個 repo 刪掉 `loop/state/` 也能重建。

---

## 四個契約

1. **agent 契約** — `KM_AGENT_CMD` 只要滿足「接受一段文字當 argv，且能寫檔案」就能換。
   `opencode run --auto`、`claude -p`、`tools/agent.py` 都只是符合這個契約的執行器。
   **純 chat completion 不行**：長不出 vault 的東西不叫圖書館員。
2. **`Raw/` 唯讀** — 人類丟進去的原始素材，agent 只讀不改不刪。
3. **lint 是門檻** — 0 error 才進 main。壞筆記進不去。
4. **git 由外層做** — agent 不執行 git。它只寫檔案，外骨骼負責 commit。

---

## 兩個模型，不同工作

別搞混，它們的能力需求相反：

| 用途 | 需要 | 例 |
|---|---|---|
| `KM_VISION_MODEL` | **vision**，不需要 tools | `qwen3.8:27b` |
| `KM_MODEL`（agent） | **tools**，vision 可有可無 | `qwen3.6:35b-a3b` |

沒有 tool calling 的模型再聰明也寫不了檔案。用 Ollama 的話，
`curl -s $KM_API_BASE/api/tags | jq '.models[] | {name, capabilities}'` 可以確認。

---

## 掃描件：OCR 還是 vision

密排、小字、多語混排的教材**用 vision，不要用 OCR**。實測同一頁單字表：

| | OCR | vision @300dpi |
|---|---|---|
| 單字 | `ロ 店 中 Sash orem Yamatic whey` | `□店員(てんいん)` |
| 譯文欄（英中韓越） | 全毀 | 正確轉錄 |
| 速度 | ~3s/頁 | ~35s/頁 |

理由不只是準確度：**雜訊比空白危險**。下游模型讀到亂碼不會說「我讀不到」，
它會用先驗把洞補起來，補出一份看似合理、與課本無關的摘要。本 repo 真的發生過，
見 `vault/Daily/2026-08-16.md` 的撤回紀錄。

**DPI 是關鍵旋鈕**。150dpi 下日文標音（ふりがな）太小，模型會改用自己的知識生讀音：
`画家(がが)`、`パートで働く(はたく)`、`歯医者(はやし)`。同一頁 300dpi 全部正確。
猜讀音和編造章節是同一種錯，只是規模小。

抽取結果有快取，key 是**來源 mtime ＋ recipe**（模型／DPI／頁數上限）。
改設定會重抽——早期版本只看 mtime，換模型會拿到舊結果而且回報成功。

---

## 環境變數

| 變數 | 預設 | 作用 |
|---|---|---|
| `KM_AGENT_CMD` | `opencode run --auto` | 執行器 |
| `KM_MODEL` | — | 傳給執行器的 `--model` |
| `KM_MAX_ITEMS` | 3 | 每天最多做幾件事（成本上限） |
| `KM_SKIP_AGENT` | — | `=1` 空跑，只驗流程不花錢 |
| `KM_NO_PULL` / `KM_NO_PUSH` | — | 跳過對應 git 步驟 |
| `KM_TIMEOUT` | 3600 | agent 超時秒數 |
| `KM_PYTHON` | 自動偵測 | 工具鏈用的直譯器 |
| `KM_VISION_MODEL` | — | 設了掃描件才走 vision |
| `KM_RASTER_DPI` | 200 | 掃描件轉圖解析度（**教材建議 300**） |
| `KM_VISION_MAX_PAGES` | 0（全部） | 先試幾頁 |
| `KM_LOCK_STALE_SEC` | 120 | 心跳停多久算廢鎖 |
| `KM_HEARTBEAT_SEC` | 30 | 持有者多久 touch 一次心跳 |
| `KM_TOPIC` | — | 設了就跑隨選深潛（`prompts/learn.md`）而非每日迴圈 |
| `KM_OCR_LANG` | 自動偵測 | 覆寫 tesseract 語言，如 `jpn+eng` |
| `KM_VISION_TIMEOUT` | 900 | vision 每頁的秒數上限 |
| `KM_SHELL` | 自動找 `bash` | `render.py` 展開 `` !`…` `` 用的 shell |

`tools/agent.py`（內建 runner）另外讀這些：

| 變數 | 預設 | 作用 |
|---|---|---|
| `KM_API_BASE` | `http://localhost:11434` | 模型 endpoint |
| `KM_API_STYLE` | `ollama` | `ollama`（原生 `/api/chat`）或 `openai` |
| `KM_API_KEY` | — | 有設就帶 `Authorization: Bearer` |
| `KM_NUM_CTX` | 32768 | context 視窗（**只有 ollama 格式能指定**） |
| `KM_MAX_STEPS` | 60 | 工具呼叫次數上限，防無限迴圈 |
| `KM_HTTP_TIMEOUT` | 900 | 每次模型請求的秒數上限 |

---

## 單獨跑各元件

沒有 `make` 的話（Windows 常見），每個 target 都是一行：

| `make X` | 直接指令 |
|---|---|
| `daily` | `./loop/daily.sh` |
| `extract` | `$KM_PYTHON tools/extract.py` |
| `scan` | `$KM_PYTHON tools/vault.py scan` |
| `lint` | `$KM_PYTHON tools/vault.py lint` |
| `stats` | `$KM_PYTHON tools/vault.py stats` |
| `prompt` | `$KM_PYTHON tools/render.py prompts/daily.md` |
| `doctor` | `./scripts/doctor.sh` |
| `test` | `$KM_PYTHON -m unittest discover tests` |
| `learn TOPIC="x"` | `KM_TOPIC="x" ./loop/daily.sh` |

`make prompt` 會印出這次要送出去的完整 prompt，**一個 token 都不花**——
改 `prompts/` 或 Style Guide 之後先看這個，比跑一圈再後悔便宜得多。

---

## Windows 上的陷阱

這一節不是雜項，是這個 repo 幾個設計決策的來源。共通模式：
**名字解析到什麼，跟你以為的不一樣。**

| 你以為 | 實際上 | 後果與對策 |
|---|---|---|
| `python3` 在 PATH 上就能用 | Microsoft Store 轉址空殼，exit 49 不做事 | 不能查 PATH，要**實跑**才知道 → `KM_PYTHON` |
| PowerShell 的 `bash` 是 Git Bash | `C:\WINDOWS\system32\bash.exe`＝WSL | 要寫完整路徑 `& 'C:\Program Files\Git\bin\bash.exe'` |
| `kill -0 $pid` 能判斷存活 | **兩個 Git Bash 實例的 MSYS pid namespace 是分開的**，互相看不到 | 鎖改用心跳，不問 pid |
| `taskkill /T` 會殺掉整棵樹 | MSYS 的 POSIX process tree 在 Windows 端不存在；agent 不是 subshell 的 Windows 子行程 | 在 MSYS 端遞迴走 tree，逐一收掉 |
| Python 印中日文沒問題 | stdout 走 cp950，遇日文直接 crash | `tools/` 四支都 `reconfigure(encoding='utf-8')` |
| `date -r file` 到處都能取 mtime | GNU 讀成路徑，BSD／macOS 讀成 epoch 秒數 | 用 Python 取 mtime，兩邊語意才一致 |
| `subprocess(shell=True)` 是 sh | Windows 上是 **cmd.exe**，`date +%F`、`${VAR:-x}` 全爆 | `render.py` 明確指定 bash |
| 外部工具開得了任何檔名 | `pdftotext` 走 ANSI codepage，日文檔名開不了——而且錯誤長得像「PDF 沒有文字層」 | 非 ASCII 檔名先複製成 ASCII 暫存檔 |
| `VAR=x time cmd` 會計時 | 環境變數前綴後面**一定當命令查**，`time` 不再是 keyword | 用 `SECONDS=0; cmd; echo $SECONDS` |

Ctrl-C 能真的停下來，靠三件事同時成立：agent 跑在背景並用 `wait`（前景的話
trap 要等指令回來，agent 可以跑一小時）、`set -m` 讓 agent 自己一個 process group
（否則 group SIGINT 先殺掉外層 subshell，把原生 agent 丟給 init 當孤兒）、
以及上面那個手動走 tree 的收屍程序。

---

## 出事時怎麼查

```bash
./scripts/doctor.sh                   # 這台機器缺什麼
tail -40 loop/logs/$(date +%F).log    # 這圈發生什麼
cat loop/state/prompt-$(date +%F).md  # agent 實際收到什麼
$KM_PYTHON tools/extract.py --list    # 素材讀到了沒、用什麼方法讀的
git diff HEAD~1 -- vault/             # 它到底寫了什麼
```

**卡住不動**時先看 CPU：0% 且沒有子行程，就不是在算而是在等。
Git Bash 視窗被滑鼠點過會進入選取模式（QuickEdit），**凍結所有輸出**，
按 Esc 就會繼續——這是最常見的假當機。

鎖卡住用 `make unlock`。心跳超過 `KM_LOCK_STALE_SEC` 的話下一圈會自己回收，
並把回收原因寫進日誌。

> ⚠️ **迴圈跑的時候不要改 `loop/daily.sh`。** bash 是邊讀邊執行的，它記的是檔案裡的
> 位移量而不是行號；跑到一半把前面的內容加長或縮短，它接下來會從錯誤的位置繼續讀，
> 冒出指向無辜行號的怪錯誤（例如某個明明有賦值的變數說 unbound）。一圈要跑數十分鐘，
> 這個窗口比想像中大。改完等下一圈再跑。
