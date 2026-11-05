# AI Office 製品機能マップ・ギャップ分析・最小開発計画 v0.1

- **状態:** 製品方針確認済み（実装開始とIssueスコープ変更は別途承認）
- **調査基準:** `toshio1822/ai-office` main `09df23175dd16465f2bbc426c30e87298dde83a5`（2026-11-05確認）
- **対象:** AI Office単独で利用できる最初のソフトウェア修正業務。既存の`article-production`との共通性を維持する。
- **根拠:** 現行ソース・Workflow・CLI・テスト・runbook、および Issue #802 / #803 / #805とPR #826の実証記録。
- **注意:** 「実装済み」「オフラインテストあり」「実モデルで実証済み」は異なる。この文書の「未実証」を自動的に「未実装」とは扱わない。

## 1. 製品として提供する体験（最初の完成目標）

利用者はAI Office自身のCLIに**GitHub Issue URLと対象リポジトリ**を渡す。AI Officeは読み取り専用でIssueと固定revisionのリポジトリを調べ、変更候補ファイル・目的・受け入れ条件・実行上限を提示する。人間が内容、読み取り/変更許可範囲、Provider/local-effect権限を承認した後、AI OfficeがRun Inputを内部で確定し、既存の共通Workflowで変更・隔離検証・独立レビュー・必要な有限改訂を実施する。検証済み候補は**別のGitHub書込み承認**を受けてDraft PRへ配信し、CI結果と証跡を利用者へ提示する。Ready化・mergeは人間が判断する。

**利用者がすること:** 業務の依頼、計画・権限の承認、異常時の判断、最終受け入れ。

**利用者に要求しないこと:** `source_context`の手入力、SHA-256計算、Run Input JSONの転記、各AI社員間の成果物コピー、OpenClawなど外部エージェントによる補助実行。

**「単独」の意味:** OpenClaw/Codex等の*外部AIエージェント*を必要としない。AI Provider、Python、Git、GitHub CLI、Bubblewrapなど明示した実行依存まで不要にする意味ではない。必要なプログラム・資格情報・実行可能なOS条件は事前に案内する。

**最初の適用範囲:** 小規模なPythonソース修正、固定されたGit revision、有限の対象ファイル/コマンド、既存のbounded review/revision。任意の規模・言語・秘密を含む全リポジトリの自律変更は対象外。

## 2. 現行機能の棚卸し

判定: **実装済み**=現行ソースに実行経路がある、**実証済み**=実Job/PRまたは明示したオフライン検証がある、**部分対応**=部品はあるが製品体験として未接続、**未実装**=要求された振る舞いの実行経路がない。

| ID | 利用者から見た機能 | 現状 | 根拠・不足の実態 |
|---|---|---|---|
| C01 | Employee・Workflowを設定で定義 | **実装済み** | `employees/*.yaml`、`workflows/*.yaml`、定義の検証/計画CLIがある。 |
| C02 | モデル・DestinationをStepごとに選択 | **実装済み** | 既存Workflow、CLIのStep上書き、Provider対応がある。 |
| C03 | 有限Jobの作成・実行、使用回数の制限 | **実装済み・実Job実証済み** | `bounded-job-run`、immutable Run/Job scope。#802 Job #02は3/6送信、retry 0。 |
| C04 | 独立複数Reviewer・有限改訂・再レビュー | **実装済み** | 設定で人数と回数を宣言。複数Reviewerの実モデルPASS、改訂経路はfake transport＋sandboxのオフラインテストで検証。実モデル改訂はJob #02では未発火。 |
| C05 | Artifact・Manifest・Evidence・出所を保存 | **実装済み・実Job実証済み** | Artifact lineageとlocal-effect証跡をJob #02で検証。 |
| C06 | ファイル変更を不変Artifactとして生成 | **実装済み・実Job実証済み** | Unified diff→`file-change-set.v1`変換。許可ファイルだけを扱う。 |
| C07 | 隔離されたローカル変更・固定コマンド検証 | **実装済み・対象Oracle実証済み** | Bubblewrap、承認済みargv、固定 read/write、リソース上限、local-effect Evidence。対象Oracleのexit 0は確認済み。 |
| C08 | Sandbox内でpytestとRuffを併用 | **現構成では不可** | 現trusted bundleにpytest依存なし。Ruff native executableはbundleのmode/コマンド制約と不一致。一般的な静的解析をJob内で完遂したとはいえない。 |
| C09 | 検証済み候補をDraft PRへ配信 | **実装済み・実GitHub実証済み** | `workflows draft-pr`。PR #826のcommit/push/Draft PR、後のmain mergeとCI成功。Github書込みは別承認。 |
| C10 | 固定対象のGitHub Issue・ソース証拠取得 | **実装済み** | `collect-engineering-evidence`等。取得対象repo/commit/pathは人間が明示。任意の対象探索ではない。 |
| C11 | ツール名の定義・API用tool schema変換 | **実装済み** | `ToolCatalog`は定義解決とProvider payloadの生成。**モデルが要求したFileRead等を実行するランタイムではない。** |
| C12 | GitHub Issue URLから作業開始 | **未実装** | 現行Job CLIは完成済みassignmentを要求。Issue URLを製品側の仕事依頼へ変換する入口がない。 |
| C13 | リポジトリを読み取り専用で探索し対象候補を提案 | **未実装** | operatorの固定`source_context`を読むだけ。Issueからコード候補を選定する経路なし。 |
| C14 | 承認済み対象からRun Inputを自動構成 | **未実装** | 生ソースの取得、`source_context`、`source_sha256`、revision、scope、criteriaの機械的な組立は外部作業。 |
| C15 | 計画提示→人間承認→Job→納品の一貫した操作 | **部分対応** | 既存CLIに個別機能はあるが、利用者に内部ID・JSON・コマンドの連携を求める。 |
| C16 | PR後のCIを候補の最終品質ゲートとして提示 | **部分対応** | GitHub Actionsでpytest/Ruffは実行可能、PR #826で成功。ただしJobからPR/CI結果を一貫した状態で提示する製品経路は未確立。 |
| C17 | 仕事の進捗・停止理由・成果物を一画面/一コマンドで把握 | **部分対応** | Jobのinspect、Workflowのresult、Artifact表示などはある。利用者向けの単一「仕事の状態」は不足。 |
| C18 | 記事制作を同じEngineで実行 | **実装済み** | `article-production`が共通のbounded reviewを利用。記事の実モデルによる品質保証と、入力準備自動化は別の課題。 |

