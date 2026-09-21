# AI Office 開発ガイド

GitHub Issue を仕様の正本として扱う。作業前に対象 Issue、関連するドキュメント、既存コード、テストを確認すること。

## 作業原則

- Issue の完了条件を満たす最小の変更だけを実装する。
- ワークフローの順序や人間承認を暗黙に補完しない。
- 定義の不備は実行前に検出できる設計を優先する。
- 実行時には定義、入力、プロンプト、生応答、成果物、イベントを追跡可能にする。
- retry、replay、自動継続、外部副作用を暗黙に実行しない。必要な場合は、その許可条件と停止条件を外部から観測可能な契約として明示する。
- 対象外の機能を先行実装しない。仕様が不足する場合は推測で広げず、未解決事項として報告する。
- 既存の別プロジェクトのコードをコピーしない。必要な知見は、このプロジェクトの目的に合わせて再設計する。
- 保存済みの実行データ、イベント、成果物は、対象 Issue で明示された場合を除き変更しない。
- コミット、push、Pull Request 作成は、人間から明示的な許可を得た場合だけ行う。

## 単純性とアーキテクチャ境界

- Phase 番号は開発履歴を識別するためのものであり、runtime architecture の階層や永続的な public API を意味しない。
- 新しい Phase、boundary、adapter、wrapper、helper、public model を追加する前に、既存構造の統合・簡素化・再利用で同じ保証を実現できないか確認する。
- 新しい boundary は、新しい責務を持つ場合に限る。代表例は、状態遷移の所有、永続化の所有、外部副作用の制御、人間承認、または外部 trust boundary である。
- 既存値を再検証して別の内部関数へ委譲するだけの wrapper は、独立した責務や外部契約がない限り追加しない。
- 安全性のための防御は、外部入力、永続化、provider、transport、filesystem、human approval などの実際の trust / side-effect boundary に集中させる。信頼済み内部関数間の全 hop を外部境界として扱わない。
- dependency injection は、I/O、外部副作用、時刻・乱数などの非決定性、交換可能な実装ポートに優先して使う。内部 call graph をテストするためだけに public dependency seam を増やさない。
- public export は利用者が依存すべき安定契約に限定する。過去 Phase の内部境界を、存在するという理由だけで新しい上位コードから参照しない。
- 既存テストや既存 Phase の存在だけを理由に構造を温存しない。より単純な構造で同じ外部保証を保てる場合は、既存テストを含めて技術的負債として見直してよい。

## テスト原則

- テストは、外部から観測できる振る舞い、状態遷移、永続化結果、外部副作用、停止条件、不変条件を優先して検証する。
- provider送信、external publication、one-use claim、永続化など、副作用安全性に直接関係する exactly-once / at-most-once / zero-call は重要な契約として検証する。
- pure helper や信頼済み内部関数について、特定関数への委譲回数、引数順、Python object identity、default dependency object、source code の文字列・AST形状を、外部契約上の理由なしに固定しない。
- object identity が本当に必要なのは、同一objectの保持自体が外部契約または副作用所有権に意味を持つ場合だけとする。それ以外は値・状態・副作用の等価性を優先する。
- subclass rejection や exact runtime type を要求する場合は、それが serialization、安全性、trust boundary、互換性に必要な理由を明示する。
- refactoringで振る舞いが変わらないのに壊れるテストは、実装詳細を固定している可能性を疑う。

## 実装手順

1. Issue の目的、外部契約、対象範囲、対象外、完了条件を確認する。
2. `docs/` と既存実装を読み、既存の責務と再利用可能な境界を確認する。
3. 新しい構造を追加する前に、より単純な構造で同じ保証を実現できないか検討する。
4. `src/` レイアウトを守り、公開 CLI は `ai-office` に追加する。
5. 観測可能な振る舞いと不変条件をテストで確認する。
6. `pytest` と `ruff check .` を実行し、失敗を解消する。
7. 変更ファイル、設計判断、簡素化判断、テスト結果、未解決事項、完了条件の充足状況を報告する。

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
