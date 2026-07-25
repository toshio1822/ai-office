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

compact serializerは不要な空白なし、pretty serializerは2 space indentで出力します。いずれも入力辞書を変更せずに挿入順序を保持し、`sort_keys=True`は使わず、`ensure_ascii=False`でUnicodeを保持します。tools、properties、requiredの順序と重複、空文字列、空list、空dict、JSONの`true` / `false`、`None`からJSONの`null`への変換を保持します。改行・引用符・バックスラッシュはJSON仕様に従ってescapeされ、compact版とpretty版はparse後に同じ構造になります。CLIの`JSON payload:`以降はparse可能なpretty JSONです。

JSONファイル出力、HTTP request body送信、OpenAI API・SDK・HTTP通信、APIキー、課金、tool実行、Runtime、response処理は行いません。

## OpenAI Responses HTTP Request Template（Phase 12）

Phase 11のcompact JSONを、未認証かつ不変のHTTP request templateへ配置できます。

```bash
ai-office workflows provider-http-request openai research-and-summarize 1
```

templateは`POST`、`https://api.openai.com/v1/responses`、順序付きの`Content-Type: application/json`、変更しないcompact JSON bodyだけを保持します。Authorization headerは含まず、API keyや環境変数を読まず、HTTP通信、API呼び出し、課金は発生しません。Phase 13のAuthentication Boundaryが明示入力のAPI keyを安全に付加します。

## OpenAI Responses Authentication Boundary（Phase 13）

明示的に渡された不変の`OpenAIApiKey`から、未認証HTTP request templateへBearer Authorization headerを追加できます。API keyは通常の`repr()`と`str()`でマスクされ、空文字列や改行を含む値は拒否されます。認証済みrequestも不変で、既存headerの順序を保持したままAuthorizationを最後に1件だけ追加します。

このPhaseにCLIはありません。CLI引数やoptionでのAPI key受取はshell historyやprocess inspectionで露出する可能性があるため、secure credential acquisitionは後続Phaseで設計します。環境変数・設定ファイル・keyringの読込、HTTP通信、API呼び出し、response処理、tool実行、Runtimeは実装していません。

## OpenAI API Key Environment Acquisition（Phase 14）

`OPENAI_API_KEY`だけを読み、既存の不変・マスク済み`OpenAIApiKey`へ変換できます。caller-supplied mappingを渡す場合はそのmappingだけを参照するため決定的にテストでき、`None`の場合だけこの取得境界内で現在のprocess environmentを参照します。値はstripや正規化をせず、空文字列とCR/LFはPhase 13の検証で拒否されます。

このPhaseにCLIはありません。credentialを表示・確認するだけのCLIには価値がなく、露出リスクを増やすためです。`.env`、設定ファイル、keyring、prompt、credential persistence、requestの自動認証、HTTP transport、response処理、tool実行、Runtimeは実装していません。

## OpenAI Responses HTTPS Transport（Phase 15）

認証済みの不変`OpenAIResponsesAuthenticatedHttpRequest`を、Python標準ライブラリの同期HTTPS接続で1回だけ送信し、不変の`OpenAIResponsesRawHttpResponse`としてstatus、reason、順序・重複を保持したheaders、未解析のbody bytesを返します。request headerはtuple順・重複を保持し、bodyはUTF-8へ1回だけencodeします。完了した3xx、4xx、5xxも解釈せずraw responseとして返し、transport failureは秘密値を含まないprovider固有エラーになります。connectionは成功・失敗のいずれでもcloseします。

このPhaseにCLIはありません。実際の有料API実行を、response validation、出力処理、Runtime、人間向けsafeguardより先に公開しないためです。retry、timeout設定、redirect、response JSON parsing、OpenAI error解釈、usage、tools、Runtimeは実装していません。

## OpenAI Responses HTTP Response Boundary（Phase 16）

Phase 15の不変raw HTTP responseをUTF-8で1回だけdecodeし、JSONを1回だけparseして、2xxでは不変success response、非2xxでは不変API-error responseへ分類します。successは`id`、`object: "response"`、非空`status`、`output` arrayを最小検証し、API errorは非空messageとstringまたはnullのtype、param、codeを検証します。payloadは完全に保持しつつ再帰的に不変化し、最初の`x-request-id`を大文字小文字非区別で取得します。

無効UTF-8、無効JSON、契約不一致はbodyやpayload内容を露出しない安全なinvalid-response errorになります。completed非2xxは例外化せずdataとして返します。このPhaseにCLIはありません。output text抽出、詳細output schema、usage、retry、tool handling、persistence、Runtimeは実装していません。

## OpenAI Responses Output Text Boundary（Phase 17）

