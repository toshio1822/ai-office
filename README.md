# AI Office

人間が定義した業務ワークフローを、明示的な状態遷移と検証のもとで AI が処理するための基盤です。

開発方針は [プロダクトビジョン](docs/product-vision.md) と [アーキテクチャ](docs/architecture.md) を参照してください。

## 開発

Python 3.12 以上が必要です。

```bash
python -m pip install -e '.[dev]'
pytest
ruff check .
ai-office --help
```

## 社員定義

社員定義は `employees/` 直下の `.yaml` または `.yml` ファイルに記載します。全ての定義は読み込み時に検証され、追加フィールドや不足フィールドは受け付けません。

```yaml
id: general-researcher
name: General Researcher
role: Gathers and organizes information for an assigned task.
instructions: |
  Work only on the assigned step.
  Separate confirmed facts from assumptions.
model: codex
allowed_tools: []
```

一覧表示と事前検証には次を使用します。`--directory` で別の定義ディレクトリを指定できます。

```bash
ai-office employees list
ai-office employees validate
ai-office employees list --directory path/to/employees
```

## ワークフロー定義

ワークフロー定義は `workflows/` 直下の `.yaml` または `.yml` ファイルに記載します。`steps` の配列順が実行計画上の順序です。

```yaml
id: research-and-summarize
name: Research and Summarize
description: Researches an assigned topic and produces a summary.

steps:
  - id: research
    name: Research
    employee: general-researcher
    instructions: |
      Gather and organize relevant information for the assigned topic.
```

一覧表示と事前検証には次を使用します。`--directory` と `--employees-directory` で別の定義ディレクトリを指定できます。

```bash
ai-office workflows list
ai-office workflows validate
ai-office workflows list --directory path/to/workflows --employees-directory path/to/employees
```

特定のworkflowの決定的な実行計画を確認するには、次を使用します。

```bash
ai-office workflows plan research-and-summarize
ai-office workflows plan research-and-summarize \
  --directory path/to/workflows \
  --employees-directory path/to/employees
```

`workflows plan` はworkflowを実行しません。AIを呼び出さず、実行状態・履歴・成果物も保存しません。表示する計画には、workflowの順序、stepの担当employee ID、step instructionsのみを含めます。employee instructionsとの結合は行いません。

特定stepの構造化された実行要求を確認するには、1始まりのstep indexを指定します。

```bash
ai-office workflows request research-and-summarize 1
ai-office workflows request research-and-summarize 1 \
  --directory path/to/workflows \
  --employees-directory path/to/employees
```

`workflows request` は実行要求を表示するだけで、AIやtoolを呼び出しません。employee instructionsとstep instructionsは別々に保持・表示し、状態、履歴、成果物は保存しません。

特定stepを将来のAIプロバイダーへ渡すための、provider非依存のモデル呼び出し要求として確認するには次を使用します。

```bash
ai-office workflows invocation research-and-summarize 1
ai-office workflows invocation research-and-summarize 1 \
  --directory path/to/workflows \
  --employees-directory path/to/employees
```

`workflows invocation` はmodel、allowed tools、system instructions、task instructionsを分離したまま表示します。AIやtoolの呼び出しはまだ行わず、promptの結合、状態、履歴、成果物の保存も行いません。

## OpenAI Responses Adapter（Phase 6）

provider非依存のモデル呼び出し要求を、OpenAI Responses API向けの実行前情報へ決定的に変換できます。

```bash
ai-office workflows provider-request openai research-and-summarize 1
ai-office workflows provider-request openai research-and-summarize 1 \
  --directory path/to/workflows \
  --employees-directory path/to/employees
```

このコマンドはOpenAI APIを呼び出さず、APIキーも不要で、課金も発生しません。表示する`OpenAIResponsesRequest`はHTTP payloadやJSON wire formatではなく、将来のOpenAI Runtimeへ渡す不変の実行前モデルです。tool名はまだOpenAI固有のschemaへ解決されていません。Codex CLI対応はこのPhaseの対象外です。

## Tool Catalog（Phase 7）

workflow stepのallowed tool名を、プロバイダー非依存の静的なtool定義へ解決できます。

