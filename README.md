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

## Approved Next-Step Preparation Boundary（Phase 26）

`prepare_approved_next_workflow_step()`は、Phase 25の`prepare_next_step` decisionと、そのcurrent/next step identityに完全に束縛された明示approvalだけを消費します。workflow、loaded history、decision、approval、selected employee definitionを再照合し、staleまたは不一致な入力は内容を露出しないsafe errorとして拒否します。

返す不変`PreparedWorkflowStep`は、選択済みstepとemployeeのidentity、employee instructions、step instructions、model、allowed tool namesを定義順のまま保持します。provider-specific request、credential lookup、tool resolution、実行、state mutation、event creation、persistence、retry、automatic continuation、paid CLI execution、GUIは行いません。

Phase 21 execution、Phase 22 transition、Phase 23 persistence、Phase 24 loading、Phase 25 decision、Phase 26 preparationは別々の明示呼び出しです。後続boundaryはprepared stepを正確なexecution requestとcontrolled running-state transitionへ変換できますが、executionやpersistenceを隠しません。

## Pure Prepared-Step Execution Start Boundary（Phase 27）

`prepare_prepared_step_execution_start()`はPhase 26の不変prepared stepとloaded historyを再検証し、既存の`ModelInvocationRequest`と、実行前に別boundaryで保存すべきproposed `running` stateを返します。completed step historyはそのまま保持し、failure categoryはclearします。`StepExecutionRequest`はprepared dataにないemployee name/roleを必要とするため、このPhaseで捏造しません。provider request、credential lookup、tool resolution、execution、event creation、persistence、retry、automatic continuation、paid CLI execution、GUIは行いません。

## Explicit Running-State Persistence Boundary（Phase 28）

`persist_prepared_running_state()`はPhase 27のproposed `running` stateだけを、caller suppliedなstate targetへ決定的JSONとして安全に置換保存します。保存はcallerがPhase 21を明示実行する前に完了しなければなりません。start eventは作成せず、runtime event fileは変更しません。provider execution、completed-result transition、completion/failure persistence、retry、automatic continuationは別の明示boundaryのままです。

## Persisted-Start Single-Step Execution Boundary（Phase 29）

`execute_persisted_start_openai_step()`は、Phase 27の`PreparedStepExecutionStart`、明示state target、検証済み`WorkflowDefinition`と`EmployeeDefinition`、resolved tools、API credential、paid-execution approvalを受けます。すべてのin-memory inputを検証してから、Phase 24の厳格なstate JSON parserでtargetをread-onlyに読み、Phase 27のproposed `running` stateと完全一致することを確認します。さらにworkflow ID、1-based current step index、current step ID、current employee IDを検証済みworkflowに照合します。その後だけ、workflow/stepの表示名をdefinitionから取得して既存の必須`StepExecutionRequest`を構築し、同一の`ModelInvocationRequest`を既存Phase 21 `execute_openai_runtime_step()`へ一度だけ渡します。

順序は `Phase 25 decision → Phase 26 approval/preparation → Phase 27 request + proposed running state → Phase 28 persist running state → Phase 29 verify persisted state + execute Phase 21 once → Phase 30 reload running state + Phase 22 transition + Phase 23 persistence → later explicit progression decision` です。Phase 29は明示承認済みの有料provider callを1回だけ行えますが、認証・transportの前にpersistenceを検証し、結果state/eventの保存、retry、自動継続、paid CLI、GUIを行いません。

## Executed-Step Transition Persistence Boundary（Phase 30）

`persist_executed_step_transition()`は、既存のPhase 21 runtime result、明示state target、明示runtime-event targetだけを受けます。stateを厳格にread-onlyで再読込し、`running` stateとruntime resultのidentityを検証してから、既存Phase 22 `transition_workflow_execution_from_step_result()`を一度だけ呼びます。互換性を確認したtransitionは既存Phase 23 `persist_workflow_execution_transition()`へ一度だけ渡され、最終stateと一つのeventを保存します。provider、credential、approval、tool、retry、次step選択・実行、自動継続、paid CLI、GUIは扱わず、Phase 23のcompensationをそのまま保持します。

## Persisted-Success Progression Decision Boundary（Phase 31）