Phase 16の不変`OpenAIResponsesSuccessResponse`から、対応するmessage output内の`output_text`だけを定義順に抽出し、不変`OpenAIResponsesOutputText`として返します。各textは空文字列、空白、改行、Unicodeを加工せずに`text_parts`へ保持し、`text`は区切り文字なしの連結です。対応しないoutput itemやcontent itemは無視しますが、messageの`content`や`output_text`の`text`が欠けるなど、対応すると主張する構造が不正な場合は安全な`OpenAIResponsesInvalidOutputError`になります。

このPhaseにCLIはありません。raw HTTP response、JSON decode・parse、API errorの解釈、credentials、HTTP通信、usage、tool handling、persistence、Runtimeは扱いません。

## Model Invocation Result Boundary（Phase 18）

OpenAI固有の安全なoutput text、API error、transport error、invalid-response error、invalid-output errorを、provider-independent かつ不変の`ModelInvocationSuccess`または`ModelInvocationFailure`へ正規化できます。successはresponse ID、request ID、status、text parts、textを無加工で保持し、failureは`api_error`、`transport_error`、`invalid_response`、`invalid_output`の安定したcategoryと安全な公開messageだけを保持します。API errorではrequest ID、HTTP status、provider error type、codeも保持しますが、payloadとparamはコピーしません。

このPhaseにCLIはありません。retryability、transient/permanent分類、runtime orchestration、persistence、state transitions、tool handling、usage/cost accounting、人間向け表示は未実装です。HTTP、JSON parsing、credentials、環境変数、OpenAI SDK、通信も扱いません。

## Explicit Paid-Execution Approval Boundary（Phase 20）

有料のOpenAI provider executionには、callerが明示的に作成した不変`ModelInvocationExecutionApproval`が必要です。approvalはprovider、非秘密の`approved_by`と`approval_id`、およびrequest model・system/task instructions・ordered allowed tools・resolved tool definitionsの決定的SHA-256 fingerprintに束縛されます。fingerprintはAPI key、headers、body、環境、responseを含まず、入力順序と重複を保持します。

`approve_model_invocation_execution()`は明示metadataと現在の入力からapprovalを作る純粋helperであり、自動承認、prompt、保存、時刻、有効期限、retryは扱いません。実行時はtool整合性を先に確認してからapprovalを検証するため、tool mismatchは`invalid_request`、false・provider不一致・stale fingerprint・空metadataは安全な`approval_required`結果になります。認証とtransportは有効なapprovalの後にだけ進みます。このPhaseにCLIはありません。

## Guarded OpenAI Provider Execution Boundary（Phase 19 + Phase 20）

明示的な`ModelInvocationRequest`、位置順まで一致する解決済み`ToolDefinition` tuple、明示的な`OpenAIApiKey`、明示的なapprovalを受け取り、既存のrequest、tool schema、payload、JSON、HTTP template、authentication、transport、response、output、result正規化の境界を順に合成できます。unapprovedな公開実行経路はありません。tool名がrequestの`allowed_tools`と位置・個数まで一致しない場合は、approval、認証や通信の前に`invalid_request`結果として安全に返します。

一致する入力では既存transportによるHTTPS requestをちょうど1回だけ実行し、success、API error、transport error、invalid response、invalid outputをprovider-independent な結果へ正規化します。このPhaseにCLIはありません。環境からのcredential取得、retry、tool execution、usage/cost、approval persistence、runtime state、CLIによる有料API実行は扱いません。

## Single-Step Runtime Execution Result Boundary（Phase 21）

`StepRuntimeExecutionInput`は、すでに準備済みの`StepExecutionRequest`、`ModelInvocationRequest`、解決済みtool tuple、明示approvalだけを保持する不変inputです。API keyは保持せず、OpenAI-backed `execute_openai_runtime_step()`へ別の明示引数として渡します。runtimeはmodel、employee instructions、step instructions、allowed toolsの完全一致をprovider認証・通信前に検証し、不一致は詳細を露出しない`invalid_request`のruntime failureに正規化します。

整合する入力では既存のapproval-gated OpenAI executionを一度だけ委譲し、`StepRuntimeExecutionSuccess`または`StepRuntimeExecutionFailure`へwrapします。workflow ID、step ID、既存の1始まりstep index、employee ID、および既存のprovider-independent invocation resultを無加工で保持します。state transition、次step選択、event/artifact保存、retry、tool execution、agent loop、CLI paid execution、GUIは実装していません。次の境界が、このresultを明示的なworkflow state transitionとevent recordingへ使用します。

## Pure Workflow State Transition and Runtime Event Boundary（Phase 22）

