# AI Office 製品機能マップ・ギャップ分析・最小開発計画 v0.2

- **状態:** 製品方針確認済み（実装開始とIssueスコープ変更は別途承認）
- **調査基準:** `toshio1822/ai-office` main `09df23175dd16465f2bbc426c30e87298dde83a5`（2026-11-05確認）
- **対象:** AI Office単独で利用できる最初のソフトウェア修正業務。既存の`article-production`との共通性を維持する。
- **確定した製品設計:** [Issue→Draft PR 統合製品設計 v0.4](issue-to-pr-product-design.md)。**第1段階は固定Oracle＋PR後CI、第2段階はJob内pytest/Ruffと既知の不合格からの有限改訂**。
- **根拠:** 現行ソース・Workflow・CLI・テスト・runbook、および Issue #802 / #803 / #805とPR #826の実証記録。
- **注意:** 「実装済み」「オフラインテストあり」「実モデルで実証済み」は異なる。この文書の「未実証」を自動的に「未実装」とは扱わない。

## 1. 製品として提供する体験（最初の完成目標）

利用者はAI Office自身のCLIに**GitHub Issue URLと対象リポジトリ**を渡す。人間が固定base・探索/外部送信/変更可能範囲・有限予算を事前承認した後、AI Office自身がGit検索と**有限のAI追加調査**を行い、根拠を基に具体的な変更対象ファイルを選ぶ。選定対象が承認済み上限内なら逐次のファイル選択は求めない。AI OfficeがRun Inputを内部で確定し、既存の共通Workflowで変更・固定Oracleによる隔離検証・独立レビュー・レビュー指摘に基づく有限改訂を実施する。検証済み候補は**別のGitHub書込み承認**を受けてDraft PRへ配信し、CI結果と証跡を利用者へ提示する。Ready化・mergeは人間が判断する。

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
| C08 | Sandbox内でpytestとRuffを併用 | **実装済み（offline CLI E2E）** | 固定`python-tests-and-ruff` Oracle profileが、operator-pinned trusted bundleのABI/moduleを事前確認してからpytest/Ruffを既存Bubblewrap local executionで実行する。終了コード1だけを既知の検証不合格とし、その他の実行不能・不明は停止する。 |
| C09 | 検証済み候補をDraft PRへ配信 | **実装済み・実GitHub実証済み** | `workflows draft-pr`。PR #826のcommit/push/Draft PR、後のmain mergeとCI成功。Github書込みは別承認。 |
| C10 | 固定対象のGitHub Issue・ソース証拠取得 | **実装済み** | `collect-engineering-evidence`等。取得対象repo/commit/pathは人間が明示。任意の対象探索ではない。 |
| C11 | ツール名の定義・API用tool schema変換 | **実装済み** | `ToolCatalog`は定義解決とProvider payloadの生成。**モデルが要求したFileRead等を実行するランタイムではない。** |
| C12 | GitHub Issue URLから作業開始 | **有限自律継続まで実装済み（offline proof）** | `engineering-issue-run` がIssue URLから既存Runで有限調査を続け、`ready`後に既存Bounded Jobへ引き継ぐ。Runごとの承認、別Job承認、Draft PRの別承認を維持。実Provider・実GitHub製品実行は未実証。 |
| C13 | リポジトリを読み取り専用で探索し対象候補を提案 | **有限Workflow実装済み（live AI未実証）** | `engineering-issue-investigation` が根拠・引用・path候補を返す。追加資料は成功Runから次の通常Runとして有限準備し、Git bytes/scope/budgetを再検証する。 |
| C14 | 承認済み対象からRun Inputを自動構成 | **入力準備実装済み（offline proof）** | `prepare-engineering-job-input` が選択対象の固定base bytes/hash、既存bounded-change-review assignment、operator選択の登録policy profile由来local-effect policyを生成する。JSON/argv手組みやJob起動を求めず、既存承認は維持。 |
| C15 | 計画提示→人間承認→Job→納品の一貫した操作 | **有限調査から既存Jobまで実装済み（offline fake E2E）** | `engineering-issue-run` が承認付きの有限調査Runを続け、ready結果をJSON/IDの手動転記なしで既存Jobへ渡す。Job承認と別確認付き`draft-pr --issue-url`は維持。実Provider/製品Job/実GitHub配信は未実証。 |
| C16 | PR後のCIを候補の最終品質ゲートとして提示 | **第1段階実装済み（read-only経路）** | `engineering-issue-status --issue-url` が保存済みDraft PRと同一repo/base/正確なHEADを検査し、そのHEADにおける必須GitHub Actions `test` job（`.github/workflows/ci.yml`のpytest＋Ruff）のcheckだけを必須ゲートとして成功/失敗/進行中/不明に提示する。別checkの成功では代替せず、重複・欠落・取得不能は成功としない。CI失敗時のJob再実行・再送はしない。 |
| C17 | 仕事の進捗・停止理由・成果物を一画面/一コマンドで把握 | **部分実装** | `engineering-issue-run` が調査ラウンドと停止理由を出力し、Issue URL statusが既存Job・Artifact/Evidence・Draft PR・HEAD CIを集約する。調査Run履歴とJob/納品状態の一画面統合は未実装。 |
| C18 | 記事制作を同じEngineで実行 | **実装済み** | `article-production`が共通のbounded reviewを利用。記事の実モデルによる品質保証と、入力準備自動化は別の課題。 |