`decide_persisted_success_progression()`は、検証済み`WorkflowDefinition`と明示state/event targetをread-onlyで受けます。既存Phase 24 history loaderでpersisted successと最新success eventを厳格に照合し、既存Phase 25 `decide_workflow_progression()`を一度だけ呼んで既存decisionをそのまま返します。承認、準備、persistence、provider実行、retry、自動継続、paid CLI、GUIは扱いません。順序は `Phase 25 → Phase 26 → Phase 27 → Phase 28 → Phase 29 → Phase 30 → Phase 31 persisted success reload + one Phase 25 decision → later explicit human approval/preparation or completion handling` です。

## Approved Next-Step Reentry Boundary（Phase 32）

`prepare_approved_next_step_reentry()`は、Phase 31から得た正確な`prepare_next_step` decision、新規に明示されたその同一next stepへの`NextStepPreparationApproval`、検証済みworkflow/employee、明示state/event targetを受けます。Phase 24でpersisted succeeded historyと最新`step_succeeded` eventをread-onlyで再読込し、workflow順序、decisionのcurrent/next identity、approvalを再検証してから、既存Phase 26 `prepare_approved_next_workflow_step()`を一度だけ呼びます。返却する既存`PreparedWorkflowStep`もdecisionと照合し、同一objectをそのまま返します。

順序は `Phase 25 progression decision → Phase 26 explicit approval/preparation → Phase 27 request + proposed running state → Phase 28 persist running state → Phase 29 verify persisted state + execute Phase 21 exactly once → Phase 30 transition + persistence → Phase 31 reload persisted success + Phase 25 decision exactly once → Phase 32 verify exact prepare_next_step decision + fresh human approval + Phase 26 preparation exactly once → later explicit Phase 27 start preparation` です。Phase 32はread-onlyで、provider request、running state、persistence、execution、retry、自動継続、paid CLI、GUIを作成しません。

## Prepared-Step Start Reentry Boundary（Phase 33）

`prepare_persisted_prepared_step_start()`は、正確なPhase 32 `PreparedWorkflowStep`、対応するemployee definition、workflow、明示state/event targetを受けます。Phase 24でpersisted successful historyをread-onlyで再読込し、prepared step のidentity、instructions、model、allowed toolsを定義と完全照合してから、既存Phase 27 `prepare_prepared_step_execution_start()`を一度だけ呼びます。返却された既存request/proposed running-state resultも照合し、同一objectを返します。

順序は `Phase 31 persisted success + Phase 25 decision → Phase 32 exact prepare_next_step + fresh approval + Phase 26 → Phase 33 exact PreparedWorkflowStep + Phase 27 → later explicit Phase 28 running-state persistence` です。Phase 33はread-onlyで、running stateの保存、provider実行、credentials/tool resolution、retry、自動継続、paid CLI、GUIを行いません。

## Prepared Running-State Persistence Reentry Boundary（Phase 34）

`persist_prepared_running_state_reentry()`は、正確なPhase 33 start result、workflow/employee、明示state/event targetを受けます。Phase 24でpersisted successを再読込してstart resultを検証し、既存Phase 28 `persist_prepared_running_state()`を一度だけ呼びます。呼出し後はstrict state reloadでproposed running stateとの一致、結果byte count、event targetのbyte-for-byte不変を確認して同一result objectを返します。provider、credentials/tools、実行、retry、自動継続、paid CLI、GUIは扱いません。

## Persisted-Running Execution Reentry Boundary（Phase 35）

`execute_persisted_running_openai_step()`は正確なPhase 33 start、persisted `running` state、workflow/employee、resolved tools、credential、paid approvalを受け、既存Phase 29を一度だけ呼びます。state bytes は実行前後で不変を確認し、注入依存による改変は補償復元します。実行結果は保存・transition・event appendをせず、そのまま返します。

## Executed-Result Transition Persistence Reentry Boundary（Phase 36）

`persist_executed_result_transition_reentry()`は、正確な既存Phase 21/35 runtime result、検証済み`WorkflowDefinition`、明示state/event targetだけを受けます。Phase 24 strict loaderでpersisted `running` stateを再読込し、workflow、current step、employee、completed-step history、runtime result identityを検証してから、既存Phase 30 `persist_executed_step_transition()`を一度だけ呼びます。返却するのは同一の既存`WorkflowExecutionPersistenceResult`です。呼出し後はstrict history reloadで、success/failure state、一つだけ追加されたruntime event、byte countを検証し、注入依存が契約を破った場合は両targetを呼出し前のbytesへ復元します。

