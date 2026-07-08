# Stop-takayasu

Kabutan の「本日のストップ高銘柄」「本日のストップ安銘柄」から、銘柄名と前日比を中心に保存し、GitHub Pages で閲覧する静的サイトです。

公開URL: https://al00000000.github.io/Stop-takayasu/

## 出力

- `history/YYYY-MM-DD.json`
- `output/stop_takayasu_YYYY-MM-DD.csv`
- `docs/data/YYYY-MM-DD.json`
- `docs/data/index.json`

## 実行

```powershell
py fetch_stop_takayasu.py
```

ブラウザ User-Agent を付けて `https://kabutan.jp/warning/?mode=3_1` と `https://kabutan.jp/warning/?mode=3_2` を取得します。ライブ更新ボタンはブラウザから CORS プロキシ経由で取得し、表示だけ更新します。
