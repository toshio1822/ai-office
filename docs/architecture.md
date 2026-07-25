# アーキテクチャ

## 構成

```text
definitions (YAML / Markdown)
        |
        v
validation
        |
        v
planning: ExecutionPlan -> StepExecutionRequest
        |
        v
invocation: ModelInvocationRequest
        ↓
OpenAIResponsesRequest
        │
        ├─ allowed_tool_names
        │       ↓
        │   tools: Tool Catalog
        │       ↓
        │   ToolDefinition
        │       ↓
        │   OpenAI Responses Tool Adapter
        │       ↓
        │   OpenAIResponsesFunctionTool
        │
        └──────────────┐
                       ↓
          OpenAI Responses Payload Adapter
                       ↓
             OpenAIResponsesPayload
                       ↓
    OpenAI Responses Dictionary Payload Adapter
                       ↓
       JSON-compatible Python dictionary
                       ↓
       OpenAI Responses JSON Serializer
                       ↓
                 JSON string
                       ↓
 OpenAI Responses HTTP Request Template Builder
                       ↓
      OpenAI Responses Authentication Boundary
                       ↓
    OpenAI Responses HTTPS Transport Boundary
                       ↓
    OpenAIResponsesRawHttpResponse
                       ↓
         future Response Boundary
                       ↓
             future OpenAI runtime

explicit environment mapping or current process environment
                       ↓
 OpenAI API Key Environment Acquisition Boundary
                       ↓
       OpenAIApiKey ───────────────→ Authentication Boundary
```

| 層 | 責務 |
| --- | --- |
| `definitions` | 社員・ワークフローのテキスト定義をモデル化する。 |
| `planning` | 検証済み定義を、順序・担当employee・step instructionsを明示した不変の実行計画へ変換し、1 step分の構造化実行要求を生成する。AI実行、状態、保存は扱わない。 |
| `invocation` | `StepExecutionRequest` を、モデル・system instructions・task instructions・allowed toolsだけからなるprovider非依存の不変なモデル呼び出し要求へ変換する。planning上のworkflow、step、employee文脈は持ち込まず、prompt結合、provider固有message形式、AI実行は扱わない。 |
| `providers.openai` | `ModelInvocationRequest` をOpenAI Responses API用の不変な実行前情報 `OpenAIResponsesRequest` へ純粋に変換する。`system_instructions` は `instructions`、`task_instructions` は `input` に対応し、文字列を結合・加工しない。`allowed_tool_names` は定義順の未解決tool名である。OpenAI Responses Tool Adapterは解決済み`ToolDefinition`を静的な`OpenAIResponsesFunctionTool`へ変換し、OpenAI Responses Payload Adapterは基本request情報と解決済みtool schemaを`OpenAIResponsesPayload`へ統合する。Dictionary Payload AdapterはそれをJSON互換Python辞書へ、JSON Serializerはその辞書を決定的なJSON文字列へ、HTTP Request Template Builderは非秘密headerを持つ未認証templateへ変換する。Authentication Boundaryは明示入力のAPI keyをBearer headerへ付加し、Environment Acquisition Boundaryだけが`OPENAI_API_KEY`を明示mappingまたはprocess environmentから取得する。HTTPS Transport Boundaryは認証済みrequestを1回だけ送信し、未解析raw responseを返す。credential persistenceやresponse解釈は扱わない。 |
| `engine` | 定義済みの状態遷移、検証、再試行を決定的に管理する。 |
| `runtime` | 実行状態とイベントを扱う。 |
| `storage` | JSON の状態、JSONL のイベント、ファイルの成果物を永続化する。 |
| `tools` | provider非依存の静的なTool Catalogを保持し、未解決tool名を完全一致で`ToolDefinition`へ決定的に解決する。`ToolDefinition`はHTTP payloadでも実行可能オブジェクトでもなく、provider schema変換、executor、Runtimeは後続Phaseで扱う。 |

## 境界と不変条件