### 既存機能で特に注意する境界

- `collect-engineering-evidence`のパッケージ本文は秘匿・最大30,000文字への切り詰めがあるため、**生の`source_context`へ無条件に転記不可**。選択された固定Git revisionから原文bytesを取得し、別途SHA-256を照合する。
- Reviewerの`PASS`は候補品質の判断であり、GitHubへの書込み/merge承認ではない。
- GitHub CLIの`--yes`や同一OSユーザーの認証情報利用を、独立した本人認証境界とは呼ばない。
- 「GitHub CI成功」は「Jobのsandbox内でpytest/Ruffを実行した」という意味ではない。

## 3. 作る必要がある機能 — 優先順位

### P0-A: Issue受付・read-only調査・作業案の提示

**What:** AI Office自身がIssue URLとrepositoryを受け取り、Issueの要求を読み取り、固定baseのリポジトリ構造から少数の変更候補/関連テスト候補を提示する。読取り対象、読み取り上限、Provider送信範囲を利用者に示す。Issue本文やソース内の指示を権限として扱わない。

**Why:** `source_context`を用意するために外部エージェントを必須とする現状をなくす。

**Constraints:** 最初はGitと既存GitHub読み取り経路を再利用。無制限探索、一般的なtool-call agent、任意コマンド実行は前提にしない。候補が確定できなければ不足情報を提示して停止する。**read scopeとwrite scopeは別**。AIがwrite権限を確定しない。

**Acceptance:** 新しい小規模Python Issueを入力すると、外部AIエージェントなしで目的と候補パスを提示できる。未承認のソース・シークレットをモデルへ送らず、GitHubやrepoを書き換えない。

### P0-B: 承認済み計画→Run Input/実行スコープの確定

**What:** 人間が目的・criteria・base・read/write paths・有限予算を承認したら、AI Officeが固定Git bytesから必要な`source_context`と`source_sha256`を組み立て、既存Jobへ渡す。内部JSONを利用者に編集させない。

**Why:** これまでのハッシュ計算、ファイル内容転記、policy/Job参照の突き合わせを製品の正常な準備工程にする。

**Constraints:** 既存Manifest、policy、preimage照合の不変条件は維持。生sourceとdigestの一致、許可範囲、利用者承認、モデル送信/ローカル効果の権限を分ける。既存の`collect-engineering-evidence`の加工済み本文を生sourceと誤用しない。

**Acceptance:** 承認した2ファイルのbase SHA/bytes/hashを再取得してRun Inputが決定的に一致する。許可範囲にないパスへ変更を要求した場合は停止する。外部手動スクリプトなしで既存のbounded Jobが開始できる。

### P0-C: 一つの「仕事」として既存Job・Draft PR・CIを接続