`route_executed_result_transition_reentry()`（Phase 43）は、正確なPhase 42の`StepRuntimeExecutionSuccess`または`StepRuntimeExecutionFailure`を`persist_executed_result_transition_reentry()`へ正確に一度だけ渡し、同一の`WorkflowExecutionPersistenceResult`を返します。既存boundary内部でPhase 30 persistenceがterminal stateと一つのruntime eventを保存しますが、Phase 43自身はそれらを構築しません。正確な`workflow_complete` decisionはtargetをread-onlyで確認して無変更のまま返します。partial/invalid dependency writeは補償復元し、retry、自動継続、progression、workflow completion finalization、paid CLI/GUIは行いません。

## Executed-Result Transition Persistence Bridge Reentry Boundary（Phase 50）

`route_executed_result_transition_persistence_bridge_reentry()`は、正確なPhase 49 resultを一つだけ受けるbridgeです。runtime success/failureは既存Phase 43 `route_executed_result_transition_reentry()`へ正確に一度だけ委譲し、同じ`WorkflowExecutionPersistenceResult` objectを返します。`workflow_complete`と`persisted_failure`はstrict terminal state/historyをread-onlyで照合して同じobjectを返し、Phase 43を呼びません。runtime routeではPhase 43 の正確なstate transitionと一つのruntime eventだけを許し、不正・partial dependency writeは元bytesへ補償復元します。Phase 36/43の処理を複製せず、outcome classification、progression、next-step preparation/execution、retry、自動継続、completion/failure finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

```text
Phase 49
runtime success | runtime failure | workflow_complete | persisted_failure
    ↓
Phase 50
runtime success/failure → Phase 43 exactly once → same WorkflowExecutionPersistenceResult
workflow_complete → unchanged stop
persisted_failure → unchanged stop
    ↓
future explicit boundary
```

## Executed-Result Transition Persistence Phase Bridge Reentry Boundary（Phase 57）

`route_executed_result_transition_persistence_phase_bridge_reentry()`は、正確なPhase 56 resultを一つだけ受けます。正確なruntime success/failureだけを既存Phase 50 `route_executed_result_transition_persistence_bridge_reentry()`へ一度だけ渡し、同じ`WorkflowExecutionPersistenceResult` objectを返します。`workflow_complete`と`persisted_failure`はstrict terminal state/historyをread-onlyで照合し、Phase 50を呼ばず同じsupplied objectを返します。runtime routeではPhase 50が許す正確なtransition persistenceだけを許可し、不正・partial・unexpected dependency writeは元bytesへ補償復元します。Phase 50/43/36を複製せず、outcome classification、progression、次step準備・実行、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

```text
Phase 56
runtime success | runtime failure | workflow_complete | persisted_failure
    ↓
Phase 57
runtime success/failure → Phase 50 exactly once → same WorkflowExecutionPersistenceResult
workflow_complete | persisted_failure → unchanged stop
    ↓
future explicit boundary
```

## Persisted Terminal Outcome Classification Bridge Reentry Boundary（Phase 51）

`route_persisted_terminal_outcome_classification_bridge_reentry()`は、正確なPhase 50 resultを一つだけ受けるread-only bridgeです。`WorkflowExecutionPersistenceResult`だけを既存Phase 44 `route_persisted_terminal_outcome_classification_reentry()`へ正確に一度だけ委譲し、同じ`PersistedExecutionOutcome` objectを返します。`workflow_complete`と既存`persisted_failure`はstrict terminal state/historyをread-onlyで照合して同じobjectを返し、Phase 44を呼びません。依存によるtarget改変、例外、または不正な返却値は元bytesへ補償復元します。Phase 37/44を複製せず、classified outcomeの後続routing、progression、next-step preparation/execution、retry、自動継続、completion/failure finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

## Persisted Terminal Outcome Classification Phase Bridge Reentry Boundary（Phase 58）

