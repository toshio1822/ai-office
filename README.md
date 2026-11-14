# AI Office

このREADMEは[2026-11-14時点のmain](https://github.com/toshio1822/ai-office/commit/b230fb593c89f446e218356249095ed59a9a7be5)を基準に、AI Office を初めて知る日本語話者の技術者向けにまとめたものです。

## 目的と対象

AI Office は、人間が定義した仕事を AI 社員が担当工程ごとに処理できる汎用基盤です。製品CLIは設定駆動で、OpenClaw/Codex 等の外部 AI エージェントを必須としません。人間が目的・受入条件・範囲を定め、AI 社員が担当工程を処理します。

対象は、AI Office を初めて評価・利用・実装に参加する技術者です。

主な利点:

- 仕事を工程と職能に分解し、再利用可能な AI 社員に任せられる
- Workflow で順序・モデル・引継ぎ・有限レビューを定義できる
- Engine が検証・認可・上限・状態・Evidence/Artifact を扱い、曖昧な結果を安全に止める

## 概念と関係

```text
┌─ Engine: 検証・認可・上限・Run状態・Evidence/Artifact記録 ─┐
│                                                            │
│ Run Input ──> Workflow ──> Employee(s) ──> Artifact(s)     │
│                 順序・モデル・引継ぎ・有限レビューを定義     │
└────────────────────────────────────────────────────────────┘
```

Engine は下流の一工程ではなく、Workflow/Employeeの実行全体を検証・認可・上限管理し、Run状態とEvidence/Artifactを記録する基盤です。

- **Employee**: 再利用可能な職能・行動原則・許可 tool
- **Workflow**: 順序・モデル・引継ぎ・有限レビュー
- **Run Input**: 個別依頼・事実・根拠
- **Engine**: 検証・認可・上限・状態・Evidence/Artifact

### 現行の記事 Workflow 例

[workflows/article-production.yaml](workflows/article-production.yaml) は次の順で構成されています。

```text
write-draft（article-writer）
        ↓
review-quality（document-reviewer）
review-evidence（evidence-auditor）
        ↓
revise（article-writer）
```

- Writer / Revision: `article-writer`
- 独立 Reviewer: `document-reviewer`（文書品質・要件適合）、`evidence-auditor`（帰属された事実・根拠）
- 既定モデル: writer/revise は `deepseek-v4-pro`、両 Reviewer は `glm-5.3-flash`
- 設定上の改訂上限: 3
- 各 Employee の指示は未提供の事実・経験を発明しないこと。Reviewer は割当専門分野で成果物全体を確認する

## 主な機能と実証レベル

### 実装済みの機能

以下は固定main上の実装状況です。実モデルで実証された範囲とは分けて読んでください。

- **有限Job実行** — 既存Workflowからbounded Jobを作り、承認済みの送信・Reviewer・改訂上限内で実行します。Run state、Artifact、Evidenceを保存し、上限到達や結果不明では安全停止します。[記事制作Workflowの運用](docs/runbooks/article-production.md)
- **独立レビューと有限改訂** — Workflowは独立Reviewerと有限の改訂・再レビュー回数を定義します。現行の `article-production` は独立2名、定義上の改訂上限3回です。個別JobのPreviewではより小さい上限を選べます。[記事制作Workflowの運用](docs/runbooks/article-production.md)
- **Issue調査** — `engineering-issue-run` / `engineering-issue-investigation` は明示された対象と予算の範囲でIssue・ソース証拠を調査し、`ready`の結果を既存bounded Jobへ引き継げます。各Run/JobとGitHub書込みの承認は別です。[Issue調査runbook](docs/runbooks/engineering-issue-investigation.md)
- **Draft PR配信** — `ai-office workflows draft-pr` は全ReviewerのPASSと必要なlocal verificationが確認された `file-change-set.v1` ArtifactをDraft PRへ配信する実装です。GitHub書込みは別承認で、mergeは行いません。[Draft PR配信runbook](docs/runbooks/draft-pr-delivery.md)

### 限定的な実モデル実証

- **Issue #836:** 小規模な変更例は、単一Pythonファイルのクラスdocstringを1行変更した bounded Job です。独立Reviewer 2名のPASSとJob内local-effect AST検証を記録しています。実施範囲、Artifact/Evidence、PR・CIの詳細は[Issue #836の最終記録](https://github.com/toshio1822/ai-office/issues/836#issuecomment-6796860971)を参照してください。この例はJob内pytest/Ruffや一般的な自律修正の実証ではありません。
- **Issue #848:** 今回の `article-production` Job はWriter 1回でREADME候補Artifactを作り、独立Reviewer 2名がPASSしました（計3 Provider送信、改訂なし）。これは1件の記事制作Jobの実証であり、README.mdの変更やDraft PR配信を行ったものではありません。成果物・両Reviewer・送信Evidenceは[Issue #848の実行記録](https://github.com/toshio1822/ai-office/issues/848#issuecomment-6875972368)にあります。
- [Issue #802の最終受け入れ記録](https://github.com/toshio1822/ai-office/issues/802#issuecomment-6824220761)には初期MVPの受け入れとRepository CIの詳細があります。Repository CIは、個別Job内でpytest/Ruffを実行した証拠とは別です。

### offline 検証のみ

- fake Provider / temp Git / sandbox による Job 内 pytest/Ruff
- 既知 validation failure からの有限修正

これらはofflineの検証範囲です。実モデルJob内でpytest/Ruffを実行した証拠とは区別してください。

### 未実証 / 将来目標

以下は、上記の限られた事例からは広く実証されていません。

- 大規模変更や継続的な複数Job運用
- 多業務への一般化
- 実Provider/GitHubを通したIssue調査からJob・Draft PR・CIまでの一貫運用

[Issue-to-PRの製品設計](docs/issue-to-pr-product-design.md)は将来設計を扱う資料です。目標と現状を区別してください。

## 開発者向け導入と安全な CLI 確認

Python 3.12 以上を前提とします。開発用追加依存 `.[dev]` には pytest / Ruff を含みます。

```bash
python -m pip install -e '.[dev]'
```

以下のコマンドはローカルでの定義確認です。Job 実行ではありません。

```bash
ai-office --help
ai-office employees list
ai-office employees validate
ai-office workflows list
ai-office workflows validate
ai-office workflows plan article-production
```

特に `ai-office workflows plan article-production` は定義を検証して plan を表示する読み取り専用操作です。Provider を呼ばず、Run/Artifact を保存しません。plan を Job 実行方法と誤認しないでください。

実際の Job 実行には、別の Destination・モデル・入力・権限 preview/承認が必要です。この README の例から無断で live 送信しないでください。接続先については[OpenAI互換Destinationの説明](docs/openai-compatible-destinations.md)を参照してください。

## レビュー・承認・停止の基本

- **Artifact**: 成果物
- **独立レビュー**: `document-reviewer` と `evidence-auditor` が独立に確認する
- **有限改訂**: 記事 Workflow の改訂上限は 3
  - 通常 attempt 上限は `(R + 1) * (N + 1)`
  - 記事 Workflow では R=3、N=2 なので `(3 + 1) * (2 + 1) = 12`
- **現在の Reviewer 出力と形式訂正**
  - verdict は先頭行を `Verdict: PASS` / `Verdict: CHANGES_REQUIRED` / `Verdict: BLOCKED` のいずれかに固定
  - `CHANGES_REQUIRED` は番号付き Finding と、Requirement / Artifact location / Evidence source/reference / Impact / Requested correction の各項目を含む
  - `PASS` は `Unresolved material issues: none` と、番号付き Check（Requirement / Artifact location / Evidence source/reference / Assessment）を含む
  - Artifact location は読み手が分かる記述、Evidence source/reference は plain-language pointer でよい。正確な line number、逐語引用、`assignment:L...` 形式は要求されない
  - 判定形式訂正は既定で無効（0回）です。Job開始前のPreviewで `--max-review-verdict-corrections 1` を指定して事前承認された場合に限り、同じReviewerの判断内容を変えず判定行だけをJob全体で最大1回訂正します。Job開始後に後付けで有効化することはできず、事前承認がない形式違反では停止します
  - 事前承認済みの訂正送信もtotal attempt capに含まれ、HTTP retryとは別です。忠実に訂正できない場合、2回目の形式違反、`BLOCKED`、Provider結果不明では訂正・再送せず停止します
- **PASS の限界**: Workflow の PASS は人間受け入れ候補であり、公開許可でも意味的な正しさの証明でもない
- **検査の限界**: 構造・Artifact lineage 検査は、モデル審査の網羅性・真実性を証明しない。Runbook/Engine は判定・必須項目・入力 Artifact/Evidence の真正性/lineage を検査するが、文章の意味的裏づけ、assignment line/Artifact quote の一致、レビューの完全性/真実性は機械証明しない
- **安全停止**: BLOCKED、上限到達、結果不明は安全停止し、曖昧な結果を暗黙 retry しない

## 次に読む資料

- [製品目的・非目標・独立性](docs/product-vision.md)
- [製品機能一覧](docs/product-capabilities.md) — 2026-11-05基準のため、本READMEと固定mainの実装を優先してください
- [Engineの目標アーキテクチャ](docs/engine-target-architecture.md)
- [アーキテクチャ](docs/architecture.md)
- [開発ワークフロー](docs/development-workflow.md)
- [OpenAI互換Destination](docs/openai-compatible-destinations.md)
- [記事制作Workflow runbook](docs/runbooks/article-production.md)
- [Engineering Issue調査runbook](docs/runbooks/engineering-issue-investigation.md)
- [Draft PR配信runbook](docs/runbooks/draft-pr-delivery.md)
- [Issue-to-PR製品設計](docs/issue-to-pr-product-design.md) — 将来設計。目標と現状を区別する場合のみ参照

長大な過去 Phase 別実装記録は README に再録しません。