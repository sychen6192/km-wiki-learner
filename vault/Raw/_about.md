# Raw — 不可變素材層

把想讓維基消化的原始素材丟進這個資料夾：

- 網頁剪藏（推薦 [Obsidian Web Clipper](https://obsidian.md/clipper)，手機也能剪）
- 課堂講義、作業、論文、文字檔、會議記錄

明天清晨的迴圈會把每個新檔案寫成一篇 `Sources/` 摘要，並把觀念織進概念筆記。

## 餵進來之前先轉成文字

agent 讀的是文字。二進位檔（PDF、docx、圖片）要先轉：

```bash
# Google Docs：在文件裡 檔案 → 下載 → Markdown (.md)，然後把檔案丟進來

# PDF → 文字（需要 poppler-utils；macOS: brew install poppler）
pdftotext -layout 講義.pdf vault/Raw/講義.txt

# 一次轉一整批
for f in ~/Downloads/*.pdf; do
  pdftotext -layout "$f" "vault/Raw/$(basename "${f%.pdf}").txt"
done

# 掃描件或手機拍的作業（沒有文字層，pdftotext 會出空檔）→ 要 OCR
tesseract 作業.jpg vault/Raw/作業 -l jpn   # 日文；中文用 chi_tra
```

檔名就是給 agent 的第一個線索，取有意義的名字（`2026-08-15 第3課 講義.txt`
比 `scan001.txt` 好用得多）。

規則：agent 對這裡**只讀不寫不刪**；檔名以 `_` 或 `.` 開頭的檔案會被略過（例如本說明）。