`route_persisted_terminal_outcome_classification_phase_bridge_reentry()`は、正確なPhase 57 resultを一つだけ受けるread-only boundaryです。正確な`WorkflowExecutionPersistenceResult`だけを既存Phase 51へ一度だけ渡し、同じ`PersistedExecutionOutcome` objectを返します。`workflow_complete`と`persisted_failure`はstrict terminal state/historyを照合し、Phase 51を呼ばず同じsupplied objectを返します。全routeでtargetはread-onlyで、不正・malformed・unexpectedな依存改変は元bytesへ補償復元します。Phase 51/44/37を複製せず、classified outcomeの後続routing、progression、next-step preparation/execution、retry、自動継続、completion/failure finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

```text
Phase 57
persistence result | workflow_complete | persisted_failure
    ↓
Phase 58
persistence result → Phase 51 exactly once → same PersistedExecutionOutcome
workflow_complete | persisted_failure → unchanged stop
    ↓
future explicit boundary
```

```text
Phase 50
persistence result | workflow_complete | persisted_failure
    ↓
Phase 51
persistence result → Phase 44 exactly once → same PersistedExecutionOutcome
workflow_complete → unchanged stop
persisted_failure → unchanged stop
```

## Classified Persisted Outcome Routing Bridge Reentry Boundary（Phase 52）

`route_classified_persisted_outcome_bridge_reentry()`は、正確なPhase 51 resultを一つだけ受けるread-only bridgeです。`persisted_success`と`persisted_failure`だけを既存Phase 45 `route_classified_persisted_outcome_reentry()`へ正確に一度だけ委譲し、同じdependency result objectを返します。`workflow_complete`はstrict terminal historyを照合して同じobjectを返し、Phase 45を呼びません。依存のtarget改変は元bytesへ補償復元し、Phase 38の直接呼出し、next-step preparation/execution、retry、自動継続、finalization、paid CLI/GUIは追加しません。

## Approved Next-Step Preparation Bridge Reentry Boundary（Phase 53）

`route_approved_next_step_preparation_bridge_reentry()`は、正確なPhase 52 `prepare_next_step`に明示approvalと正確なnext employeeを要求し、既存Phase 32へ一度だけ委譲するread-only bridgeです。`workflow_complete`と`persisted_failure`はapproval/employeeなしで無変更のまま返します。approval作成・employee選択・running state保存・実行・retry・自動継続・finalization・paid CLI/GUIは行いません。

```text
Phase 42 result
  StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure | workflow_complete
    ↓
Phase 43 routing
  runtime result → existing transition reentry once → terminal state + one event
  workflow_complete → unchanged stop
    ↓
future explicit outcome routing
```

順序は `Phase 31 persisted success + Phase 25 decision → Phase 32 fresh approval + Phase 26 → Phase 33 exact PreparedWorkflowStep + Phase 27 → Phase 34 exact start + Phase 28 running-state persistence → Phase 35 strict running verification + Phase 29 exactly once → Phase 36 exact runtime result + Phase 30 transition persistence exactly once → later explicit persisted-history progression/failure handling` です。Phase 36はprovider実行、credential/tool resolution、retry、progression、自動継続、paid CLI、GUIを行いません。

## Persisted Execution Outcome Classification Reentry Boundary（Phase 37）

`classify_persisted_execution_outcome_reentry()`は、検証済み`WorkflowDefinition`と明示state/event targetをread-onlyで受け、既存Phase 24 history loaderを一度だけ使ってPhase 36の終端outcomeを厳格に再読込します。`succeeded`は`persisted_success`、`failed`は既存の安全なfailure categoryを含む`persisted_failure`として、不変の最小classification resultに分類するだけです。Phase 25とPhase 31は呼ばず、progression判断、次step準備、retry、workflow完了/finalization、provider実行、persistenceを行いません。

## Persisted Terminal Outcome Classification Routing Reentry Boundary（Phase 44）

`route_persisted_terminal_outcome_classification_reentry()`は、正確なPhase 43 `WorkflowExecutionPersistenceResult`をterminal state/event bytesに照合し、`classify_persisted_execution_outcome_reentry()`を正確に一度だけ呼んで同じ`PersistedExecutionOutcome` objectを返すread-only boundaryです。`workflow_complete`はPhase 37を呼ばず同じdecision objectを返します。Phase 38 routing、Phase 31 progression、次step準備、retry、自動継続、workflow completion finalization、paid CLI/GUIは行いません。

