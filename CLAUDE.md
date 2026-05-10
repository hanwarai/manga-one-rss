# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

マンガワン（manga-one.com）の無料章を取得し、Atom RSS フィードとして配信するジェネレータ。GitHub Actions で 12 時間ごとに自動実行され、GitHub Pages として公開される。

## Commands

```bash
# 依存パッケージインストール
uv sync --all-extras

# フィード生成
uv run main.py

# テスト実行（requests-mock で I/O モック）
uv run pytest

# 型検査（CI ゲート）
uv run mypy main.py
```

## Architecture

```
feed.csv → main.py → feeds/*.xml + feeds/index.html → GitHub Pages
```

**処理フロー（main.py）:**
1. `feed.csv` から `title_id,chapter_id` のペアを読み込む（chapter_id は API 呼び出し用のアンカー、任意の章でよい）
2. `POST https://manga-one.com/api/client?rq=viewer_v2&title_id=X&chapter_id=Y&...&limit=500` を呼ぶ。レスポンスは **protobuf バイナリ** （JSON ではない）
3. 同梱の `proto_decode` でスキーマレスにツリーへ展開し、作品メタ情報（field 5）と章リスト（field 11）を取り出す
4. 章エントリの `field 16` がサブメッセージ（例: `{1:1, 2:1}`）なら有料章として除外。空文字列または欠落なら無料章
5. chapter_id 降順で並べて Atom に詰め、Jinja2 テンプレート（`templates/index.html`）で `feeds/index.html` を生成

**主要ファイル:**
- `main.py` — 全処理ロジック（pure-Python protobuf decoder 同梱、外部依存なし）
- `feed.csv` — トラッキング対象 `title_id,chapter_id`（1 行 1 作品、両方とも数値）
- `templates/index.html` — Jinja2 テンプレート（Bootstrap 5）
- `feeds/` — 生成ファイル出力先（gitignore 済み、`.gitkeep` のみ管理）
- `tests/fixtures/*.bin` — 実 API レスポンスのバイナリスナップショット

## CI/CD

GitHub Actions（`.github/workflows/gh-pages.yaml`）:
- トリガー: main へ push、12 時間ごとの schedule、`workflow_dispatch`
- 処理: `uv sync` → `uv run mypy main.py` → `uv run pytest` → `uv run main.py` → `feeds/` を GitHub Pages にデプロイ
- scheduled run が失敗した場合、`notify-failure` ジョブが `ci-failure` ラベルで Issue を起票（既存 open Issue があればコメント追記）

## Notes

- パッケージマネージャーは `uv`（`pip` は使わない）
- Python 3.13（`.python-version`）
- 出力 URL: `https://hanwarai.github.io/manga-one-rss/{title_id}.xml`
- 日付は `YYYY/MM/DD`（JST = UTC+9 と解釈）

## Gotchas

- API レスポンスは **JSON ではなく protobuf バイナリ**。スキーマは公開されていないので `proto_decode` でフィールド番号ベースに parse する。manga-one が proto 定義を変更すると静かに壊れる
- `chapter_id` は viewer_v2 の必須パラメータ。API はこの章を中心に最大 `limit` 件を返すので、現在無料の章でなくてもよい（ID が有効でさえあれば作品全体の章リストが返る）
- `viewer_v2` レスポンスの章リスト（field 11）の各エントリは `field 16` の有無で無料／有料を判別:
  - 欠落 or 空文字列 (`wire 2 length 0`) → 無料
  - サブメッセージ（`{1:1, 2:1}` など）→ 有料（アイテム消費が必要）
- 章リストは降順 (`sort_type=desc`) で取得し、`limit=500` で全章を一括取得（最大 393 章まで観測）
- 章エントリには `第N話` 以外に「コミックPR」「人物紹介」「アニメ情報」など販促章も混ざる。これらも掲載期間内は無料なので RSS に含める
- WAF/Bot 対策は現状なし。User-Agent を付ければ素の `requests.post()` で取得可能
- `__NEXT_DATA__` のような単一 JSON 埋め込みは存在しない。Next.js App Router の `__next_f.push` ストリームのみで、章メタ情報は **API 経由でのみ取得可能**
