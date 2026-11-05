# 開発ワークフロー

この文書は **AI Office 自体を開発する際の進め方** の正本である。AI Office が製品として実行する業務 Workflow の仕様ではない。

製品の目的は [Product Vision](product-vision.md)、現状・不足・優先順位は [Product Capabilities](product-capabilities.md)、Engine の不変条件は [Engine Target Architecture](engine-target-architecture.md) を参照する。矛盾する場合、過去の Phase 記録や以前の実装判断を根拠に新しい構造を正当化しない。

## 役割と判断権限

| 役割 | 担当すること | 原則として担当しないこと |
| --- | --- | --- |
| 人間（製品責任者） | 目標・優先順位・権限・設計変更・最終受け入れと merge の判断 | 関数やクラスの構成などの逐次判断 |
| ChatGPT（設計者・レビュアー） | 全体設計、What / Why / Constraints / 受け入れ条件、責務と不変条件、PR の成果物レビュー | 内部 API、関数分割、テスト内部構成の固定 |
| openClaw（実装者） | 既存コード調査、最小の実装方法の選択、変更、テスト、ブランチ管理、Draft PR、CI 結果の報告 | 製品目標や承認範囲の独断変更 |

**openClaw は AI Office の開発時の実装者であり、完成した AI Office の実行に必要な依存ではない。**

## 開発単位と進め方

1. **設計を先にまとめる。** ChatGPT は関連機能を一つの製品フローとして設計し、既存コード・ドキュメントとのギャップを確認する。実装方法を細かく固定しない。
2. **大きな機能単位の Issue にする。** 1 Issue は利用者から観測できる完成条件を持つまとまりとし、What / Why / Constraints / 受け入れ条件を中心に記載する。関数・内部 API・Phase 単位へ機械的に分割しない。
3. **openClaw は Issue を一貫して実装する。** 現行 main の確認、再利用可能な既存機能の調査、実装、振る舞いのテスト、作業ブランチへの commit / push、Draft PR 作成、対象 HEAD の CI 確認までを委任する。Issue の通常の内部実装判断については逐一 ChatGPT に質問しない。
4. **ChatGPT は Draft PR をレビューする。** 差分、設計の不変条件、受け入れ条件、テスト結果、既存機能への影響を確認する。内部構造・呼び出し回数だけを理由に過度な実装固定をしない。
5. **人間が merge と次の Issue への進行を判断する。** 不合格なら同じ Issue / PR 内で修正し、設計変更が必要なら先に合意する。main への直接変更・push、無断 merge はしない。

現時点の大きな実装単位は、**A: 自律的 Issue 調査と Run Input 自動準備**、**B: 既存 Job・独立レビュー・Draft PR・CI の統合**、**C: Job 内 pytest/Ruff とテスト失敗からの有限自律修正** の3件を基本とする。A・B は第1段階（固定 Oracle＋Draft PR 後の CI）、C は第2段階。これは開発上の区分であり、新しい Engine / Phase を3つ作る指示ではない。詳細な範囲は[統合製品設計 v0.4](issue-to-pr-product-design.md)と[Issue A #828](https://github.com/toshio1822/ai-office/issues/828)・[Issue B #829](https://github.com/toshio1822/ai-office/issues/829)・[Issue C #830](https://github.com/toshio1822/ai-office/issues/830)を正本とする。

## 設計・実装の原則

- **KISS / YAGNI:** 同じ保証をより単純に実現できないかを先に検討する。既存の Workflow / Run / Approval / Artifact / Evidence / Job を再利用・統合し、不要な構造の削除も検討する。
- **新しい構造は理由が必要:** 新しい Engine、Phase、adapter、wrapper、二重の状態保存・認可基盤、専用エラー型は、既存の責務では満たせない外部契約がある場合のみ導入する。「将来のため」「念のため」は理由にしない。
- **業務非依存:** 共通 Engine に Workflow ID・Employee ID・業務名を条件とする専用分岐を足さない。
- **失敗は実在するものだけ扱う:** 型・不変条件・上位層で保証された事項を重ねて検査しない。実際の失敗を検出し、原則として停止・上位伝播する。局所リカバリや fallback は明示された要件がある場合だけとする。
- **再試行は有限:** Provider 内部 retry の一律禁止は要求しない。ただし Workflow が総試行数・費用・対象範囲を制御し、結果不明の外部副作用を推測で再送しない。
- **テストは外部から観測できる動作を検証:** 正常系・実際に起こる失敗・権限境界・既存 Workflow の回帰を対象とする。内部の関数構成を固定するためのテストを増やさない。
- **Issue の受け入れ条件が完了点:** 条件を満たしたら停止し、先回りの機能追加・無関係なリファクタリングを行わない。

## 承認と停止点

- **開発用 Git 操作:** 「Issue を実装し Draft PR まで作成する」という明示的な依頼は、その Issue の作業ブランチに対する commit / push / Draft PR 作成の委任とする。別目的の Git 書込みや main の更新は含まない。
- **製品の実行に伴う外部副作用:** 実 Provider API の呼出し、AI Office による実 Job 実行、対象リポジトリへの変更配信、公開・merge 等は、開発用 Git 操作とは別に対象・上限・権限を示して明示承認を受ける。fake Provider・ローカル検証はこれらの承認と混同しない。
- **途中で止めて相談する条件:** Issue の目的や受け入れ条件を変更する必要がある、承認された read/write/Provider 範囲の拡大が必要、共通 Engine の不変条件を変更する必要がある、外部副作用の結果が不明、または必要な検証を実施できない場合。通常の実装判断では停止しない。
- **設計変更:** openClaw は必要性と代替案を短く報告し、ChatGPT が全体設計への影響を評価し、人間が決定する。合意後に必要な正本を更新する。過去の設計が過剰ならその設計自体を見直す。

## Draft PR の完了報告

openClaw は原則として1 Issue の完了時に、Issue / Draft PR URL、変更の要約、再利用・統合した機能、受け入れ条件ごとの結果、pytest / Ruff / CI の結果、最終 HEAD、未解決事項と外部副作用の有無を簡潔に報告する。

**ChatGPT のレビュー PASS は merge 許可ではない。** 最終的な merge は人間が判断する。第1段階の固定 Oracle・PR 後 CI と、第2段階の Job 内 pytest/Ruff は別の完成条件であり、未実施の検証を実施済みと記載しない。Issue #802 の Job 内テスト・静的解析の要件は、第1段階だけで完了扱いにしない。
