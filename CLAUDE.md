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

# テスト実行（requests-mock で I/O モック、カバレッジ 80% 未満で失敗）
uv run pytest

# lint / format（CI ゲート）
uv run ruff check .
uv run ruff format .

# 型検査（CI ゲート）
uv run mypy

# commit 時に ruff / mypy を自動実行させる（初回だけ）
uv run pre-commit install
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
- `.pre-commit-config.yaml` — commit 時の ruff / mypy。ruff と mypy は mirrors ではなく local hook で `uv run` する（rev と uv.lock が独立に動いて CI と結果がずれるのを防ぐ）

## CI/CD

ワークフローは 3 本。うち `gh-pages.yaml` と `ci.yaml` のセットアップ手順（uv version 解決 → setup-uv → setup-python → `uv sync --locked --all-extras`）は意図的に同内容で重複させている。両方 `.github/workflows/` 配下なので Dependabot (github-actions) が同一 PR で両者を bump する。

**`.github/workflows/gh-pages.yaml`** — ビルドとデプロイ:
- トリガー: main へ push、12 時間ごとの schedule、`workflow_dispatch`
- 処理: `uv sync` → `uv run mypy` → `uv run pytest` → `uv run main.py` → `feeds/` を GitHub Pages にデプロイ
- scheduled run が失敗した場合、`notify-failure` ジョブが `ci-failure` ラベルで Issue を起票（既存 open Issue があればコメント追記）

**`.github/workflows/ci.yaml`** — PR 検証（デプロイなし）:
- トリガー: main を base とする `pull_request`
- 処理: `uv sync` → `uv run ruff check .` → `uv run ruff format --check .` → `uv run mypy` → `uv run pytest`
- `uv run main.py` は含めない。live API を叩くため PR ごとの実行は不安定で、push/schedule 実行でカバー済み
- `--frozen` ではなく `--locked` を使う。`--frozen` は `uv.lock` をそのまま使うだけで `pyproject.toml` との整合性を検証しないため、Dependabot PR の lock ずれが auto-merge を素通りする
- main は branch protection で `check` を required status check にしてある。赤いと `gh pr merge` は拒否される
- `enforce_admins: false` なので admin は `gh pr merge --admin` で上書きでき、main への直接 push も従来どおり可能（`check` は push では走らないため、これを塞ぐと直接 push が恒久的に不可能になる）
- **`ci.yaml` の job 名 `check` は required status check の context 名そのもの。**リネームすると protection が存在しない context を待ち続け、PR が永久にマージ不能になる。変える場合は branch protection 側も同時に更新する

**`.github/dependabot.yml`** — 3 エコシステム（`uv` / `github-actions` / `pre-commit`）を weekly で更新。いずれも `patterns: ["*"]` の 1 グループにまとめてある。`pre-commit` は `.pre-commit-config.yaml` の remote repo（pre-commit-hooks）の rev だけを追う。**ruff / mypy は local hook なので Dependabot は動かさない**（`uv.lock` 側の bump が効く）

**`.github/workflows/dependabot-auto-merge.yaml`** — Dependabot PR の自動マージ:
- トリガー: `pull_request_target`（`pull_request` だと Dependabot 起因のトークンが read-only 固定で、`permissions:` でも昇格できないため）
- `update-type` が `version-update:semver-major` 以外なら `gh pr merge --auto --squash`。`check` が green になり次第マージされる
- **major は自動マージしない。**グループ PR の `update-type` は「その PR に含まれる最大の semver 変更」を指すので、`patterns: ["*"]` の全部入りグループでも major は弾ける
- **このワークフローに checkout / ビルド / テストのステップを足してはいけない。**`pull_request_target` は write 権限つきトークンで走るため、PR 側のコードを実行すると任意コードに write token を渡すことになる。検証は read-only で走る `ci.yaml` の責務
- auto-merge は `GITHUB_TOKEN` が有効化するため、**マージ後の main への push では `gh-pages.yaml` が起動しない**（GITHUB_TOKEN 起因の push はワークフローを再帰起動しない仕様）。依存更新はフィード内容を変えないうえ 12 時間ごとの schedule が再デプロイするので実害はない。もし依存更新が `main.py` を壊していれば次の scheduled run が失敗し `notify-failure` が Issue を立てる

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
- ruff の `RUF002`/`RUF003` は全角括弧（`（）`）や全角スラッシュを混同文字として弾く。コメントと docstring では ASCII の `()` `/` を使う（他の RSS リポジトリも同じ慣例）。文字列リテラルを見る `RUF001` は現状 0 件
- `proto_decode` は `C901`/`PLR0911`/`PLR0912` を踏むため、wire 種別ごとの読み取りを `_read_fixed` / `_decode_length_delimited` / `_decode_fields` に分割し、失敗は `_DecodeError` に集約して `proto_decode` の 1 箇所だけで `None` に変換している。**`_decode_length_delimited` の再帰先は `_decode_fields` ではなく `proto_decode`。**例外を伝播させると入れ子の解釈失敗時に str/bytes へフォールバックできなくなる