## Persisted Execution Outcome Routing Reentry Boundary（Phase 38）

`route_persisted_execution_outcome_reentry()`は、正確なPhase 37 outcome を明示 target に対して再分類し、field-for-field で照合します。`persisted_success`だけを既存Phase 31へ一度委譲して同じdecision objectを返し、`persisted_failure`はPhase 31を呼ばず同じsupplied outcome objectを返します。target bytes は各依存呼出し後に検証・必要時のみ復元します。次step準備、completion persistence/finalization、retry/recovery、provider実行、データ書込みは行いません。

## Classified Persisted Outcome Routing Bridge（Phase 45）

`route_classified_persisted_outcome_reentry()`は、正確なPhase 44 outcomeをterminal historyへ照合して`route_persisted_execution_outcome_reentry()`（Phase 38）へ正確に一度だけ渡すread-only bridgeです。`workflow_complete`はPhase 38を呼ばず同じdecision objectを返します。Phase 37やPhase 31を直接呼ばず、next-step preparation/execution、retry、自動継続、completion/failure finalization、paid CLI/GUIは行いません。

## Progression Preparation Routing Bridge（Phase 46）

`route_progression_preparation_reentry()`は、正確なPhase 45 result、`NextStepPreparationApproval`、`EmployeeDefinition`を明示入力として受けるread-only bridgeです。`prepare_next_step`だけを、同じapproval・employee・workflow・target objectsのまま`route_persisted_success_progression_reentry()`（Phase 39）へ正確に一度だけ委譲し、その同じprepared-step result objectを返します。`workflow_complete`と`persisted_failure`はapproval・employeeを使用せずPhase 39を呼ばず、同じ supplied objectを返します。依存がtargetを変更した場合は元bytesへ補償復元します。Phase 31/32を直接呼ばず、approvalの作成・変更、start/execution、running persistence、retry、自動継続、completion/failure finalization、paid CLI/GUIを行いません。

```text
Phase 45: prepare_next_step | workflow_complete | persisted_failure
    ↓
Phase 46: prepare_next_step → Phase 39 exactly once → existing PreparedWorkflowStep
          workflow_complete | persisted_failure → unchanged stop
    ↓
future explicit approval/start or finalization boundary
```

## Prepared-Step Start Routing Bridge（Phase 47）

`route_prepared_step_start_bridge_reentry()`は、正確な`PreparedWorkflowStep`だけを、caller suppliedの`WorkflowDefinition`、`EmployeeDefinition`、state/event `Path` objectsとともに`route_prepared_step_start_reentry()`（Phase 40）へ正確に一度だけ委譲するread-only bridgeです。返却された同じ`PreparedStepExecutionStart` objectをそのまま返します。正確な`workflow_complete`と`persisted_failure`はstrict terminal state/historyを検証してPhase 40を呼ばず、同じ supplied objectのまま停止します。依存がtargetを変更した場合は元bytesへ補償復元します。Phase 34を直接呼ばず、running-state persistence、runtime event append、provider/tool execution、retry、自動継続、workflow completion/failure finalizationを行いません。

```text
Phase 47: PreparedWorkflowStep → Phase 40 exactly once → existing PreparedStepExecutionStart
          workflow_complete | persisted_failure → unchanged stop
    ↓
future explicit running-state persistence or finalization boundary
```

## Prepared-Step Start Phase Bridge（Phase 54）

`route_prepared_step_start_phase_bridge_reentry()`は、正確なPhase 53 resultを一つだけ受けるread-only bridgeです。正確な`PreparedWorkflowStep`だけを、同一の`WorkflowDefinition`、`EmployeeDefinition`、state/event `Path` objectsのままPhase 47 `route_prepared_step_start_bridge_reentry()`へ正確に一度だけ委譲し、同じ`PreparedStepExecutionStart` objectを返します。`workflow_complete`と`persisted_failure`はemployeeを`None`にしてstrict terminal state/historyを照合し、Phase 47を呼ばず同じobjectを返します。依存のtarget改変、不正返却、例外は元bytesへ補償復元します。Phase 40/34を複製・直接呼出しせず、running-state persistence、provider/tool execution、retry、自動継続、terminal finalization、scheduler、loop、parallel execution、paid CLI/GUIを追加しません。

