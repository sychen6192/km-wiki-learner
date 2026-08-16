---
description: 園藝模式 — 只做結構健檢與修復（lint 錯誤、孤兒、走失的連結），不產新內容
agent: librarian
---

執行 vault 結構修復。目前 lint 結果：

```
!`python3 tools/vault.py lint || true`
```

規則：

1. 逐條修復上面的 **ERROR**（frontmatter 缺欄位、非法值、壞日期）。修 frontmatter 時不改動內文。
2. **WARN 的孤兒**：把它連進最相關的 Map 或概念筆記（真的無處可放才留著）。
3. 不新增筆記、不改寫內容、不動 `Raw/`、不動 `<!--SR:...-->` 註解；dangling links 是正常的知識前緣，**不要**為了消滅它們而亂建頁面。
4. 完成後跑 `python3 tools/vault.py lint` 確認 0 error。不要執行 git commit／push。