`WorkflowExecutionState`は、明示的な単一stepのworkflow ID、current step identity、ordered completed step IDs、有限status、last failure categoryだけを保持する不変stateです。`build_running_workflow_execution_state()`は既存の`StepExecutionRequest`から、1始まりstep indexを変換せずに`running` stateを作ります。

`transition_workflow_execution_from_step_result()`はcompleted `StepRuntimeExecutionResult`を既存`running` stateと完全一致で検証し、1つの`WorkflowExecutionTransition`（previous state、next state、runtime event）を返します。successは`running -> succeeded`、current step IDを一度だけappendし、empty output textを含む安全なsuccess metadataを保持します。failureは`running -> failed`、completed IDsを変えず、既存failure categoryとsafe messageを保持します。ここでの`succeeded`は明示的な単一step execution stateの完了であり、multi-step workflow全体の完了を意味しません。

このPhaseは時刻、event ID、永続化、JSON/JSONL、next-step selection、retry、provider実行、tool execution、agent loop、CLI paid executionを扱いません。Phase 21 executionとPhase 22 transitionは別々の明示呼び出しです。次の境界が返されたstateとeventをatomicまたはcompensatableに保存します。

## Compensatable State and Event Persistence Boundary（Phase 23）

`persist_workflow_execution_transition()`は、完成済み`WorkflowExecutionTransition`をcaller suppliedな2つのpathへ保存します。next stateは決定的UTF-8 JSON（1 newline）、runtime eventは決定的compact UTF-8 JSONL record（正確に1 newline）です。state/event identity、status、target pathの整合性を先に検証し、completed step order、duplicate IDs、Unicode、empty output、`None`をそのまま保持します。

保存前に両targetの存在と正確なbytesを捕捉し、state replacementの後にevent appendを行います。扱うfilesystem failureで部分更新が発生した場合は、events、stateの決定順で両targetを元のbytesへ復元し、元は存在しなかったtargetを削除します。rollback自体が失敗しても両復元を試み、distinct safe rollback errorを返します。これはin-process compensationであり、二つのfileに対するcrash-safe transactionではありません。

このPhaseはlocks、fsync guarantee、database、crash recovery、state/event load、next-step orchestration、retry、provider実行、tool execution、CLI paid execution、GUIを扱いません。Phase 21 execution、Phase 22 transition、Phase 23 persistenceは別々の明示呼び出しです。

## Strict State and Event Loading Boundary（Phase 24）

`load_workflow_execution_history()`は、Phase 23の明示的なstate/event target pathsをread-onlyで読み、`LoadedWorkflowExecutionHistory`として不変の`WorkflowExecutionState`と順序付き`RuntimeStepEvent` tupleを再構築します。state JSONとevent JSONLはUTF-8、完全なfield集合、厳格な型、有限status/category、event意味制約を検証し、duplicate key、未知field、欠損field、blank JSONL record、truncated recordを拒否します。regular fileを指すsymbolic linkは許可し、directory targetは拒否します。最後のeventはstateのidentity、status、failure category、completed stepと照合されます。空event fileは`ready`または`running` stateだけで許可します。

この読み込みはrepair、migration、書込み、automatic resume、next-step selection、retry、tool/provider execution、paid CLI execution、GUIを行いません。Phase 21 execution、Phase 22 transition、Phase 23 persistence、Phase 24 loadingはいずれも別の明示呼び出しです。後続のboundaryが、成功した読み込み結果がhuman-approvedなcontrolled next-step preparationに適格かを判断します。

## Pure Workflow Progression Decision Boundary（Phase 25）

`decide_workflow_progression()`は、検証済み`WorkflowDefinition`と読み込み済みの不変`LoadedWorkflowExecutionHistory`を受け、stateのworkflow/step/employee identityとcompleted step順序を現在の定義へ安全に照合します。不一致またはstaleな履歴は、内容を露出しない互換性errorとして拒否します。

返す不変`WorkflowProgressionDecision`は、`prepare_next_step`、`workflow_complete`、`stopped_failed`、`not_progressable`のいずれかです。成功した非終端stepでは定義上ただ1つの直後stepだけを選び、ready/runningは自動開始・再開せず、failedはretryせず、最後の成功stepはstateを書き換えず完了として知らせます。decisionはstateを変更せず、request構築、approval作成、provider/tool実行、persistence、paid CLI execution、GUIを行いません。

Phase 21 execution、Phase 22 transition、Phase 23 persistence、Phase 24 loading、Phase 25 decisionはすべて別々の明示呼び出しです。後続boundaryだけが、明示承認された`prepare_next_step` decisionを決定的な次step preparation requestへ変換できますが、実行はしません。