```text
Phase 53
PreparedWorkflowStep | workflow_complete | persisted_failure
    ↓
Phase 54
PreparedWorkflowStep + exact employee → Phase 47 exactly once → same PreparedStepExecutionStart
workflow_complete | persisted_failure → unchanged stop
    ↓
future explicit persistence or finalization boundary
```

## Prepared-Start Persistence Phase Bridge（Phase 55）

`route_prepared_start_persistence_phase_bridge_reentry()`は正確なPhase 54 resultを一つだけ受ける明示boundaryです。正確な`PreparedStepExecutionStart`だけを正確なemployeeとcaller suppliedの同一objectsのままPhase 48へ一度だけ渡し、提案済みの正確な`running` stateだけの永続化と同じ`RunningStatePersistenceResult` objectを許可します。`workflow_complete`と`persisted_failure`はemployeeを`None`としてPhase 48を呼ばず同じobjectで停止します。依存の不正改変・不正返却・例外は両targetを補償復元します。Phase 41/35を複製・直接呼出しせず、runtime event append、provider/tool実行、retry、自動継続、execution-result transition、terminal finalization、scheduler、loop、parallel execution、paid CLI/GUIを追加しません。

```text
Phase 54
PreparedStepExecutionStart | workflow_complete | persisted_failure
    ↓
Phase 55
PreparedStepExecutionStart + exact employee → Phase 48 exactly once
    → exact running-state persistence → same RunningStatePersistenceResult
workflow_complete | persisted_failure → unchanged stop
    ↓
future explicit provider-execution or finalization boundary
```

## Persisted-Running Execution Phase Bridge（Phase 56）

`route_persisted_running_execution_phase_bridge_reentry()`は正確なPhase 55 resultを一つだけ受ける明示boundaryです。正確な`RunningStatePersistenceResult`だけを、同一の元`PreparedStepExecutionStart`、matching employee、resolved tools、`OpenAIApiKey`、paid-execution approvalとともにPhase 49へ正確に一度だけ渡し、同じstep runtime execution resultを返します。`workflow_complete`と`persisted_failure`はexecution-only inputsをすべて`None`としてstrict terminal state/historyを検証し、Phase 49を呼ばず同じobjectで停止します。state/event targetはread-onlyで、依存の不正変更・例外は変更時だけ両targetを補償復元し、無変更エラーでは書き込みません。Phase 42/36/29/21やprovider、approval、credential、tool-resolution logicを複製・直接呼出しせず、execution-result transition/persistence、retry、自動継続、terminal finalization、scheduler、loop、parallel execution、paid CLI/GUIを追加しません。

```text
Phase 55
RunningStatePersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 56
RunningStatePersistenceResult + exact prepared start/employee/tools/credential/approval
    → Phase 49 exactly once → same step runtime execution result
workflow_complete | persisted_failure → unchanged stop
    ↓
future explicit transition or finalization boundary
```

## Prepared-Start Persistence Routing Bridge（Phase 48）

`route_prepared_start_persistence_bridge_reentry()`は、正確なPhase 47 resultを受ける明示bridgeです。正確な`PreparedStepExecutionStart`だけをcaller suppliedの`WorkflowDefinition`、`EmployeeDefinition`、state/event `Path` objectsとともに`route_prepared_start_persistence_reentry()`（Phase 41）へ正確に一度だけ委譲し、同じ`RunningStatePersistenceResult` objectを返します。許可される副作用は提案済みの正確な`running` stateをstate targetへ永続化することだけであり、event targetはbyte-for-byte不変です。`workflow_complete`と`persisted_failure`はstrict terminal state/historyを検証してPhase 41を呼ばず、同じ supplied objectのまま停止します。Phase 35またはPhase 41の処理を複製せず、runtime event append、provider/tool execution、retry、自動継続、execution-result transition、completion/failure finalization、paid CLI/GUIを行いません。

```text
Phase 47: PreparedStepExecutionStart | workflow_complete | persisted_failure
    ↓
Phase 48: PreparedStepExecutionStart → Phase 41 exactly once
          → same RunningStatePersistenceResult
          workflow_complete | persisted_failure → unchanged stop
    ↓
future explicit execution or finalization boundary
```