### 既存機能で特に注意する境界

- `collect-engineering-evidence`のパッケージ本文は秘匿・最大30,000文字への切り詰めがあるため、**生の`source_context`へ無条件に転記不可**。選択された固定Git revisionから原文bytesを取得し、別途SHA-256を照合する。
- Reviewerの`PASS`は候補品質の判断であり、GitHubへの書込み/merge承認ではない。
- GitHub CLIの`--yes`や同一OSユーザーの認証情報利用を、独立した本人認証境界とは呼ばない。
- 「GitHub CI成功」は「Jobのsandbox内でpytest/Ruffを実行した」という意味ではない。

## 3. 作る必要がある機能 — 優先順位

### P0-A: Issue受付・read-only調査・作業案の提示

**What:** AI Office自身がIssue URLとrepositoryを受け取り、固定baseでGit/AIによる有限調査を行う。AI社員は根拠・原因仮説・修正候補・必要な追加資料を提示し、承認済み範囲内なら追加調査を継続する。読取・外部送信・変更の上限は人間が事前承認する。Issue本文やソース内の指示を権限として扱わない。

**Why:** `source_context`を用意するために外部エージェントを必須とする現状をなくす。

**Constraints:** Gitと既存GitHub読み取り経路を再利用。無制限探索、一般的なtool-call agent、任意コマンド実行は前提にしない。**read scope・Provider送信範囲・write scopeは別**。AIは承認済み最大write範囲内で具体的な候補を選べるが、その上限を決定・拡張できない。予算や権限が不足すれば理由付きで停止する。

**Acceptance:** ファイル名未指定の小規模Python Issueで、AIが許可範囲内で追加調査し、根拠と具体的な変更対象・関連テストを選定できる。未承認ソースをProviderへ送らず、調査段階でGitHub/repoを書き換えない。

### P0-B: 承認済み計画→Run Input/実行スコープの確定

**What:** 人間が目的・criteria・base・最大read/write/send範囲・有限予算を承認し、AIがその範囲内で具体的な対象を選んだら、AI Officeが固定Git bytesから`source_context`と必要なハッシュ・実行ポリシーを組み立て、既存Jobへ渡す。内部JSONを利用者に編集させない。

**Why:** これまでのハッシュ計算、ファイル内容転記、policy/Job参照の突き合わせを製品の正常な準備工程にする。

**Constraints:** 既存Manifest、policy、preimage照合の不変条件は維持。生sourceとdigestの一致、許可範囲、利用者承認、モデル送信/ローカル効果の権限を分ける。既存の`collect-engineering-evidence`の加工済み本文を生sourceと誤用しない。

**Acceptance:** 承認済み上限内で選定した2ファイルのbase SHA/bytes/hashが固定Run Input・local-effect policyと一致し、利用者のJSON/ハッシュ転記なく既存bounded Jobを準備できる。許可範囲外の選定は停止する。

### P0-C: 一つの「仕事」として既存Job・Draft PR・CIを接続

**What:** 入力準備から既存bounded Job、candidateの確認、**別承認**による既存`draft-pr`の実行、PR/CI結果提示までを、AI Office自身の利用者向けCLIと状態表示から操作できるようにする。実際の実行は既存Job/Artifact/Evidence/配信機能を再利用する。

**Why:** 個別のCLIの呼び分けやID/成果物の手動受け渡しをなくす。