- 実行時は、開始時点の定義を保存してから処理する。
- エンジンは定義にない遷移を作らない。
- 検証失敗は AI 実行前に報告する。
- 実行計画のstep順はworkflow YAMLの`steps`配列順だけで決まり、計画生成は定義を補正・並び替え・暗黙補完しない。
- 実行計画は元の定義モデルやファイル配置場所への参照を持たない。provenance、定義スナップショット、監査情報の保存は後続Phaseで扱う。
- 実行要求は実行アダプタへの不変の入力であり、runtime stateではない。元定義やファイル配置場所への参照を持たず、prompt組立、AI実行、tool解決、保存を扱わない。
- モデル呼び出し要求はprovider adapterへの不変の入力であり、model、分離されたsystem instructionsとtask instructions、定義順のallowed toolsだけを持つ。
- OpenAI Responses Adapterは純粋な変換層であり、`OpenAIResponsesRequest` はHTTP payloadでもwire formatでもない。model、instructions、input、未解決のallowed tool namesだけを保持し、SDK、認証、通信、tool schema解決、AI実行を扱わない。
- Tool Catalogはprovider非依存であり、`ModelInvocationRequest.allowed_tools`や`OpenAIResponsesRequest.allowed_tool_names`を置き換えない。未解決名を順序・重複そのままで`ToolDefinition`へ解決するだけで、provider schema、tool executor、Runtimeを扱わない。
- OpenAI Responses Tool Adapterは、`ToolDefinition`を`OpenAIResponsesFunctionTool`へ変換する純粋なprovider固有層である。tool typeは`function`、parameters typeは`object`、`additional_properties`と`strict`は`False`に固定し、propertiesとrequired名は順序・重複を保持したtupleで保持する。dict、JSON文字列、HTTP request bodyを生成しない。後続のrequest payload adapterだけがHTTP送信用のdictまたはJSON payloadを担当し、さらに後続のRuntimeがAPI呼び出しと結果処理を担当する。
- OpenAI Responses Payload Adapterは、`OpenAIResponsesRequest`と解決済み`OpenAIResponsesFunctionTool`を`OpenAIResponsesPayload`へ統合する純粋なprovider固有層である。payloadはmodel、instructions、input、toolsだけを保持し、未解決の`allowed_tool_names`、Catalog、HTTP情報、Runtime情報を保持しない。
- OpenAI Responses Dictionary Payload Adapterは、`OpenAIResponsesPayload`をJSON互換Python辞書へ決定的に変換する。toolsとrequiredはlistへ変換し、`additional_properties`は`additionalProperties`へ写像する。property名の重複は後の値で上書きするが、Python辞書の最初のkey位置を保持する。JSON文字列化、HTTP request body送信、API呼び出し、Runtimeは扱わず、future JSON serializerが文字列化を、future runtimeがHTTP通信、API呼び出し、response処理を担当する。
- OpenAI Responses JSON Serializerは、JSON互換Python辞書を変更せずに決定的なJSON文字列へ変換する。compact形式は不要な空白なし、pretty形式は2 space indentとし、入力dictの挿入順序を維持して`ensure_ascii=False`でUnicodeを保持する。`None`はJSONの`null`として表現する。後続のHTTP Request Template Builderがmethod、endpoint、非秘密headers、bodyを配置し、future runtimeがHTTP通信、API呼び出し、response処理を担当する。
- OpenAI Responses HTTP Request Template Builderは、Phase 11のJSON文字列を変更せず、`POST`、Responses endpoint、順序付きの非秘密`Content-Type` header、bodyからなる不変templateへ配置する。API key、Authorization、認証、HTTP通信、timeout、response処理は扱わない。後続のAuthentication Boundaryが認証情報を付加し、future HTTP Transportが通信・timeout・通信エラーを担当し、future Response Boundaryがresponseを受信・検証・解析する。
- OpenAI Responses Authentication Boundaryは、明示入力の不変`OpenAIApiKey`を使い、未認証templateの既存header順序を保持して最後にBearer Authorization headerを付加する。keyは通常の表現でマスクし、空文字列とCR/LFを拒否する。環境・設定ファイル・keyring・CLIからcredentialを取得せず、HTTP通信も行わない。future HTTP Transportが通信・timeout・通信エラーを、future Response Boundaryがresponseを受信・検証・解析する。
- OpenAI API Key Environment Acquisition Boundaryは、provider固有の`OPENAI_API_KEY`だけを読み、既存の`OpenAIApiKey`を返す。caller-supplied mappingがある場合はそれだけを参照し、`None`の場合に限りこのモジュール内でprocess environmentを参照する。値を変形せず、`.env`、設定ファイル、keyring、prompt、CLI、credential persistence、requestの自動認証、HTTP通信は扱わない。
- OpenAI Responses HTTPS Transport Boundaryは、認証済みrequestをPython標準ライブラリで同期的に1回送信し、status、reason、順序・重複を保持したheaders、未解析body bytesからなる不変raw responseを返す。completed HTTP statusは解釈せず返し、transport failureは秘密を含まないprovider固有エラーにする。connectionは成功・失敗のどちらでもcloseし、retry、redirect、timeout設定、response JSON parsing、tool実行、Runtimeは扱わない。
- 依存方向は `provider-independent invocation model -> provider-specific adapter -> provider-specific request model -> future runtime` とする。Runtimeからdefinitionsやplanningのモデルへ逆依存させない。
- provider共通抽象は、複数providerの実装から実際の共通点が確認されるまで作らない。Codex CLIは承認・sandbox・tool実行・agent loopを伴う実行基盤であるため、将来は別のAdapterとRuntime経路として検討する。
- 人間承認が必要な遷移は、承認済みの明示的な入力なしに進めない。
- 成果物とイベントは実行 ID に紐付け、後から検証できるようにする。

## 初期ディレクトリ

```text
src/ai_office/
  cli.py
  definitions/
  planning/
  invocation/
  providers/
    openai/
  engine/
  runtime/
  storage/
  tools/
employees/
workflows/
schemas/
```

`employees/` と `workflows/` はテキスト定義の配置場所であり、定義の読込・検証とCLIによる確認を提供する。`planning/` は検証済み定義から実行計画と、1 step分の構造化実行要求を生成する。`invocation/` は実行要求からprovider非依存のモデル呼び出し要求を生成する。`tools/` は未解決名を静的な`ToolDefinition`へ解決する。`providers/openai/` はモデル呼び出し要求をOpenAI固有の実行前モデルへ、解決済みtool定義を静的function tool schemaモデルへ、payloadをJSON互換Python辞書と決定的なJSON文字列へ、未認証HTTP request templateと認証済みtemplateへ変換し、`OPENAI_API_KEY`を限定された環境取得境界でだけ取得して、認証済みrequestを1回だけHTTPS送信してraw responseを返す。Response BoundaryとOpenAI Runtimeは今後のPhaseで扱う。