## Persisted-Running Execution Routing Bridge（Phase 49）

`route_persisted_running_execution_bridge_reentry()`は、正確な`RunningStatePersistenceResult`だけを、caller suppliedの実行入力とともに`route_persisted_running_execution_reentry()`（Phase 42）へ正確に一度だけ委譲し、同じruntime execution result objectを返します。`workflow_complete`と`persisted_failure`はすべての実行入力を`None`としたstrict terminal routeであり、Phase 42を呼ばず同じobjectを返します。Phase 36を直接呼ばず、target改変は補償復元し、transition persistence、runtime-event append、retry、自動継続、completion/failure finalization、paid CLI/GUIを行いません。

## Persisted Success Preparation Routing Reentry Boundary（Phase 39）

`route_persisted_success_progression_reentry()`は、Phase 38 からの正確なPhase 31 decision を再判定して全 field を照合します。`prepare_next_step`だけを明示 approval と employee を伴ってPhase 32へ一度委譲し同じprepared-step objectを返し、`workflow_complete`はPhase 32を呼ばず同じsupplied decision objectを返します。approval作成、実行、running state、completion persistence/finalization、retry、provider実行、データ書込みは行いません。

## Prepared-Step Start Routing Reentry Boundary（Phase 40）

`route_prepared_step_start_reentry()`は、正確なPhase 39 resultを受けるread-only boundaryです。`PreparedWorkflowStep`はcaller suppliedの正確なmatching employeeとともにPhase 34へ一度だけ委譲し、同じ`PreparedStepExecutionStart` objectを返します。正確な`workflow_complete` decisionはPhase 34を呼ばず同じdecision objectを返し、completion persistence/finalizationを行いません。proposed running stateやruntime eventを保存せず、provider、tools、credentialsを解決・実行せず、retry、自動継続、データ書込み、paid CLI/GUIも行いません。Phase 34呼出しの前後でstate/event target bytesを検証し、改変時は補償復元します。

```text
Phase 39
prepare_next_step → PreparedWorkflowStep
workflow_complete → WorkflowProgressionDecision
    ↓
Phase 40
PreparedWorkflowStep → explicit employee → Phase 34 → PreparedStepExecutionStart
workflow_complete → stop without completion persistence/finalization
    ↓
future explicit boundaries
prepared start: running-state persistence
workflow complete: completion persistence/finalization
```

## Prepared-Start Persistence Routing Reentry Boundary（Phase 41）

`route_prepared_start_persistence_reentry()`は、正確なPhase 40 resultを受け、`PreparedStepExecutionStart`だけをcaller suppliedの正確なemployeeとともにPhase 35へ一度委譲します。Phase 35の同じ`RunningStatePersistenceResult`を返し、提案済み`running` stateだけの永続化を許可します。runtime event targetはbyte-for-byte不変です。`workflow_complete`はPhase 35を呼ばず同じdecision objectを返します。provider実行、credentials/tools/approval、runtime event append、completed result transition、retry、自動継続、completion persistence/finalization、CLI/GUIは行いません。

## Persisted-Running Execution Routing Reentry Boundary（Phase 42）

`route_persisted_running_execution_reentry()`は、正確なPhase 41の`RunningStatePersistenceResult`を対応するPhase 33 `PreparedStepExecutionStart`、workflow、employee、明示実行入力と照合し、既存Phase 36を一度だけ呼びます。Phase 36を通じて最大一回の明示承認済みOpenAI executionだけを行えます。状態targetをstrict loaderで再読込してstartの`running` stateとbyte countを照合し、実行後はstate/event targetをbyte-for-byte検証します。変更されたtargetだけを呼出し前bytesへ補償復元し、同一の既存runtime resultを返します。`workflow_complete`は実行入力を受けずPhase 36を呼ばずに同じdecision objectを返します。credential取得、approval作成、結果保存、transition、event append、retry、自動継続、completion finalization、paid CLI/GUIは行いません。

```text
Phase 41
RunningStatePersistenceResult → exact start + explicit approved execution inputs
    → Phase 42 → Phase 36 once → same StepRuntimeExecutionResult
workflow_complete → stop unchanged
    ↓
future explicit runtime-result transition and terminal persistence
```
