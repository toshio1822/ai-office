# AI Office 開発ガイド

GitHub Issue を仕様の正本として扱う。作業前に対象 Issue、関連するドキュメント、既存コード、テストを確認すること。

## 作業原則

- Issue の完了条件を満たす最小の変更だけを実装する。
- ワークフローの順序や人間承認を暗黙に補完しない。
- 定義の不備は実行前に検出できる設計を優先する。
- 実行時には定義、入力、プロンプト、生応答、成果物、イベントを追跡可能にする。
- 対象外の機能を先行実装しない。仕様が不足する場合は推測で広げず、未解決事項として報告する。
- 既存の別プロジェクトのコードをコピーしない。必要な知見は、このプロジェクトの目的に合わせて再設計する。
- 保存済みの実行データ、イベント、成果物は、対象 Issue で明示された場合を除き変更しない。
- コミット、push、Pull Request 作成は、人間から明示的な許可を得た場合だけ行う。

## コマンド承認ポリシー

- 読み取り専用の調査コマンドは、コマンドごとの確認を求めず実行してよい。例: `git status`、`git diff`、`git log`、`git rev-parse`、`git branch --show-current`、`rg`、`gh auth status`、`gh issue view`、`gh pr view`、`gh run list`、`gh run view`、`gh run watch`。
- 人間が Issue 本文またはチャットで開発バッチを明示的に許可した場合、そのバッチ内では、branch 作成・checkout、`git add`、通常の `git commit`、Issue branch への通常 push、Draft Pull Request 作成、Pull Request・Issue・CI の確認を、コマンドごとの再確認なしで実行してよい。
- 人間がレビュー完了後に merge バッチを明示的に許可した場合、そのバッチ内では、Pull Request の Ready 化、最新 head の CI 確認、Merge commit 方式の merge、関連 Issue の completed close、最終状態確認を、コマンドごとの再確認なしで実行してよい。
- 1つの shell invocation に複数コマンドを連結してよい。ただし、含まれるすべてのコマンドが同じ許可済みバッチの範囲内であること。
- 許可済みバッチ中に確認を求めて停止するのは、初期状態と異なる未追跡・未コミット変更、競合、branch・head SHA の不一致、失敗した CI、権限不足、または許可範囲外の操作が必要になった場合だけとする。
- `reset`、`clean`、`stash`、`commit --amend`、rebase、squash、force push、force-with-lease、`main` への直接 commit/push、branch 削除は、個別に明示許可されない限り行わない。
- リポジトリ内の許可ルールは、Codex のホストアプリケーション、CLI、sandbox、network、managed policy が強制する承認を解除するものではない。

## 実装手順

1. Issue の背景、対象範囲、対象外、完了条件を確認する。
2. `docs/` と既存実装を読み、変更範囲を決める。
3. `src/` レイアウトを守り、公開 CLI は `ai-office` に追加する。
4. 振る舞いをテストで確認する。
5. `pytest` と `ruff check .` を実行し、失敗を解消する。
6. 変更ファイル、設計判断、テスト結果、未解決事項、完了条件の充足状況を報告する。

Phase 実装では、対象 Issue に加えて
[`docs/development/phase-implementation-contract.md`](docs/development/phase-implementation-contract.md)
を読み、その共通規約に従うこと。Issue が明確に上書きする要件だけは、その指定を優先する。

## コマンド

```bash
python -m pip install -e '.[dev]'
pytest
ruff check .
ai-office --help
```
