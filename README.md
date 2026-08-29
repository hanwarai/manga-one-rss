# manga-one-rss

[マンガワン](https://manga-one.com) の**無料公開中**の章だけを Atom フィードとして配信するジェネレーター。GitHub Actions が 12 時間ごとに実行し、GitHub Pages へ公開する。

- 一覧: https://hanwarai.github.io/manga-one-rss/
- 各フィード: `https://hanwarai.github.io/manga-one-rss/{title_id}.xml`

## 仕組み

```
feed.csv → main.py → feeds/*.xml + feeds/index.html → GitHub Pages
```

1. `feed.csv` の `title_id,chapter_id` を読む
2. `POST https://manga-one.com/api/client?rq=viewer_v2&...` を呼ぶ。レスポンスは JSON ではなく **protobuf バイナリ**
3. スキーマが公開されていないため、同梱のスキーマレスデコーダでフィールド番号ベースに展開し、作品メタ情報と章リストを取り出す
4. 各章の `field 16` が空または欠落なら無料、サブメッセージなら有料として除外し、新しい順に Atom フィードへ入れる
5. Jinja2 テンプレートで一覧ページ `feeds/index.html` を生成する

## 作品の追加

`feed.csv` に `title_id,chapter_id` を 1 行追記して push するだけ。次のデプロイからフィードが生える。

`chapter_id` は API 呼び出しのアンカーで、**現在無料でない章でもよい**（ID が有効なら作品全体の章リストが返る）。どちらも作品ページの URL から取れる数値。

## 開発

```bash
uv sync --all-extras          # 依存インストール
uv run main.py                # フィード生成 (feeds/ 配下に出力)
uv run pytest                 # テスト (カバレッジ 80% 未満で失敗)
uv run ruff check .           # lint
uv run ruff format .          # フォーマット
uv run mypy                   # 型検査
uv run pre-commit install     # コミット時に上記を自動実行
```

Python 3.13 / パッケージマネージャーは [uv](https://docs.astral.sh/uv/)。