**What:** 入力準備から既存bounded Job、candidateの確認、**別承認**による既存`draft-pr`の実行、PR/CI結果提示までを、AI Office自身の利用者向けCLIと状態表示から操作できるようにする。実際の実行は既存Job/Artifact/Evidence/配信機能を再利用する。

**Why:** 個別のCLIの呼び分けやID/成果物の手動受け渡しをなくす。

**Constraints:** 新しい実行エンジン・第二のJob状態権威・重複したArtifact保存基盤は導入しない。Provider・sandbox・GitHubの外部効果は各々明示承認。変更対象が変わった場合は再計画・再承認。CI失敗や結果不明は理由と証拠を表示し**暗黙の新Job/再送をしない**。

**Acceptance:** 人間が承認した仕事を開始すると、追加の手動JSON転記なしで既存Jobが完了して候補を提示できる。人間のGitHub書込み承認後にDraft PRが作られ、URL・HEAD・CI・変更ファイル・元のArtifact/Evidenceを一つの仕事として追える。mainへ自動mergeしない。

### P0-D: 実用上の回帰・権限境界を確認

**What:** 共通Engineを変更せずに、上記の利用経路と`article-production`の既存動作を観測可能なテストで確認する。必要な利用方法を短いドキュメントへまとめる。

**Acceptance:** ①成功Job→Draft PR、②write scope外提案の拒否、③Provider/ローカル検証失敗での停止、④PR送信結果不明でのread-only照合、⑤既存記事Workflowの回帰なし。通常はfake Provider/local Gitで確認し、実Provider/実GitHubはその都度別承認の小規模実証のみ。

### P1: 実需が確認されてから着手するもの

- **Job内pytest/Ruff:** 現行sandboxではpytest依存とRuff native実行が課題。PR前に必須とする製品契約なら最小の実行環境改善を評価する。MVPでは固定Oracle＋Draft PR後のCIを明確に分ける。なお、候補が編集できるテストだけで合格判定しない。
- **CI失敗からの新たな改訂:** 現段階では失敗内容を提示して停止。自動改訂が実際のボトルネックになったときに、独立認可と有限予算を設計する。
- **記事制作の入口統合:** 同じ入力準備の責務を記事ブリーフにも適用する。記事ごとの成果品質の向上と混同しない。

### 現時点で作らないもの

新しいソフトウェア専用Engine、無制限のAI tool-call loop、業務ごとのadapter/Phase、別の証跡DB、CIへの無制限retry、AIによる変更権限の自己拡大、PRの自動merge、GUI、全言語/全規模リポジトリ対応。

## 4. 受け入れシナリオと完了判定

**S1 — 最小正常系:** AI Officeのみを起動し、GitHub Issue URLと対象repoを渡す → 小規模Python修正候補の読み取り/書き込み範囲・criteriaを提示 → 人間が承認 → Run Inputが自動確定 → 作者+独立Reviewerが固定Artifactを検証しPASS → 人間がGitHub書込みを承認 → Draft PRを作成 → CI結果と出所を利用者が確認できる。内部JSONの手作業やOpenClawは不要。

**S2 — 権限拒否:** モデルが承認外ファイルや未許可コマンドを要求しても実行しない。元のRun、リポジトリ、シークレットを変更せず理由を残す。

**S3 — 不確実/失敗:** Provider送信結果不明、Oracle失敗、CI失敗、PR結果不明などのうち実際に起こる経路で、再送/再実行/権限拡大をせず確認可能な停止理由を提示する。

**S4 — 共通性:** `article-production`とソフトウェア修正が同一のRun/権限/Evidence/Artifact/有限レビュー機構で動く。WorkflowとEmployeeの切替を、業務別Python分岐の追加で実現しない。

**完成判定:** **AI Office単独の入口からDraft PRとCI確認まで到達**できること。今回の#826は後半工程の実証に成功したが、前半のIssue→作業案→Run Input準備は外部オペレーションで補っていたため、製品のend-to-end完成とは扱わない。

## 5. 既存実装の簡素化・文書整備（機能追加とは分離）

- `README.md`（約5,900行）や`docs/architecture.md`（約7,100行）には過去Phase説明が多く、実装の現状把握が難しい。**行数だけで削除を決めない**が、利用者の入口・現在利用可能な機能・制約を短く案内できるよう整理する。
- READMEの「現行の選択可能な定義はarticle-production 1件」という記述は、実際の`bounded-change-review`の存在と不一致。製品機能表の「実装済み」「未実装」「実証済み」を一箇所に集約し、重複の追記をやめる。
- 新機能の前に既存API・状態遷移・Approval・Artifact管理が同じ責務を二重に持っていないか確認。**削除・統合で同じ振る舞いを保てる場合は新しいwrapperを作らない**。
- この文書は`product-vision.md`（目的）や`engine-target-architecture.md`（不変条件）を置き換えない。**実装状況と次に作る製品機能の単一の一覧表**として維持する。