```bash
ai-office workflows resolve-tools research-and-summarize 1
ai-office workflows resolve-tools research-and-summarize 1 \
  --directory path/to/workflows \
  --employees-directory path/to/employees
```

Tool名は完全一致で解決され、順序・重複・大文字小文字・空白を保持します。Tool Catalogは名前、説明、入力項目を確認するためのものであり、Catalogに登録されていてもtoolを実行できることを意味しません。`ToolDefinition`は実行可能オブジェクトやHTTP payloadではありません。OpenAI tool schema、JSON Schema、executor、Runtime、API、SDK、HTTP通信は未実装であり、APIキー不要で課金も発生しません。

## OpenAI Responses Tool Schema Adapter（Phase 8）

解決済みの`ToolDefinition`を、OpenAI Responses向けの不変なfunction tool schemaモデルへ決定的に変換できます。

```bash
ai-office workflows provider-tools openai research-and-summarize 1
ai-office workflows provider-tools openai research-and-summarize 1 \
  --directory path/to/workflows \
  --employees-directory path/to/employees
```

このコマンドは、静的な`OpenAIResponsesFunctionTool`を人間向けに表示するだけです。tool typeは常に`function`、parameters typeは常に`object`、`additional_properties`と`strict`は常に`False`です。toolとparameterの順序・重複・大文字小文字・空白・説明・型は保持します。dict payload、JSON文字列、HTTP request bodyは生成しません。`web_search`はOpenAI組み込みWeb Searchではなく通常のcustom function定義として扱い、`FileRead`も実行可能toolではありません。tool実行、OpenAI API、SDK、HTTP通信は行わないため、APIキーは不要で課金も発生しません。

## OpenAI Responses Payload Model（Phase 9）

OpenAI固有の基本request情報と、解決・変換済みのfunction tool schemaを、送信直前の不変な`OpenAIResponsesPayload`へ統合できます。

```bash
ai-office workflows provider-payload openai research-and-summarize 1
```

payloadには未解決の`allowed_tool_names`を含めず、解決・変換済みの`tools`だけを保持します。toolが0件でも`tools=()`を明示的に保持し、順序・重複・空白・改行・大文字小文字・Unicodeを加工しません。これはdict payload、JSON、HTTP request bodyではなく、API呼び出し、SDK、HTTP通信、APIキー、課金、tool実行、Runtime、response処理も伴いません。

## OpenAI Responses Dictionary Payload Adapter（Phase 10）

`OpenAIResponsesPayload`を、JSON互換のPython辞書へ決定的に変換できます。

```bash
ai-office workflows provider-dict-payload openai research-and-summarize 1
```

辞書のkey順序は保持され、toolsはtupleからlistへ、requiredはtupleからlistへ変換されます。`additional_properties`は`additionalProperties`へ変換し、property名は`properties`辞書のkeyとして使用します。同名propertyが複数ある場合は後の値が上書きしますが、keyの位置は最初の挿入位置を保持します。toolとrequiredの順序・重複は保持し、toolが0件でも`tools: []`、空propertiesは`{}`、空requiredは`[]`として明示します。

これはJSON文字列ではなく、`json.dumps`、HTTP request body送信、OpenAI API・SDK・HTTP通信、APIキー読込、課金、tool実行、Runtime、response処理は行いません。

## OpenAI Responses JSON Serializer（Phase 11）

JSON互換Python辞書を、決定的なJSON文字列へ変換できます。

```bash
ai-office workflows provider-json openai research-and-summarize 1
```

compact serializerは不要な空白なし、pretty serializerは2 space indentで出力します。いずれも入力辞書の挿入順序を保持し、`sort_keys=True`は使わず、`ensure_ascii=False`でUnicodeを保持します。tools、properties、requiredの順序と重複、空文字列、空list、空dict、JSONの`true` / `false`を保持します。改行・引用符・バックスラッシュはJSON仕様に従ってescapeされ、compact版とpretty版はparse後に同じ構造になります。CLIの`JSON payload:`以降はparse可能なpretty JSONです。

JSONファイル出力、HTTP request body送信、OpenAI API・SDK・HTTP通信、APIキー、課金、tool実行、Runtime、response処理は行いません。