**Constraints:** 新しい実行エンジン・第二のJob状態権威・重複したArtifact保存基盤は導入しない。Provider・sandbox・GitHubの外部効果は各々明示承認。変更対象が変わった場合は再計画・再承認。CI失敗や結果不明は理由と証拠を表示し**暗黙の新Job/再送をしない**。

**Acceptance:** 人間が承認した仕事を開始すると、追加の手動JSON転記なしで既存Jobが完了して候補を提示できる。人間のGitHub書込み承認後にDraft PRが作られ、URL・HEAD・CI・変更ファイル・元のArtifact/Evidenceを一つの仕事として追える。mainへ自動mergeしない。

### P0-D: 実用上の回帰・権限境界を確認

**What:** 共通Engineを変更せずに、上記の利用経路と`article-production`の既存動作を観測可能なテストで確認する。必要な利用方法を短いドキュメントへまとめる。

**Acceptance:** ①成功Job→Draft PR、②write scope外提案の拒否、③Provider/ローカル検証失敗での停止、④PR送信結果不明でのread-only照合、⑤既存記事Workflowの回帰なし。通常はfake Provider/local Gitで確認し、実Provider/実GitHubはその都度別承認の小規模実証のみ。

### 第2段階（Issue C）— 必須の後続機能

- **Job内pytest/Ruff:** `python-tests-and-ruff` Oracle profile、operator-pinned trusted bundle、既存local executionを再利用して実行する。実モデルまたは製品配信の実証とは区別する。
- **既知の不合格からの有限自律修正:** pytest/Ruffの検証不合格をEvidenceとしてAIへ渡し、同じ承認範囲と予算で新Artifact→再検証→独立レビューを行う。実行不能・結果不明・予算超過は停止する。

### 別途実需が確認されたら検討

- **PR後CI失敗からの新たな改訂:** 第1・第2段階ともCI不合格は停止。自動でPRを更新し続ける機能は対象外。
- **記事制作の入口統合:** 同じ入力準備の責務を記事ブリーフにも適用するが、今回のIssue A/B/Cには含めない。

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

## 6. 開発順序（決定済み）

1. [統合製品設計 v0.4](issue-to-pr-product-design.md)と[開発ワークフロー](development-workflow.md)を基準に、まず文書整備PR #827をレビュー・main統合する。
2. **Issue A（第1段階）:** P0-A/Bの自律調査・Run Input自動準備を、既存読み取り・Job/認可機能の再利用で実装する。
3. **Issue B（第1段階）:** P0-C/Dの既存Job・固定Oracle・Draft PR・実HEAD CI表示・回帰検証・Runbookを一体で実装する。
4. **Issue C（第2段階）:** Job内pytest/Ruffと不合格からの有限改訂・再検証を実装する。
5. 各Issueは調査からDraft PR・最新HEAD CI確認までopenClawへまとめて委任し、ChatGPTがPRをレビュー、人間がmergeを判断する。実Providerや実GitHubへの製品副作用は都度別承認。

実装単位: [Issue A #828](https://github.com/toshio1822/ai-office/issues/828) → [Issue B #829](https://github.com/toshio1822/ai-office/issues/829) → [Issue C #830](https://github.com/toshio1822/ai-office/issues/830)。

**Issue #802は第1段階のみでcloseしない。** 第2段階のJob内pytest/Ruff・失敗からの自律修正を含め、原Issueの要求と照合した上で人間が判断する。

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
- **今後の機能契約（今回の範囲）:** [統合製品設計 v0.4](issue-to-pr-product-design.md)。これ以外の製品正本を増やさない。
- **設計上の不変条件（正本）:** [Engine Target Architecture](engine-target-architecture.md)。現在の機能一覧ではない。
- **実装と過去の経緯（参考）:** [Current Architecture](architecture.md)。過去のPhase構造を将来の実装に強制しない。
- **現行の運用手順:** [Runbooks](runbooks/) と [Destination guide](openai-compatible-destinations.md)。
- **歴史的な検証記録:** [Issue #805 proof](development/issue-805-offline-delivery-proof.md) と [Issue #807 proof](development/issue-807-offline-github-delivery-proof.md)。製品の最新配信手順はDraft PR runbookを使う。
- **過去の開発規約:** [Phase Implementation Contract](development/phase-implementation-contract.md)。新Phaseの追加要件にはしない。

**整理原則:** 過去の証跡・リンクを調べずに文書を削除・移動しない。READMEとCurrent Architectureの大量のPhase説明は、必要な参照を残して別途簡素化を検討する。重複の解消のために新しい正本を増やさない。
