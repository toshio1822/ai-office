# AI Office 統合製品設計 v0.4 — 自律調査からDraft PRまでの段階的開発

- 決定日: 2026-11-05
- 状態: **製品目標・段階・開発単位について人間合意済み**。個々の実Provider実行・外部書込み・mergeの承認ではない。
- 基準: `toshio1822/ai-office` main `09df23175dd16465f2bbc426c30e87298dde83a5`（2026-11-05確認）
- 関連: [親Issue #802](https://github.com/toshio1822/ai-office/issues/802)、[文書整備PR #827](https://github.com/toshio1822/ai-office/pull/827)、[Product Capabilities](product-capabilities.md)、[開発ワークフロー](development-workflow.md)
- 位置付け: 第1・第2段階の**製品機能と受け入れ条件の設計**。製品の目的はProduct Vision、不変条件はEngine Target Architecture、実装状況はProduct Capabilitiesを正本とする。既存の設計を無条件に正しいものとして扱わず、実装前に現行コードで照合する。

## 1. 完成目標と適用範囲

**人間がGitHub Issue URLを渡すと、AI Office自身のAI社員が原因を調査し、修正対象を選定し、実装・検証・独立レビューまで進める。** 成果物は人間の別承認によりDraft PRとして提出し、そのPRの正確なHEADに対するCI結果を確認できる。

AI Officeの実行にOpenClaw・Codexなどの外部AIエージェントを必須としない。AI Provider、Git、Python、GitHub CLI (`gh`)、Bubblewrap等の明示された依存は認める。人間にRun Input JSON、ソース本文、SHA-256、Job/Artifact IDの手動転記を要求しない。

初期適用は**小規模Pythonリポジトリ、既存ファイルの変更、固定Git commit、有限の読み取り/変更対象、固定検証コマンド**に限定する。すべてのIssueの原因特定や修正成功を保証しない。

### 段階の区分（決定済み）

| 段階 | 完成する製品体験 | 検証・修正の扱い |
| --- | --- | --- |
| **第1段階（Issue A+B）** | Issue受付 → 有限AI調査 → 対象選定 → Run Input自動生成 → 既存Jobの変更・独立レビュー → 別承認のDraft PR → PR実HEADのCI確認 | **PR前は承認済み固定Oracle**。Oracle不合格はEvidenceを残して停止。PR後のpytest/RuffはGitHub CIで確認し、CI不合格でも停止。Reviewerの指摘に基づく既存の有限改訂は利用する。 |
| **第2段階（Issue C）** | 第1段階の同じ経路で、PR前のテストと自律修正を強化 | **Job内pytest/Ruff**を実行し、既知のテスト不合格をAIへ引き継いで、承認予算内で新しいArtifactを有限改訂・再検証する。PR後CIも継続。 |

第1段階の完了だけで親Issue #802のJob内テスト・静的解析・検証失敗からの自律修正を完了扱いにしない。親Issueのcloseは第2段階後に他の受け入れ条件も照合して人間が判断する。

## 2. 利用者体験と承認

1. 利用者がIssue URLと対象リポジトリを指定する。既定の許可範囲・モデル・予算があれば提案として提示する。
2. **調査・実行前に**、固定base、参照可能範囲、Provider送信範囲、変更可能範囲、使えるOracle、モデル/試行/改訂/時間/資源予算を、人間が確認・承認する。承認不足の場合は効果を起こさず停止する。
3. AI Officeが許可範囲内で調査を進め、根拠を基に具体的なread/write pathsを選定する。承認済みの最大範囲内なら、ファイルを見つけるたびの逐次承認は不要。AIが上限を拡大してはならない。
4. 選定した対象と固定baseのソースから、製品がRun Input・実行ポリシーを作成し既存bounded Jobへ渡す。Business / Execution / local-effectの目的別権限は、操作画面をまとめても意味を混同せず記録・強制する。
5. Jobが停止理由またはレビュー済み候補を提示する。**GitHub書込みはここで別途**、正確な候補Artifact・対象repo/base/branch・変更対象を示して承認する。
6. 既存`workflows draft-pr`が新規branchに配信する。PR URLと実HEADに対応するCI実行の結論を表示する。Ready化・mergeは行わない。

`--yes`や同一OSユーザーのGitHub認証情報は独立した本人認証・権限隔離ではない。最初の版では別OSユーザーによる新たな特権分離基盤を作らず、この制約を運用手順に記載する。Issue本文、ソース中のコメント、AIの提案、ReviewerのPASSは認可の根拠にならない。

## 3. 自律調査の契約（第1段階・Issue A）

- **入力:** Issueの要求、固定Git base、事前承認された探索パス、Provider送信可能な資料、有限の読取バイト/ファイル数・調査回数・API予算。
- **調査:** 既存GitHub GET/標準Gitを使い、固定baseのファイル構成・検索結果・許可済みソースを取得。AI社員が原因仮説、証拠、候補、次に読むべきファイルを提案する。許可範囲内であれば追加資料を取得し、予算内で再検討する。
- **権限:** モデルの追加読取要求は**提案**であって実行許可ではない。外部送信は人間が許可した本文と範囲に限定する。探索可能範囲とモデル送信可能範囲、書込可能範囲はそれぞれ別に管理する。
- **終点:** 修正対象と関連テスト、原因仮説、検証可能な受け入れ条件を提示できたとき、具体的なread/write pathsを承認された最大範囲の部分集合として確定する。AIの原因仮説を事実として保証しない。
- **停止:** 原因・対象の候補を根拠付けられない、必要な追加資料が権限外、送信・探索上限到達、Provider結果不明の場合は、得られた事実と理由を提示して停止する。勝手に別repoへ移動したり、権限を拡大したりしない。

**一般的な無制限tool-call agentや新しい探索専用Engineは要件としない。** AIが追加資料を要求し、製品が許可を判定して取得する**有限の調査**が実現できれば十分。`ToolCatalog`に定義があることを実際のファイル読取ツール実行機能と混同しない。

## 4. 調査結果と既存Jobの境界（第1段階・Issue A/B）

- 調査結果は**判断材料**であって認可ではない。固定Git revisionから取得した実際のUTF-8ソースbytesを基に`source_context`を作る。既存`collect-engineering-evidence`の切詰め・秘匿加工済み表示を、生の`source_context`へ転記しない。
- Run Inputは既存の`request`、`repository.path/base_revision`、`acceptance_criteria`、`allowed_paths.read/write`、`source_context`等の契約と整合させる。ハッシュや関連manifest/policyの生成・照合はAI Office自身が実施する。取得したbytes・Git base・許可範囲・Job入力が一致しなければ開始しない。
- Job開始後のRun Input/Workflow/Employee/モデル・Destination/最大回数/権限を暗黙に変更しない。Source内容が変化したら再計画・再承認が必要。既存Run/Job/Artifact/Evidenceを正本に使い、同じ事実を所有する第二のDBやJob状態機械を作らない。
- `bounded-change-review`、bounded Job、既存の独立Reviewer/有限レビュー改訂、Bubblewrap local execution、Git/gh Draft PR配信を再利用する。開発業務のために新しい共通Engineや別publisherを作らない。
- 調査→Job→Artifact→PR→CIの紐付き情報を一つの利用者向け表示から追えるようにする。**既存の各正本を読み取り集約**し、独立した第二の状態権威を持たない。

## 5. 検証の契約

### 第1段階: 固定Oracle + PR後CI

- 修正の可否を判定するOracleは人間が事前に固定・承認した実行コマンドと期待条件。AIが変更できるテスト**だけ**を合格根拠にしない。
- Oracleは既存のlocal executionの隔離・固定argv・資源上限で実行する。合格が証明する範囲と、未実施の検証を区別する。Oracleの不合格、検証環境の実行不能、結果不明は停止。**Oracle失敗からの自動修正は第1段階では行わない。**
- Oracle成功後の独立Reviewer指摘は既存の有限改訂で扱える。最終候補について、固定OracleとReviewerの結果を対応するArtifact版で照合する。
- Draft PR配信後は実際のPR HEAD SHAと一致したGitHub CI（pytest/Ruff）をread-onlyで確認。未完了、失敗、対象SHA不一致を成功としない。CI失敗からの自動再Job・再pushは行わない。
- `Job候補成立`、`Draft PR作成`、`CI成功`は異なる状態・事実。PR作成だけで全検証合格やmerge可能を宣言しない。

### 第2段階: Job内pytest/Ruff + 有限自律修正

- 既存のSandboxで、承認済みの依存・コマンド・信頼できるテスト/静的解析を実行可能にする。既存trusted bundleにはpytestがなく、Ruff実行形式にも制限があるため、設定変更のみで完成と主張しない。
- **既知のテスト不合格**（検証コマンドが起動・終了し、終了コード等からテスト不合格が確定した状態）が発生したら、固定したOracle/pytest/Ruffの実行結果・制限付き出力・Artifact同一性をEvidenceに残し、AI社員へ渡す。新しいArtifact版で有限改訂・再検証し、成功すれば独立Reviewerへ進む。
- 検証コマンド自体が起動できない、実行結果不明、許可範囲違反、ソース同一性の破損、予算超過は**修正のための追加試行に転換せず停止**する。テスト失敗と実行失敗を混同しない。
- 親Issue #802の受け入れ基準を維持する。ただしPR後CI不合格からの自動PR更新は第2段階の対象外。

## 6. 正本、独立性、エラー方針

| 責務 | 所有するもの | 禁止事項 |
| --- | --- | --- |
| Product Vision / Architecture | 目的と共通Engineの不変条件 | 過去のPhaseを新機構の根拠にすること |
| Employee / Workflow | 職能、工程、引継ぎ、Reviewerの人数と順序、モデル選択 | 業務名/社員IDによるPython専用分岐 |
| 人間の承認 | 最大read/write/send範囲、実行予算、外部効果 | AI提案による権限の自己拡大 |
| 既存Run/Job/Manifest/Evidence/Artifact | 実行時の固定入力・認可・結果・履歴 | 新しい第二のJob状態保存・独立の証跡DB |
| 既存Draft PR / GitHub | 別承認後の新branch・Draft PR・CI | main直接変更、暗黙push/merge/Ready化 |

- **扱う失敗:** 許可外読取/送信/変更、base/ソース不一致、探索・Provider予算上限、Provider結果不明、Oracle不合格/実行不能、pytest/Ruff不合格/実行不能（第2段階）、GitHub書込み結果不明、CI失敗/HEAD不一致。
- **扱わない失敗:** 上位で成立している型や不変条件の重複防御、理論上だけの回復分岐、無関係な将来機能のfallback。
- **再試行:** Provider内部retryの一律禁止は不要。ただしワークフロー側で実際の送信を含めた有限総予算を制御し、不明な副作用の無条件再送はしない。GitHub書込み結果が曖昧なら既存のread-only照合を行う。
- **テスト:** 内部APIや呼び出し回数でなく、観測できる正常系、実際の失敗経路、副作用、権限の不変条件を確認する。

## 7. 実装Issue（大きな3件）と依存関係

| 順序 | 範囲 | 主な受け入れ条件 | 依存 |
| --- | --- | --- | --- |
| **[Issue A (#828)](https://github.com/toshio1822/ai-office/issues/828)** | 自律Issue調査、有限の追加資料取得、変更範囲の選定、承認後Run Input/実行ポリシーの自動準備 | ファイル名未指定の小規模Python Issueから、許可範囲内で追加調査して、正確なJob入力を人手のJSON/ハッシュ転記なしに作る | 本設計とPR #827のmain統合 |
| **[Issue B (#829)](https://github.com/toshio1822/ai-office/issues/829)** | Issue Aから既存Job・Reviewer・固定Oracle・Draft PR・PR実HEADのCIまでの統一操作、第一段階の受け入れテスト/Runbook | AI Office単独でIssue→候補→別承認→Draft PR→CIを追え、Oracle/CI不合格で停止し、記事Workflowに回帰がない | Issue Aがmainへ統合済み |
| **[Issue C (#830)](https://github.com/toshio1822/ai-office/issues/830)** | Job内pytest/Ruffと、不合格を原因とした有限Artifact改訂・再検証、第二段階の受け入れテスト/Runbook | Job内の実際のpytest/Ruff失敗を受けて有限修正・再検証・独立レビューが成功し、上限/権限外/実行不能では停止する | Issue Bがmainへ統合済み |

各Issueは**What / Why / Constraints / 受け入れ条件**を中心に記載し、How（内部API・関数・クラス・テスト内部構造）は原則としてopenClawに委ねる。Issue単位で調査・実装・関連テスト・Draft PR・latest-head CI確認まで依頼する。ChatGPTは完成PRの差分と外部から観測できる振る舞いをレビューし、人間がmergeを判断する。Issueだけを根拠に**実Provider/実Job/実リポジトリへの配信を実行する権限は付与しない**。開発用の作業ブランチcommit/push/Draft PRは該当Issueの明示的実装依頼に含められる。

## 8. 受け入れシナリオ

- **S1 第1段階正常系:** Issue URLのみでは具体的ファイル名が不明な修正依頼に対し、開始時の人間承認範囲内で追加Git/AI調査を実施。選定ファイルの固定ソースからRun Inputを作り、既存Jobが修正・固定Oracle・独立Reviewerまで完了。別承認でDraft PRを作り、PR HEADに一致するCIを表示する。OpenClawは製品実行に不要。
- **S2 調査継続・上限:** 初回検索で不十分な場合は許可範囲内で追加調査でき、範囲外・上限超過なら理由付き停止。
- **S3 権限・ソース固定:** モデルが未承認ソース/コマンド/ファイルを要求しても副作用を発生させない。読み取り範囲と送信範囲の違いを強制し、変更対象・baseの不一致を拒否する。
- **S4 第1段階の検証失敗:** 固定Oracle不合格・CI不合格をそれぞれ別に表示し、暗黙の改訂・再送を起こさない。PR不明結果ではread-only照合のみ実施する。
- **S5 共通性:** `article-production`と`bounded-change-review`が既存の共通Job/Artifact/Evidence/Approval/有限レビューを利用し続ける。
- **S6 第2段階の検証失敗からの修正:** 信頼できるテストで検出したpytest/Ruff不合格に対し、新しいArtifact・Evidenceを作り、有限の修正・再検証・独立レビューへ到達する。予算超過・実行不能は停止する。

通常はfake Provider、一時Git repository、擬似gh/GitHubと既存CIの観測で検証する。実Providerを使用した製品動作実証や実GitHubへの自動配信は、**別の対象・費用/送信上限・効果を示した承認**のもとでのみ行う。

## 9. 開発運用の正本

[開発ワークフロー](development-workflow.md)をAI Office自身の開発プロセスの正本とする。**全体設計→大きなIssue→openClawがIssueをまとめて実装・Draft PR化→ChatGPTがレビュー→人間がmerge判断**の順序を維持する。設計に不一致が判明したら、既存設計の複雑さも再評価してから正本を更新する。新しいPhase・helper・error typeの導入自体を成果にしない。

本文での設計合意は**実装を開始すること・Draft PRをmergeすること・実Provider APIを呼ぶことの承認ではない**。Issue Aからの実装は設計/ドキュメント変更のmain統合後に開始する。