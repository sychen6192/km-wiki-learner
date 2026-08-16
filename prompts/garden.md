園藝模式：只修結構，不產新內容。目前 lint 結果：

```
!`python3 tools/vault.py lint || true`
```

1. 逐條修掉上面的 **ERROR**（frontmatter 缺欄位、非法值、壞日期）。只動 frontmatter，不改內文。
2. **WARN 的孤兒**：把它連進最相關的 Map 或概念筆記；真的無處可放就留著。
3. 不新增筆記、不改寫內容、不動 `vault/Raw/`、不動 `<!--SR:...-->` 註解。
   dangling link 是正常的知識前緣，**不要**為了消滅它們而亂建頁面。
4. 完成後跑 `python3 tools/vault.py lint` 確認 0 error。**不要 git commit 或 push。**
