# アーキテクチャ

## 構成

```text
definitions (YAML / Markdown)
        |
        v
validation -> planning -> engine -> runtime -> storage
                  |
                  v
                tools
```

| 層 | 責務 |
| --- | --- |
| `definitions` | 社員・ワークフローのテキスト定義をモデル化する。 |
| `planning` | 検証済み定義を、順序・担当employee・step instructionsを明示した不変の実行計画へ変換する。AI実行、状態、保存は扱わない。 |
| `engine` | 定義済みの状態遷移、検証、再試行を決定的に管理する。 |
| `runtime` | 実行状態とイベントを扱う。 |
| `storage` | JSON の状態、JSONL のイベント、ファイルの成果物を永続化する。 |
| `tools` | AI 実行方式などの外部機能との境界を提供する。初期実装の候補は Codex CLI。 |

## 境界と不変条件

- 実行時は、開始時点の定義を保存してから処理する。
- エンジンは定義にない遷移を作らない。
- 検証失敗は AI 実行前に報告する。
- 実行計画のstep順はworkflow YAMLの`steps`配列順だけで決まり、計画生成は定義を補正・並び替え・暗黙補完しない。
- 実行計画は元の定義モデルやファイル配置場所への参照を持たない。provenance、定義スナップショット、監査情報の保存は後続Phaseで扱う。
- 人間承認が必要な遷移は、承認済みの明示的な入力なしに進めない。
- 成果物とイベントは実行 ID に紐付け、後から検証できるようにする。

## 初期ディレクトリ

```text
src/ai_office/
  cli.py
  definitions/
  planning/
  engine/
  runtime/
  storage/
  tools/
employees/
workflows/
schemas/
```

`employees/` と `workflows/` はテキスト定義の配置場所であり、定義の読込・検証とCLIによる確認を提供する。`planning/` は検証済み定義から実行計画を生成する。`schemas/`、実行エンジン、runtime、storage、toolsは今後のPhaseで扱う。