## 6. 進め方と判断が必要な事項

1. まず本書で**最初の製品範囲**を人間が承認する。コードは変更しない。
2. P0-A/Bについて現行機能の再利用を検討し、必要な最小Issueを**まとめて**作る。細かいPhaseや関数ごとのIssueには分割しない。
3. P0-C/Dは既存`bounded-job-run`・`draft-pr`・CIの構成で実現し、新しいengineや状態保存を追加しない。最初はオフラインで振る舞いを確認。
4. 実Provider Jobや実GitHub配信は、対象・権限・費用/送信上限を提示して別途明示承認を得る。
5. P0の外部受け入れシナリオを満たした後に、#802の扱いを判断。原Issueにある「Job内でのpytest・静的解析」等を削るなら黙って完了扱いせず、親Issueとの差分を明記して人間がスコープ変更を決定する。

**人間の確認が必要な未決定点:** 最初の製品版で「固定Oracle＋PR後のCI」を正式な検証条件として認めるか、それとも「pytest・RuffまでJob内で完了」を必須とするか。この選択でP1の一部がP0へ移る。ほかは現行の小規模Python/GitHub Issueという範囲で進められる。

## 7. 主な根拠資料

- [Product Vision](https://github.com/toshio1822/ai-office/blob/09df23175dd16465f2bbc426c30e87298dde83a5/docs/product-vision.md)
- [Engine Target Architecture](https://github.com/toshio1822/ai-office/blob/09df23175dd16465f2bbc426c30e87298dde83a5/docs/engine-target-architecture.md)
- [CLI実装](https://github.com/toshio1822/ai-office/blob/09df23175dd16465f2bbc426c30e87298dde83a5/src/ai_office/cli.py)
- [bounded-change-review](https://github.com/toshio1822/ai-office/blob/09df23175dd16465f2bbc426c30e87298dde83a5/workflows/bounded-change-review.yaml)
- [独立レビュー/有限Job実装](https://github.com/toshio1822/ai-office/blob/09df23175dd16465f2bbc426c30e87298dde83a5/src/ai_office/engine/workflow_bounded_job.py)
- [local-effect/bwrap実装](https://github.com/toshio1822/ai-office/blob/09df23175dd16465f2bbc426c30e87298dde83a5/src/ai_office/engine/local_execution.py)
- [Evidence収集](https://github.com/toshio1822/ai-office/blob/09df23175dd16465f2bbc426c30e87298dde83a5/src/ai_office/engineering_evidence.py)
- [Draft PR配信](https://github.com/toshio1822/ai-office/blob/09df23175dd16465f2bbc426c30e87298dde83a5/docs/runbooks/draft-pr-delivery.md)
- [親Issue #802](https://github.com/toshio1822/ai-office/issues/802)、[子Issue #803](https://github.com/toshio1822/ai-office/issues/803)、[子Issue #805](https://github.com/toshio1822/ai-office/issues/805)、[実モデル変更PR #826](https://github.com/toshio1822/ai-office/pull/826)
## 8. ドキュメントの役割と整理対象

- **製品の目的（正本）:** [Product Vision](product-vision.md)。外部AIエージェント非依存、業務非依存、責務と非目標。
- **現在の実装・不足・優先順位（正本）:** 本書。基準日を明示し、実装進展時に更新する。
- **設計上の不変条件（正本）:** [Engine Target Architecture](engine-target-architecture.md)。現在の機能一覧ではない。
- **実装と過去の経緯（参考）:** [Current Architecture](architecture.md)。過去のPhase構造を将来の実装に強制しない。
- **現行の運用手順:** [Runbooks](runbooks/) と [Destination guide](openai-compatible-destinations.md)。
- **歴史的な検証記録:** [Issue #805 proof](development/issue-805-offline-delivery-proof.md) と [Issue #807 proof](development/issue-807-offline-github-delivery-proof.md)。製品の最新配信手順はDraft PR runbookを使う。
- **過去の開発規約:** [Phase Implementation Contract](development/phase-implementation-contract.md)。新Phaseの追加要件にはしない。

**整理原則:** 過去の証跡・リンクを調べずに文書を削除・移動しない。READMEとCurrent Architectureの大量のPhase説明は、必要な参照を残して別途簡素化を検討する。重複の解消のために新しい正本を増やさない。
