# tw-cosmos.github.io

靜態頁面以 YAML 資料檔為來源，透過 Python 腳本生成，部署於 GitHub Pages。

## 結構

```
data/          資料檔（YAML）
scripts/       生成腳本
*/index.html   生成頁面
```

## 生成頁面

```bash
python scripts/build.py
```
