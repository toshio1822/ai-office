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

## Classified Persisted Outcome Routing Phase Bridge Reentry Boundary（Phase 59）

`route_classified_persisted_outcome_routing_phase_bridge_reentry()`は、正確なPhase 58
`PersistedExecutionOutcome`または`workflow_complete` decisionを一つだけ受けるread-only
boundaryです。`persisted_success`と`persisted_failure`だけを、同じresult、workflow、state/event
`Path` objectsのまま既存Phase 52へ正確に一度だけ委譲し、Phase 52の正確なdecisionまたは同じ
failure objectを返します。`workflow_complete`はPhase 52を呼ばず、strict terminal state/history
とtargetのbyte-for-byte不変を確認して同じdecisionで停止します。依存のtarget改変、不正返却、
safe error、unexpected errorは安全に補償処理し、retryは行いません。Phase 52/45/38のrouting
logicを複製せず、next-step準備・実行、state persistence、provider/tool実行、retry、自動継続、
terminal finalization、scheduler、loop、parallel execution、paid CLI/GUIを追加しません。

```text
Phase 58
persisted_success | persisted_failure | workflow_complete
    ↓
Phase 59
persisted_success/failure → Phase 52 exactly once
    → prepare_next_step | workflow_complete | same persisted_failure
workflow_complete → unchanged stop
    ↓
future explicit boundary
```

## Approved Next-Step Preparation Phase Bridge Reentry Boundary（Phase 60）

`route_approved_next_step_preparation_phase_bridge_reentry()`は、正確なPhase 59 resultを一つだけ受けるread-only boundaryです。`prepare_next_step`だけに明示approvalと正確なnext employeeを要求し、同じworkflow、state/event `Path`、decision、approval、employee objectsのまま既存Phase 53へ正確に一度だけ委譲し、正確な`PreparedWorkflowStep`を返します。`workflow_complete`と`persisted_failure`はapproval/employeeなしでstrict terminal state/historyとtargetの不変を確認し、Phase 53を呼ばず同じobjectで停止します。依存のtarget改変、不正返却、safe/unexpected errorは安全に補償復元し、retryは行いません。approval作成、employee選択、step開始、state persistence、provider/tool実行、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

```text
Phase 59
prepare_next_step | workflow_complete | persisted_failure
    ↓
Phase 60
prepare_next_step + approval + employee → Phase 53 exactly once → PreparedWorkflowStep
workflow_complete / persisted_failure → no approval/employee → unchanged stop
    ↓
Phase 54
```

## Prepared Next-Step Start Routing Phase Bridge Reentry Boundary（Phase 61）

`route_prepared_next_step_start_routing_phase_bridge_reentry()`は、正確なPhase 60 resultを一つだけ受けるread-only boundaryです。正確な`PreparedWorkflowStep`と正確なemployeeを、workflow、employee、state/event `Path` objectsのまま既存Phase 54へ正確に一度だけ委譲し、正確な`PreparedStepExecutionStart`を返します。`workflow_complete`と`persisted_failure`はemployeeなしでstrict terminal state/historyとtargetの不変を確認し、Phase 54を呼ばず同じobjectで停止します。依存のtarget改変、不正返却、例外は安全に補償復元し、retryは行いません。Phase 47/54のlogicを複製・直接呼出しせず、employee選択、running-state persistence、provider/tool実行、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

```text
Phase 60
PreparedWorkflowStep | workflow_complete | persisted_failure
    ↓
Phase 61
PreparedWorkflowStep + employee → Phase 54 exactly once → PreparedStepExecutionStart
workflow_complete / persisted_failure → no employee → unchanged stop
    ↓
Phase 55
```

## Prepared-Start Persistence Routing Phase Bridge Reentry Boundary（Phase 62）

`route_prepared_start_persistence_routing_phase_bridge_reentry()`は、正確なPhase 61 resultを一つだけ受けるread-only boundaryです。正確な`PreparedStepExecutionStart`と正確なemployeeを、workflow、employee、state/event `Path` objectsのまま既存Phase 55へ正確に一度だけ委譲し、同じ`RunningStatePersistenceResult` objectを返します。許可される副作用は提案済みの正確な`running` stateをstate targetへ永続化することだけで、event targetはbyte-for-byte不変です。`workflow_complete`と`persisted_failure`はemployeeなしでstrict terminal state/historyとtargetの不変を確認し、Phase 55を呼ばず同じobjectで停止します。Phase 48/55を複製せず、provider/tool実行、outcome分類、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

```text
Phase 61
PreparedStepExecutionStart | workflow_complete | persisted_failure
    ↓
Phase 62
PreparedStepExecutionStart + exact employee → Phase 55 exactly once
    → exact running-state persistence → same RunningStatePersistenceResult
workflow_complete | persisted_failure → no employee → unchanged stop
    ↓
Phase 56
```

## Persisted-Running Execution Routing Phase Bridge Reentry Boundary（Phase 63）

`route_persisted_running_execution_routing_phase_bridge_reentry()`は、正確なPhase 62 resultを一つだけ受けるread-only boundaryです。正確な`RunningStatePersistenceResult`、元の`PreparedStepExecutionStart`、matching employee、ordered `ToolDefinition` tuple、`OpenAIApiKey`、valid approval、transportを同一objectsのまま既存Phase 56へ正確に一度だけ委譲し、同じ`StepRuntimeExecutionSuccess`または`StepRuntimeExecutionFailure` objectを返します。state/event targetはbyte-for-byte不変で、依存の不正改変・不正返却・例外は安全に補償復元します。`workflow_complete`と`persisted_failure`はexecution-only inputsをすべて`None`としてstrict terminal state/historyを検証し、Phase 56を呼ばず同じobjectで停止します。Phase 49/56を複製・直接呼出しせず、employee/tool/credential/approval選択、provider/tool実行、結果transition、分類、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

```text
Phase 62
RunningStatePersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 63
RunningStatePersistenceResult + exact execution inputs
    → Phase 56 exactly once
    → exact StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
workflow_complete | persisted_failure
    → all execution-only inputs absent
    → unchanged stop
    ↓
Phase 57
```

## Executed-Result Transition Persistence Routing Phase Bridge Reentry Boundary（Phase 64）

`route_executed_result_transition_persistence_routing_phase_bridge_reentry()`は、正確なPhase 63 runtime resultを一つだけ受けるread-only routing boundaryです。正確な`StepRuntimeExecutionSuccess`または`StepRuntimeExecutionFailure`を、workflowとstate/event `Path` objectsのまま既存Phase 57へ正確に一度だけ委譲し、同じ`WorkflowExecutionPersistenceResult` objectを返します。successは正確なsucceeded stateと`step_succeeded` event、failureは正確なfailed stateと`step_failed` eventのtransitionだけを許可し、malformed・partial・unrelated・reorderedな変更は元bytesへ補償復元します。`workflow_complete`と`persisted_failure`はPhase 57を呼ばず同じobjectで停止します。Phase 50/57を複製・直接呼出しせず、provider/tool実行、runtime result作成、outcome分類、progression、next-step preparation、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

```text
Phase 63
StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
| workflow_complete | persisted_failure
    ↓
Phase 64
runtime result → Phase 57 exactly once → exact WorkflowExecutionPersistenceResult
workflow_complete | persisted_failure → unchanged stop
    ↓
Phase 58
```

## Persisted Terminal Outcome Classification Routing Phase Bridge Reentry Boundary（Phase 65）

`route_persisted_terminal_outcome_classification_routing_phase_bridge_reentry()`は、正確なPhase 64
`WorkflowExecutionPersistenceResult`、`workflow_complete`、または`persisted_failure`を一つだけ受ける
read-only routing boundaryです。正確なpersistence resultだけを同じworkflow、state/event `Path`
objectsのまま既存Phase 58へ正確に一度だけ委譲し、同じ`PersistedExecutionOutcome` objectを返します。
`workflow_complete`と`persisted_failure`はPhase 58を呼ばず、strict terminal state/historyとtargetの
byte-for-byte不変を確認して同じobjectで停止します。不正・malformed・unexpectedな依存改変は元bytesへ
補償復元し、safe errorはidentityを保持し、retryは行いません。Phase 51/58のclassification logicを
複製せず、provider/tool実行、transition persistence、classified outcomeの後続routing、progression、
next-step preparation、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUI
は追加しません。

```text
Phase 64
WorkflowExecutionPersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 65
persistence result → Phase 58 exactly once → exact PersistedExecutionOutcome
workflow_complete | persisted_failure → unchanged stop
    ↓
Phase 59
```

## Classified Persisted Outcome Routing Phase Bridge Continuation Boundary（Phase 66）

`route_classified_persisted_outcome_routing_phase_bridge_continuation()`は、正確なPhase 65
`PersistedExecutionOutcome`または`workflow_complete` decisionを一つだけ受けるread-only continuation
boundaryです。`persisted_success`と`persisted_failure`は同じresult、workflow、state/event `Path`
objectsのまま既存Phase 59へ正確に一度だけ委譲します。successは正確な`prepare_next_step`または
`workflow_complete` decisionだけを受け入れ、failureは同じfailure objectだけを受け入れます。
`workflow_complete`はPhase 59を呼ばず、strict terminal state/historyを確認して同じobjectで停止します。
依存の改変、不正返却、safe/unexpected errorは安全に補償処理し、retryは行いません。provider/tool実行、
transition persistence、classification/routing logicの複製、next-step preparation/execution、approval、
retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

```text
Phase 65
persisted_success | persisted_failure | workflow_complete
    ↓
Phase 66
persisted_success/failure → Phase 59 exactly once
    → prepare_next_step | workflow_complete | same persisted_failure
workflow_complete → unchanged stop
```

## Approved Next-Step Preparation Phase Bridge Continuation Boundary（Phase 67）

`route_approved_next_step_preparation_phase_bridge_continuation()`は、正確なPhase 66 resultを一つだけ受けるread-only boundaryです。`prepare_next_step`には明示approvalと正確なnext employeeを要求し、同じresult、workflow、state/event `Path`、approval、employee objectsのまま既存Phase 60へ正確に一度だけ委譲し、正確な`PreparedWorkflowStep`だけを受け入れます。`workflow_complete`と`persisted_failure`はapproval/employeeなしでstrict terminal state/historyとtargetの不変を確認し、依存Phaseを呼ばず同じobjectで停止します。state/eventsは別々に検査・読み込みし、依存のtarget改変、不正返却、safe/unexpected error、rollback failureは安全に分類・補償復元し、retryは行いません。approval作成、employee選択、step開始、state persistence、provider/tool実行、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

```text
Phase 66
prepare_next_step + approval + employee
    ↓
Phase 67
    → Phase 60 exactly once → PreparedWorkflowStep
workflow_complete | persisted_failure
    → unchanged stop
    ↓
Phase 61
```

## Approved Next-Step Preparation Phase Bridge Cycle Continuation Boundary（Phase 74）

`route_approved_next_step_preparation_phase_bridge_cycle_continuation()`は、Phase 73の正確なresultを一つだけ受けるread-only boundaryです。`prepare_next_step`では明示approvalと一致するnext employeeを要求し、同じresult、workflow、approval、employee、state/event targetsを既存Phase 67へ正確に一度だけ委譲し、正確な`PreparedWorkflowStep`を同じobjectで返します。`workflow_complete`と`persisted_failure`はapproval/employeeなしでstrict terminal state/historyとtargetの不変を確認し、Phase 67を呼ばず同じobjectで停止します。依存によるtarget改変、不正返却、safe/unexpected errorは安全に分類し、両targetをbyte-for-byte補償復元します。approval作成、employee選択、step開始、persistence、provider/tool実行、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

```text
Phase 73
prepare_next_step | workflow_complete | persisted_failure
    ↓
Phase 74
prepare_next_step + approval + next employee → Phase 67 exactly once → PreparedWorkflowStep
workflow_complete | persisted_failure → unchanged stop
    ↓
Phase 68
```

## Prepared Next-Step Start Routing Phase Bridge Continuation Boundary（Phase 68）

`route_prepared_next_step_start_routing_phase_bridge_continuation()`は、正確なPhase 67 resultを一つだけ受けるread-only continuation boundaryです。正確な`PreparedWorkflowStep`とmatchingする正確な`EmployeeDefinition`を、同じresult、workflow、employee、state/event `Path` objectsのまま既存Phase 61へ正確に一度だけ委譲し、正確な`PreparedStepExecutionStart`だけを受け入れます。`workflow_complete`と`persisted_failure`はemployeeなしでstrict terminal state/historyとtargetの不変を確認し、Phase 61を呼ばず同じobjectで停止します。state/eventsは別々に検査・読み込みし、依存のtarget改変、不正返却、safe/unexpected error、rollback failureは安全に分類・補償復元し、retryは行いません。employee選択、running-state persistence、provider/tool実行、outcome分類、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

```text
Phase 67
PreparedWorkflowStep | workflow_complete | persisted_failure
    ↓
Phase 68
PreparedWorkflowStep + employee
    → Phase 61 exactly once → PreparedStepExecutionStart
workflow_complete | persisted_failure
    → no employee
    → unchanged stop
    ↓
Phase 62
```

## Prepared Next-Step Start Routing Phase Bridge Cycle Continuation Boundary（Phase 75）

`route_prepared_next_step_start_routing_phase_bridge_cycle_continuation()`は、Phase 74の正確なresultを一つだけ受けるread-only cycle continuation boundaryです。正確な`PreparedWorkflowStep`とmatchingする正確な`EmployeeDefinition`を、同じresult、workflow、employee、state/event `Path` objectsのまま既存Phase 68へ正確に一度だけ委譲し、正確な`PreparedStepExecutionStart`だけを受け入れます。`workflow_complete`と`persisted_failure`はemployeeなしでstrict terminal state/historyとtargetの不変を確認し、Phase 68を呼ばず同じobjectで停止します。依存のtarget改変、不正返却、safe/unexpected error、rollback failureは安全に分類・補償復元し、retryは行いません。employee選択、running-state persistence、provider/tool実行、outcome分類、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

```text
Phase 74
PreparedWorkflowStep | workflow_complete | persisted_failure
    ↓
Phase 75
PreparedWorkflowStep + employee → Phase 68 exactly once → PreparedStepExecutionStart
workflow_complete | persisted_failure → no employee → unchanged stop
    ↓
Phase 69
```

## Prepared Start Persistence Routing Phase Bridge Cycle Continuation Boundary（Phase 76）

`route_prepared_start_persistence_routing_phase_bridge_cycle_continuation()`は、Phase 75の正確な`PreparedStepExecutionStart`、`workflow_complete`、または`persisted_failure`を受けるcycle continuation boundaryです。prepared startだけをmatchingする正確な`EmployeeDefinition`、同じworkflow、state/event targetsとともに既存Phase 69へ正確に一度だけ委譲し、正確な`RunningStatePersistenceResult`を返します。completion/failure stop routeはemployeeなしでstrict terminal state/historyを検証し、Phase 69を呼ばず同じobjectで停止します。依存のtarget改変、不正返却、safe/unexpected error、rollback failureは両targetをbyte-for-byte補償復元し、安全なdetail classificationに変換します。employee選択、provider/tool実行、outcome分類、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

```text
Phase 75
PreparedStepExecutionStart | workflow_complete | persisted_failure
    ↓
Phase 76
PreparedStepExecutionStart + employee → Phase 69 exactly once → RunningStatePersistenceResult
workflow_complete | persisted_failure → no employee → unchanged stop
    ↓
Phase 70
```

## Prepared Start Persistence Routing Phase Bridge Continuation Boundary（Phase 69）

`route_prepared_start_persistence_routing_phase_bridge_continuation()`は、正確なPhase 68 resultを一つだけ受けるread-only continuation boundaryです。正確な`PreparedStepExecutionStart`とmatchingする正確な`EmployeeDefinition`を、同じresult、workflow、employee、state/event `Path` objectsのまま既存Phase 62へ正確に一度だけ委譲し、state targetへの正確なrunning-state persistenceとbyte countを検証した正確な`RunningStatePersistenceResult`だけを受け入れます。`workflow_complete`と`persisted_failure`はemployeeなしでstrict terminal state/historyとtargetの不変を確認し、Phase 62を呼ばず同じobjectで停止します。state/eventsは別々に検査・読み込みし、許可外の変更、不正返却、safe/unexpected error、rollback failureは安全に分類・補償復元し、retryは行いません。employee選択、provider/tool実行、outcome分類、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

```text
Phase 68
PreparedStepExecutionStart | workflow_complete | persisted_failure
    ↓
Phase 69
```

## Persisted Running Execution Routing Phase Bridge Continuation Boundary（Phase 70）

`route_persisted_running_execution_routing_phase_bridge_continuation()`は、正確なPhase 69 `RunningStatePersistenceResult`を一つだけ受けるread-only continuation boundaryです。matchingする正確な`PreparedStepExecutionStart`、`EmployeeDefinition`、resolved tools、credential、approval、transportを検証し、state/eventsと先行step historyを再検証した後、同じ引数identityで既存Phase 63へ正確に一度だけ委譲します。Phase 63からはmatchingする正確なruntime success/failureだけを受け入れ、state/eventsの不変を確認します。`workflow_complete`と`persisted_failure`はexecution-only inputなしでstrict terminal state/historyを確認し、Phase 63を呼ばず停止します。state/eventsは別々に検査・読み込みし、不正返却、依存エラー、target mutation、rollback failureは分類・補償復元し、retryは行いません。employee選択、provider/tool実行、outcome分類、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

```text
Phase 69
RunningStatePersistenceResult + execution inputs
    → Phase 63 exactly once
    → StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
workflow_complete | persisted_failure
    → no execution-only inputs
    → unchanged stop
    ↓
Phase 64
```

## Persisted Running Execution Routing Phase Bridge Cycle Continuation Boundary（Phase 77）

`route_persisted_running_execution_routing_phase_bridge_cycle_continuation()`は、Phase 76の正確な`RunningStatePersistenceResult`、`workflow_complete`、または`persisted_failure`を受けるread-only cycle continuation boundaryです。execution routeでは元の正確な`PreparedStepExecutionStart`、workflow、employee、resolved tools、OpenAI API key、approval、transportを検証し、state/event targetsと先行step historyを再検証した後、同じ10引数のobject identityで既存Phase 70へ正確に一度だけ委譲し、正確な`StepRuntimeExecutionSuccess`または`StepRuntimeExecutionFailure`を返します。completion/failure stop routeはexecution-only inputsをすべて`None`としてstrict terminal state/historyを検証し、Phase 70を呼ばず同じobjectで停止します。依存のtarget改変、不正返却、safe/unexpected error、rollback failureは両targetをbyte-for-byte補償復元し、安全なdetail classificationに変換します。employee/tool/credential/approval選択、provider/tool実行、transition persistence、outcome分類、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

```text
Phase 76
RunningStatePersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 77
RunningStatePersistenceResult + execution inputs → Phase 70 exactly once
    → StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
workflow_complete | persisted_failure
    → all execution-only inputs absent
    → unchanged stop
    ↓
Phase 71
```

## Executed-Result Transition Persistence Routing Phase Bridge Cycle Continuation Boundary（Phase 78）

`route_executed_result_transition_persistence_routing_phase_bridge_cycle_continuation()`は、Phase 77から渡された正確なruntime success/failureを、同じresult、workflow、state/event `Path` objectsのままPhase 71へ正確に一度だけ直接委譲し、Phase 78自身で入力、snapshot、永続化結果、state/event effectを検証して正確な`WorkflowExecutionPersistenceResult`を返します。`workflow_complete`と`persisted_failure`はstrict terminal state/historyを確認してPhase 71を呼ばず、同じobjectで停止します。safe error identityと両targetの補償復元を保持し、retry、runtime result作成、provider/tool実行、outcome分類、progression、next-step preparation、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

## Executed-Result Transition Persistence Routing Phase Bridge Continuation Boundary（Phase 71）

`route_executed_result_transition_persistence_routing_phase_bridge_continuation()`は、正確なPhase 70 runtime resultを一つだけ受けるread-only continuation boundaryです。正確な`StepRuntimeExecutionSuccess`または`StepRuntimeExecutionFailure`を、persisted running stateとpredecessor historyに照合した後、同じresult、workflow、state/event `Path` objectsのまま既存Phase 64へ正確に一度だけ委譲し、正確な`WorkflowExecutionPersistenceResult`だけを受け入れます。successは正確なsucceeded stateと一つの`step_succeeded` event、failureは正確なfailed stateと一つの`step_failed` eventだけを許可し、byte count、path、history、transitionを検証します。`workflow_complete`と`persisted_failure`はstrict terminal state/historyを確認してPhase 64を呼ばず同じobjectで停止します。依存の不正返却、partial/unrelated mutation、safe/unexpected error、rollback failureは安全に分類・補償復元し、retryは行いません。provider/tool実行、runtime result作成、outcome分類、progression、next-step preparation、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

```text
Phase 70
StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
| workflow_complete | persisted_failure
    ↓
Phase 71
runtime result → Phase 64 exactly once → exact WorkflowExecutionPersistenceResult
workflow_complete | persisted_failure → unchanged stop
    ↓
Phase 65
```

## Persisted Outcome Classification Routing Phase Bridge Continuation Boundary（Phase 72）

`route_persisted_outcome_classification_routing_phase_bridge_continuation()`は、正確なPhase 71 `WorkflowExecutionPersistenceResult`を一つだけ受けるread-only continuation boundaryです。state/events Path object identity、terminal state/history、byte count、最後のterminal eventを再検証した後、同じresult、workflow、state/event `Path` objectsのまま既存Phase 65へ正確に一度だけ委譲し、matchingする正確な`PersistedExecutionOutcome`だけを受け入れます。succeeded persistenceは`persisted_success`、failed persistenceはmatching failure categoryの`persisted_failure`へ対応します。terminal eventの`request_id`は既存のbuilt-in str semanticsを保持しつつ、Issue #373の緩和として`succeeded`状態・built-in int `current_step_index >= 6`・`request_id is None`・built-in str `provider == "openai"`の同時成立時のみ`None`を許容します。`workflow_complete`と`persisted_failure`はstrict terminal state/historyを確認してPhase 65を呼ばず同じobjectで停止します。依存の不正返却、mutation、safe/unexpected error、rollback failureは安全に分類・補償復元し、retryは行いません。employee/tool/provider実行、transition persistence、classification logicの複製、classified outcome routing、progression、next-step preparation、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

```text
Phase 71
WorkflowExecutionPersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 72
persistence result → Phase 65 exactly once → exact PersistedExecutionOutcome
workflow_complete | persisted_failure → unchanged stop
    ↓
Phase 59
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

## Classified Outcome Routing Phase Bridge Continuation Boundary（Phase 73）

`route_classified_outcome_routing_phase_bridge_continuation()`は、Phase 72
の正確な`persisted_success`だけを既存Phase 59へ同じresult、workflow、
state/event target objectのまま一度だけ渡します。`persisted_failure`と
workflow completionはPhase 59を呼ばず同じobjectで停止します。targetの
byte-for-byte不変、safe error identity、unexpected error sanitization、
dependency mutationの両target補償、rollback failure、no-retryを保証し、
provider/tool実行、persistence、outcome classification、progression再実行、
next-step execution、retry、自動継続、finalization、scheduler、loop、parallel
execution、paid CLI/GUIは追加しません。

## Persisted Outcome Classification Routing Phase Bridge Cycle Continuation Boundary（Phase 79）

`route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation()`は、正確なPhase 78 `WorkflowExecutionPersistenceResult`を一つだけ受けるread-only continuation boundaryです。state/events Path object identity、terminal state/history、byte count、最後のterminal eventを再検証した後、同じresult、workflow、state/event `Path` objectsのまま既存Phase 72へ正確に一度だけ委譲し、matchingする正確な`PersistedExecutionOutcome`だけを受け入れます。succeeded persistenceは`persisted_success`、failed persistenceはmatching failure categoryの`persisted_failure`へ対応します。terminal eventの`request_id`は既存のbuilt-in str semanticsを保持しつつ、Issue #373の緩和として`succeeded`状態・built-in int `current_step_index >= 6`・`request_id is None`・built-in str `provider == "openai"`の同時成立時のみ`None`を許容します。`workflow_complete`と`persisted_failure`はstrict terminal state/historyを確認してPhase 72を呼ばず同じobjectで停止します。依存の不正返却、mutation、safe/unexpected error、rollback failureは安全に分類・補償復元し、retryは行いません。employee/tool/provider実行、transition persistence、classification logicの複製、classified outcome routing、progression、next-step preparation、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

## Classified Outcome Routing Phase Bridge Cycle Continuation Boundary（Phase 80）

`route_classified_outcome_routing_phase_bridge_cycle_continuation()`は、正確なPhase 79 `persisted_success`を一つだけ受けるread-only continuation boundaryです。succeeded terminal state/historyを再検証した後、同じresult、workflow、state/event `Path` objectsのまま既存Phase 73へ正確に一度だけ委譲し、matchingする正確な`prepare_next_step`または`workflow_complete`だけを受け入れます。`persisted_failure`と`workflow_complete`はstrict terminal state/historyを確認してPhase 73を呼ばず同じobjectで停止します。依存の不正返却、mutation、safe/unexpected error、rollback failureは安全に分類・補償復元し、retryは行いません。employee/tool/provider実行、transition persistence、persisted outcome classification、next-step preparation/execution、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

## Approved Next-Step Preparation Phase Bridge Cycle Reentry Continuation Boundary（Phase 81）

`route_approved_next_step_preparation_phase_bridge_cycle_reentry_continuation()`は、正確なPhase 80 `prepare_next_step`、`workflow_complete`、または`persisted_failure`を受けるread-only continuation boundaryです。prepare routeではexact approvalとnext employee、succeeded terminal state/historyを検証し、同じresult、workflow、state/event `Path`、approval、employeeを既存Phase 74へ正確に一度だけ渡し、正確な`PreparedWorkflowStep`だけを受け入れます。completion/failure stop routeはapprovalとemployeeを指定せずstrict terminal state/historyを確認し、Phase 74を呼ばず同じobjectで停止します。依存の不正返却、mutation、safe/unexpected error、rollback failureは安全に分類・補償復元し、retryは行いません。approval作成、employee選択、step実行、persistence、provider/tool実行、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

## Prepared Next-Step Start Routing Phase Bridge Cycle Reentry Continuation Boundary（Phase 82）

`route_prepared_next_step_start_routing_phase_bridge_cycle_reentry_continuation()`は、正確なPhase 81 `PreparedWorkflowStep`、`workflow_complete`、または`persisted_failure`を受けるread-only continuation boundaryです。prepared routeではexact employeeと直前のsucceeded terminal state/historyを検証し、同じresult、workflow、employee、state/event `Path`を既存Phase 75へcanonical orderで正確に一度だけ渡し、正確な`PreparedStepExecutionStart`だけを受け入れます。completion/failure stop routeはemployeeを指定せずstrict terminal state/historyを確認し、Phase 75を呼ばず同じobjectで停止します。依存の不正返却、mutation、safe/unexpected error、rollback failureは安全に分類・補償復元し、retryは行いません。employee選択、running-state persistence、provider/tool実行、result classification、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

## Prepared Next-Step Start Dispatch Phase Bridge Cycle Reentry Continuation Boundary（Phase 89）

`route_prepared_next_step_start_dispatch_phase_bridge_cycle_reentry_continuation()`は、正確なPhase 88 `PreparedWorkflowStep`、`workflow_complete`、または`persisted_failure`を受けるread-only dispatch boundaryです。prepared routeではexact employeeと直前のsucceeded terminal state/historyを検証し、同じ5引数をcanonical orderで既存Phase 82へ正確に一度だけ渡し、正確な`PreparedStepExecutionStart`だけを返します。completion/failure stop routeはemployeeを指定せずstrict terminal state/historyを確認し、Phase 82を呼ばず同じobjectで停止します。依存の不正返却、mutation、safe/unexpected error、rollback failureは安全に分類・補償復元し、retryは行いません。Phase 89はemployee選択、running-state persistence、provider/tool実行、結果分類、Phase 75直接呼出し、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIを追加しません。

## Prepared Next-Step Start Dispatch Continuation Boundary（Phase 96）

`route_prepared_next_step_start_dispatch_continuation_boundary()`は、正確なPhase 95 `PreparedWorkflowStep`、`workflow_complete`、または`persisted_failure`を受けるread-only dispatch continuation boundaryです。prepared routeではexact workflow、employee、直前のsucceeded terminal state/history、Path targetsを検証し、同じ5引数をcanonical orderで既存Phase 89へ正確に一度だけ直接渡し、正確な`PreparedStepExecutionStart`を返します。completion/failure stop routeはemployeeを指定せずstrict terminal state/historyを確認し、Phase 89を呼ばず同じobjectで停止します。依存の不正返却、mutation、safe/unexpected error、rollback failureはdetail-safeに分類・両targetを補償復元し、retryは行いません。Phase 96はemployee選択、running-state persistence、provider/tool実行、結果分類、Phase 82直接呼出し、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIを追加しません。

## Prepared Start Persistence Dispatch Continuation Boundary（Phase 97）

`route_prepared_start_persistence_dispatch_continuation_boundary()`は、正確なPhase 96 `PreparedStepExecutionStart`、`workflow_complete`、または`persisted_failure`を受けるdispatch continuation boundaryです。prepared startではexact request/running state、employee、直前のsucceeded terminal state/history、regular Path targetsを検証し、同じ5引数をcanonical orderで既存Phase 90へ正確に一度だけ直接渡し、正確な`RunningStatePersistenceResult`だけを返します。許可される副作用はPhase 90が行うrunning-state persistence transitionだけで、state bytes・byte count・event bytes・persisted state/historyを厳密に再検証します。completion/failure stop routeはemployeeを指定せずstrict terminal state/historyを確認し、Phase 90を呼ばず同じobjectで停止します。依存の不正返却、partial/unrelated mutation、safe/unexpected error、rollback failureはdetail-safeに分類・両targetを補償復元し、retryは行いません。Phase 97はemployee選択、provider/tool実行、結果分類、Phase 83直接呼出し、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIを追加しません。

## Persisted Running Execution Routing Phase Bridge Cycle Reentry Continuation Boundary（Phase 84）

`route_persisted_running_execution_routing_phase_bridge_cycle_reentry_continuation()`は、正確なPhase 83 `RunningStatePersistenceResult`、`workflow_complete`、または`persisted_failure`を受けるread-only boundaryです。running persistence resultではexecution inputs、strict running state/history、state/event targetsを再検証し、同じ10引数をcanonical orderで既存Phase 77へ正確に一度だけ渡し、正確なruntime success/failureを返します。completion/failure stop routeはexecution-only inputsをすべて指定せず、Phase 77を呼ばず同じobjectで停止します。依存の不正返却、target mutation、safe/unexpected error、rollback failureはdetail-safeに分類し、必要時は両targetをbyte-for-byte補償復元します。employee選択、tool解決、credential/approval作成、provider/tool実行、transition persistence、結果分類、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

## Persisted Running Execution Dispatch Phase Bridge Cycle Reentry Continuation Boundary（Phase 91）

`route_persisted_running_execution_dispatch_phase_bridge_cycle_reentry_continuation()`は、正確なPhase 90 `RunningStatePersistenceResult`、`workflow_complete`、または`persisted_failure`を受けるread-only dispatch boundaryです。running persistence resultではexecution-only inputs、厳密なpersisted running state/history、state/event targetsを再検証し、同じ10引数をcanonical orderで既存Phase 84へ正確に一度だけ直接渡し、正確なruntime success/failureを返します。completion/failure stop routeはexecution-only inputsをすべて指定せず、Phase 84を呼ばず同じobjectで停止します。依存の不正返却、target mutation、safe/unexpected error、rollback failureはdetail-safeに分類し、必要時は両targetをbyte-for-byte補償復元します。tool解決、credential/approval作成、provider/tool実行、transition persistence、結果分類、Phase 77直接呼出し、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

## Executed-Result Transition Persistence Dispatch Phase Bridge Cycle Reentry Continuation Boundary（Phase 92）

`route_executed_result_transition_persistence_dispatch_phase_bridge_cycle_reentry_continuation()`は、正確なPhase 91 runtime success/failure、`workflow_complete`、または`persisted_failure`を受けるdispatch boundaryです。runtime resultではpersisted running state/historyと両targetを厳密に検証し、同じ4引数をcanonical orderで既存Phase 85へ正確に一度だけ直接渡し、正確な`WorkflowExecutionPersistenceResult`を返します。runtime routeの唯一の副作用は既存Phase 85/78/71による正確なterminal transition persistenceです。completion/failure stop routeはPhase 85を呼ばず同じobjectで停止します。依存の不正返却、partial/unrelated/malformed persistence、target mutation、safe/unexpected error、rollback failureはdetail-safeに分類し、必要時は両targetをbyte-for-byte補償復元します。Phase 78を直接呼ばず、provider/tool実行、runtime result作成、outcome分類、progression、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

## Executed-Result Transition Persistence Routing Phase Bridge Cycle Reentry Continuation Boundary（Phase 85）

`route_executed_result_transition_persistence_routing_phase_bridge_cycle_reentry_continuation()`は、正確なPhase 84 runtime success/failure、`workflow_complete`、または`persisted_failure`を受けるread-only boundaryです。runtime resultではpersisted running state/historyと両targetを厳密に検証し、同じ4引数をcanonical orderで既存Phase 78へ正確に一度だけ直接渡し、正確な`WorkflowExecutionPersistenceResult`を返します。completion/failure stop routeはPhase 78を呼ばず同じobjectで停止します。依存の不正返却、target mutation、safe/unexpected error、rollback failureは安全に分類・補償復元し、retryは行いません。provider/tool実行、runtime result作成、Phase 71直接呼出し、outcome分類、progression、next-step preparation、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

## Persisted Outcome Classification Routing Phase Bridge Cycle Reentry Continuation Boundary（Phase 86）

`route_persisted_outcome_classification_routing_phase_bridge_cycle_reentry_continuation()`は、正確なPhase 85 `WorkflowExecutionPersistenceResult`、`workflow_complete`、または`persisted_failure`を受けるread-only boundaryです。persistence resultではstate/event Path identity、terminal state/history、byte counts、最後のterminal eventを厳密に検証し、同じ4引数をcanonical orderで既存Phase 79へ正確に一度だけ直接渡し、正確な`PersistedExecutionOutcome`を返します。terminal eventの`request_id`は既存のbuilt-in str semanticsを保持しつつ、Issue #373の緩和として`succeeded`状態・built-in int `current_step_index >= 6`・`request_id is None`・built-in str `provider == "openai"`の同時成立時のみ`None`を許容します。completion/failure stop routeはPhase 79を呼ばず同じobjectで停止します。依存の不正返却、target mutation、safe/unexpected error、rollback failureは安全に分類・補償復元し、retryは行いません。employee/tool/credential/approval選択、provider/tool実行、runtime result作成、transition persistence、Phase 72直接呼出し、classification logicの複製、progression、next-step preparation、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

## Prepared Start Persistence Routing Phase Bridge Cycle Reentry Continuation Boundary（Phase 83）

`route_prepared_start_persistence_routing_phase_bridge_cycle_reentry_continuation()`は、正確なPhase 82 `PreparedStepExecutionStart`、`workflow_complete`、または`persisted_failure`を受けるboundaryです。prepared startではmatchingする正確な`EmployeeDefinition`、workflow、state/event targets、先行 succeeded terminal historyを検証し、5引数をcanonical order `(result, workflow, employee, state_path, events_path)`で既存Phase 76へ正確に一度だけ渡し、正確な`RunningStatePersistenceResult`を返します。completion/failure stop routeはemployeeなしでstrict terminal state/historyを確認し、Phase 76を呼ばず同じobjectで停止します。依存の不正返却、target mutation、safe/unexpected error、rollback failureはdetail-safeに分類し、必要時は両targetをbyte-for-byte補償復元します。employee選択、provider/tool実行、結果分類、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

`route_prepared_start_persistence_dispatch_phase_bridge_cycle_reentry_continuation()`は、正確なPhase 89 `PreparedStepExecutionStart`、`workflow_complete`、または`persisted_failure`を受けるPhase 90 dispatch boundaryです。prepared startではmatchingする正確な`EmployeeDefinition`、workflow、state/event targets、先行 succeeded terminal historyを検証し、5引数をcanonical orderで既存Phase 83へ正確に一度だけ直接渡します。Phase 83が許可する正確なrunning-state persistenceだけを検証して同じ`RunningStatePersistenceResult`を返し、completion/failure stop routeはemployeeなしで同じobjectを返します。Phase 83以外の直接呼出し、retry、自動継続、employee選択、provider/tool実行、結果分類、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

## Classified Outcome Routing Phase Bridge Cycle Reentry Continuation Boundary（Phase 87）

`route_classified_outcome_routing_phase_bridge_cycle_reentry_continuation()`は、正確なPhase 86 `PersistedExecutionOutcome`または`workflow_complete`を受けるread-only boundaryです。`persisted_success`だけを同じresult、workflow、state/event target objectのまま既存Phase 80へ正確に一度だけ渡し、matchingする`prepare_next_step`または`workflow_complete`を同じobjectで返します。`persisted_failure`とcompletionはstrict terminal state/historyを確認してPhase 80を呼ばず停止します。依存の不正返却、mutation、safe/unexpected error、rollback failureは安全に分類・補償復元し、safe error identityとno-retryを保持します。Phase 87は分類、progression、next-step preparation/execution、persistence、provider/tool実行、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIを追加しません。

## Approved Next-Step Preparation Routing Phase Bridge Cycle Reentry Continuation Boundary（Phase 88）

`route_approved_next_step_preparation_routing_phase_bridge_cycle_reentry_continuation()`は、正確なPhase 87 `prepare_next_step`、`workflow_complete`、または`persisted_failure`を受けるread-only boundaryです。prepare routeではexact approvalとnext employee、succeeded terminal state/historyを検証し、同じ6引数をcanonical orderで既存Phase 81へ正確に一度だけ渡し、正確な`PreparedWorkflowStep`だけを返します。completion/failure stop routeはapprovalとemployeeを指定せずstrict terminal state/historyを確認し、Phase 81を呼ばず同じobjectで停止します。依存の不正返却、mutation、safe/unexpected error、rollback failureは安全に分類・補償復元し、retryは行いません。Phase 88はapproval作成、employee選択、step開始、persistence、provider/tool実行、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIを追加せず、Phase 74を直接呼びません。

## Persisted Outcome Classification Dispatch Phase Bridge Cycle Reentry Continuation Boundary（Phase 93）

`route_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation()`は、正確なPhase 92 `WorkflowExecutionPersistenceResult`、`workflow_complete`、または`persisted_failure`を受けるdispatch boundaryです。persistence resultではterminal state/history、byte count、最後のterminal eventを厳密に検証し、同じ4引数をcanonical orderで既存Phase 86へ正確に一度だけ直接渡し、正確な`PersistedExecutionOutcome`を返します。completion/failure stop routeはPhase 86を呼ばず同じobjectで停止します。依存の不正返却、mutation、safe/unexpected error、rollback failureはdetail-safeに分類し、必要時は両targetをbyte-for-byte補償復元します。Phase 79直接呼出し、classification logicの複製、progression、step preparation/execution、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。

## Classified Outcome Dispatch Phase Bridge Cycle Reentry Continuation Boundary（Phase 94）

`route_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation()`は、正確なPhase 93 `persisted_success`、`persisted_failure`、または`workflow_complete`を受けるdispatch boundaryです。`persisted_success`ではsucceeded terminal state/historyを厳密に検証し、同じ4引数をcanonical orderで既存Phase 87へ正確に一度だけ直接渡し、正確な`prepare_next_step`または`workflow_complete` decisionを返します。`persisted_failure`とcompletion stop routeはPhase 87を呼ばず同じobjectで停止します。依存の不正返却、mutation、safe/unexpected error、rollback failureはdetail-safeに分類し、必要時は両targetをbyte-for-byte補償復元します。Phase 80直接呼出し、employee/tool/provider実行、persistence、outcome分類、step preparation/execution、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIは追加しません。
## Phase 95 approved next-step preparation dispatch

Phase 95 adds the read-only `route_approved_next_step_preparation_dispatch_phase_bridge_cycle_reentry_continuation()` boundary. It validates one exact Phase 94 progression result, delegates `prepare_next_step` to Phase 88 exactly once, and preserves completion/failure stop results unchanged with zero dependency calls. Target mutation is detected and byte-for-byte compensated; no retry, provider call, persistence, or automatic continuation is introduced.

Phase 98 adds `route_persisted_running_execution_dispatch_continuation_boundary()`. An exact Phase 97 running persistence result is forwarded to Phase 91 with the canonical ten arguments exactly once; completion and persisted failure are unchanged zero-call stop routes. Target mutation, malformed returns, and dependency errors are classified safely and compensated byte-for-byte without retry.

Phase 99 adds `route_executed_result_transition_persistence_dispatch_continuation_boundary()`. Exact Phase 98 runtime success/failure results are forwarded to Phase 92 with the canonical four arguments exactly once; workflow completion and persisted failure remain strict unchanged zero-call stop routes. Target mutation, malformed returns, and dependency errors are classified safely and compensated byte-for-byte without retry.

Phase 100 adds `route_persisted_outcome_classification_dispatch_continuation_boundary()`. Exact Phase 99 persistence results are forwarded to Phase 93 with the canonical four arguments exactly once; workflow completion and persisted failure remain strict unchanged zero-call stop routes. Terminal state/history, byte counts, target identity, outcome fields, dependency mutations, and rollback failures are validated and classified without retry.

Phase 101 adds `route_classified_outcome_cycle_closure_continuation_boundary()`. Exact Phase 100 persisted-success outcomes are forwarded to Phase 94 with the canonical four arguments exactly once and must return the exact matching `prepare_next_step` or `workflow_complete` decision. Exact persisted-failure outcomes and workflow completion are strict unchanged zero-call stop routes. Phase 101 closes only the outcome-classification-to-progression edge of one engine cycle; it does not call Phase 95, load or select employees, resolve tools, create credentials or approvals, prepare or execute a step, persist state, invoke providers or tools, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

Phase 102 adds `route_approved_next_step_cycle_continuation_boundary()`. An exact Phase 101 `prepare_next_step` decision with an exact approval and next employee is forwarded directly to Phase 95 in canonical six-argument order exactly once and must return the exact matching `PreparedWorkflowStep`. Exact workflow completion and persisted failure require approval and employee to be absent and are strict unchanged zero-call stop routes. Phase 102 advances only progression-to-preparation; it does not call Phase 96, create approvals, select or load employees, start steps, persist state, execute providers or tools, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

Phase 103 adds `route_prepared_step_start_cycle_continuation_boundary()`. An exact Phase 102 `PreparedWorkflowStep` with the exact employee is forwarded directly to the public Phase 96 boundary in canonical five-argument order exactly once and must return the exact matching `PreparedStepExecutionStart`. Exact workflow completion and persisted failure require employee to be absent and are strict unchanged zero-call stop routes. Phase 103 advances only preparation-to-start construction; it does not call Phase 89 directly, persist running state, execute providers or tools, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

Phase 104 adds `route_prepared_start_persistence_cycle_continuation_boundary()`. An exact Phase 103 `PreparedStepExecutionStart` with the exact employee is forwarded directly to the public Phase 97 boundary in canonical five-argument order exactly once and must return its exact valid `RunningStatePersistenceResult`. Exact workflow completion and persisted failure require employee to be absent and are strict unchanged zero-call stop routes. Phase 104 advances only execution-start-to-running-persistence, and its only permitted side effect is the existing exact running-state persistence transition. It does not call Phase 90 directly or Phase 98, execute providers or tools, classify runtime results, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

Phase 105 adds `route_persisted_running_execution_cycle_continuation_boundary()`. An exact Phase 104 `RunningStatePersistenceResult` plus the complete exact execution inputs is forwarded directly to the public Phase 98 boundary in canonical ten-argument order exactly once, and its exact matching `StepRuntimeExecutionSuccess` or `StepRuntimeExecutionFailure` is returned unchanged. Exact workflow completion and persisted failure require every execution-only input to be absent and remain strict unchanged zero-call stop routes. Phase 105 is read-only: target mutation, malformed returns, and dependency errors are detail-safely classified and both targets are compensated byte-for-byte without retry. It advances only persisted-running state to runtime execution; it does not call Phase 91 directly or Phase 99, persist terminal transitions, classify persisted outcomes, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

Phase 106 adds `route_runtime_result_transition_persistence_cycle_continuation_boundary()`. An exact Phase 105 runtime success or failure is forwarded directly to the public Phase 99 boundary in canonical four-argument order exactly once, and its exact matching `WorkflowExecutionPersistenceResult` is returned. Exact workflow completion and persisted failure remain strict unchanged zero-call stop routes. The runtime route's only permitted side effect is Phase 99's existing terminal transition persistence; invalid writes, malformed returns, and dependency errors are detail-safely classified and both targets are compensated byte-for-byte without retry. Phase 106 advances only runtime-result-to-terminal-transition persistence. It does not call Phase 92 directly or Phase 100, classify persisted outcomes, decide progression, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

Phase 107 adds `route_persisted_transition_outcome_classification_cycle_continuation_boundary()`. An exact Phase 106 `WorkflowExecutionPersistenceResult` is forwarded directly to the public Phase 100 boundary in canonical four-argument order exactly once, and its exact matching `PersistedExecutionOutcome` is returned unchanged. Exact workflow completion and persisted failure remain strict unchanged zero-call stop routes. Phase 107 is read-only: target mutation, malformed returns, and dependency errors are detail-safely classified and both targets are compensated byte-for-byte without retry. It advances only persisted terminal transitions into persisted outcome classification; it does not call Phase 93 directly, decide progression, prepare a next step, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

Phase 108 adds `route_classified_persisted_outcome_progression_cycle_continuation_boundary()`. An exact persisted-success `PersistedExecutionOutcome` is forwarded directly to the public Phase 101 boundary in canonical four-argument order exactly once, and its exact matching `prepare_next_step` or `workflow_complete` `WorkflowProgressionDecision` is returned unchanged. Exact persisted failure and workflow completion are strict unchanged zero-call stop routes. Phase 108 is read-only: target mutation, malformed returns, and dependency errors are detail-safely classified and both targets are compensated byte-for-byte without retry. It advances only classified persisted success into progression and closes one execution-cycle edge; it does not call Phase 94 directly, prepare or execute the next step, persist running state, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

Phase 109 adds `route_progression_to_approved_preparation_cycle_reentry_continuation_boundary()`. An exact Phase 108 `prepare_next_step` decision with exact approval and next employee is forwarded directly to the public Phase 102 boundary in canonical six-argument order exactly once and must return the exact matching `PreparedWorkflowStep`. Exact workflow completion and persisted failure are strict unchanged zero-call stop routes and require approval and employee to be absent. It revalidates workflow, route-specific decision/completion/failure fields, approval and employee linkage, canonical terminal history, exact prepared-step fields, and read-only two-target transaction behavior. Phase 109 advances only progression-to-approved-preparation and does not bypass Phase 102, call Phase 95 directly, start the prepared step, persist running state, execute providers or tools, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

Phase 110 adds `route_prepared_step_start_cycle_reentry_continuation_boundary()`. An exact Phase 109 `PreparedWorkflowStep` with the exact employee is forwarded directly to the public Phase 103 boundary in canonical five-argument order exactly once and must return the exact matching `PreparedStepExecutionStart`. Exact workflow completion and persisted failure require employee to be absent and are strict unchanged zero-call stop routes. Phase 110 advances only prepared-step-to-execution-start preparation; it does not call Phase 96 directly or Phase 104, persist running state, execute providers or tools, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

Phase 111 adds `route_prepared_start_persistence_cycle_reentry_continuation_boundary()`. An exact Phase 110 `PreparedStepExecutionStart` with the exact employee is forwarded directly to the public Phase 104 boundary in canonical five-argument order exactly once and must return the exact matching `RunningStatePersistenceResult`. Exact workflow completion and persisted failure require employee to be absent and are strict unchanged zero-call stop routes. Phase 111 advances only execution-start preparation into running-state persistence; it does not call Phase 97 directly or Phase 105, execute providers or tools, create runtime results, transition terminal state, classify outcomes, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

Phase 112 adds `route_persisted_running_execution_cycle_reentry_continuation_boundary()`, the read-only boundary that receives one exact Phase 111 `RunningStatePersistenceResult`, `workflow_complete` decision, or `persisted_failure`. For a running persistence result, it revalidates the complete execution-only inputs, strict persisted-running state/history, and both state/event targets, then delegates directly to the public Phase 105 boundary exactly once in canonical ten-argument order and returns its exact matching `StepRuntimeExecutionSuccess` or `StepRuntimeExecutionFailure` unchanged. Workflow completion and persisted failure require every execution-only input to be absent and remain strict unchanged zero-call stop routes. Malformed dependency returns, target mutation, safe or unexpected dependency errors, and rollback failures are classified detail-safely; both targets are compensated byte-for-byte where possible, without retry. Phase 112 does not call Phase 98 directly or Phase 106, duplicate execution logic, resolve tools, create credentials or approvals, persist terminal transitions, classify outcomes, retry, automatically continue, finalize, schedule, loop, run in parallel, or add paid CLI/GUI behavior.

Phase 113 adds `route_runtime_result_transition_persistence_cycle_reentry_continuation_boundary()`. One exact Phase 112 `StepRuntimeExecutionSuccess` or `StepRuntimeExecutionFailure` is forwarded directly to the public Phase 106 boundary exactly once in canonical `(result, workflow, state_path, events_path)` order and must return its exact matching `WorkflowExecutionPersistenceResult`. Exact `workflow_complete` and `persisted_failure` results are strict read-only, unchanged zero-call stop routes. Phase 113 advances only one exact runtime result into one terminal transition persistence through Phase 106; it does not call Phase 99 directly or Phase 107, classify persisted outcomes, decide progression, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

```text
Phase 112
StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
| workflow_complete | persisted_failure
    ↓
Phase 113 runtime-result transition persistence cycle reentry continuation boundary
runtime success | runtime failure
    → Phase 106 exactly once in canonical four-argument order
    → exact WorkflowExecutionPersistenceResult
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 107 (future explicit caller action)
```

Phase 114 adds `route_persisted_transition_outcome_classification_cycle_reentry_continuation_boundary()`. One exact Phase 113 `WorkflowExecutionPersistenceResult` is forwarded directly to the public Phase 107 boundary exactly once in canonical `(result, workflow, state_path, events_path)` order and must return its exact matching `PersistedExecutionOutcome`. Exact `workflow_complete` and `persisted_failure` results are strict read-only, unchanged zero-call stop routes. Phase 114 advances only one exact persisted terminal transition into one persisted-outcome classification through Phase 107; it does not call Phase 100 directly or Phase 108, decide progression, prepare the next step, retry, automatically continue, loop, finalize, schedule, run in parallel, or add paid CLI/GUI behavior.

```text
Phase 113
WorkflowExecutionPersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 114 persisted-transition outcome classification cycle reentry continuation boundary
persisted transition
    → Phase 107 exactly once in canonical four-argument order
    → exact PersistedExecutionOutcome
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 108 (future explicit caller action)
```

## Runtime-Result Transition Persistence Cycle Handoff Reentry Continuation Boundary（Phase 120）
`route_runtime_result_transition_persistence_cycle_handoff_reentry_continuation_boundary()`は、正確なPhase 119の`StepRuntimeExecutionSuccess`、`StepRuntimeExecutionFailure`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を受けるread-only handoff boundaryです。runtime success/failureだけを同じresult、workflow、state/event target objectのまま既存Phase 113へcanonical 4引数順`(result, workflow, state_path, events_path)`で正確に一度だけ委譲し、正確な`WorkflowExecutionPersistenceResult`を同じobjectとして返します。`workflow_complete`と`persisted_failure`はPhase 113を呼ばず、strict terminal state/historyとunchanged targetsを確認して同一オブジェクトを返します。
Phase 120はPhase 113を通じたruntime-result persistence handoffだけを行い、Phase 106を直接呼びません。Phase 114へ自動的に進まず、persisted outcomeの分類、workflow progression、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI追加も行いません。focused testsはinjected Phase 113 fakeだけを使用し、real provider、network、有料API、external toolを呼びません。

## Persisted Transition Outcome Classification Cycle Handoff Reentry Continuation Boundary（Phase 121）
`route_persisted_transition_outcome_classification_cycle_handoff_reentry_continuation_boundary()`は、Phase 120の正確な`WorkflowExecutionPersistenceResult`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を受けるread-only handoff boundaryです。persisted transitionだけを、同じresult、workflow、state/event target objectのまま、既存の公開Phase 114 boundaryへcanonical 4引数順`(result, workflow, state_path, events_path)`で正確に一度だけ委譲し、matchingする`PersistedExecutionOutcome`を同じobjectとして返します。`workflow_complete`と`persisted_failure`はPhase 114を呼ばず、strict terminal state/historyとunchanged targetsを確認して同一オブジェクトを返します。
Phase 121は明示的に許可された1回のpersisted-transition classification handoffだけを行います。Phase 107を直接参照・呼び出しせず、workflow progression、next-step preparation、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しません。focused testsはinjected Phase 114 fakeだけを使用し、real provider、network、有料API、external toolを呼びません。

```text
Phase 120
WorkflowExecutionPersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 121 persisted-transition outcome classification cycle handoff reentry continuation boundary
WorkflowExecutionPersistenceResult
    → Phase 114 exactly once in canonical four-argument order
    → exact PersistedExecutionOutcome
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 115 (future explicit caller action)
```

## Classified Persisted Outcome Progression Cycle Reentry Continuation Boundary（Phase 115）
`route_classified_persisted_outcome_progression_cycle_reentry_continuation_boundary()`は、正確なPhase 114の`PersistedExecutionOutcome`または`workflow_complete`を受けるread-only boundaryです。`persisted_success`だけを同じresult、workflow、state/event target objectのまま既存Phase 108へcanonical 4引数で正確に一度だけ渡し、matchingする`prepare_next_step`または`workflow_complete`の`WorkflowProgressionDecision`を同じobjectで返します。`persisted_failure`とcompletionはstrict terminal state/historyを確認してPhase 108を呼ばず同じobjectで停止します。依存の不正返却、mutation、safe/unexpected error、rollback failureは安全に分類・補償復元し、retryは行いません。Phase 115はnext-step preparation、execution start、persistence、provider/tool実行、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIを追加せず、Phase 101を直接呼びません。
`route_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary()`は、Phase 121の正確な`PersistedExecutionOutcome`または`workflow_complete`を受け、`persisted_success`（継続step index 2以上）のみを公開Phase 115へcanonical 4引数で正確に一度だけ委譲します。`persisted_failure`と`workflow_complete`はstrict terminal state/historyを検証して同一objectを返すzero-call stopです。Phase 122はPhase 115を通じた1回の明示的なclassified-persisted-success progression handoffだけを行い、Phase 108を直接呼ばず、next-step preparation、retry、自動継続、finalize、schedule、loop、parallel execution、provider/tool、CLI/GUI behaviorを追加しません。focused testsはinjected Phase 115 fakeのみを使用します。

## Progression To Approved Preparation Cycle Handoff Reentry Continuation Boundary（Phase 116）
`route_progression_to_approved_preparation_cycle_handoff_reentry_continuation_boundary()`は、正確なPhase 115の`WorkflowProgressionDecision(prepare_next_step)`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を受けるread-only handoff boundaryです。`prepare_next_step`だけを同じresult、workflow、approval、employee、state/event target objectのまま既存Phase 109へcanonical 6引数で正確に一度だけ渡し、matchingする`PreparedWorkflowStep`を同じobjectで返します。completionとfailureはPhase 109を呼ばず、strict terminal state/historyとunchanged targetsを確認して同じobjectで停止します。Phase 116はPhase 109を迂回せず、Phase 102を直接呼ばず、execution start、running-state persistence、provider/tool実行、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIを追加しません。

## Prepared Step Start Cycle Handoff Reentry Continuation Boundary（Phase 117）
`route_prepared_step_start_cycle_handoff_reentry_continuation_boundary()`は、正確なPhase 116の`PreparedWorkflowStep`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を受けるread-only handoff boundaryです。`PreparedWorkflowStep`だけを同じresult、workflow、employee、state/event target objectのまま既存Phase 110へcanonical 5引数で正確に一度だけ渡し、matchingする`PreparedStepExecutionStart`を同じobjectで返します。completionとfailureはPhase 110を呼ばず、strict terminal state/historyとunchanged targetsを確認して同じobjectで停止します。Phase 117はPhase 110を迂回せず、Phase 103を直接呼ばず、Phase 111、running-state persistence、provider/tool実行、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIを追加しません。

## Prepared Start Persistence Cycle Handoff Reentry Continuation Boundary（Phase 118）

`route_prepared_start_persistence_cycle_handoff_reentry_continuation_boundary()`は、正確なPhase 117の`PreparedStepExecutionStart`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を受けるhandoff boundaryです。`PreparedStepExecutionStart`だけを同じresult、workflow、employee、state/event target objectのまま既存Phase 111へcanonical 5引数で正確に一度だけ渡し、matchingする`RunningStatePersistenceResult`を同じobjectで返します。completionとfailureはPhase 111を呼ばず、strict terminal state/historyとunchanged targetsを確認して同じobjectで停止します。Phase 118はPhase 111を迂回せず、Phase 104を直接呼ばず、provider/tool実行、retry、自動継続、finalization、scheduler、loop、parallel execution、paid CLI/GUIを追加しません。
## Persisted Running Execution Cycle Handoff Reentry Continuation Boundary（Phase 119）
`route_persisted_running_execution_cycle_handoff_reentry_continuation_boundary()`は、正確なPhase 118の`RunningStatePersistenceResult`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を受けるread-only handoff boundaryです。`RunningStatePersistenceResult`だけを、matchingするPhase 117の`PreparedStepExecutionStart`と明示的なemployee、resolved tools、credential、approval、transportを伴って、既存Phase 112へcanonical ten-argument orderで正確に一度だけ渡し、matchingする`StepRuntimeExecutionSuccess`または`StepRuntimeExecutionFailure`を同じobjectで返します。completionとfailureはPhase 112を呼ばず、strict terminal state/historyとunchanged targetsを確認して同じobjectで停止します。Phase 119は明示的に許可された1回のruntime execution handoffだけを担当し、Phase 112経由でprovider/tool attemptを発生させ得ますが、Phase 105を直接呼ばず、runtime resultをpersistまたはclassifyせず、retry、自動継続、progress、finalize、schedule、loop、parallel execution、paid CLI/GUIを追加しません。focused testsはfakesだけを使い、real provider、network、paid API、external toolを呼びません。

Phase 123 adds `route_progression_to_approved_preparation_cycle_handoff_chain_reentry_continuation_boundary()`. It accepts one exact Phase 122 progression result: an exact `prepare_next_step` decision with matching workflow, approval, employee, terminal history, and regular state/event targets is delegated exactly once to the public Phase 116 boundary in canonical six-argument order and returns its exact `PreparedWorkflowStep`. Exact `workflow_complete` and `persisted_failure` results are strict unchanged zero-call stops.

Phase 123 performs one explicitly authorized progression-to-approved-preparation handoff through Phase 116. It does not call Phase 109 directly, start the prepared step, persist start state, execute a provider or tool, retry, automatically continue, finalize, schedule, loop, run in parallel, or add CLI/GUI behavior. Focused tests inject Phase 116 fakes only and make no real provider, network, paid API, or external tool calls.

## Prepared Step Start Cycle Handoff Chain Reentry Continuation Boundary（Phase 124）

`route_prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary()`は、正確なPhase 123の`PreparedWorkflowStep`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を受けるread-only handoff boundaryです。継続経路の`PreparedWorkflowStep`（`step_index >= 2`）だけを、正確なworkflow、employee、predecessor terminal history、state/event targetとともに、公開Phase 117 boundaryへcanonical 5引数順`(result, workflow, employee, state_path, events_path)`で正確に一度だけ委譲し、matchingする正確な`PreparedStepExecutionStart`を返します。completionとfailureはPhase 117を呼ばず、strict terminal state/historyとunchanged targetsを確認して同じobjectで停止します。

Phase 124はPhase 117を通じた1回の明示的なprepared-step start handoffだけを行い、Phase 110を直接呼び出しません。prepared-start stateの永続化、provider/tool実行、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しません。依存のエラー、不正な返却、target mutationは安全に分類し、byte-for-byteに補償復元し、retryは行いません。focused testsはinjected Phase 117 fakeのみを使用し、real provider、network、有料API、external toolを呼びません。

## Prepared Start Persistence Cycle Handoff Chain Reentry Continuation Boundary（Phase 125）

`route_prepared_start_persistence_cycle_handoff_chain_reentry_continuation_boundary()`は、正確なPhase 124の`PreparedStepExecutionStart`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を受けるread-only handoff boundaryです。継続経路のprepared start（`current_step_index >= 2`）だけを、正確なworkflow、employee、predecessor terminal history（predecessor index = `current_step_index - 1`; step 2 continuationではexact succeeded step 1）、state/event targetとともに、公開Phase 118 boundaryへcanonical 5引数順`(result, workflow, employee, state_path, events_path)`で正確に一度だけ委譲し、Phase 118が生成した正確な`RunningStatePersistenceResult`を返します。Phase 118のstate persistence、running-state bytes、byte count、event target不変性を検証します。

completionとfailureはPhase 118を呼ばず、strict terminal state/historyとunchanged targetsを確認して同じobjectで停止します。Phase 125はPhase 111を直接呼ばず、provider/tool実行、runtime resultの分類、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しません。依存のsafe errorはidentityを保持し、unexpected errorや不正な返却・target mutationは安全に分類して可能な限りstate/eventをbyte-for-byteに補償復元し、retryは行いません。focused testsはinjected Phase 118 fakeのみを使用し、real provider、network、有料API、external toolを呼びません。

```text
Phase 124
PreparedStepExecutionStart | workflow_complete | persisted_failure
    ↓
Phase 125 prepared-start persistence cycle handoff chain reentry continuation boundary
PreparedStepExecutionStart (running index >= 2) + exact employee
    → Phase 118 exactly once in canonical five-argument order
    → exact RunningStatePersistenceResult
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 126 (future explicit caller action)
```

## Persisted Running Execution Cycle Handoff Chain Reentry Continuation Boundary（Phase 126）

`route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary()`は、正確なPhase 125の`RunningStatePersistenceResult`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を受けるread-only handoff boundaryです。継続経路（`PreparedStepExecutionStart.running_state.current_step_index >= 2`）では、matchingする`PreparedStepExecutionStart`、workflow、employee、regular state/event targets、resolved tools、credential、approval、transportを再検証し、既存の公開Phase 119 boundaryへcanonical ten-argument order`(result, start, workflow, employee, state_path, events_path, resolved_tools, api_key, approval, transport)`で正確に一度だけ委譲します。step 2では、exact persisted running stateとexact succeeded step-1 predecessor historyを受け渡します。Phase 119の正確な`StepRuntimeExecutionSuccess`または`StepRuntimeExecutionFailure`を同じobjectで返します。supplied object identityを維持し、state/event targetsをbyte-for-byte不変に保ち、retryは行いません。

`workflow_complete`と`persisted_failure`は実行専用入力をすべて`None`にし、strict terminal state/historyとtargetsのbyte-for-byte不変性を確認して、Phase 119を呼ばずに同じobjectを返すzero-call stop routeです。Phase 126は1回の明示的に許可されたpersisted-running execution handoffだけを行い、Phase 112を直接参照・呼び出しせず、runtime resultの永続化、persisted outcomeの分類、workflow progression、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しません。依存のsafe errorはidentityを保持し、unexpected error、不正な返却、target mutationはdetail-safeに分類して可能な限り両targetをbyte-for-byteに補償復元します。focused testsはinjected Phase 119 fakeのみを使用し、real provider、network、有料API、external tool、real transportを呼びません。

```text
Phase 125
RunningStatePersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 126 persisted-running execution cycle handoff chain reentry continuation boundary
RunningStatePersistenceResult + exact PreparedStepExecutionStart + exact execution inputs
    → Phase 119 exactly once in canonical ten-argument order
    → exact StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 120 (future explicit caller action)
```

## Runtime Result Transition Persistence Cycle Handoff Chain Reentry Continuation Boundary（Phase 127）

`route_runtime_result_transition_persistence_cycle_handoff_chain_reentry_continuation_boundary()`は、Phase 126から受け取った正確な`StepRuntimeExecutionSuccess`または`StepRuntimeExecutionFailure`を検証し、既存の公開Phase 120 boundaryへcanonical four-argument order`(result, workflow, state_path, events_path)`と同一object identityで正確に一度だけ委譲します。Phase 120の正確な`WorkflowExecutionPersistenceResult`と、そのstate/event targetsへのbyte-count、terminal state、追加event、runtime linkageを再検証し、supplied return objectを返します。

正確な`WorkflowProgressionDecision(workflow_complete)`と`PersistedExecutionOutcome(persisted_failure)`はstrict terminal state/historyとtarget不変性を確認してPhase 120を呼ばず、同じobjectを返すzero-call stop routeです。Phase 127はPhase 120を通る1回の明示的に許可されたruntime-result transition persistence handoffだけを行い、Phase 113を直接呼び出さず、persisted outcomeの分類、workflow progression、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しません。Focused testsはinjected Phase 120 fakeのみを使用し、real provider、network、有料API、external tool、real transportを呼びません。

```text
Phase 126
StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure | workflow_complete | persisted_failure
    ↓
Phase 127 runtime-result transition persistence cycle handoff chain reentry continuation boundary
StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
    → Phase 120 exactly once in canonical four-argument order
    → exact WorkflowExecutionPersistenceResult
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 121 (future explicit caller action)
```

## Persisted Transition Outcome Classification Cycle Handoff Chain Reentry Continuation Boundary（Phase 128）

`route_persisted_transition_outcome_classification_cycle_handoff_chain_reentry_continuation_boundary()`は、Phase 127から受け取った正確な`WorkflowExecutionPersistenceResult`、またはstrict stop用の`WorkflowProgressionDecision(workflow_complete)` / `PersistedExecutionOutcome(persisted_failure)`を受けるread-only boundaryです。継続経路では、workflow、regular state/event targets、target identity、positive built-in byte counts、current step index（`>= 3`）、terminal state/history、predecessor history、terminal eventのworkflow/step/employee/provider/request/result/failure linkageを再検証し、既存の公開Phase 121 boundaryへcanonical four-argument order`(result, workflow, state_path, events_path)`と同一object identityで正確に一度だけ委譲します。Phase 121が返す正確な`PersistedExecutionOutcome`を同一objectで返し、正常経路ではstate/eventsをbyte-for-byte不変に保ちます。

`workflow_complete`と`persisted_failure`はstrict terminal state/historyとtarget不変性を検証してPhase 121を呼ばず、同じobjectを返すzero-call stop routeです。Phase 128はPhase 121を通る1回の明示的に許可されたpersisted-transition outcome-classification handoffだけを行い、Phase 114を直接参照・呼び出しせず、workflow progression、next-step preparation、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しません。依存のsafe errorはidentityを保持し、unexpected error、不正な返却、target mutationはdetail-safeに分類して可能な限り両targetをbyte-for-byteに補償復元します。Focused testsはinjected Phase 121 fakeのみを使用し、real provider、network、有料API、external tool、real transportを呼びません。

```text
Phase 127
WorkflowExecutionPersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 128 persisted-transition outcome classification cycle handoff chain reentry continuation boundary
WorkflowExecutionPersistenceResult
    → Phase 121 exactly once in canonical four-argument order
    → exact PersistedExecutionOutcome
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 122 (future explicit caller action)
```

## Classified Persisted-Outcome Progression Cycle Handoff Chain Reentry Continuation Boundary（Phase 129）

`route_classified_persisted_outcome_progression_cycle_handoff_chain_reentry_continuation_boundary()`は、Phase 128から受け取った正確な`PersistedExecutionOutcome(persisted_success)`、またはstrict stop用の`PersistedExecutionOutcome(persisted_failure)` / `WorkflowProgressionDecision(workflow_complete)`を受けるread-only boundaryです。`persisted_success`では、workflow、regular state/event targets、current step index（`>= 3`）、succeeded terminal state/history、predecessor history、terminal event、workflow/current-step/employee linkageを再検証し、公開Phase 122 boundaryへcanonical four-argument order`(result, workflow, state_path, events_path)`と同一object identityで正確に一度だけ委譲します。

中間stepではPhase 122の正確な`prepare_next_step`とnext-step id/index/employee、`reason == "next_step_available"`を検証し、final stepでは正確な`workflow_complete`、next fieldsの`None`、`reason == "last_step_succeeded"`を検証して同一objectを返します。`persisted_failure`と`workflow_complete`はstrict terminal state/historyとtarget不変性を確認してPhase 122を呼ばず、同じobjectを返すzero-call stop routeです。Phase 129はPhase 122を通る1回の明示的に許可されたclassified-persisted-success progression handoffだけを行い、Phase 115を直接参照・呼び出しせず、next-step preparation、prepared-step start、start-state persistence、provider/tool execution、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しません。依存のsafe errorはidentityを保持し、unexpected error、不正な返却、target mutationはdetail-safeに分類して可能な限り両targetをbyte-for-byteに補償復元します。Focused testsはinjected Phase 122 fakeのみを使用し、real provider、network、有料API、external tool、real transportを呼びません。

```text
Phase 128
persisted_success | persisted_failure | workflow_complete
    ↓
Phase 129 classified persisted-outcome progression cycle handoff chain reentry continuation boundary
persisted_success
    → Phase 122 exactly once in canonical four-argument order
    → prepare_next_step | workflow_complete
persisted_failure | workflow_complete
    → unchanged zero-call stop
    ↓
Phase 123 (future explicit caller action)
```

## Progression-to-Approved Preparation Cycle Handoff Chain Bridge（Phase 130）

`route_progression_to_approved_preparation_cycle_handoff_chain_bridge_reentry_continuation_boundary()`は、Phase 129から受け取った正確な`WorkflowProgressionDecision(prepare_next_step)`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を検証するread-only bridgeです。prepare routeでは、workflow、step、approval、employee、regular state/event targets、`current_step_index >= 1`（workflow step 1 onward）、exact terminal succeeded history、completed-step prefix、terminal eventのruntime linkageとprovider=`"openai"`を再検証し、既存の公開Phase 123 boundaryへ`(result, workflow, approval, employee, state_path, events_path)`のcanonical six-argument orderとobject identityで正確に一度だけ委譲します。Phase 123の正確な`PreparedWorkflowStep`だけを受け入れ、正常経路でもstate/eventsをbyte-for-byte不変に保ちます。

`workflow_complete`と`persisted_failure`はapproval/employeeを`None`に限定し、Phase 129 stop-routeと同じprovider厳格性でterminal state/historyとtarget不変性を検証して、Phase 123を呼ばずに同じobjectを返すzero-call stop routeです。Phase 130はPhase 116を直接呼び出さず、prepared stepのstart、start-state persistence、provider/tool execution、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しません。safe dependency errorはidentityを保持し、unexpected error、malformed return、target mutationはdetail-safeに分類して可能な限り両targetをbyte-for-byteに補償復元します。復元失敗は`dependency_rollback`とし、retryは行いません。Focused testsはinjected Phase 123 fakeのみを使い、real provider、network、有料API、external tool、real transportを呼びません。

```text
Phase 129
prepare_next_step | workflow_complete | persisted_failure
    ↓
Phase 130 progression-to-approved-preparation cycle handoff chain bridge reentry continuation boundary
prepare_next_step + exact approval + exact employee
    → Phase 123 exactly once in canonical six-argument order
    → exact PreparedWorkflowStep
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 124 (future explicit caller action)
```

## Prepared Step Start Cycle Handoff Chain Bridge Reentry Continuation Boundary（Phase 131）

`route_prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary()`は、Phase 130から受け取った正確な`PreparedWorkflowStep`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を検証するread-only bridgeです。prepared-step routeでは、Phase 130 continuationに対応する`step_index >= 2`、exact workflow/step/employee linkage、succeeded predecessor terminal state/history、terminal eventのruntime linkageとprovider `"openai"`、regular state/event targetsを再検証します。その後、既存の公開Phase 124 `route_prepared_step_start_cycle_handoff_chain_reentry_continuation_boundary()`へ、supplied object identityを保持した`(result, workflow, employee, state_path, events_path)`のcanonical five-argument orderで正確に一度だけ委譲し、exact valid `PreparedStepExecutionStart`を返します。

Phase 124の返却については、exact `PreparedStepExecutionStart`、nested `ModelInvocationRequest`、running `WorkflowExecutionState`、request/runtime linkage、completed-step prefix、`last_failure_category`を再検証します。`workflow_complete`と`persisted_failure`はemployeeが`None`であり、Phase 130 stop routeより不必要に厳しいprovider条件を追加せず、Phase 124を呼ばないunchanged zero-call stopです。Phase 131はPhase 117を直接参照・呼び出しせず、prepared-start persistence、provider/tool execution、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しません。safe dependency errorはsuccessful compensation後もidentityを保持し、unexpected error、malformed return、target mutationはdetail-safeに分類して両targetをbyte-for-byteに補償復元し、復元失敗を`dependency_rollback`とします。Focused testsはinjected Phase 124 fakesのみを使用し、real provider、network、paid API、external tool、real transportを呼びません。

```text
Phase 130
PreparedWorkflowStep | workflow_complete | persisted_failure
    ↓
Phase 131 prepared-step start cycle handoff chain bridge reentry continuation boundary
PreparedWorkflowStep (step_index >= 2) + exact employee
    → Phase 124 exactly once in canonical five-argument order
    → exact PreparedStepExecutionStart
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 125 (future explicit caller action)
```

## Prepared Start Persistence Cycle Handoff Chain Bridge Reentry Continuation Boundary（Phase 132）

`route_prepared_start_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary()`は、Phase 131から受け取った正確な`PreparedStepExecutionStart`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を検証するbridgeです。prepared-start routeでは`current_step_index >= 2`、exact workflow/step/employee/request/running-state linkage、succeeded predecessor terminal state/history（predecessor index = `current_step_index - 1`; step 2 continuationではexact succeeded step 1）、completed-step prefix、terminal eventのprovider=`"openai"`、regular state/event targetsを再検証します。その後、既存の公開Phase 125 boundaryへ、supplied object identityを保持した`(result, workflow, employee, state_path, events_path)`のcanonical five-argument orderで正確に一度だけ委譲します。

Phase 125は明示的に許可されたstate persistenceを担当します。Phase 132は、正確な`RunningStatePersistenceResult`、canonical running-state bytes、positive exact built-in byte count、reloaded running stateを検証し、state targetだけが supplied running stateへ置換され、events targetはbyte-for-byte不変であることを確認して同じresult objectを返します。`workflow_complete`と`persisted_failure`はPhase 125を呼ばないunchanged zero-call stop routeです。Phase 132はPhase 118を直接参照・呼び出しせず、provider/tool execution、runtime-result persistence、classification、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しません。safe dependency errorは成功した補償後もidentityを保持し、unexpected error、malformed result、target mutationはdetail-safeに分類し、両targetを可能な限りbyte-for-byteに復元します。復元失敗は`dependency_rollback`です。Focused testsはinjected Phase 125 fakesのみを使い、real provider、network、有料API、external tool、real transportを呼びません。

```text
Phase 131
PreparedStepExecutionStart | workflow_complete | persisted_failure
    ↓
Phase 132 prepared-start persistence cycle handoff chain bridge reentry continuation boundary
PreparedStepExecutionStart (current_step_index >= 2) + exact employee
    → Phase 125 exactly once in canonical five-argument order
    → exact RunningStatePersistenceResult; state replaced, events unchanged
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 126 (future explicit caller action)
```

## Persisted-Running Execution Cycle Handoff Chain Bridge Reentry Continuation Boundary（Phase 133）

`route_persisted_running_execution_cycle_handoff_chain_bridge_reentry_continuation_boundary()`は、Phase 132から受け取った正確な`RunningStatePersistenceResult`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を検証するouter bridgeです。実行routeでは`PreparedStepExecutionStart.running_state.current_step_index >= 2`、exact workflow/start/request/running-state/employee/tools/credential/approval/target linkage、Phase 132のcanonical running-state bytes、succeeded predecessor history、直前terminal eventのprovider=`"openai"`を再検証します。step 2ではpredecessor historyはexact succeeded step 1です。以前のpredecessor provider条件は不必要に厳格化しません。

検証後、既存の公開Phase 126 `route_persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary()`へ、supplied object identityを保持した`(result, start, workflow, employee, state_path, events_path, resolved_tools, api_key, approval, transport)`のcanonical ten-argument orderで正確に一度だけ委譲します。Phase 126からはexact `StepRuntimeExecutionSuccess`または`StepRuntimeExecutionFailure`だけを受け入れ、nested invocation result、OpenAI provider、runtime linkage、target byte-for-byte不変性を再検証して同じresult objectを返します。

`workflow_complete`と`persisted_failure`はexecution-only inputを`None`に限定し、Phase 132 stop routeより厳しいprovider条件を追加せず、Phase 126を呼ばないunchanged zero-call stop routeです。Phase 133はPhase 126を一度だけ明示的にhandoffし、runtime-result persistence、outcome classification、workflow progression、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しません。safe dependency errorはsuccessful compensation後もidentityを保持し、unexpected error、malformed return、target mutationはdetail-safeに分類します。両targetを可能な限り元bytesへ補償復元し、復元失敗は`dependency_rollback`、dependency callはretryしません。Focused testsはinjected Phase 126 fakesのみを使い、real provider、network、paid API、external tool、credential use、real transportを呼びません。

```text
Phase 132
RunningStatePersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 133 persisted-running execution cycle handoff chain bridge reentry continuation boundary
RunningStatePersistenceResult + exact execution inputs
    → Phase 126 exactly once in canonical ten-argument order
    → exact StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 127 (future explicit caller action)
```

## Runtime Result Transition Persistence Cycle Handoff Chain Bridge Reentry Continuation Boundary（Phase 134）

`route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary()`は、Phase 133 continuationからの正確な`StepRuntimeExecutionSuccess`または`StepRuntimeExecutionFailure`、またはstrict stop用の`WorkflowProgressionDecision(workflow_complete)` / `PersistedExecutionOutcome(persisted_failure)`を受けるruntime-result persistence bridgeです。実行routeでは`current_step_index >= 4`、exact workflow、persisted running state、Phase 133 provenanceを持つsucceeded predecessor history、直前predecessor provider=`"openai"`、runtime resultとnested invocation resultのOpenAI linkageを再検証します。直前より前のvalid predecessor providerはPhase 133契約どおり不必要に制限しません。

検証後、既存の公開Phase 127 `route_runtime_result_transition_persistence_cycle_handoff_chain_reentry_continuation_boundary()`へ、supplied object identityを保持した`(result, workflow, state_path, events_path)`のcanonical four-argument orderで正確に一度だけ委譲します。Phase 127のexact `WorkflowExecutionPersistenceResult`について、target identity、positive exact built-in byte counts、exact terminal state/event、元event bytesの完全prefix、terminal linkage、provider=`"openai"`、append countを再検証して同じresult objectを返します。`workflow_complete`と`persisted_failure`はPhase 127を呼ばず、Phase 133 stop-routeより厳しいprovider条件を追加しないunchanged zero-call stop routeです。

Phase 134はPhase 120を直接参照・呼び出しせず、1回の明示的なruntime-result transition persistence handoffだけを行います。outcome classification、workflow progression、next-step preparation、prepared-step start、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorは追加しません。safe dependency errorはsuccessful compensation後もidentityを保持し、unexpected error、malformed return、invalid persistence、target mutationはdetail-safeに分類します。両targetを元bytesへ補償復元し、復元失敗は`dependency_rollback`、dependency callはretryしません。Focused testsはinjected Phase 127 fakesのみを使用し、real provider、network、paid API、external tool、credential、real transportを呼びません。

```text
Phase 133
StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure | workflow_complete | persisted_failure
    ↓
Phase 134 runtime-result transition persistence cycle handoff chain bridge reentry continuation boundary
StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure (current_step_index >= 4)
    → Phase 127 exactly once in canonical four-argument order
    → exact WorkflowExecutionPersistenceResult
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 128 (future explicit caller action)
```

## Phase 135: Persisted-Transition Outcome Classification Cycle Handoff Chain Bridge

Phase 135は、Phase 134から受け取った正確な`WorkflowExecutionPersistenceResult`を、既存の公開Phase 128へ`(result, workflow, state_path, events_path)`のcanonical four-argument orderとobject identityを保持して正確に一度だけ委譲するouter bridgeです。Phase 128のexact `PersistedExecutionOutcome`を再検証して同じobjectを返し、`workflow_complete`と`persisted_failure`はPhase 128を呼ばないunchanged zero-call stop routeです。classification routeではcurrent step index `>= 4`、exact terminal state/history、Phase 134 predecessor provenance、直前predecessorとterminalのprovider=`"openai"`、byte counts、target identityを再検証し、正常経路でstate/eventsを変更させません。

Phase 135はPhase 121を直接参照・呼び出しせず、persisted-transition outcome classification handoffを一回だけ行います。progression、next-step preparation、prepared start、provider/tool execution、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorは追加しません。safe dependency errorはsuccessful compensation後もidentityを保持し、unexpected error、malformed outcome、target mutationはdetail-safeに分類して両targetを補償復元します。復元失敗は`dependency_rollback`で、retryはありません。Focused testsはinjected Phase 128 fakesのみを使用し、real provider、network、paid API、external tool、credential、transportを呼びません。

このPhaseでは、既存Phase 128の成功terminal `output_text`契約を、exact built-in `str`（空文字を含む）へ狭く互換修正しました。Phase 127/134の有効なempty success outputを受理するための変更であり、response/provider、predecessor、failure semantics、Phase 128 public APIおよび他の契約は緩和していません。

```text
Phase 134
WorkflowExecutionPersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 135 persisted-transition outcome classification cycle handoff chain bridge reentry continuation boundary
WorkflowExecutionPersistenceResult
    → Phase 128 exactly once in canonical four-argument order
    → exact persisted_success | persisted_failure
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 136 (future explicit caller action)
```

## Classified Persisted-Outcome Progression Cycle Handoff Chain Bridge Reentry Continuation Boundary（Phase 136）

`route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_reentry_continuation_boundary()`は、Phase 135から受け取った正確な`PersistedExecutionOutcome(persisted_success)`、またはstrict stop用の`PersistedExecutionOutcome(persisted_failure)` / `WorkflowProgressionDecision(workflow_complete)`を検証するouter bridgeです。persisted-success routeでは、Phase 135 provenanceを含むexact workflow、step、state、history、regular targets、current step index `>= 4`、workflow/current-step/index/employee linkage、直前predecessor provider=`"openai"`、predecessorのprovider/request/response/output contract、terminal provider=`"openai"`、terminal response/request/output/failure semanticsを再検証します。直前より前のvalid non-openai predecessor providerは許容します。

検証後、公開Phase 129 `route_classified_persisted_outcome_progression_cycle_handoff_chain_reentry_continuation_boundary()`へ、supplied object identityを保持した`(result, workflow, state_path, events_path)`のcanonical four-argument orderで正確に一度だけ委譲します。Phase 129から返るexact `WorkflowProgressionDecision`について、intermediateの`prepare_next_step` current/next linkageと`reason == "next_step_available"`、finalの`workflow_complete`と`reason == "last_step_succeeded"`を再検証し、同じdecision objectを返します。正常経路ではstate/eventsをbyte-for-byte不変に保ちます。persisted-successのterminal succeeded eventだけは、Phase 129の狭い互換契約に従いexact built-in `str`の空`output_text`を許容します。

`persisted_failure`と`workflow_complete`はPhase 129 stop contractを継承したstrict terminal validation後にPhase 129を呼ばず、同じobjectを返すzero-call stop routeです。workflow_completeのsucceeded terminal eventはempty `output_text`を許容しません。Phase 136はPhase 122を直接参照・呼び出しせず、progression、preparation、start、persistence、runtime execution、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しません。safe dependency errorはsuccessful compensation後もidentityを保持し、unexpected error、malformed decision、target mutationはdetail-safeに分類して可能な限り両targetを元bytesへ補償復元します。復元失敗は`dependency_rollback`、retryはありません。Focused testsはinjected Phase 129 fakesのみを使用し、real provider、network、paid API、external tool、credential、transportを呼びません。

```text
Phase 135
persisted_success | persisted_failure | workflow_complete
    ↓
Phase 136 classified persisted-outcome progression cycle handoff chain bridge reentry continuation boundary
persisted_success (current_step_index >= 4)
    → Phase 129 exactly once in canonical four-argument order
    → exact prepare_next_step | workflow_complete decision
persisted_failure | workflow_complete
    → unchanged zero-call stop
    ↓
Phase 137 (future explicit caller action)
```

## Progression-to-Approved Preparation Cycle Handoff Chain Bridge Outer Reentry Continuation Boundary（Phase 137）

`route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary()`は、Phase 136から受け取った正確な`WorkflowProgressionDecision(prepare_next_step)`、またはstrict stop用の`WorkflowProgressionDecision(workflow_complete)` / `PersistedExecutionOutcome(persisted_failure)`を検証するouter bridgeです。prepare routeではPhase 136 provenanceを維持し、exact workflow/step/approval/employee、regular state/event targets、`current_step_index >= 1`（workflow step 1 onward）、current/next/reason linkage、completed-step prefix、直前predecessor provider=`"openai"`、terminal provider=`"openai"`、terminal response/request/outputのexact contractを再検証します。Phase 136の有効な空success outputも、exact built-in `str`として許容します。

検証後、公開Phase 130 `route_progression_to_approved_preparation_cycle_handoff_chain_bridge_reentry_continuation_boundary()`へ、supplied object identityを保持した`(result, workflow, approval, employee, state_path, events_path)`のcanonical six-argument orderで正確に一度だけ委譲します。Phase 130のexact `PreparedWorkflowStep`について、workflow/step/index/employee、instructions、model、allowed-tool tupleを再検証し、同じobjectを返します。正常経路ではstate/eventsをbyte-for-byte不変に保ち、safe error identity、両target補償、unexpected errorのsanitize、`dependency_rollback`、no retryを維持します。

`workflow_complete`と`persisted_failure`はapproval/employeeが`None`であり、Phase 136 stop contractより不必要に厳しいprovider/index条件を追加せず、Phase 130を呼ばず同じobjectを返すunchanged zero-call stop routeです。workflow_completeのsuccess terminal outputはPhase 136 stop contractどおりnon-emptyです。Phase 137はPhase 123やPhase 131を直接呼び出さず、prepared-step start、start-state persistence、provider/tool execution、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しません。Focused testsはinjected Phase 130 fakesのみを使用し、real provider、network、paid API、external tool、credential、transportを呼びません。

Phase 130については、prepare-next-step routeだけで、Phase 136が正当に生成しうるexact built-in empty success `output_text`を受理する狭い互換修正を行いました。workflow current-step linkage、completed prefix、predecessor、terminal provider/response/request、failure semantics、stop behavior、public API、および共有terminal history contractは緩和していません。

```text
Phase 136
prepare_next_step | workflow_complete | persisted_failure
    ↓
Phase 137 progression-to-approved-preparation cycle handoff chain bridge outer reentry continuation boundary
prepare_next_step (current_step_index >= 1) + exact approval/employee
    → Phase 130 exactly once in canonical six-argument order
    → exact PreparedWorkflowStep
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 131 (future explicit caller action)
```

## Prepared-step Start Cycle Handoff Chain Bridge Outer Reentry Continuation Boundary（Phase 138）

`route_prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary()`は、Phase 137から受け取ったexact `PreparedWorkflowStep`とexact `EmployeeDefinition`を検証するouter bridgeです。prepared routeでは`step_index >= 2`、workflow/step/employee linkage、prepared predecessorのstate/history、completed prefix、直前predecessor provider=`"openai"`、terminal response/request/outputのexact contractを再検証し、empty success `output_text`もexact built-in `str`として許容します。

検証後、公開Phase 131 `route_prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary()`へ、`(result, workflow, employee, state_path, events_path)`のcanonical five-argument orderとsupplied-object identityを保持して正確に一度だけ委譲します。Phase 131のexact `PreparedStepExecutionStart`とnested request/running stateを再検証し、正常経路のstate/eventsをbyte-for-byte不変に保ちます。safe error identity、両target補償、unexpected errorのsanitize、`dependency_rollback`、no retryを維持します。

`workflow_complete`と`persisted_failure`はemployeeが`None`であり、Phase 137 stop contractより不必要に厳しいprovider/index条件を追加せず、Phase 131を呼ばず同じobjectを返すunchanged zero-call stop routeです。workflow-complete success terminal outputはstop contractどおりnon-emptyとします。Phase 138はPhase 124やPhase 132を直接呼び出さず、prepared-start persistence、provider/tool execution、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しません。Focused testsはinjected Phase 131 fakesのみを使用し、real provider、network、paid API、external tool、credential、transportを呼びません。

Phase 131には、prepared-step routeだけでPhase 137のvalid exact empty success `output_text`を受理する狭い互換修正を追加しました。workflow linkage、completed prefix、predecessor、terminal provider/response/request、failure semantics、stop behavior、public API、および共有terminal history contractは緩和していません。

```text
Phase 137
PreparedWorkflowStep | workflow_complete | persisted_failure
    ↓
Phase 138 prepared-step start cycle handoff chain bridge outer reentry continuation boundary
PreparedWorkflowStep (step_index >= 2) + exact employee
    → Phase 131 exactly once in canonical five-argument order
    → exact PreparedStepExecutionStart
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 132 (future explicit caller action)
```

## Prepared-start Persistence Cycle Handoff Chain Bridge Outer Reentry Continuation Boundary（Phase 139）

`route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary()`は、Phase 138から受け取ったexact `PreparedStepExecutionStart`と`EmployeeDefinition`を、公開Phase 132へcanonical five-argument order `(result, workflow, employee, state_path, events_path)`でexactly once委譲するouter persistence bridgeです。prepared routeでは`running_state.current_step_index >= 2`、exact nested request/running state、workflow/step/employee linkage、Phase 138 predecessor terminal state/history（predecessor index = `running_state.current_step_index - 1`; step 2 continuationではexact succeeded step 1）、immediate predecessor provider=`"openai"`、terminal response/request/output contractを再検証します。exact built-in empty success `output_text`は許容します。

正常なPhase 132 persistenceではstate targetだけをsupplied running stateのbytesへ更新し、events targetをbyte-for-byte不変に保ち、exact `RunningStatePersistenceResult` identityを返します。malformed result、wrong persistence、target mutation、safe/unexpected errorは既存のcompensation、detail-safe classification、`dependency_rollback`、no-retry契約を維持します。`workflow_complete`と`persisted_failure`はemployee=`None`でPhase 132を呼ばず、同じobjectを返すunchanged zero-call stopです。workflow-completeのempty success outputは拒否します。Phase 139はPhase 125を直接呼ばず、Phase 133、provider/tool execution、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しません。Focused testsはinjected Phase 132 fakesのみを使用します。

Phase 132には、prepared-start routeだけでexact built-in empty success `output_text`を受理する狭い互換fallbackを追加しました。workflow linkage、completed prefix、predecessor、terminal provider/response/request、failure semantics、stop behavior、public API、`terminal_history_contract.py`は緩和していません。

```text
Phase 138
PreparedStepExecutionStart | workflow_complete | persisted_failure
    ↓
Phase 139 prepared-start persistence cycle handoff chain bridge outer reentry continuation boundary
PreparedStepExecutionStart (running index >= 2) + exact employee
    → Phase 132 exactly once in canonical five-argument order
    → exact RunningStatePersistenceResult
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 133 (future explicit caller action)
```

## Non-final Empty-success Terminal-history Compatibility（Phase 140）

Phase 140は新しいouter bridgeや公開APIを追加せず、Phase 139のprepared-start persistence default chainが、既にPhase 138/139で受理している非final succeeded historyをそのまま処理できるようにする共有契約の互換修正です。`state.status == "succeeded"`かつ`state.current_step_index < len(workflow.steps)`のhistoryでは、terminalおよびそれ以前の succeeded eventの`output_text`にexact built-in `str`の空文字を許容します。workflow、step、index、employee、response、failure、message、history order、completed prefix、file-loadingの契約は変更しません。

final succeeded workflow-complete historyでは従来どおりterminal success `output_text`はnon-emptyであり、failure historyの契約も不変です。これにより、Phase 139からPhase 132/125/118/111/104へ続くreal default dependency chainが、empty success outputをsentinelへ変換したり、provider/toolを実行したりせずに、exact `RunningStatePersistenceResult`を返せることをregression testで確認します。Phase 140はretry、自動継続、Phase 139→133 outer bridge、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しません。

Phase 129のpersisted-success local validationも、shared loaderが正常成功する非final historyでは同じexact built-in `str`のempty/non-empty output rangeを受理します。legacyのfinal persisted-success empty-output fallbackは維持し、workflow-completeのempty success、failed history、provider/request/response、linkage、failure/message semanticsは変更しません。これにより、互換修正はPhase 139 default chain全体で同じ非final契約になる最小範囲に留まります。

## Persisted-Running Execution Cycle Handoff Chain Bridge Outer Reentry Continuation Boundary（Phase 141）

`route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary()`は、Phase 139から受け取ったexact `RunningStatePersistenceResult`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を、公開Phase 133へcanonical ten-argument order `(result, start, workflow, employee, state_path, events_path, resolved_tools, api_key, approval, transport)`でexactly once委譲するouter boundaryです。実行routeでは`PreparedStepExecutionStart.running_state.current_step_index >= 2`、exact workflow/start/request/running-state/employee/tools/credential/approval/transport、regular targets、Phase 139のcanonical running-state bytes、succeeded predecessor history、immediate predecessor provider=`"openai"`を再検証します。step 2ではexact persisted running stateとexact succeeded step-1 predecessor historyを使用します。exact built-in empty success `output_text`はPhase 140の非final契約どおり許容します。

検証後、公開Phase 133 `route_persisted_running_execution_cycle_handoff_chain_bridge_reentry_continuation_boundary()`へsupplied object identityを保持したまま正確に一度だけ委譲し、exact `StepRuntimeExecutionSuccess`または`StepRuntimeExecutionFailure`、nested invocation result、provider=`"openai"`、runtime linkage、target byte-for-byte不変性を再検証して同じresult objectを返します。`workflow_complete`と`persisted_failure`はexecution-only inputを`None`に限定し、Phase 133を呼ばず同じobjectを返すunchanged zero-call stop routeです。

Phase 133には、実行routeの非final predecessor historyだけでexact built-in empty success `output_text`を受理する狭い互換修正を追加しました。workflow_complete final-historyのnon-empty契約、failed history、provider/response/request、failure semantics、stop behavior、public APIは緩和していません。Phase 141はPhase 126を直接呼ばず、runtime-result persistence、outcome classification、workflow progression、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorを追加しません。safe dependency errorはsuccessful compensation後もidentityを保持し、unexpected error、malformed return、target mutationはdetail-safeに分類します。両targetを可能な限り元bytesへ補償復元し、復元失敗は`dependency_rollback`、dependency callはretryしません。Focused testsはinjected Phase 133 fakesのみを使い、real provider、network、paid API、external tool、credential use、real transportを呼びません。

```text
Phase 139
RunningStatePersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 141 persisted-running execution cycle handoff chain bridge outer reentry continuation boundary
RunningStatePersistenceResult (current_step_index >= 2) + exact execution inputs
    → Phase 133 exactly once in canonical ten-argument order
    → exact StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 134 (future explicit caller action)
```

## Runtime-Result Transition Persistence Cycle Handoff Chain Bridge Outer Reentry Continuation Boundary（Phase 142）

`route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary()`は、Phase 141から受け取ったexact `StepRuntimeExecutionSuccess`/`StepRuntimeExecutionFailure`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を、公開Phase 134へcanonical four-argument order `(result, workflow, state_path, events_path)`でexactly once委譲するouter boundaryです。実行routeでは`current_step_index >= 5`、exact workflow/running-state/runtime-result linkage、regular targets、succeeded predecessor history、immediate predecessor provider=`"openai"`を再検証します。exact built-in empty success `output_text`は実行routeのpredecessor検証で許容します。

検証後、公開Phase 134 `route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary()`へsupplied object identityを保持したまま正確に一度だけ委譲し、exact `WorkflowExecutionPersistenceResult`、target identity、byte counts、reloadしたterminal state/history、terminal eventのlinkage・semantics、provider=`"openai"`、request/response provenanceを再検証して同じpersistence result objectを返します。`workflow_complete`と`persisted_failure`はterminal historyを先に検証し、Phase 134を呼ばず同じobjectを返すunchanged zero-call stop routeです。

Phase 134には、実行routeの非final predecessor historyだけでexact built-in empty success `output_text`を受理する狭い互換修正を追加しました。workflow_complete stop routeのterminal `output_text` non-empty契約、failed history、provider/response/request、failure semantics、stop behavior、public APIは緩和していません。Phase 142自身はpersistence logicを重複実装せず、Phase 134をexactly once呼ぶことで明示的に認可されたruntime-result transition persistenceを行います。Phase 127は直接呼びません。outcome classification、workflow progression、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorは行いません。safe dependency errorはsuccessful compensation後もidentityを保持し、unexpected error、malformed return、target mutationはdetail-safeに分類します。両targetを可能な限り元bytesへ補償復元し、復元失敗は`dependency_rollback`、dependency callはretryしません。Focused testsはinjected Phase 134 fakesのみを使い、real provider、network、paid API、external tool、credential use、real transportを呼びません。

```text
Phase 141
StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure | workflow_complete | persisted_failure
    ↓
Phase 142 runtime-result transition persistence cycle handoff chain bridge outer reentry continuation boundary
StepRuntimeExecutionSuccess | StepRuntimeExecutionFailure (current_step_index >= 5) + exact runtime inputs
    → Phase 134 exactly once in canonical four-argument order
    → exact WorkflowExecutionPersistenceResult
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 135 (existing explicit caller action)
```

## Persisted-Transition Outcome Classification Cycle Handoff Chain Bridge Outer Reentry Continuation Boundary（Phase 143）

`route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary()`は、Phase 142から受け取ったexact `WorkflowExecutionPersistenceResult`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を処理するouter boundaryです。persistence/classification routeでは、exact workflow/step models、regular targets、target identity、positive exact byte counts、terminal state/history、current step index `>= 5`、succeeded predecessor history、immediate predecessor provider=`"openai"`、terminal event linkageを再検証し、公開Phase 135 `route_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary()`へcanonical four-argument order `(result, workflow, state_path, events_path)`とsupplied-object identityでexactly once委譲します。exact `PersistedExecutionOutcome`を再検証して同一objectで返し、正常経路でstate/eventsをbyte-for-byte不変に保ちます。

`workflow_complete`と`persisted_failure`はPhase 135を呼ばず、terminal state/historyを検証して同じobjectを返すunchanged zero-call stop routeです。stop routeでは非終端succeeded predecessorのexact built-in `str output_text == ""`を許容しますが、workflow_completeの最終terminal succeeded eventの`output_text` non-empty契約とpersisted-failure terminal semanticsは維持します。

Phase 143はPhase 128を直接参照・呼び出しせず、Phase 136へ進みません。progression、next-step preparation、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorは追加しません。safe dependency errorはsuccessful compensation後もidentityを保持し、unexpected error、malformed return、target mutationはdetail-safeに分類して両targetを補償復元します。復元失敗は`dependency_rollback`、retryはありません。Focused testsはinjected Phase 135 fakesのみを使用し、real provider、network、paid API、external tool、credential、transportを呼びません。

```text
Phase 142
WorkflowExecutionPersistenceResult | workflow_complete | persisted_failure
    ↓
Phase 143 persisted-transition outcome-classification cycle handoff chain bridge outer reentry continuation boundary
WorkflowExecutionPersistenceResult (current_step_index >= 5)
    → Phase 135 exactly once in canonical four-argument order
    → exact PersistedExecutionOutcome
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 136 (future explicit caller action)
```

## Classified Persisted-Outcome Progression Cycle Handoff Chain Bridge Outer Reentry Continuation Boundary（Phase 144）

`route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary()`は、Phase 143から受け取ったexact `PersistedExecutionOutcome(persisted_success)`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を処理するouter boundaryです。persisted-success routeでは、exact workflow/step models、regular targets、target identity、positive exact byte counts、terminal state/history、current step index `>= 5`、succeeded predecessor history、immediate predecessor provider=`"openai"`、terminal event linkageを再検証し、公開Phase 136 `route_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_reentry_continuation_boundary()`へcanonical four-argument order `(result, workflow, state_path, events_path)`とsupplied-object identityでexactly once委譲します。返却された`WorkflowProgressionDecision`を再検証し、同一objectを返します。正常経路でstate/eventsをbyte-for-byte不変に保ちます。

`workflow_complete`と`persisted_failure`はPhase 136を呼ばず、terminal state/historyを検証して同じobjectを返すunchanged zero-call stop routeです。stop routeは`minimum_index=1`を受理し、非openai terminal providerと非終端succeeded predecessorのexact built-in `str output_text == ""`を許容しますが、workflow_completeの最終terminal succeeded eventの`output_text` non-empty契約とpersisted-failure terminal semanticsは維持します。

Phase 144自身はprogression logicを重複実装しません。public Phase 136をexactly once呼ぶことで、明示的に認可された1回のprogression handoffを実行します。Phase 129は直接呼ばず、Phase 137へ自動継続しません。next-step preparation、step start、start-state persistence、runtime execution、runtime-result persistence、retry、自動継続、finalize、schedule、loop、parallel execution、CLI/GUI behaviorは追加しません。Phase 129/137/143のpublic route identifier、`._validate_`、`._top`、`._raise`は使用しません。safe dependency errorはsuccessful compensation後もidentityを保持し、unexpected error、malformed return、target mutationはdetail-safeに分類して両targetを補償復元します。復元失敗は`dependency_rollback`、retryはありません。Focused testsはinjected Phase 136 fakesのみを使用し、real provider、network、paid API、external tool、credential、transportを呼びません。

```text
Phase 143
PersistedExecutionOutcome(persisted_success) | workflow_complete | persisted_failure
    ↓
Phase 144 classified persisted-outcome progression cycle handoff chain bridge outer reentry continuation boundary
PersistedExecutionOutcome(persisted_success) (current_step_index >= 5)
    → Phase 136 exactly once in canonical four-argument order
    → exact WorkflowProgressionDecision
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 137 (future explicit caller action; not called by Phase 144)
```

## Progression-to-Approved Preparation Cycle Handoff Chain Bridge Outer-Chain Reentry Continuation Boundary（Phase 145）

`route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary()`は、Phase 144から受け取ったexact `WorkflowProgressionDecision(prepare_next_step)`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を処理するouter-chain boundaryです。prepare routeでは、exact workflow/step models、regular targets、current step index `>= 1`（workflow step 1 onward; historical Phase-position lower bounds are removed）、current/next/reason linkage、completed-step prefix、Phase 144 provenanceのsucceeded predecessor history、immediate predecessor provider=`"openai"`、terminal event linkage（terminal provider=`"openai"`、response_id non-empty、request_id `None`またはnon-empty、success `output_text`はexact built-in `str`でemptyも許容）を再検証し、公開Phase 137 `route_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary()`へcanonical six-argument order `(result, workflow, approval, employee, state_path, events_path)`でexactly once委譲します。返却された`PreparedWorkflowStep`を再検証し、正常経路でstate/eventsをbyte-for-byte不変に保ちます。

`workflow_complete`と`persisted_failure`はPhase 137を呼ばず、terminal state/historyを検証して同じobjectを返すunchanged zero-call stop routeです。stop routeは`minimum_index=1`を受理し、非openai terminal providerとsucceeded predecessorのexact built-in `str output_text == ""`を許容しますが、workflow_completeの最終terminal succeeded eventの`output_text` non-empty契約とpersisted-failure terminal semanticsは維持します。

Phase 145自身はprogression logicを重複実装しません。public Phase 137をexactly once呼ぶことで、明示的に認可された1回のprogression-to-preparation handoffを実行します。Phase 130/138/144のpublic route identifier、`._validate_`、`._top`、`._raise`は使用しません。Phase 145はPhase 138や他の後続phaseを直接呼ばず、provider、network、paid API、external tool、credential、transport、start-state persistence、retry、自動継続を実行しません。safe dependency error（Phase 137 error）はsuccessful compensation後もidentityを保持し、unexpected error、malformed return、target mutationはdetail-safeに分類して両targetを補償復元します。復元失敗は`dependency_rollback`、retryはありません。Focused testsはinjected Phase 137 fakesのみを使用し、real provider、network、paid API、external tool、credential、transportを呼びません。

```text
Phase 144
WorkflowProgressionDecision(prepare_next_step) | workflow_complete | persisted_failure
    ↓
Phase 145 progression-to-approved-preparation cycle handoff chain bridge outer-chain reentry continuation boundary
prepare_next_step (current_step_index >= 1, Phase 144 provenance; existing compatibility thresholds preserved)
    → Phase 137 exactly once in canonical six-argument order
    → exact PreparedWorkflowStep
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 138 (future explicit caller action; not called by Phase 145)
```

## Prepared-Step Start Cycle Handoff Chain Bridge Outer-Chain Reentry Continuation Boundary（Phase 146）

`route_prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary()`は、Phase 145から受け取ったexact `PreparedWorkflowStep`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を処理するouter-chain boundaryです。prepared-step routeでは、exact `PreparedWorkflowStep`、exact `WorkflowDefinition`/`WorkflowStepDefinition`、exact `EmployeeDefinition`、regular targets、exact built-in `int step_index >= 2`（generic continuation domain; historical Phase-position lower bound removed; Phase 145 provenance/compatibility thresholds below are preserved）、workflow/step/index/employee linkage、employee ID/instructions/model/allowed-tools linkage、exact built-in tuple `allowed_tool_names`、persisted predecessor terminal succeeded state（`prepared.step_index - 1`、step 2 continuationでは exact succeeded step 1がpredecessor）、complete ordered succeeded predecessor/terminal history、succeeded completed-step prefix、`last_failure_category is None`、Phase 145 provenance（全historyがexact `RuntimeStepEvent`、earlier predecessorはexact `step_succeeded`・`running -> succeeded`・provider non-empty str・immediate predecessor provider=`"openai"`・still earlier providerはvalid non-`openai`・response_id/request_id non-empty・`output_text`はexact built-in `str`でemptyも許容・failure_category/messageは`None`、terminal eventはprovider=`"openai"`・request_id `None`またはnon-empty・response_id non-empty・`output_text`はexact built-in `str`でemptyも許容）を再検証し、公開Phase 138 `route_prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary()`へcanonical five-argument order `(result, workflow, employee, state_path, events_path)`でexactly once委譲します。返却されたexact `PreparedStepExecutionStart`（exact nested `ModelInvocationRequest`/`WorkflowExecutionState`、request linkage、running stateはstatus=`"running"`・`current_step_index >= 2`・completed-step prefix継承・`last_failure_category is None`）を再検証し、正常経路でstate/eventsをbyte-for-byte不変に保ちます。

`workflow_complete`と`persisted_failure`はPhase 138を呼ばず、Phase 145 stop-route契約でterminal state/historyを検証して同じobjectを返すunchanged zero-call stop routeです。stop routeは`step_index >= 6`を課さず、non-openai terminal providerとsucceeded predecessorのexact built-in `str output_text == ""`を許容しますが、workflow_completeの最終terminal succeeded eventの`output_text` non-empty契約とpersisted-failure terminal semanticsは維持します。

Phase 146自身はstart logicを重複実装しません。public Phase 138をexactly once呼ぶことで、明示的に認可された1回のprepared-step-start handoffを実行します。Phase 131/139/145のpublic route identifier、`._validate_`、`._top`、`._raise`は使用しません。Phase 146はPhase 139、provider、network、paid API、external tool、credential、transport、start-state persistence、retry、自動継続を呼びません。safe dependency error（Phase 138 error）はsuccessful compensation後もidentityを保持し、unexpected error、malformed return、target mutationはdetail-safeに分類して両targetを補償復元します。復元失敗は`dependency_rollback`、retryはありません。Focused testsはinjected Phase 138 fakesのみを使用し、real provider、network、paid API、external tool、credential、transportを呼びません。

```text
Phase 145
PreparedWorkflowStep | workflow_complete | persisted_failure
    ↓
Phase 146 prepared-step start cycle handoff chain bridge outer-chain reentry continuation boundary
PreparedWorkflowStep (step_index >= 2) + exact employee
    → Phase 138 exactly once in canonical five-argument order
    → exact PreparedStepExecutionStart
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 139 (future explicit caller action; not called by Phase 146)
```

## Prepared-Start Persistence Cycle Handoff Chain Bridge Outer-Chain Reentry Continuation Boundary（Phase 147）

`route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary()`は、Phase 146 continuation pathが生成したexact `PreparedStepExecutionStart`、`WorkflowProgressionDecision(workflow_complete)`、または`PersistedExecutionOutcome(persisted_failure)`を受け取るouter-chain boundaryです。prepared-start routeでは、exact `PreparedStepExecutionStart`、exact nested `ModelInvocationRequest`/`WorkflowExecutionState`、exact `WorkflowDefinition`/`WorkflowStepDefinition`、exact `EmployeeDefinition`、regular targets、exact built-in `int running_state.current_step_index >= 2`（generic continuation domain; historical Phase-position lower bound removed）、workflow/step/index/employee linkage、request model/system/task instructions/allowed-tools linkage、exact built-in tuple `allowed_tools`、succeeded completed-step prefix、`last_failure_category is None`、persisted predecessor terminal succeeded state（predecessor index = `running_state.current_step_index - 1`; step 2 continuationではexact succeeded step 1）、complete ordered predecessor/terminal history、Phase 146 provenance（全historyがexact `RuntimeStepEvent`、earlier predecessorはexact `step_succeeded`・`running -> succeeded`・provider non-empty built-in `str`・immediate predecessor provider=`"openai"`・still earlier providerはvalid non-`openai`・response_id/request_id non-empty・`output_text`はexact built-in `str`でemptyも許容・failure_category/messageは`None`、terminal eventはprovider=`"openai"`・request_id `None`またはnon-empty・response_id non-empty・`output_text`はexact built-in `str`でemptyも許容）を再検証し、公開Phase 139 `route_prepared_start_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary()`へcanonical five-argument order `(result, workflow, employee, state_path, events_path)`でexactly once委譲します。Phase 139が実行する明示的に認可された1回のprepared-start running-state persistenceの結果、exact `RunningStatePersistenceResult`（exact built-in `int state_bytes_written > 0`、state target bytesがserialized running stateと一致、reloadでexact `WorkflowExecutionState`がsupplied running stateと等しい、event targetはbyte-for-byte不変、index `>= 2`維持）を再検証し、dependency objectをidentityそのまま返します。

`workflow_complete`と`persisted_failure`はPhase 139を呼ばず、`employee is None`でPhase 146 stop-route契約（`current_step_index >= 6`を課さず、valid non-openai terminal providerを許容、non-final succeeded predecessor `output_text`はexact built-in `str`でemptyも許容、workflow_complete最終terminal `output_text` non-empty契約とpersisted-failure terminal semanticsは維持）に従いterminal state/historyを検証して同じobjectを返すunchanged zero-call stop routeです。

Phase 147自身はpersistence logicを重複実装しません。Phase 132、Phase 141、Phase 146を直接呼ばず、public Phase 139をexactly once呼ぶことで明示的に認可された1回のprepared-start persistence handoffを実行します。Phase 139のunderscore/private member、`._validate_`、`._top`、`._raise`は使用しません。Phase 147はrunning step実行、provider/network/paid API/external tool/credential/transport、runtime-result persistence、outcome classification、progression、retry、自動継続、finalize/schedule/loop/parallel、CLI/GUIを実行しません。safe dependency error（Phase 139 error）はsuccessful compensation後もidentityを保持し、unexpected error、malformed return、target mutationはdetail-safeに分類して両targetを補償復元します。復元失敗は`dependency_rollback`、retryはありません。Focused testsはinjected Phase 139 fakesのみを使用し、real provider、network、paid API、external tool、credential、transportを呼びません。

```text
Phase 146
PreparedStepExecutionStart | workflow_complete | persisted_failure
    ↓
Phase 147 prepared-start persistence cycle handoff chain bridge outer-chain reentry continuation boundary
PreparedStepExecutionStart (running index >= 2) + exact employee
    → Phase 139 exactly once in canonical five-argument order
    → exact RunningStatePersistenceResult
workflow_complete | persisted_failure
    → unchanged zero-call stop
    ↓
Phase 141 (future explicit caller action; not called by Phase 147)
```

## Phase 148: Phase 147 Default Persistence Chain Multi-Continuation Empty-Success Compatibility Repair

Phase 148は新しいorchestration boundaryではなく、Phase 147マージ後に判明した1つの互換性ギャップを修復するcompatibility/correctness repairです。Phase 140は非final succeeded continuation history全体でexact built-in `str output_text == ""`を有効と定め、Phase 147とその直接依存のPhase 139は、それ以前のsucceeded predecessor eventのempty `output_text`を含めてその範囲を維持するよう修正済みです。しかし実default dependency chainではPhase 132が全てのearlier predecessorをlocal `_valid_predecessor()`で再検証し、`_nonempty_string(event.output_text)`を要求していました。このため、Phase 140/146/147で有効なmulti-continuation historyがPhase 147とPhase 139の検証は通過しても、実defaultのPhase 132依存が公開継続契約より厳しいために失敗していました。

Phase 148はPhase 132 prepared-start persistence routeだけに1つの狭い修正を加えます。prepared routeのpredecessor検証に`allow_empty_predecessor_output`許容を追加し、earlier predecessorのsucceeded event `output_text`がexact built-in `str`のまま、emptyでもnon-emptyでも有効にします。この緩和は全てのearlier predecessorに適用されるため、1回の継続だけでなく複数回の継続をまたいだhistoryでも互換性が維持されます。

修復後の経路:

```text
Phase 147
  → Phase 139
    → Phase 132
      → Phase 125 default persistence chain

non-final succeeded predecessor history
  → every success output_text remains exact built-in str
  → empty or non-empty is preserved through the real default persistence chain
```

predecessor `response_id`は既存のexact non-empty built-in `str`契約を維持し、`request_id`は既存のPhase 132契約（valid `None`を含む）を維持し、providerは既存のPhase 132契約を維持してearlier predecessorに新しい`"openai"`要求を追加しません。exact workflow/step/index/employee linkage、`running -> succeeded`、failure/message semantics、history ordering/length、completed prefix、start/request/employee linkage、target semantics、persistence result、compensation、classification、retry behavior、Phase 132 continuation lower bound、`workflow_complete`/`persisted_failure` stop routes、final workflow-complete success outputのstrict non-empty契約、failed historyは全て変更しません。

Phase 148は以下を行いません:

- 新しいpublic boundaryの追加
- Phase 141の呼び出し
- provider/toolの実行
- runtime resultのpersistence
- outcomeのclassification
- workflowのprogression
- retry
- 他のstepの自動継続
- final workflow-complete semanticsの変更
- `src/ai_office/engine/terminal_history_contract.py`の変更（Phase 140が共有契約を所有）
- finalize/schedule/loop/parallel behaviorの追加
- CLI/GUI behaviorの追加

Phase 141 execution-chainの互換性監査/修復は、このPhaseのレビューとマージ後に明示的な作業として残ります。

## Phase 149: Phase 141 Default Execution Chain Optional Immediate-Predecessor Request-ID Compatibility Repair

Phase 149は新しいorchestration boundaryではなく、Phase 148レビュー後の明示的な作業として残されたPhase 141 execution-chainの互換性ギャップを修復するcompatibility/correctness repairです。Phase 141の実default dependency chainでは、Phase 141とその直接依存のPhase 133がlocal `_valid_predecessor_event()`でpredecessorを再検証し、succeeded predecessor eventの`request_id`にexact non-empty built-in `str`を要求していました。しかし実default chainが最終transport境界まで到達する経路では、直前のpredecessor（immediate predecessor）のsucceeded event `request_id`が`None`になることがあります。これはPhase 147/148のprepared-start persistence chainのterminal successが`request_id=None`を保持する契約と整合します。このため、実default chainでimmediate predecessorの`request_id=None`がPhase 141とPhase 133の検証を通過できず、Phase 126以下へ委譲されないという互換性ギャップがありました。

Phase 149はPhase 141とPhase 133のexecution routeだけに1つの狭い修正を加えます。predecessor検証に`allow_none_request_id`許容を追加し、`position == len(expected_steps)`のimmediate predecessorに限り、succeeded event `request_id`を`None`またはexact non-empty built-in `str`のどちらでも有効にします（immediate predecessor: `request_id` is `None` OR `request_id` is an exact non-empty built-in `str`）。stop route（`WorkflowProgressionDecision`/`PersistedExecutionOutcome`）とそれより前のpredecessorは従来どおりexact non-empty built-in `str`の`request_id`を要求し（earlier predecessor: `request_id` is an exact non-empty built-in `str`）、`request_id`の`None`許可はimmediate predecessorだけに限定されます。

修復後の経路:

```text
Phase 141
  → Phase 133
    → Phase 126 default execution chain

running state at the final step
  → immediate predecessor succeeded request_id is None or exact non-empty str
  → None or exact non-empty str is preserved through the real default execution chain
  → earlier predecessor request_id stays exact non-empty str
```

predecessor `output_text`は既存のPhase 141/133契約を維持します（Phase 149はempty-outputを変更せず、実default-chain回帰では全てnon-emptyにしてPhase 126以下の既知のempty-output問題と分離します）。`response_id`は既存のexact non-empty built-in `str`契約を維持し、providerは既存のPhase 141/133契約（immediate predecessorのみ`"openai"`要求）を維持し、earlier predecessorに新しい`"openai"`要求を追加しません。exact workflow/step/index/employee linkage、`running -> succeeded`、failure/message semantics、history ordering/length、completed prefix、start/request/employee linkage、target semantics、persistence result、compensation、classification、retry behavior、Phase 126 continuation lower bound、`workflow_complete`/`persisted_failure` stop routes、final workflow-complete success outputのstrict non-empty契約、failed history、transport非実行時の契約は全て変更しません。

Phase 149は以下を行いません:

- 新しいpublic boundaryの追加
- Phase 126以下の変更（empty `output_text`問題や`request_id`検証を含む）
- provider/toolの実行
- runtime resultのpersistence
- outcomeのclassification
- workflowのprogression
- retry
- 他のstepの自動継続
- final workflow-complete semanticsの変更
- `src/ai_office/engine/terminal_history_contract.py`の変更（Phase 140が共有契約を所有）
- finalize/schedule/loop/parallel behaviorの追加
- CLI/GUI behaviorの追加

## Phase 150: Phase 126 → 119 → 112 Execution Segment Empty-Success Compatibility Repair

Phase 150は新しいorchestration boundaryではなく、Phase 149レビュー後に明示的な作業として残されたpersisted-running execution-chainの互換性ギャップのうち、最初の境界セグメントを修復するstaged compatibility/correctness repairです。Phase 140は非final succeeded continuation history eventの`output_text`がexact built-in `str`である限りemptyでもnon-emptyでも有効と定め、Phase 147/148はその有効なhistoryを保存し、Phase 141/133はexecution routeでempty exact-string predecessor outputを受理済みです。しかし実default lower execution chainでは、succeeded predecessor eventを非empty `output_text`要求で再検証していました。

Phase 150はその下流セグメントのうち、次の3つの実boundaryだけを修復します:

```text
Phase 141 / Phase 133（変更なし: empty exact-string predecessor outputを受理）
  ↓
Phase 126（修正: empty exact built-in str output_textのsucceeded predecessorを受理）
  ↓
Phase 119（修正: 同上）
  ↓
Phase 112（修正: 同上）
  ↓
Phase 105（変更なし: empty output_textを拒否したまま = 次の明示的シーム、対象外）
```

### 契約

persisted-running execution routeの各succeeded predecessor history eventについて、`output_text`はexact built-in `str`のまま、`output_text == ""`は有効、non-empty exact built-in `str`は有効、`None`は無効、非string値は無効のままです。provenance/linkage、provider規則、request-ID/response-ID規則、workflow/step/index/employee/status/history-order/history-length linkage、state/result byte-count、runtime-result validation、compensation、dependency-error、rollback、stop-route semantics、final `workflow_complete` terminal success outputのstrict non-empty契約は全て変更しません。共有Phase 140 terminal-history契約は変更せず、final/failed terminal semanticsを引き続き所有します。

### 修正範囲

- `persisted_running_execution_cycle_handoff_chain_reentry_continuation_boundary.py` — Phase 126 empty-success output互換のみ
- `persisted_running_execution_cycle_handoff_reentry_continuation_boundary.py` — Phase 119 empty-success output互換のみ
- `persisted_running_execution_cycle_reentry_continuation_boundary.py` — Phase 112 empty-success output互換のみ

`src/ai_office/engine/__init__.py`は変更せず、新しいpublic APIは追加しません。Phase 105以下は変更せず、`Phase 105 → Phase 98 → Phase 91 → Phase 84 → Phase 77 → Phase 70 → Phase 63 → Phase 56 → Phase 49 → Phase 42 / Phase 36`のlower chain修復は将来の明示的Phaseに委ねます。

Phase 150は以下を行いません:

- 新しいorchestration boundaryの追加
- Phase 141/133/149のproduction behavior変更
- Phase 105以下の変更
- Phase 147/148のpersistence behavior変更
- `src/ai_office/engine/terminal_history_contract.py`の変更
- request-ID/provider/response-ID semanticsの拡張・強化
- final workflow-complete terminal success outputのempty化
- failed terminal history semanticsの変更
- provider/network/paid API/external toolの実行
- runtime resultのpersistence
- outcomeのclassification
- workflowのprogression
- retry・自動継続
- finalize/schedule/loop/parallel behaviorの追加
- CLI/GUI behaviorの追加

## Phase 151: Phase 105 → 98 → 91 Execution Segment Empty-Success Compatibility Repair

Phase 151は新しいorchestration boundaryではなく、Phase 150レビュー後に明示的な作業として残されたpersisted-running execution-chainの互換性ギャップのうち、次の境界セグメントを修復するstaged compatibility/correctness repairです。Phase 140は非final succeeded continuation history eventの`output_text`がexact built-in `str`である限りemptyでもnon-emptyでも有効と定め、Phase 150はPhase 126 → Phase 119 → Phase 112の3境界でempty exact-string predecessor outputを受理済みです。しかし実default lower execution chainでは、Phase 105より下流のsucceeded predecessor eventを非empty `output_text`要求で再検証していました。

Phase 151はその下流セグメントのうち、次の3つの実boundaryだけを修復します:

```text
Phase 126 / Phase 119 / Phase 112（変更なし: empty exact-string predecessor outputを受理）
  ↓
Phase 105（修正: empty exact built-in str output_textのsucceeded predecessorを受理）
  ↓
Phase 98（修正: 同上）
  ↓
Phase 91（修正: 同上）
  ↓
Phase 84（変更なし: empty output_textを拒否したまま = 次の明示的シーム、対象外）
```

### 契約

persisted-running execution routeの各succeeded predecessor history eventについて、`output_text`はexact built-in `str`のまま、`output_text == ""`は有効、non-empty exact built-in `str`は有効、`None`は無効、非string値は無効のままです。provenance/linkage、provider規則、request-ID/response-ID規則、workflow/step/index/employee/status/history-order/history-length linkage、state/result byte-count、runtime-result validation、compensation、dependency-error、rollback、stop-route semantics、final `workflow_complete` terminal success outputのstrict non-empty契約は全て変更しません。共有Phase 140 terminal-history契約は変更せず、final/failed terminal semanticsを引き続き所有します。

### 修正範囲

- `persisted_running_execution_cycle_continuation_boundary.py` — Phase 105 empty-success output互換のみ
- `persisted_running_execution_dispatch_continuation_boundary.py` — Phase 98 empty-success output互換のみ
- `persisted_running_execution_dispatch_phase_bridge_cycle_reentry_continuation.py` — Phase 91 empty-success output互換のみ

`src/ai_office/engine/__init__.py`は変更せず、新しいpublic APIは追加しません。Phase 84以下は変更せず、`Phase 84 → Phase 77 → Phase 70 → Phase 63 → Phase 56 → Phase 49 → Phase 42 / Phase 36`のlower chain修復は将来の明示的Phaseに委ねます。

Phase 151は以下を行いません:

- 新しいorchestration boundaryの追加
- Phase 126/119/112以上のproduction behavior変更
- Phase 84以下の変更
- `src/ai_office/engine/terminal_history_contract.py`の変更
- request-ID/provider/response-ID semanticsの拡張・強化
- final workflow-complete terminal success outputのempty化
- failed terminal history semanticsの変更
- provider/network/paid API/external toolの実行
- runtime resultのpersistence
- outcomeのclassification
- workflowのprogression
- retry・自動継続
- finalize/schedule/loop/parallel behaviorの追加
- CLI/GUI behaviorの追加

## Phase 152: Phase 84 → 77 → 70 Execution Segment Empty-Success Compatibility Repair

Phase 152は新しいorchestration boundaryではなく、Phase 151レビュー後に明示的な作業として残されたpersisted-running execution-chainの互換性ギャップのうち、次の境界セグメントを修復するstaged compatibility/correctness repairです。Phase 140は非final succeeded continuation history eventの`output_text`がexact built-in `str`である限りemptyでもnon-emptyでも有効と定め、Phase 150はPhase 126 → Phase 119 → Phase 112、Phase 151はPhase 105 → Phase 98 → Phase 91の3境界でempty exact-string predecessor outputを受理済みです。しかし実default lower execution chainでは、Phase 84より下流のsucceeded predecessor eventを非empty `output_text`要求で再検証していました。

Phase 152はその下流セグメントのうち、次の3つの実boundaryだけを修復します:

```text
Phase 105 / Phase 98 / Phase 91（変更なし: empty exact-string predecessor outputを受理）
  ↓
Phase 84（修正: empty exact built-in str output_textのsucceeded predecessorを受理）
  ↓
Phase 77（修正: 同上）
  ↓
Phase 70（修正: 同上）
  ↓
Phase 63（変更なし: empty output_textを拒否したまま = 次の明示的シーム、対象外）
```

### 契約

persisted-running execution routeの各succeeded predecessor history eventについて、`output_text`はexact built-in `str`のまま、`output_text == ""`は有効、non-empty exact built-in `str`は有効、`None`は無効、非string値は無効のままです。provenance/linkage、provider規則、request-ID/response-ID規則、workflow/step/index/employee/status/history-order/history-length linkage、state/result byte-count、runtime-result validation、compensation、dependency-error、rollback、stop-route semantics、final `workflow_complete` terminal success outputのstrict non-empty契約は全て変更しません。共有Phase 140 terminal-history契約は変更せず、final/failed terminal semanticsを引き続き所有します。

### 修正範囲

- `persisted_running_execution_routing_phase_bridge_cycle_reentry_continuation.py` — Phase 84 empty-success output互換のみ
- `persisted_running_execution_routing_phase_bridge_cycle_continuation.py` — Phase 77 empty-success output互換のみ
- `persisted_running_execution_routing_phase_bridge_continuation.py` — Phase 70 empty-success output互換のみ

`src/ai_office/engine/__init__.py`は変更せず、新しいpublic APIは追加しません。Phase 63以下は変更せず、`Phase 63 → Phase 56 → Phase 49 → Phase 42 / Phase 36`のlower chain修復は将来の明示的Phaseに委ねます。Phase 42はpersisted running executionをPhase 36へrouteし、predecessor eventの`output_text`を自身では再検証しないため、Phase 152ではPhase 42/36も変更しません。

Phase 152は以下を行いません:

- 新しいorchestration boundaryの追加
- Phase 105/98/91以上のproduction behavior変更
- Phase 63以下の変更
- `src/ai_office/engine/terminal_history_contract.py`の変更
- request-ID/provider/response-ID semanticsの拡張・強化
- final workflow-complete terminal success outputのempty化
- failed terminal history semanticsの変更
- provider/network/paid API/external toolの実行
- runtime resultのpersistence
- outcomeのclassification
- workflowのprogression
- retry・自動継続
- finalize/schedule/loop/parallel behaviorの追加
- CLI/GUI behaviorの追加

## Phase 153: Phase 63 → 56 → 49 Execution Segment Empty-Success Compatibility Repair

Phase 153は新しいorchestration boundaryではなく、persisted-running execution-chainの最後に残っていたlocally-strict predecessor-history segmentを修復するstaged compatibility/correctness repairです。Phase 140は非final succeeded continuation history eventの`output_text`がexact built-in `str`である限りemptyでもnon-emptyでも有効と定め、Phase 150はPhase 126 → Phase 119 → Phase 112、Phase 151はPhase 105 → Phase 98 → Phase 91、Phase 152はPhase 84 → Phase 77 → Phase 70の3境界でempty exact-string predecessor outputを受理済みです。しかし実default lower execution chainでは、Phase 63より下流のsucceeded predecessor eventを非empty `output_text`要求で再検証していました。

Phase 153はその最後の下流セグメントのうち、次の3つの実boundaryだけを修復します:

```text
Phase 84 / Phase 77 / Phase 70（変更なし: empty exact-string predecessor outputを受理）
  ↓
Phase 63（修正: exact built-in strのままtruthiness要求のみ除去）
  ↓
Phase 56（修正: isinstance(str)方針を維持したままtruthiness要求のみ除去）
  ↓
Phase 49（修正: isinstance(str)方針を維持したままtruthiness要求のみ除去）
  ↓
Phase 42（変更なし: predecessor event historyを再検証しない）
  ↓
Phase 36（変更なし: state/execution boundary、predecessor event history検証なし）
```

### 契約

persisted-running execution routeの各succeeded predecessor history eventについて、`output_text`はPhase 63ではexact built-in `str`のまま、Phase 56/49では`isinstance(..., str)`方針のまま、`output_text == ""`は有効、non-empty stringは有効、`None`・非string値は無効のままです。Phase 153はempty-output修復を理由にローカルtype semanticsを変更しません。provenance/linkage、provider規則、request-ID/response-ID規則、workflow/step/index/employee/status/history-order/history-length linkage、state/result byte-count、runtime-result validation、compensation、dependency-error、rollback、stop-route semantics、final `workflow_complete` terminal success outputのstrict non-empty契約は全て変更しません。共有Phase 140 terminal-history契約は変更せず、final/failed terminal semanticsを引き続き所有します。

### 修正範囲

- `persisted_running_execution_routing_phase_bridge_reentry.py` — Phase 63 empty-success output互換のみ（exact `type(...) is str`維持・truthiness要求のみ除去）
- `persisted_running_execution_phase_bridge_reentry.py` — Phase 56 `_prior_success_contract()`のみ（`isinstance`維持・`bool(event.output_text)`のみ除去）
- `persisted_running_execution_bridge_reentry.py` — Phase 49 running-route predecessor validationのみ（`isinstance`維持・`bool(event.output_text)`のみ除去）

`src/ai_office/engine/__init__.py`は変更せず、新しいpublic APIは追加しません。Phase 42/36はpredecessor eventの`output_text`を再検証しないため変更しません。`Phase 42 → Phase 36`の実default chain全体（Phase 141からexecution pathまで）のreal-default regressionは将来の明示的Phaseに委ねます。

Phase 153は以下を行いません:

- 新しいorchestration boundaryの追加
- Phase 84/77/70以上のproduction behavior変更
- Phase 42/36/29・provider/transport実装の変更
- `src/ai_office/engine/terminal_history_contract.py`の変更
- Phase 56/49の`isinstance`からexact型への引き締め
- request-ID/provider/response-ID semanticsの拡張・強化
- final workflow-complete terminal success outputのempty化
- failed terminal history semanticsの変更
- provider/network/paid API/external toolの実行
- runtime resultのpersistence
- outcomeのclassification
- workflowのprogression
- retry・自動継続
- finalize/schedule/loop/parallel behaviorの追加
- CLI/GUI behaviorの追加

## Phase 154: Phase 141 → Execution Whole-Chain Real-Default Empty-Success Regression

Phase 154はPhase 140–153のempty-success compatibility lineを閉じる**integration/closure proof**であり、production codeを一切変更しないcoverage-only Phaseです。公開Phase 141 `route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(...)`の実default dependency chainだけを呼び、persisted-running execution boundaryから実際のexecution/transport boundaryまでを1つの単位として通します。

```text
real Phase 141
  → real Phase 133
    → real Phase 126
      → real Phase 119
        → real Phase 112
          → real Phase 105
            → real Phase 98
              → real Phase 91
                → real Phase 84
                  → real Phase 77
                    → real Phase 70
                      → real Phase 63
                        → real Phase 56
                          → real Phase 49
                            → real Phase 42
                              → real Phase 36
                                → actual execution path
                                  → synthetic final transport only
```

合成できるのは最終`transport` callableだけです。Phase dependencyのoverride、fake boundary、monkeypatch、wrapperは一切使いません。synthetic transportは実認証済みOpenAI requestをexactly once受けて決定的なsynthetic HTTP responseを返し、実`StepRuntimeExecutionSuccess`とresponse/request-ID/status/outputの各フィールドを検証します。

### 検証済みシナリオ（6 collected cases）

1. **earlier empty predecessor** — step 2 `output_text == ""`、immediate step-5 `request_id`はexact non-empty `str`、他のpredecessor outputはnon-empty
2. **immediate empty predecessor + Phase 149 provenance** — step 5 `output_text == ""`、step 5 `request_id is None`、earlier request IDsはexact non-empty string（Phase 149 request-ID repairとPhase 150–153 empty-output repairの合成証明）
3. **multiple earlier empty predecessors** — steps 2と4がempty、immediate outputはnon-empty、immediate `request_id is None`
4. **earlier + immediate empty outputs together** — step 2とstep 5の両方がempty、immediate `request_id is None`
5–6. **invalid output rejected before transport**（parametrized 2 cases）— predecessor `output_text is None` / `output_text == 123`はPhase 141 entryが既存のsafe compatibility classification（`persistence_result_contract`）で拒否、transport call countは0、state/eventsはbyte-for-byte不変

各valid caseで最低限、transport callがexactly once、受信requestが実認証済みOpenAI request type、戻り値が実`StepRuntimeExecutionSuccess`、workflow/step/index/employeeがstep-6期待値、provider=`"openai"`、response ID/request ID/status=`"completed"`/output textが決定的synthetic値、state/events targetがbyte-for-byte不変であることを検証します。

### 契約保持

Phase 154はproduction contractを変更しません。Phase 141/133のimmediate predecessor `request_id=None` allowance、earlier predecessor request-IDのexact non-empty built-in string、predecessor response-ID/provider/linkage規則、Phase 63 exact built-in `str` policy、Phase 56/49 local `isinstance(..., str)` policy、final `workflow_complete` succeeded terminal outputのstrict non-empty、failed terminal semantics、persistence/compensation/rollback、runtime-result validation、provider/tool/credential behaviorは全て不変です。`src/ai_office/engine/terminal_history_contract.py`は変更しません。

### 変更範囲

- `tests/test_persisted_running_execution_default_chain_empty_success_compatibility.py` — 新規（6 collected cases）
- `README.md` — 本ドキュメント
- `docs/architecture.md` — Phase 154 section

`src/`配下は一切変更しません。Phase 149 request-ID regressionとPhase 150–153 segment regressionsも変更しません。

Phase 154は以下を行いません:

- production codeの変更
- 新しいorchestration boundaryの追加
- orchestration、retry、自動継続、schedule、parallelism、GUI、provider behaviorの追加
- Phase dependencyのfake/inject/monkeypatch
- real network/provider/paid API/tool call
- final workflow-complete terminal success outputのempty化
- failed terminal history semanticsの変更
- CLI/GUI behaviorの追加

## Phase 155: Persisted-Running Execution Cycle Handoff Chain Bridge Outer-Chain Reentry Continuation Boundary

Phase 155はPhase 147のpersisted-running execution結果を公開Phase 141へ明示的にhandoffする**新しいouter-chain orchestration boundary**です。Phase 147が生成したexact `RunningStatePersistenceResult`と対応するexact `PreparedStepExecutionStart`（`running_state.current_step_index >= 2`、Phase 147 continuation provenance）を、公開Phase 141 `route_persisted_running_execution_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary(...)`へcanonical ten-argument order `(result, start, workflow, employee, state_path, events_path, resolved_tools, api_key, approval, transport)`でexactly once委譲します。step 2ではexact persisted running stateとexact succeeded step-1 predecessor historyを受け取り、下流実行が返すruntime resultだけを返します。

```text
Phase 147 persisted-running execution result
  → Phase 155 outer-chain boundary（新規）
    → public Phase 141（exactly once）
      → real default lower chain
        → synthetic final transport only
```

`phase141_function`はkeyword-onlyで、公開Phase 141関数を既定値とします。依存呼び出し前にexact model/type（`RunningStatePersistenceResult`、`PreparedStepExecutionStart`、nested `ModelInvocationRequest`/`WorkflowExecutionState`、`WorkflowDefinition`/`WorkflowStepDefinition`、`EmployeeDefinition`、`ToolDefinition`/`ToolParameterDefinition`、`OpenAIApiKey`/`SecretStr`、`ModelInvocationExecutionApproval`）、workflow/step/index/employee linkage、approval contract、state bytesがserialized running stateと一致すること、`RunningStatePersistenceResult` byte count、predecessor history（immediate predecessorはempty `output_text`・`request_id is None`またはexact non-empty `str`・provider=`"openai"`、earlier predecessorはexact non-empty built-in `str` request ID・既存Phase 141のprovider許容範囲維持）を検証します。

`WorkflowProgressionDecision(workflow_complete)`と`PersistedExecutionOutcome(persisted_failure)`のstop routeはPhase 141を呼ばず、Phase 155内でstop-domain（terminal history、final succeeded non-empty output、failed terminal semantics）を自前検証して同一オブジェクトをidentityで返します。stop routeのpredecessorはempty `output_text`とterminalのnon-`"openai"` providerを許容しますが、workflow-completeの最終succeeded outputは非空exact built-in `str`を要求します。

dependencyは一度だけ呼びます。正常なexact runtime result（`StepRuntimeExecutionSuccess`/`StepRuntimeExecutionFailure`）はidentityのまま返し、malformed returnまたはtarget mutationは両targetをcompensationします。safe Phase 141 errorはcompensation後もidentityを保持し、unexpected errorはdetail-safeにsanitize（`dependency_error`）、rollback failureは`dependency_rollback`、retryはありません。分類は`persistence_result_contract` / `terminal_contract` / `start_contract` / `execution_inputs` / `runtime_contract` / `result_type` / `workflow_definition` / `employee_contract` / `tools_contract` / `credential_contract` / `approval_contract` / `completion_contract` / `failure_contract` / `state_target` / `event_target` / `target_conflict` / `dependency_error` / `dependency_rollback`です。

### 変更範囲

- `src/ai_office/engine/persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py` — 新規（production semantic changeはこのmoduleのみ）
- `src/ai_office/engine/__init__.py` — 公開exportのみ
- `tests/test_persisted_running_execution_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py` — 新規（134 focused cases + 1 real-default smoke）
- `README.md` — 本ドキュメント
- `docs/architecture.md` — Phase 155 section

Phase 141以下、Phase 133、Phase 142、Phase 147、`src/ai_office/engine/terminal_history_contract.py`は変更しません。Phase 133の直接呼び出し、Phase 142の呼び出し、Phase 147の再呼び出し、Phase 141のbypass/duplicate、runtime resultのpersist、outcomeのclassify、progression、retry、自動継続は行いません。

Phase 155は以下を行いません:

- Phase 141以下・Phase 133・Phase 142・Phase 147のproduction behavior変更
- `src/ai_office/engine/terminal_history_contract.py`の変更
- Phase 133直接呼び出し・Phase 142呼び出し・Phase 147再呼び出し・Phase 141 bypass/duplicate
- runtime resultのpersistence
- outcomeのclassification
- workflowのprogression
- retry・自動継続
- real network/provider/paid API/tool call（real-default smokeのsynthetic seamは最終`transport`のみ）
- finalize/schedule/loop/parallel behaviorの追加
- CLI/GUI behaviorの追加

### Issue #371: Phase 155 → 141 → 133 Aged Request-ID Provenance Execution Compatibility Repair

Issue #371は新しいorchestration boundaryではなく、Phase 176のcapture-only delegating Phase 147 adapterがrunning step 6をrunning step 7へ進めた後、同じ不変のstep-5 succeeded event（`request_id=None`、provider=`"openai"`）が「直前のpredecessor」から「それより前のpredecessor」へagingする際の互換性を修復するaged-provenance compatibility repairです。

```text
canonical step-5 success
request_id=None, provider=openai
    ↓ was immediate predecessor of running step 6
Phase 176 advances to running step 7
    ↓ same immutable event is now an earlier predecessor
Phase 155 → 141 → 133
    preserve that already-valid historical provenance
    ↓
Phase 126 and lower execution chain unchanged
```

Phase 155 / Phase 141 / Phase 133のexecution routeだけに、predecessor検証の`allow_none_request_id`を`position >= 5 or position == len(expected_steps)`へ狭く緩和します。positions 1-4は従来どおり`request_id`にexact non-empty built-in `str`を要求し、`request_id==""`・非str・非Noneは常にinvalid、`request_id=None`のときはproviderが正確に`"openai"`であることを要求します（非空`request_id`のearlier eventの既存provider semanticsは維持）。immediate predecessorのprovider=`"openai"`要求はNone/非空`request_id`の両方で維持します。stop route、Phase 126以下、`terminal_history_contract.py`は変更しません。

このIssueはPhase 177を追加せず、Phase 176後に自動実行せず、新しいruntime resultをpersistせず、再度progressせず、retry/loop/schedule/finalize/parallel/CLI/GUI behaviorを変更しません。

## Phase 156: Phase 142 → 134 → 127 Transition-Persistence Segment Phase-155 Provenance Compatibility Repair

Phase 156は、Phase 155以降で有効になったrunning continuation provenanceを、最初のtransition-persistence互換セグメント（Phase 142 → Phase 134 → Phase 127）が正しく受け渡せるようにする**staged compatibility/correctness repair**です。新しいorchestration boundaryは追加しません。

```text
Phase 155 runtime result
    ↓ explicit caller action
Phase 142 → Phase 134 → Phase 127
    repaired to preserve Phase-155 empty-output / immediate-request_id-None provenance
    ↓
Phase 120
    remains the next explicit strict seam; unchanged
```

Phase 155は現在、以下を同時に満たすrunning continuation provenanceを正しく生成・受理します。

- `current_step_index >= 6`
- succeeded predecessor `output_text`はexact built-in `str`（empty/non-empty）
- earlier predecessor `request_id`はexact non-empty built-in `str`
- immediate predecessor `request_id`は`None`またはexact non-empty built-in `str`
- immediate predecessor providerはexact `"openai"`

Phase 156は、この全ドメインを最初のtransition-persistenceセグメントが保持できるよう、以下の3つのproduction boundaryだけを狭く修正します。

### Production correction A — Phase 142 runtime route

`src/ai_office/engine/runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py`

- 既存のruntime-route empty-output許容を維持
- earlier predecessorは引き続きexact non-empty built-in `str request_id`を要求
- immediate succeeded predecessorは`request_id is None`またはexact non-empty built-in `str`を許容
- empty string・non-string・non-`None`は引き続きinvalid
- immediate providerはexact `"openai"`、`response_id`はexact non-empty built-in `str`、`output_text`はexact built-in `str`（empty/non-empty valid）を維持
- stop routesは変更なし

`allow_none_request_id`フラグは`_valid_predecessor_event`のローカル引数として追加し、非runtime利用は厳格なまま維持します。

### Production correction B — Phase 134 runtime route

`src/ai_office/engine/runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary.py`

Phase 142と同じ狭いimmediate-predecessor request-ID互換規則を適用します。provider/response/output/linkage規則とstop routesは変更なし、Phase 127の呼び出し方・persistence semanticsは変更しません。

### Production correction C — Phase 127 runtime route

`src/ai_office/engine/runtime_result_transition_persistence_cycle_handoff_chain_reentry_continuation_boundary.py`

- succeeded predecessor `output_text`のtruthiness/non-empty要件だけを除去
- `output_text`はexact built-in `str`を維持し、`""`と非空を許容
- `None`・non-stringは引き続きinvalid
- 新しいrequest-ID要件は導入せず、既存のPhase 127 request-ID挙動を正確に維持
- provider規則の強化なし、stop routes変更なし、persistence/compensation/runtime-result契約は変更なし

### Real-segment regression

`tests/test_runtime_result_transition_persistence_phase142_127_phase155_provenance_compatibility.py`（新規）で、**実Phase 142 → 実Phase 134 → 実Phase 127 → synthetic Phase 120 seam**の6ケースを追加しました。

- exact `StepRuntimeExecutionSuccess` / `StepRuntimeExecutionFailure`、earlier-empty + immediate-empty + immediate-`request_id=None`の組み合わせがseamへexactly once到達
- 複数earlier-empty + immediate-empty/`None`もseamへexactly once到達
- earlier `request_id=None`、immediate `request_id==""`はPhase 142でPhase 134/127/seamより前にreject
- 各handoffでcanonical four-argument identity/order保持、seam結果object identityを全実境界がそのまま返すことを検証

synthetic seamは実境界のpersistence再検証を満たすため、最小のdeterministic persistence seamとしてexact expected terminal stateを書き、terminal eventを正確に1件appendし、exact `WorkflowExecutionPersistenceResult`を返します（production moduleのmonkeypatchなし）。

### Collect invariant

```text
11,220 + 24 = 11,244
```

- Phase 142 focused: +6
- Phase 134 focused: +6
- Phase 127 focused: +6
- real-segment regression: +6

### 変更範囲（9ファイル）

1. `src/ai_office/engine/runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — Phase 142 narrow request-ID compatibility
2. `tests/test_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — +6 focused collected
3. `src/ai_office/engine/runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary.py` — Phase 134 narrow request-ID compatibility
4. `tests/test_runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary.py` — +6 focused collected
5. `src/ai_office/engine/runtime_result_transition_persistence_cycle_handoff_chain_reentry_continuation_boundary.py` — Phase 127 narrow empty-output compatibility
6. `tests/test_runtime_result_transition_persistence_cycle_handoff_chain_reentry_continuation_boundary.py` — +6 focused collected
7. `tests/test_runtime_result_transition_persistence_phase142_127_phase155_provenance_compatibility.py` — 新規、exactly 6 collected
8. `README.md` — 本ドキュメント
9. `docs/architecture.md` — Phase 156 section

### 変更しないもの

- `src/ai_office/engine/__init__.py`（新しいpublic APIなし）
- Phase 155 production/tests、Phase 120およびそれ以下のtransition-persistence boundary
- Phase 143以降のclassification/progression boundary
- `src/ai_office/engine/terminal_history_contract.py`
- provider/runtime/storage generic modules

### Phase 156は以下を行いません

- 新しいpublic boundaryの追加
- Phase 155 → 142の自動継続
- Phase 143の呼び出し
- outcome classification / workflow progression
- retry / loop / schedule / parallel / finalize behavior
- CLI / GUI behavior
- real network / provider / paid API / tool call

## Phase 157: Phase 120 → 113 → 106 Transition-Persistence Segment Phase-155 Provenance Compatibility Repair

Phase 157は、Phase 156で修復した最初のtransition-persistenceセグメント（Phase 142 → 134 → 127）の次にあるセグメント（Phase 120 → Phase 113 → Phase 106）が、Phase-155 provenance runtime resultを正しく受け渡せるようにする**staged compatibility/correctness repair**です。新しいorchestration boundaryは追加しません。

```text
Phase 155 runtime result
    ↓ explicit caller action
Phase 142 → 134 → 127   (Phase 156 repaired)
    ↓
Phase 120 → 113 → 106
    repaired to preserve Phase-155 empty-output provenance
    ↓
Phase 99
    remains the next explicit strict seam; unchanged
```

Phase 155 / 156は現在、以下を同時に満たすrunning continuation provenanceを正しく生成・受理します。

- `current_step_index >= 6`
- succeeded predecessor `output_text`はexact built-in `str`（empty/non-empty）
- earlier predecessor `request_id`はexact non-empty built-in `str`
- immediate predecessor `request_id`は`None`またはexact non-empty built-in `str`
- immediate predecessor providerはexact `"openai"`

Phase 157は、この全ドメインを次のtransition-persistenceセグメントが保持できるよう、以下の3つのproduction boundaryだけを狭く修正します。

### Production correction A — Phase 120 runtime route

`src/ai_office/engine/runtime_result_transition_persistence_cycle_handoff_reentry_continuation_boundary.py`

`_check_running_history`内のsucceeded predecessor `output_text`に対するtruthiness/non-empty要件だけを除去します。

- `type(event.output_text) is str`を維持し、`""`と非空を許容
- `None`・non-stringは引き続きinvalid
- `response_id`のexact non-empty built-in `str`要件を維持
- 新しいrequest-ID/provider要件は導入せず、Phase 120の現在のrequest-ID/provider挙動を正確に維持
- continuation lower bound（`current_step_index >= 2`）・stop routes・Phase 113呼び出し方・persistence/compensation/safe-error挙動は変更なし

### Production correction B — Phase 113 runtime route

`src/ai_office/engine/runtime_result_transition_persistence_cycle_reentry_continuation_boundary.py`

`_validate_running_history`内に同じ狭いempty-output修正を適用します。

- exact built-in `str`型要件を維持し、empty/non-emptyを許容
- `None`・non-stringは引き続きinvalid
- Phase 113の現在のrequest-ID/provider挙動を正確に維持
- runtime-result linkage・stop routes・Phase 106呼び出し方・persistence/compensation/error挙動は変更なし

### Production correction C — Phase 106 runtime route

`src/ai_office/engine/runtime_result_transition_persistence_cycle_continuation_boundary.py`

`_validate_running_history`内に同じ狭いempty-output修正を適用します。

- exact built-in `str`型要件を維持し、empty/non-emptyを許容
- `None`・non-stringは引き続きinvalid
- Phase 106の現在のrequest-ID/provider挙動を正確に維持
- runtime-result linkage・stop routes・Phase 99呼び出し方・persistence/compensation/error挙動は変更なし

Phase 99は変更せず、本Phase後も次のexplicit strict empty-output seamとして残ります。

### Focused regression additions

各focusedファイルにexactly 6 collected casesを追加しました（既存テストの削除・弱体化なし）。

- Phase 120 focused（`..._cycle_handoff_reentry_continuation_boundary.py`）: earlier/immediate/combined exact empty `output_text`がPhase 113へexactly once委譲、`None`/`123`/`True`はPhase 113より前にreject
- Phase 113 focused（`..._cycle_reentry_continuation_boundary.py`）: 同じ6ケースをPhase 113→106境界で検証
- Phase 106 focused（`..._cycle_continuation_boundary.py`）: 同じ6ケースをPhase 106→99境界で検証

### Real-segment regression

`tests/test_runtime_result_transition_persistence_phase120_106_phase155_provenance_compatibility.py`（新規）で、**実Phase 120 → 実Phase 113 → 実Phase 106 → synthetic Phase 99 seam**の6ケースを追加しました。

- exact `StepRuntimeExecutionSuccess` / `StepRuntimeExecutionFailure`、earlier-empty（step 2）+ immediate-empty（step 5）+ immediate-`request_id=None`の組み合わせがseamへexactly once到達
- 複数earlier-empty + immediate-empty/`None`もseamへexactly once到達
- earlier `output_text=None`、immediate `output_text=None`はPhase 120でPhase 113/106/seamより前にreject（目的のprovenanceはreloadで明示的に検証）
- 各handoffでcanonical four-argument identity/order保持、seam結果object identityを全実境界がそのまま返すことを検証
- pre-seam historyにearlier-empty・immediate-empty・immediate-`request_id=None`が実際に含まれることをexplicit reloadで検証

synthetic seamは実境界のpersistence再検証を満たすため、最小のdeterministic persistence seamとしてexact expected terminal stateを書き、terminal eventを正確に1件appendし、exact `WorkflowExecutionPersistenceResult`を返します（production moduleのmonkeypatchなし）。

### Collect invariant

```text
11,244 + 24 = 11,268
```

- Phase 120 focused: +6
- Phase 113 focused: +6
- Phase 106 focused: +6
- real-segment regression: +6

### 変更範囲（9ファイル）

1. `src/ai_office/engine/runtime_result_transition_persistence_cycle_handoff_reentry_continuation_boundary.py` — Phase 120 narrow empty-output compatibility
2. `tests/test_runtime_result_transition_persistence_cycle_handoff_reentry_continuation_boundary.py` — +6 focused collected
3. `src/ai_office/engine/runtime_result_transition_persistence_cycle_reentry_continuation_boundary.py` — Phase 113 narrow empty-output compatibility
4. `tests/test_runtime_result_transition_persistence_cycle_reentry_continuation_boundary.py` — +6 focused collected
5. `src/ai_office/engine/runtime_result_transition_persistence_cycle_continuation_boundary.py` — Phase 106 narrow empty-output compatibility
6. `tests/test_runtime_result_transition_persistence_cycle_continuation_boundary.py` — +6 focused collected
7. `tests/test_runtime_result_transition_persistence_phase120_106_phase155_provenance_compatibility.py` — 新規、exactly 6 collected
8. `README.md` — 本ドキュメント
9. `docs/architecture.md` — Phase 157 section

### 変更しないもの

- `src/ai_office/engine/__init__.py`（新しいpublic APIなし）
- Phase 155 / 156 productionまたはそのregression
- Phase 99およびそれ以下のtransition-persistence boundary
- Phase 143以降のclassification/progression boundary
- `src/ai_office/engine/terminal_history_contract.py`
- provider/runtime/storage generic modules

### Phase 157は以下を行いません

- 新しいpublic boundaryの追加
- Phase 155 → 142の自動継続
- Phase 143の呼び出し
- outcome classification / workflow progression
- retry / loop / schedule / parallel / finalize behavior
- CLI / GUI behavior
- 新しいrequest-ID/provider semantics
- real network / provider / paid API / tool call

## Phase 158: Phase 99 → 92 → 85 Transition-Persistence Segment Phase-155 Provenance Compatibility Repair

Phase 158は、Phase 157で修復したセグメント（Phase 120 → 113 → 106）の次にあるセグメント（Phase 99 → Phase 92 → Phase 85）が、Phase-155 provenance runtime resultを正しく受け渡せるようにする**staged compatibility/correctness repair**です。新しいorchestration boundaryは追加しません。

```text
Phase 155 runtime result
    ↓ explicit caller action
Phase 142 → 134 → 127   (Phase 156 repaired)
    ↓
Phase 120 → 113 → 106   (Phase 157 repaired)
    ↓
Phase 99 → 92 → 85
    repaired to preserve Phase-155 empty-output provenance
    ↓
Phase 78
    remains the next explicit strict seam; unchanged
```

Phase 155 / 156 / 157は現在、以下を同時に満たすrunning continuation provenanceを正しく生成・受理します。

- `current_step_index >= 6`
- succeeded predecessor `output_text`はexact built-in `str`（empty/non-empty）
- earlier predecessor `request_id`はexact non-empty built-in `str`
- immediate predecessor `request_id`は`None`またはexact non-empty built-in `str`
- immediate predecessor providerはexact `"openai"`

Phase 158は、この全ドメインを次のtransition-persistenceセグメントが保持できるよう、以下の3つのproduction boundaryだけを狭く修正します。

### Production correction A — Phase 99 runtime route

`src/ai_office/engine/executed_result_transition_persistence_dispatch_continuation_boundary.py`

`_validate_running_history`内のsucceeded predecessor `output_text`に対するtruthiness/non-empty要件だけを除去します。

- `type(event.output_text) is str`を維持し、`""`と非空を許容
- `None`・non-stringは引き続きinvalid
- `response_id`のexact non-empty built-in `str`要件を維持
- 新しいrequest-ID/provider要件は導入せず、Phase 99の現在のrequest-ID/provider挙動を正確に維持
- running-state/workflow/linkage validation・stop routes・Phase 92呼び出し方・persistence/compensation/safe-error挙動は変更なし

### Production correction B — Phase 92 runtime route

`src/ai_office/engine/executed_result_transition_persistence_dispatch_phase_bridge_cycle_reentry_continuation.py`

`_validate_running_history`内に同じ狭いempty-output修正を適用します。

- exact built-in `str`型要件を維持し、empty/non-emptyを許容
- `None`・non-stringは引き続きinvalid
- Phase 92の現在のrequest-ID/provider挙動を正確に維持
- runtime-result linkage・stop routes・Phase 85呼び出し方・persistence/compensation/error挙動は変更なし

### Production correction C — Phase 85 runtime route

`src/ai_office/engine/executed_result_transition_persistence_routing_phase_bridge_cycle_reentry_continuation.py`

`_validate_running_history`内に同じ狭いempty-output修正を適用します。

- exact built-in `str`型要件を維持し、empty/non-emptyを許容
- `None`・non-stringは引き続きinvalid
- Phase 85の現在のrequest-ID/provider挙動を正確に維持
- runtime-result linkage・stop routes・Phase 78呼び出し方・persistence/compensation/error挙動は変更なし

Phase 78は変更せず、本Phase後も次のexplicit strict empty-output seamとして残ります。

### Focused regression additions

各focusedファイルにexactly 6 collected casesを追加しました（既存テストの削除・弱体化なし）。

- Phase 99 focused（`..._dispatch_continuation_boundary.py`）: earlier/immediate/combined exact empty `output_text`がPhase 92へexactly once委譲、`None`/`123`/`True`はPhase 92より前にreject
- Phase 92 focused（`..._dispatch_phase_bridge_cycle_reentry_continuation.py`）: 同じ6ケースをPhase 92→85境界で検証
- Phase 85 focused（`..._routing_phase_bridge_cycle_reentry_continuation.py`）: 同じ6ケースをPhase 85→78境界で検証

### Real-segment regression

`tests/test_executed_result_transition_persistence_phase99_85_phase155_provenance_compatibility.py`（新規）で、**実Phase 99 → 実Phase 92 → 実Phase 85 → synthetic Phase 78 seam**の6ケースを追加しました。

- exact `StepRuntimeExecutionSuccess` / `StepRuntimeExecutionFailure`、earlier-empty（step 2）+ immediate-empty（step 5）+ immediate-`request_id=None`の組み合わせがseamへexactly once到達
- 複数earlier-empty + immediate-empty/`None`もseamへexactly once到達
- earlier `output_text=None`、immediate `output_text=None`はPhase 99でPhase 92/85/seamより前にreject（目的のprovenanceはreloadで明示的に検証）
- 各handoffでcanonical four-argument identity/order保持、seam結果object identityを全実境界がそのまま返すことを検証
- pre-seam historyにearlier-empty・immediate-empty・immediate-`request_id=None`が実際に含まれることをexplicit reloadで検証

synthetic seamは実境界のpersistence再検証を満たすため、最小のdeterministic persistence seamとしてexact expected terminal stateを書き、terminal eventを正確に1件appendし、exact `WorkflowExecutionPersistenceResult`を返します（production moduleのmonkeypatchなし）。

### Collect invariant

```text
11,268 + 24 = 11,292
```

- Phase 99 focused: +6
- Phase 92 focused: +6
- Phase 85 focused: +6
- real-segment regression: +6

### 変更範囲（9ファイル）

1. `src/ai_office/engine/executed_result_transition_persistence_dispatch_continuation_boundary.py` — Phase 99 narrow empty-output compatibility
2. `tests/test_executed_result_transition_persistence_dispatch_continuation_boundary.py` — +6 focused collected
3. `src/ai_office/engine/executed_result_transition_persistence_dispatch_phase_bridge_cycle_reentry_continuation.py` — Phase 92 narrow empty-output compatibility
4. `tests/test_executed_result_transition_persistence_dispatch_phase_bridge_cycle_reentry_continuation.py` — +6 focused collected
5. `src/ai_office/engine/executed_result_transition_persistence_routing_phase_bridge_cycle_reentry_continuation.py` — Phase 85 narrow empty-output compatibility
6. `tests/test_executed_result_transition_persistence_routing_phase_bridge_cycle_reentry_continuation.py` — +6 focused collected
7. `tests/test_executed_result_transition_persistence_phase99_85_phase155_provenance_compatibility.py` — 新規、exactly 6 collected
8. `README.md` — 本ドキュメント
9. `docs/architecture.md` — Phase 158 section

### 変更しないもの

- `src/ai_office/engine/__init__.py`（新しいpublic APIなし）
- Phase 155 / 156 / 157 productionまたはそのregression
- Phase 78およびそれ以下のtransition-persistence boundary
- Phase 143以降のclassification/progression boundary
- `src/ai_office/engine/terminal_history_contract.py`
- provider/runtime/storage generic modules

### Phase 158は以下を行いません

- 新しいpublic boundaryの追加
- Phase 155 → 142の自動継続
- Phase 143の呼び出し
- outcome classification / workflow progression
- retry / loop / schedule / parallel / finalize behavior
- CLI / GUI behavior
- 新しいrequest-ID/provider semantics
- real network / provider / paid API / tool call

## Phase 159: Phase 78 → 71 → 64 Transition-Persistence Segment Phase-155 Provenance Compatibility Repair

Phase 159は、Phase 158で修復したセグメント（Phase 99 → 92 → 85）の次にあるセグメント（Phase 78 → Phase 71 → Phase 64）が、Phase-155 provenance runtime resultを正しく受け渡せるようにする**staged compatibility/correctness repair**です。新しいorchestration boundaryは追加しません。

```text
Phase 155 runtime result
    ↓ explicit caller action
Phase 142 → 134 → 127   (Phase 156 repaired)
    ↓
Phase 120 → 113 → 106   (Phase 157 repaired)
    ↓
Phase 99 → 92 → 85      (Phase 158 repaired)
    ↓
Phase 78 → 71 → 64
    repaired to preserve Phase-155 empty-output provenance
    ↓
Phase 57
    remains the next explicit strict seam; unchanged
```

Phase 155 / 156 / 157 / 158は現在、以下を同時に満たすrunning continuation provenanceを正しく生成・受理します。

- `current_step_index >= 6`
- succeeded predecessor `output_text`はexact built-in `str`（empty/non-empty）
- earlier predecessor `request_id`はexact non-empty built-in `str`
- immediate predecessor `request_id`は`None`またはexact non-empty built-in `str`
- immediate predecessor providerはexact `"openai"`

Phase 159は、この全ドメインを次のtransition-persistenceセグメントが保持できるよう、以下の3つのproduction boundaryだけを狭く修正します。

### Production correction A — Phase 78 runtime route

`src/ai_office/engine/executed_result_transition_persistence_routing_phase_bridge_cycle_continuation.py`

`_validate_running_history`内のsucceeded predecessor `output_text`に対するtruthiness/non-empty要件だけを除去します。

- `type(event.output_text) is str`を維持し、`""`と非空を許容
- `None`・non-stringは引き続きinvalid
- `response_id`のexact non-empty built-in `str`要件を維持
- 新しいrequest-ID/provider要件は導入せず、Phase 78の現在のrequest-ID/provider挙動を正確に維持
- running-state/workflow/linkage validation・stop routes・Phase 71呼び出し方・persistence/compensation/safe-error挙動は変更なし

### Production correction B — Phase 71 runtime route

`src/ai_office/engine/executed_result_transition_persistence_routing_phase_bridge_continuation.py`

`_validate_running_history`内に同じ狭いempty-output修正を適用します。

- exact built-in `str`型要件を維持し、empty/non-emptyを許容
- `None`・non-stringは引き続きinvalid
- Phase 71の現在のrequest-ID/provider挙動を正確に維持
- runtime-result linkage・stop routes・Phase 64呼び出し方・persistence/compensation/error挙動は変更なし

### Production correction C — Phase 64 runtime route

`src/ai_office/engine/executed_result_transition_persistence_routing_phase_bridge_reentry.py`

`_validate_running_history`内に同じ狭いempty-output修正を適用します。

- exact built-in `str`型要件を維持し、empty/non-emptyを許容
- `None`・non-stringは引き続きinvalid
- Phase 64の現在のrequest-ID/provider挙動を正確に維持
- runtime-result linkage・stop routes・Phase 57呼び出し方・persistence/compensation/error挙動は変更なし

Phase 57は変更せず、本Phase後も次のexplicit strict empty-output seamとして残ります。

### Focused regression additions

各focusedファイルにexactly 6 collected casesを追加しました（既存テストの削除・弱体化なし）。

- Phase 78 focused（`..._routing_phase_bridge_cycle_continuation.py`）: earlier/immediate/combined exact empty `output_text`がPhase 71へexactly once委譲、`None`/`123`/`True`はPhase 71より前にreject
- Phase 71 focused（`..._routing_phase_bridge_continuation.py`）: 同じ6ケースをPhase 71→64境界で検証
- Phase 64 focused（`..._routing_phase_bridge_reentry.py`）: 同じ6ケースをPhase 64→57境界で検証

### Real-segment regression

`tests/test_executed_result_transition_persistence_phase78_64_phase155_provenance_compatibility.py`（新規）で、**実Phase 78 → 実Phase 71 → 実Phase 64 → synthetic Phase 57 seam**の6ケースを追加しました。

- exact `StepRuntimeExecutionSuccess` / `StepRuntimeExecutionFailure`、earlier-empty（step 2）+ immediate-empty（step 5）+ immediate-`request_id=None`の組み合わせがseamへexactly once到達
- 複数earlier-empty + immediate-empty/`None`もseamへexactly once到達
- earlier `output_text=None`、immediate `output_text=None`はPhase 78でPhase 71/64/seamより前にreject（目的のprovenanceはreloadで明示的に検証）
- 各handoffでcanonical four-argument identity/order保持、seam結果object identityを全実境界がそのまま返すことを検証
- pre-seam historyにearlier-empty・immediate-empty・immediate-`request_id=None`が実際に含まれることをexplicit reloadで検証

synthetic seamは実境界のpersistence再検証を満たすため、最小のdeterministic persistence seamとしてexact expected terminal stateを書き、terminal eventを正確に1件appendし、exact `WorkflowExecutionPersistenceResult`を返します（production moduleのmonkeypatchなし）。

### Collect invariant

```text
11,292 + 24 = 11,316
```

- Phase 78 focused: +6
- Phase 71 focused: +6
- Phase 64 focused: +6
- real-segment regression: +6

### 変更範囲（9ファイル）

1. `src/ai_office/engine/executed_result_transition_persistence_routing_phase_bridge_cycle_continuation.py` — Phase 78 narrow empty-output compatibility
2. `tests/test_executed_result_transition_persistence_routing_phase_bridge_cycle_continuation.py` — +6 focused collected
3. `src/ai_office/engine/executed_result_transition_persistence_routing_phase_bridge_continuation.py` — Phase 71 narrow empty-output compatibility
4. `tests/test_executed_result_transition_persistence_routing_phase_bridge_continuation.py` — +6 focused collected
5. `src/ai_office/engine/executed_result_transition_persistence_routing_phase_bridge_reentry.py` — Phase 64 narrow empty-output compatibility
6. `tests/test_executed_result_transition_persistence_routing_phase_bridge_reentry.py` — +6 focused collected
7. `tests/test_executed_result_transition_persistence_phase78_64_phase155_provenance_compatibility.py` — 新規、exactly 6 collected
8. `README.md` — 本ドキュメント
9. `docs/architecture.md` — Phase 159 section

### 変更しないもの

- `src/ai_office/engine/__init__.py`（新しいpublic APIなし）
- Phase 155 / 156 / 157 / 158 productionまたはそのregression
- Phase 57およびそれ以下のtransition-persistence boundary
- Phase 143以降のclassification/progression boundary
- `src/ai_office/engine/terminal_history_contract.py`
- provider/runtime/storage generic modules

### Phase 159は以下を行いません

- 新しいpublic boundaryの追加
- Phase 155 → 142の自動継続
- Phase 143の呼び出し
- outcome classification / workflow progression
- retry / loop / schedule / parallel / finalize behavior
- CLI / GUI behavior
- 新しいrequest-ID/provider semantics
- real network / provider / paid API / tool call

## Phase 160: Complete Phase-155 Provenance Compatibility across Phase 57 → 50 → 43 → 36 → persistence

Phase 160は、Phase 159で修復したセグメント（Phase 78 → 71 → 64）の次にある最後の遷移区間（**実Phase 57 → 実Phase 50 → 実Phase 43 → 実Phase 36 → 実Phase 30 persistence**）が、Phase-155 provenance runtime resultを正しく受け渡せるようにする**staged compatibility/correctness repair**です。新しいorchestration boundaryは追加しません。

```text
Phase 155 runtime result
    ↓ explicit caller action
Phase 142 → 134 → 127   (Phase 156 repaired)
    ↓
Phase 120 → 113 → 106   (Phase 157 repaired)
    ↓
Phase 99 → 92 → 85      (Phase 158 repaired)
    ↓
Phase 78 → 71 → 64      (Phase 159 repaired)
    ↓
Phase 57 → 50 → 43 → 36 → persistence
    repaired to preserve Phase-155 empty-output provenance
```

Phase 155 / 156 / 157 / 158 / 159は現在、以下を同時に満たすrunning continuation provenanceを正しく生成・受理します。

- `current_step_index >= 6`
- succeeded predecessor `output_text`はexact built-in `str`（empty/non-empty）
- earlier predecessor `request_id`はexact non-empty built-in `str`
- immediate predecessor `request_id`は`None`またはexact non-empty built-in `str`
- immediate predecessor providerはexact `"openai"`

Phase 160は、この全ドメインを最後のtransition-persistence区間が保持できるよう、以下の2つのproduction boundaryだけを狭く修正します。

### Production correction A — Phase 57 runtime route

`src/ai_office/engine/executed_result_transition_persistence_phase_bridge_reentry.py`

`_validate_running_history`内のsucceeded predecessor `output_text`に対するtruthiness/non-empty要件だけを除去します。

- `type(event.output_text) is str`を維持し、`""`と非空を許容
- `None`・non-stringは引き続きinvalid
- `response_id`のexact non-empty built-in `str`要件を維持
- 新しいrequest-ID/provider要件は導入せず、Phase 57の現在のrequest-ID/provider挙動を正確に維持
- running-state/workflow/linkage validation・stop routes・Phase 50呼び出し方・persistence/compensation/safe-error挙動は変更なし

### Production correction B — Phase 50 runtime route

`src/ai_office/engine/executed_result_transition_persistence_bridge_reentry.py`

`_validate_running_history`内に同じ狭いempty-output修正を適用します。

- exact built-in `str`型要件を維持し、empty/non-emptyを許容
- `None`・non-stringは引き続きinvalid
- Phase 50の現在のrequest-ID/provider挙動を正確に維持
- runtime-result linkage・stop routes・Phase 43呼び出し方・persistence/compensation/error挙動は変更なし

Phase 43 / Phase 36 / Phase 30のproduction codeは変更しません。Phase 30は実際の`persist_executed_step_transition`がそのまま最終persistenceを行います。

### Focused regression additions

各focusedファイルにexactly 6 collected casesを追加しました（既存テストの削除・弱体化なし）。

- Phase 57 focused（`..._persistence_phase_bridge_reentry.py`）: earlier/immediate/combined exact empty `output_text`がPhase 50へexactly once委譲、`None`/`123`/`True`はPhase 50より前にreject
- Phase 50 focused（`..._persistence_bridge_reentry.py`）: 同じ6ケースをPhase 50→43境界で検証

### Real lower-chain regression

`tests/test_executed_result_transition_persistence_phase57_30_phase155_provenance_compatibility.py`（新規）で、**実Phase 57 → 実Phase 50 → 実Phase 43 → 実Phase 36 → 実Phase 30（実`persist_executed_step_transition`）**の6ケースを追加しました。

- exact `StepRuntimeExecutionSuccess` / `StepRuntimeExecutionFailure`、earlier-empty（step 2）+ immediate-empty（step 5）+ immediate-`request_id=None`の組み合わせが実persistenceまでexactly once到達
- 複数earlier-empty + immediate-empty/`None`も実persistenceまでexactly once到達
- earlier `output_text=None`、immediate `output_text=None`はPhase 57でPhase 50/43/36/30より前にreject（目的のprovenanceはraw JSONL reloadで明示的に検証）
- 各handoffでcanonical four-argument identity/order保持、最終returnがexact `WorkflowExecutionPersistenceResult`であり、同一のstate/events targetsと正確なbyte countsを持つことを検証
- 実persistence後のstate/eventsにempty-output provenanceが正しく反映されることをexplicit reloadで検証（successは`succeeded` state + `step_succeeded` event、failureは`failed` state + `step_failed` event、provider/response_id/request_id/output_text/messageのexact値を検証）

### Collect invariant

```text
11,316 + 18 = 11,334
```

- Phase 57 focused: +6
- Phase 50 focused: +6
- real lower-chain regression: +6

### 変更範囲（7ファイル）

1. `src/ai_office/engine/executed_result_transition_persistence_phase_bridge_reentry.py` — Phase 57 narrow empty-output compatibility
2. `src/ai_office/engine/executed_result_transition_persistence_bridge_reentry.py` — Phase 50 narrow empty-output compatibility
3. `tests/test_executed_result_transition_persistence_phase_bridge_reentry.py` — +6 focused collected（helperはsentinelで`None`注入を修正）
4. `tests/test_executed_result_transition_persistence_bridge_reentry.py` — +6 focused collected
5. `tests/test_executed_result_transition_persistence_phase57_30_phase155_provenance_compatibility.py` — 新規、exactly 6 collected
6. `README.md` — Phase 160 documentation
7. `docs/architecture.md` — Phase 160 architecture documentation

### 変更しないもの

- `src/ai_office/engine/__init__.py`（新しいpublic APIなし）
- Phase 155 / 156 / 157 / 158 / 159 productionまたはそのregression
- Phase 43 / Phase 36 / Phase 30 production code
- Phase 143以降のclassification/progression boundary
- `src/ai_office/engine/terminal_history_contract.py`
- provider/runtime/storage generic modules

### Phase 160は以下を行いません

- 新しいpublic boundaryの追加
- Phase 155 → 142の自動継続
- Phase 143の呼び出し
- outcome classification / workflow progression
- retry / loop / schedule / parallel / finalize behavior
- CLI / GUI behavior
- 新しいrequest-ID/provider semantics
- real network / provider / paid API / tool call

## Phase 161: Phase-155 Runtime-Result Transition-Persistence Outer-Chain Continuation Boundary

Phase 161は、Phase 155 continuation pathが生成する**exact runtime result**を、既存のpublic Phase 142 boundaryへ**exactly once**渡すcaller boundaryです。Phase 156–160が修復・証明した実persistence chain（Phase 142 → 134 → 127 → 120 → 113 → 106 → 99 → 92 → 85 → 78 → 71 → 64 → 57 → 50 → 43 → 36 → 実Phase 30 persistence）に対して、唯一欠けていた**Phase 155 → Phase 142 runtime-result persistence handoff**を追加します。

```text
Phase 155 runtime result
    ↓ Phase 161
Phase 142 (exactly once, canonical four-argument order)
    ↓ repaired real chain from Phase 156–160
actual Phase 30 persistence
```

Phase 161は新しいcompatibility correctionを行いません。Phase 142以下のproduction boundaryは一切変更せず、public Phase 142関数（`route_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary`）をkeyword-only dependency（`phase142_function`）として注入可能にした上で、runtime-result routeでのみ直接exactly once呼び出します。

### Runtime-result route（success / failure）

- exact `StepRuntimeExecutionSuccess` / `StepRuntimeExecutionFailure`、exact `WorkflowDefinition`、exact `WorkflowStepDefinition`要素、exactで互いに異なるregular `Path` targetsを要求
- 供給targetsからexact running `WorkflowExecutionState`をロードし、workflow/current-step/index/employee linkageを検証
- **Phase-155 continuation provenance**としてexact built-in `int current_step_index >= 6`を要求し、**index 1–5はPhase 142呼び出し前にreject**
- predecessor historyは全stepについてexact `RuntimeStepEvent`、exact `step_succeeded` / `running -> succeeded`、workflow/step/index/employee linkage、`failure_category is None` / `message is None`
- predecessor `output_text`はexact built-in `str`（empty/non-empty許容）、`response_id`はexact non-empty built-in `str`
- earlier predecessor `request_id`はexact non-empty built-in `str`、immediate predecessor `request_id`は`None`またはexact non-empty built-in `str`
- immediate predecessor providerはexact `"openai"`、earlier provider semanticsは継承契約どおり
- runtime resultのnested invocation-resultもexact型・exact built-in field/container型・exact provider `"openai"`・exact success/failure semanticsを再検証
- Phase 142呼び出し前にoriginal state/event bytesをスナップショットし、**4引数すべてをcanonical order・同一identityでexactly once委譲**

### Phase 142 result / persistence validation

- Phase 142の戻り値はexact `WorkflowExecutionPersistenceResult`のみ受理（subclass・attribute-compatible substituteはreject）
- returned `state_path` / `events_path`は供給targetsと同一identity
- `state_bytes_written` / `event_bytes_appended`はexact positive built-in `int`（`bool`・int subclassはreject）
- state targetはsupplied runtime resultに対応するexact terminal `WorkflowExecutionState`、event targetはoriginal predecessor history + current stepのterminal eventをexactly 1件のみ
- terminal eventのworkflow/step/index/employee/provider linkage、success→succeeded / failure→failed semantics、byte counts（state-file length / canonical appended terminal-event byte length）を再検証
- 不正な戻り値・部分/不整合なtarget効果は**両targetをbyte-for-byteでpre-dependency snapshotへ復元し、retryなしでreject**
- safe Phase 142 errorはidentityを保持、unexpected exceptionはsanitize、compensation失敗は`dependency_rollback`に分類、両targetの復元を試行、**Phase 142をretryしない**

### Stop routes（zero call）

- exact `WorkflowProgressionDecision(workflow_complete)` / exact `PersistedExecutionOutcome(persisted_failure)`はPhase 155 stop-route domainを継承
- Phase 142呼び出し回数は**0**、同一supplied objectをそのまま返し、両targetはbyte-for-byte不変
- 非終端predecessorの空`output_text`、継承されたrequest-ID/provider semanticsを保持
- `workflow_complete`のsucceeded terminal output非空strictness、persisted-failure terminal semanticsを保持
- malformed stop values・unsupported progression/outcome・direct persistence/start/running-state値・subclass/substitute・invalid targets・terminal mismatchはzero-call reject

### Focused regression（180 cases）

新規Phase 161 test file（`tests/test_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py`、**182 collected total**）のうち、**focused / contract cases 180件**で、public signature/source audit、canonical four-argument identity、index 1–5 pre-reject、predecessor provenance matrix（duplicate/missing/reordered/unrelated/malformed/extra、empty output、request_id None/empty/non-string、provider）、persistence result exact型・identity・byte counts・terminal state/event semantics、compensation（state-only/event-only/both、malformed return、safe error identity、unexpected sanitize、rollback failure）、stop routes（zero call、empty predecessor output、non-openai terminal provider、empty terminal output reject）を注入Phase 142 fakeで検証します（残り2件は下記real-default persistence cases）。

### Real-default persistence regression（2 cases）

新規Phase 161 test fileの**real-default persistence cases 2件**で、**fake Phase 142 注入なし・production関数のmonkeypatchなし・実provider/network/toolなし**で、Phase 161 public entryだけを外側から呼び、**実Phase 142 → 実下位chain（Phase 156–160で修復済み）→ 実Phase 30 persistence**まで到達させます。

- exact `StepRuntimeExecutionSuccess`とexact `StepRuntimeExecutionFailure`の両ケース
- current running step 6、succeeded steps 1–5、earlier/immediate predecessor `output_text == ""`、immediate predecessor `request_id is None`、earlier request IDs exact non-empty built-in strings、immediate provider exact `"openai"`
- exact `WorkflowExecutionPersistenceResult`返却、exact terminal succeeded/failed state、current stepのterminal event exactly 1件追加、predecessor provenance不変、byte counts exact、retryなし

### Collect invariant

```text
11,334 + 182 = 11,516
```

- Phase 161 new test file: **182 collected total**
  - focused / contract cases: **180**
  - real-default persistence cases: **2**

### 変更範囲（5ファイル）

1. `src/ai_office/engine/runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py` — 新規 Phase 161 module + detail-safe error family
2. `tests/test_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py` — 新規 focused + real-default persistence tests
3. `src/ai_office/engine/__init__.py` — Phase 161 public exportsのみ
4. `README.md` — Phase 161 documentation
5. `docs/architecture.md` — Phase 161 architecture documentation

### 変更しないもの

- Phase 155 / 156 / 157 / 158 / 159 / 160 productionまたはそのregression
- Phase 142 production（呼び出しのみで修正なし）
- Phase 143以降のclassification/progression boundary（**Phase 143は呼び出さない**）
- 実Phase 30 persistence、shared storage/runtime/provider code
- `src/ai_office/engine/terminal_history_contract.py`

### Phase 161は以下を行いません

- Phase 142以下のcompatibility correction
- Phase 143の呼び出し・outcome classification / workflow progression
- 次のstepのprepare/start、provider/tool実行、retry / loop / schedule / parallel / finalize
- Phase 155の再呼び出し・他dependency経由のrouting・private/underscore validation helperの参照
- CLI / GUI behavior、real network / provider / paid API / tool call

## Phase 162: Repair Phase-155 Provenance Compatibility across Phase 143 → 135 → 128 Outcome-Classification Segment

Phase 162は、outcome-classification segment（**実Phase 143 → 実Phase 135 → 実Phase 128 → Phase 121**）がPhase-155 provenance persisted transitionを受け渡せるようにする**staged compatibility repair**です。Phase 156–161が修復・証明したruntime-result persistence chainの直後に存在するclassification segmentで、Phase-155 compatible history（`current_step_index >= 6`）を正しく受理・委譲しつつ、predecessorのrequest-ID / provider policyを追加しないことを保証します。

```text
Phase 143 (outer bridge, immediate predecessor: request_id=None + provider=="openai")
    ↓ Phase 135 (bridge, immediate predecessor: request_id=None + provider=="openai")
    ↓ Phase 128 (chain, Phase-155 compatible history: current_step_index >= 6)
Phase 121 (synthetic seam delegation / real Phase 121 terminal_contract rejection)
```

Phase 162は新規orchestration boundaryを追加せず、既存のpublic route 3つ（Phase 143 / 135 / 128）の`_valid_history` / `_valid_predecessor` / `_valid_phase155_compatible_history`を狭く修正します。Phase 121 production moduleと`terminal_history_contract.py`は変更しません。

### 互換性境界（Phase 143 / 135）

- immediate predecessorの`request_id`は`None`またはexact non-empty built-in `str`を許可し、providerはexact `"openai"`を要求
- earlier predecessorの`request_id=None`とimmediate predecessorの`request_id==""`は拒否
- predecessorの`output_text`はexact built-in `str`（空文字含む）のみ許可（`None` / non-stringは拒否）
- 無効ケースはdownstream dependency call count **zero**とし、分類文字列は`persistence_contract` / `outcome_contract` / `terminal_contract` / `dependency_error`を正確に使用

### 互換性境界（Phase 128）

- `current_step_index >= 6`のPhase-155 compatible history（6-step）を追加受理
- predecessorの`request_id` / provider policyは追加しない（Phase 143/135の境界が保持）
- terminal event semanticsはshared validator（`_valid_terminal_event`）を継承
- 有効な委譲ではcanonical four-argument delegation、dependency exactly-once、returned outcomeのexact identity、targetsのbyte-for-byte unchanged、retryなしを検証

### 実チェーン委譲（synthetic Phase 121 seam）

- **実Phase 143 → 実Phase 135 → 実Phase 128**のreal chainにsynthetic Phase 121 seamを注入
- 呼び出し前に public storage loader（`load_workflow_execution_history`）でpersisted state/historyを明示的にreloadし、Issue #330指定のearlier empty predecessor（step 2）・immediate empty predecessor（step 5）・immediate predecessor `request_id=None`を**実データとして**assert
- reloaded terminal state/historyはexpected success/failure outcome contractと一致することをassert
- `succeeded` / `failed`の両ケースでcanonical four-argument order・同一identity・exactly once委譲、dependency call count `{phase143: 1, phase135: 1, phase128: 1, seam: 1}`、returned outcomeのexact identity、両target byte-for-byte不変、retryなしを検証

### 実Phase 121 rejection reference（delegatesテスト内にinline）

- 上記delegatesテスト内で、実Phase 121ルートを`phase121_function`として渡すと、Phase-155 provenance historyは`PersistedTransitionOutcomeClassificationCycleHandoffReentryContinuationCompatibilityError`・分類`terminal_contract`でrejectされるreferenceを`succeeded` / `failed`両ケースで固定（追加のcollected caseは取らない）
- 両targetはbyte-for-byte不変

### Focused regression（+18 cases）

Phase 143 / 135 / 128の既存test moduleへ各**+6 cases**を追加します。

- Phase 143（outer bridge）: immediate predecessor `request_id=None` + empty `output_text`委譲（succeeded / failed）、immediate predecessor `request_id=None` + non-empty `output_text`委譲（succeeded / failed）、earlier predecessor `request_id=None`拒否（Phase 135へ委譲しない）、immediate predecessor `request_id==""`拒否
- Phase 135（bridge）: 同上のboundaryをPhase 135入口で検証（immediate `request_id=None` + empty / non-empty `output_text`委譲 ×2、earlier `request_id=None`拒否、immediate `request_id==""`拒否）
- Phase 128（chain）: Phase-155 compatible history委譲（earlier-empty step 2 + immediate-empty step 5 + immediate `request_id=None`、succeeded / failed）、multiple earlier empty（step 2・3）+ immediate empty/None委譲（succeeded / failed）、non-string predecessor `output_text`（`None` / `4`）拒否。`index<6`境界・request-ID policy非追加はdelegatesテスト内でinline検証（独立collected caseは取らない）

### Real-segment regression（+6 cases）

新規test file（`tests/test_persisted_transition_outcome_classification_phase143_128_phase155_provenance_compatibility.py`、**6 collected total**）:

- real chain + synthetic Phase 121 seamのdelegation（succeeded / failed）: 呼び出し前に public storage loader（`load_workflow_execution_history`）でpersisted state/historyを明示的にreloadし、earlier empty（step 2）・immediate empty（step 5）・immediate `request_id=None`を実データとしてassert、reloaded terminal state/historyをexpected success/failure outcome contractに照合。実Phase 121の`terminal_contract` rejection referenceもこのdelegatesテスト内でinline実証（追加collected caseは取らない）
- multiple earlier empty predecessors（step 2・3）のdelegation（succeeded / failed）
- earlier predecessor `request_id=None`のPhase 143拒否、immediate predecessor `request_id==""`のPhase 143拒否

### Collect invariant

```text
11,516 + 24 = 11,540
```

- Phase 143 test module: **+6 cases**
- Phase 135 test module: **+6 cases**
- Phase 128 test module: **+6 cases**
- Phase 162 real-segment test file: **+6 cases**

### 変更範囲（9ファイル）

1. `src/ai_office/engine/persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — Phase 143 boundary修正
2. `src/ai_office/engine/persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary.py` — Phase 135 boundary修正
3. `src/ai_office/engine/persisted_transition_outcome_classification_cycle_handoff_chain_reentry_continuation_boundary.py` — Phase 128 boundary修正（Phase-155 compatible history受理）
4. `tests/test_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — Phase 143 regression +6
5. `tests/test_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary.py` — Phase 135 regression +6
6. `tests/test_persisted_transition_outcome_classification_cycle_handoff_chain_reentry_continuation_boundary.py` — Phase 128 regression +6
7. `tests/test_persisted_transition_outcome_classification_phase143_128_phase155_provenance_compatibility.py` — 新規 real-segment regression（6 cases）
8. `README.md` — Phase 162 documentation
9. `docs/architecture.md` — Phase 162 architecture documentation

### 変更しないもの

- Phase 121 production module（`persisted_transition_outcome_classification_cycle_handoff_reentry_continuation_boundary.py`）
- `src/ai_office/engine/terminal_history_contract.py`
- 既存テストの削除・rename・skip・xfail・parameter-collapse・弱体化
- エラー分類・quality feedback literal・provider / request-ID semantics
- 実Phase 30 persistence、shared storage/runtime/provider code、CLI / GUI behavior

## Phase 163: Repair Phase-155 Provenance Compatibility across Phase 121 → 114 → 107 Outcome-Classification Segment

Phase 163は、outcome-classification segment（**実Phase 121 → 実Phase 114 → 実Phase 107 → Phase 100**）がPhase-155 provenance persisted transitionを正しく受け渡せるようにする**staged compatibility repair**です。Phase 162で修復したPhase 143 → 135 → 128セグメントの直後にあるclassification segmentで、`load_strict_terminal_history` がPhase-155 provenance history（`current_step_index >= 6`、predecessorの空`output_text`）を拒否する場合にのみ、public `load_workflow_execution_history` + 限定されたPhase-155互換検証へフォールバックします。

```text
Phase 121 (cycle handoff reentry, final dependency: Phase 114)
    ↓ Phase 114 (cycle reentry, final dependency: Phase 107)
    ↓ Phase 107 (cycle, final dependency: Phase 100)
Phase 100 (strict seam: Phase-155 provenance history は terminal_contract で拒否のまま)
```

### 互換性フォールバック（Phase 121 / 114 / 107 共通）

- `load_strict_terminal_history` が失敗した場合のみ、public `load_workflow_execution_history`（`WorkflowExecutionPersistenceTargets`）でreloadし、`_valid_phase155_compatible_history` を実行
- `current_step_index >= 6` のexact built-in `int` のみ許可（`< 6` は拒否）
- predecessorの`output_text`はexact built-in `str`（空文字含む）のみ許可（`None` / non-stringは拒否）
- provider / request-ID policyは追加しない（Phase 155 provenanceの `provider="other"`・`request_id=None` を許容）
- terminal event semanticsはstrictのallow-empty-success-output ruleを維持（最終stepのsucceeded空outputは拒否）
- 無効ケースはdownstream dependency call count **zero**とし、分類文字列`terminal_contract`を正確に使用
- 有効な委譲ではcanonical four-argument delegation、dependency exactly-once、returned outcomeのexact identity、targetsのbyte-for-byte unchanged、retryなしを検証

### Phase 162 regression保守（10ファイル目、scope amendment 2026-08-13承認）

- `tests/test_persisted_transition_outcome_classification_phase143_128_phase155_provenance_compatibility.py` の`test_real_chain_synthetic_seam_delegates_once`にあるstale next-seam proofを更新（assertion/import-only、collected case増加なし）
- (a) **実Phase 121受理の証明**: 同一persisted historyを実`route_persisted_transition_outcome_classification_cycle_handoff_reentry_continuation_boundary(...)`に渡し、最終依存Phase 114を決定論的test seam（contract-validな`PersistedExecutionOutcome`を返す）に置換。canonical four-argument identity/order、Phase 114 seam呼び出しちょうど1回、返り値の同一性（`out is` seam返値）、no retry、targets byte-for-byte不変をassert
- (b) **実Phase 100拒否の証明**: 同一persisted historyを実`route_persisted_outcome_classification_dispatch_continuation_boundary(...)`に直接渡し、`PersistedOutcomeClassificationDispatchContinuationCompatibilityError`＋`terminal_contract`をassert。Phase 93呼び出し0回、targets不変
- Phase 162 productionは不変、`terminal_history_contract.py`・Phase 100 productionは不変

### Focused regression（+18 cases）

Phase 121 / 114 / 107の既存test moduleへ各**+6 cases**を追加します（fixtureはexact Phase-155 provenance: earlier predecessorは`provider="other"`・`request_id=request-{step_id}`（非空）、immediate predecessor（step 5）は`provider="openai"`・`request_id=None`・空`output_text`）。

- Phase 121（cycle handoff reentry）: Phase-155 six-step historyフォールバック受理（earlier empty step 2 + immediate empty step 5 + immediate `request_id=None`、succeeded / failed、failed委譲は `message=""` でも成功＝strict contract と同一の `isinstance(str)` 意味・non-empty 強化なし、Phase 114 seam exactly-once・identity・targets不変）、multiple earlier empty predecessors（step 2・3空）+ immediate empty/None委譲（succeeded / failed）、earlier predecessor `output_text=None`拒否（`terminal_contract`・Phase 114未呼び出し・state/events byte-for-byte不変）、predecessor `output_text` non-string拒否（同上・targets不変）
- Phase 114（cycle reentry）: 同上のboundaryを`phase107_function` seamで検証
- Phase 107（cycle）: 同上のboundaryを`phase100_function` seamで検証

### Real-segment regression（+6 cases）

新規test file（`tests/test_persisted_transition_outcome_classification_phase121_107_phase155_provenance_compatibility.py`、**6 collected total**）:

- **実Phase 121 → 実Phase 114 → 実Phase 107 → synthetic Phase 100 seam**のreal chain（succeeded / failed）: 呼び出し前に public storage loader（`load_workflow_execution_history`）でpersisted state/historyを明示的にreloadし、earlier empty（step 2）・immediate empty（step 5）・immediate `request_id=None`・non-`"openai"` providerを実データとしてassert、reloaded terminal state/historyをexpected success/failure outcome contractに照合。dependency call count `{phase121: 1, phase114: 1, phase107: 1, seam: 1}`、canonical four-argument order・同一identity・exactly once委譲、returned outcomeのexact identity、両target byte-for-byte不変、retryなしを検証
- multiple earlier empty predecessors（step 2・3）のdelegation（succeeded / failed）
- **Phase 100 next-seam reference**（delegatesテスト内にinline、追加collected caseなし）: 同一persisted historyを実Phase 100に直接渡すと`PersistedOutcomeClassificationDispatchContinuationCompatibilityError`・分類`terminal_contract`で拒否、Phase 93呼び出し0回、targets不変
- predecessor `output_text=None` / non-string（`1`）のPhase 121拒否（`terminal_contract`・downstream未呼び出し・targets不変）: 2 negativeとも変異前に public loader で intact provenance（earlier request IDs non-empty・immediate step 5 `request_id=None`・terminal state/history）を明示reload/assertしてから、`None` 変異は step 2 の `request_id` を non-empty（`request-two`）維持のまま `output_text` のみ None に、non-string 変異は **immediate predecessor（step 5）** の `output_text` のみ `1` に変更（step 2 earlier empty・step 5 の `request_id=None`・provider `"openai"` 維持）して呼び出す

### Collect invariant

```text
11,540 + 24 = 11,564
```

- Phase 121 test module: **+6 cases**
- Phase 114 test module: **+6 cases**
- Phase 107 test module: **+6 cases**
- Phase 163 real-segment test file: **+6 cases**
- Phase 162 regression保守: **+0 cases**（assertion/import-only）

### 変更範囲（10ファイル、scope amendment 2026-08-13で9→10に承認）

1. `src/ai_office/engine/persisted_transition_outcome_classification_cycle_handoff_reentry_continuation_boundary.py` — Phase 121 production修正A（フォールバック追加）
2. `src/ai_office/engine/persisted_transition_outcome_classification_cycle_reentry_continuation_boundary.py` — Phase 114 production修正B（フォールバック追加）
3. `src/ai_office/engine/persisted_transition_outcome_classification_cycle_continuation_boundary.py` — Phase 107 production修正C（フォールバック追加）
4. `tests/test_persisted_transition_outcome_classification_cycle_handoff_reentry_continuation_boundary.py` — Phase 121 regression +6
5. `tests/test_persisted_transition_outcome_classification_cycle_reentry_continuation_boundary.py` — Phase 114 regression +6
6. `tests/test_persisted_transition_outcome_classification_cycle_continuation_boundary.py` — Phase 107 regression +6
7. `tests/test_persisted_transition_outcome_classification_phase121_107_phase155_provenance_compatibility.py` — 新規 real-segment regression（6 cases）
8. `tests/test_persisted_transition_outcome_classification_phase143_128_phase155_provenance_compatibility.py` — Phase 162 regression保守（next-seam proofをPhase 100へ更新、assertion/import-only、+0 cases）
9. `README.md` — Phase 163 documentation
10. `docs/architecture.md` — Phase 163 architecture documentation

### 非機能範囲（State explicitly）

Phase 163は以下のbehaviorを**一切**追加・変更しない:

- 新しいpublic boundary（新規public関数・新規ルーティング・新規API）を追加しない
- 自動継続（automatic continuation）は行わない
- Phase 144 progression call（`decide_workflow_progression` 系の呼び出し）は行わない
- workflow progression・next-step preparation・start は行わない
- provider / tool 実行は行わない
- retry・loop・schedule・parallel・finalize は行わない
- CLI・GUI behavior は追加・変更しない
- 共有 `terminal_history_contract.py` の意味を広げない（strict contract は不変）
- 新しい request-ID / provider semantics を導入しない（Phase 155 provenance の `request_id=None`・`provider="other"` を許容するだけ）

### 変更しないもの

- Phase 162 production module（`persisted_transition_outcome_classification_cycle_handoff_chain_reentry_continuation_boundary.py` ほか）
- `src/ai_office/engine/terminal_history_contract.py`
- Phase 100 production module（`persisted_outcome_classification_dispatch_continuation_boundary.py`）
- 既存テストの削除・rename・skip・xfail・parameter-collapse・弱体化
- エラー分類・quality feedback literal・provider / request-ID semantics
- 実Phase 30 persistence、shared storage/runtime/provider code、CLI / GUI behavior

## Phase 164: Repair Phase-155 Provenance Compatibility across Phase 100 → 93 → 86 Outcome-Classification Segment

Phase 164は、outcome-classification segment（**実Phase 100 → 実Phase 93 → 実Phase 86 → Phase 79**）がPhase-155 provenance persisted transitionを正しく受け渡せるようにする**staged compatibility repair**です。Phase 163で修復したPhase 121 → 114 → 107セグメントの直後にあるclassification segmentで、`load_strict_terminal_history` がPhase-155 provenance history（`current_step_index >= 6`、predecessorの空`output_text`）を拒否する場合にのみ、public `load_workflow_execution_history` + 限定されたPhase-155互換検証へフォールバックします。

```text
Phase 100 (dispatch continuation boundary, final dependency: Phase 93)
    ↓ Phase 93 (dispatch phase bridge cycle reentry, final dependency: Phase 86)
    ↓ Phase 86 (routing phase bridge cycle reentry, final dependency: Phase 79)
Phase 79 (strict seam: Phase-155 provenance history は terminal_contract で拒否のまま)
```

### 互換性フォールバック（Phase 100 / 86、Phase 93 は Phase 86 ヘルパー再利用）

- `load_strict_terminal_history` が失敗した場合のみ、public `load_workflow_execution_history`（`WorkflowExecutionPersistenceTargets`）でreloadし、`_valid_phase155_compatible_history` を実行
- `current_step_index >= 6` のexact built-in `int` のみ許可（`< 6` は拒否）
- predecessorの`output_text`はexact built-in `str`（空文字含む）のみ許可（`None` / non-stringは拒否）
- provider / request-ID policyは追加しない（Phase 155 provenance の `provider="openai"`・`request_id=None` を許容）
- terminal event semanticsは既存の `_valid_event_types(state, history[-1])` 意味を維持しつつ、fallbackでは `_valid_terminal_event_types` で strict succeeded-terminal 契約を維持（terminal `response_id` は non-empty、final succeeded `output_text` は non-empty、intermediate succeeded の empty output は許容、failed terminal `message` は任意のexact str、`""` 含む）
- 無効ケースはdownstream dependency call count **zero**とし、分類文字列`terminal_contract`を正確に使用
- 有効な委譲ではcanonical four-argument delegation、dependency exactly-once、returned outcomeのexact identity、targetsのbyte-for-byte unchanged、retryなしを検証
- **Phase 86**: strict-first local bounded compatibility fallback/helper を新規追加（base には存在しなかった）。`_validate_persistence` は `load_strict_terminal_history` を優先し、失敗時のみ `_load_compatible_terminal_history` → public `load_workflow_execution_history` + `_valid_phase155_compatible_history`（`current_step_index >= 6`、predecessor `output_text` は exact built-in str で空/非空とも可、`None`/non-string拒否、provider/request-ID gatingなし）。terminal は `_valid_terminal_event_types` で既存 succeeded terminal 契約を弱めない。Phase 93 は無変更だが Phase 86 の `_validate_persistence` / `_load_compatible_terminal_history` を再利用しているため、Phase-155 provenanceを受理する

### Phase 162/163 regression保守（+0 cases）

- Phase 162/163 real-segment test files（`...phase143_128_...`・`...phase121_107_...`）のstale next-seam proofを更新（assertion/import-only、collected case増加なし）
- (a) **実Phase 100受理の証明**: 同一persisted historyを実`route_persisted_outcome_classification_dispatch_continuation_boundary(...)`に渡し、最終依存Phase 93を決定論的test seamに置換。canonical four-argument identity/order、Phase 93 seam呼び出しちょうど1回、返り値の同一性、no retry、targets byte-for-byte不変をassert
- (b) **実Phase 79拒否の証明**: 同一persisted historyを実`route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(...)`に直接渡し、`PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError`＋`terminal_contract`をassert。Phase 72呼び出し0回、targets不変
- Phase 162/163 productionは不変、`terminal_history_contract.py`・Phase 79 productionは不変

### Focused regression（+18 cases）

Phase 100 / 93 / 86の既存test moduleへ各**+6 cases**を追加します（fixtureはexact Phase-155 provenance: earlier predecessorは`provider="other"`・`request_id=request-{step_id}`（非空）、immediate predecessor（step 5）は`provider="openai"`・`request_id=None`・空`output_text`）。

- Phase 100（dispatch continuation boundary）: Phase-155 six-step historyフォールバック受理（earlier empty step 2 + immediate empty step 5 + immediate `request_id=None`、succeeded / failed、failed委譲は `message=""` でも成功）、multiple earlier empty predecessors（step 2・3空）+ immediate empty/None委譲（succeeded / failed）、earlier predecessor `output_text=None`拒否（`terminal_contract`・Phase 93未呼び出し・state/events byte-for-byte不変）、predecessor `output_text` non-string拒否（同上・targets不変）
- Phase 93（dispatch phase bridge cycle reentry）: 同上のboundaryを`phase86_function` seamで検証
- Phase 86（routing phase bridge cycle reentry）: 同上のboundaryを`phase79_function` seamで検証

### Real-segment regression（+6 cases）

新規test file（`tests/test_persisted_outcome_classification_phase100_86_phase155_provenance_compatibility.py`、**6 collected total**）:

- **実Phase 100 → 実Phase 93 → 実Phase 86 → synthetic Phase 79 seam**のreal chain（succeeded / failed）: 呼び出し前に public storage loader（`load_workflow_execution_history`）でpersisted state/historyを明示的にreloadし、earlier empty（step 2）・immediate empty（step 5）・immediate `request_id=None`・provider `"openai"` を実データとしてassert、reloaded terminal state/historyをexpected success/failure outcome contractに照合。dependency call count `{phase100: 1, phase93: 1, phase86: 1, seam: 1}`、canonical four-argument order・同一identity・exactly once委譲、returned outcomeのexact identity、両target byte-for-byte不変、retryなしを検証
- multiple earlier empty predecessors（step 2・3）のdelegation（succeeded / failed）
- **Phase 79 next-seam reference**（delegatesテスト内にinline、追加collected caseなし）: 同一persisted historyを実Phase 79に直接渡すと`PersistedOutcomeClassificationRoutingPhaseBridgeCycleContinuationCompatibilityError`・分類`terminal_contract`で拒否、Phase 72呼び出し0回、targets不変
- predecessor `output_text=None` / non-string（`1`）のPhase 100拒否（`terminal_contract`・downstream未呼び出し・targets不変）: 2 negativeとも変異前に public loader で intact provenance（earlier request IDs non-empty・immediate step 5 `request_id=None`・terminal state/history）を明示reload/assertしてから、`None` 変異は step 2 の `request_id` を non-empty（`request-two`）維持のまま `output_text` のみ None に、non-string 変異は **immediate predecessor（step 5）** の `output_text` のみ `1` に変更（step 2 earlier empty・step 5 の `request_id=None`・provider `"openai"` 維持）して呼び出す

### Collect invariant

```text
11,564 + 24 = 11,588
```

- Phase 100 test module: **+6 cases**
- Phase 93 test module: **+6 cases**
- Phase 86 test module: **+6 cases**
- Phase 164 real-segment test file: **+6 cases**
- Phase 162/163 regression保守: **+0 cases**（assertion/import-only）

### 変更範囲（10ファイル）

1. `src/ai_office/engine/persisted_outcome_classification_dispatch_continuation_boundary.py` — Phase 100 production修正A（フォールバック追加）
2. `src/ai_office/engine/persisted_outcome_classification_routing_phase_bridge_cycle_reentry_continuation.py` — Phase 86 production修正B（strict-first local bounded fallback `_load_compatible_terminal_history` と `_valid_phase155_compatible_history` / `_valid_terminal_event_types` を新規追加）
3. `tests/test_persisted_outcome_classification_dispatch_continuation_boundary.py` — Phase 100 regression +6
4. `tests/test_persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation.py` — Phase 93 regression +6
5. `tests/test_persisted_outcome_classification_routing_phase_bridge_cycle_reentry_continuation.py` — Phase 86 regression +6
6. `tests/test_persisted_outcome_classification_phase100_86_phase155_provenance_compatibility.py` — 新規 real-segment regression（6 cases）
7. `tests/test_persisted_transition_outcome_classification_phase143_128_phase155_provenance_compatibility.py` — Phase 162 regression保守（next-seam proofをPhase 100受理 + Phase 79拒否へ更新、assertion/import-only、+0 cases）
8. `tests/test_persisted_transition_outcome_classification_phase121_107_phase155_provenance_compatibility.py` — Phase 163 regression保守（同上、+0 cases）
9. `README.md` — Phase 164 documentation
10. `docs/architecture.md` — Phase 164 architecture documentation

### 非機能範囲（State explicitly）

Phase 164は以下のbehaviorを**一切**追加・変更しない:

- 新しいpublic boundary（新規public関数・新規ルーティング・新規API）を追加しない
- 自動継続（automatic continuation）は行わない
- Phase 144 progression call（`decide_workflow_progression` 系の呼び出し）は行わない
- workflow progression・next-step preparation・start は行わない
- provider / tool 実行は行わない
- retry・loop・schedule・parallel・finalize は行わない
- CLI・GUI behavior は追加・変更しない
- 共有 `terminal_history_contract.py` の意味を広げない（strict contract は不変）
- 新しい request-ID / provider semantics を導入しない（Phase 155 provenance の `request_id=None`・`provider="openai"` を許容するだけ）

### 変更しないもの

- Phase 79 production module（`persisted_outcome_classification_routing_phase_bridge_cycle_continuation.py`）
- Phase 93 production module（`persisted_outcome_classification_dispatch_phase_bridge_cycle_reentry_continuation.py`）
- `src/ai_office/engine/terminal_history_contract.py`
- Phase 162/163 production modules（`persisted_transition_outcome_classification_cycle_handoff_*` ほか）
- 既存テストの削除・rename・skip・xfail・parameter-collapse・弱体化
- エラー分類・quality feedback literal・provider / request-ID semantics
- 実Phase 30 persistence、shared storage/runtime/provider code、CLI / GUI behavior

## Phase 165: Repair Phase-155 Provenance Compatibility across Phase 79 → 72 → 65 Outcome-Classification Segment

Phase 165は、outcome-classification segment（**実Phase 79 → 実Phase 72 → 実Phase 65 → Phase 58**）がPhase-155 provenance persisted outcomeを正しく受け渡せるようにする**staged compatibility repair**です。Phase 164で修復したPhase 100 → 93 → 86セグメントの直後にあるsegmentで、各Phaseはstrict loaderがPhase-155 provenance history（`current_step_index >= 6`、predecessorの空`output_text`）を拒否する場合にのみ、public `load_workflow_execution_history`（`WorkflowExecutionPersistenceTargets`）+ 限定されたPhase-155互換検証へフォールバックします。

```text
Phase 79 (routing phase bridge cycle continuation, final dependency: Phase 72)
    ↓ Phase 72 (routing phase bridge continuation, final dependency: Phase 65)
    ↓ Phase 65 (terminal outcome classification routing phase bridge reentry, final dependency: Phase 58)
Phase 58 (strict seam: Phase-155 provenance history は terminal_contract で拒否のまま)
```

### 互換性フォールバック（strict-first + local bounded）

各Phase（79 / 72 / 65）のproduction moduleに、**strict-first** のlocal bounded fallbackを実装:

- 既存のstrict loader（`load_strict_terminal_history`）が成功すれば従来どおりstrict経路を利用し、失敗した場合**のみ** public `load_workflow_execution_history` + `_load_phase155_compatible_history` を実行
- `current_step_index >= 6` のexact built-in `int` のみ許可（`< 6` は拒否）
- 唯一の緩和は「succeeded predecessorの空`output_text`」のみ。それ以外の`output_text`はexact built-in `str`（非空）を要求し、`None` / non-stringは拒否
- provider / request-ID policyは追加しない（Phase 155 provenance の `provider="openai"`・`request_id=None` を許容）
- terminal event semanticsは既存のstrict succeeded-terminal契約を弱めない（terminal `response_id` は non-empty、final succeeded `output_text` は non-empty、failed terminal `message` は任意のexact str、`""` 含む）
- 無効ケースはdownstream dependency call count **zero**とし、分類文字列`terminal_contract`を正確に使用
- storage系エラー（`WorkflowExecutionDataError` / `WorkflowExecutionHistoryInconsistencyError` / `WorkflowExecutionLoadError` / `OSError`）は`_raise("terminal_contract")`へ
- 有効な委譲ではcanonical four-argument delegation、dependency exactly-once、returned outcomeのexact identity、targetsのbyte-for-byte unchanged、retryなしを検証
- **cross-Phase private helper importはしない**（各Phase moduleにlocal実装。public `load_workflow_execution_history` / `WorkflowExecutionPersistenceTargets` のみ共有）
- **Phase 58は変更しない**（次の明示的strict seamとして`terminal_contract`で拒否し続けるのが期待動作）

### Phase 162/163/164 regression保守（+0 cases）

- Phase 162/163/164 real-segment test filesのstale next-seam proofを更新（assertion/import-only、collected case増加なし）
- (a) **実Phase 79受理の証明**: 同一persisted historyを実`route_persisted_outcome_classification_routing_phase_bridge_cycle_continuation(...)`に渡し、最終依存Phase 72を決定論的test seamに置換。canonical four-argument identity/order、Phase 72 seam呼び出しちょうど1回、返り値の同一性、no retry、targets byte-for-byte不変をassert
- (b) **実Phase 58拒否の証明**: 同一persisted historyを実`route_persisted_terminal_outcome_classification_phase_bridge_reentry(...)`に直接渡し、`PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError`＋`terminal_contract`をassert。Phase 51呼び出し0回、targets不変
- Phase 162/163/164 productionは不変、`terminal_history_contract.py`・Phase 58 productionは不変

### Focused regression（+18 cases）

Phase 79 / 72 / 65の既存test moduleへ各**+6 cases**を追加します（fixtureはexact Phase-155 provenance: 6-step history "one"〜"six"、predecessor 1-5＝step_succeeded（step 2とstep 5は`output_text=""`、step 5は`provider="openai"` / `request_id=None`、他は`request_id`非空）、terminal step 6＝succeeded（`response_id` "response-six", `output_text` "output-six"）/ failed（`failure_category` "api_error", `message` "safe failure"/`""`））。

- succeeded delegate once / failed delegate once（`message=""`）／multiple earlier empty（step 2・3）delegate once（succeeded / failed）／step 2 `output_text=None` reject（`request_id` "request-two"維持）／step 5 `output_text=1` reject（`request_id=None`・`provider="openai"`維持）
- inline（non-collected）assertion: fallbackはterminal-success契約を弱めない（terminal `response_id=""` reject、final succeeded `output_text=""` reject）

### Real-segment regression（+6 cases）

新規test file（`tests/test_persisted_outcome_classification_phase79_65_phase155_provenance_compatibility.py`、**6 collected total**）:

- **実Phase 79 → 実Phase 72 → 実Phase 65 → synthetic Phase 58 seam**のreal chain（succeeded / failed）: 呼び出し前に public storage loader（`load_workflow_execution_history`）でpersisted state/historyを明示的にreloadし、earlier empty（step 2）・immediate empty（step 5）・immediate `request_id=None`・provider `"openai"` を実データとしてassert、reloaded terminal state/historyをexpected success/failure outcome contractに照合。dependency call count `{phase79: 1, phase72: 1, phase65: 1, seam: 1}`、canonical four-argument order・同一identity・exactly once委譲、returned outcomeのexact identity、両target byte-for-byte不変、retryなしを検証
- multiple earlier empty predecessors（step 2・3）のdelegation（succeeded / failed）
- **Phase 58 next-seam reference**（delegatesテスト内にinline、追加collected caseなし）: 同一persisted historyを実Phase 58に直接渡すと`PersistedTerminalOutcomeClassificationPhaseBridgeCompatibilityError`・分類`terminal_contract`で拒否、Phase 51呼び出し0回、targets不変
- predecessor `output_text=None` / non-string（`1`）のPhase 79拒否（`terminal_contract`・downstream未呼び出し・targets不変）: 2 negativeとも変異前に public loader で intact provenance（earlier request IDs non-empty・immediate step 5 `request_id=None`・terminal state/history）を明示reload/assertしてから、`None` 変異は step 2 の `request_id` を non-empty（`request-two`）維持のまま `output_text` のみ None に、non-string 変異は **immediate predecessor（step 5）** の `output_text` のみ `1` に変更（step 2 earlier empty・step 5 の `request_id=None`・provider `"openai"` 維持）して呼び出す

### Collect invariant

```text
11,588 + 24 = 11,612
```

- Phase 79 test module: **+6 cases**
- Phase 72 test module: **+6 cases**
- Phase 65 test module: **+6 cases**
- Phase 165 real-segment test file: **+6 cases**
- Phase 162/163/164 regression保守: **+0 cases**（assertion/import-only）

### 変更範囲（10ファイル）

1. `src/ai_office/engine/persisted_outcome_classification_routing_phase_bridge_cycle_continuation.py` — Phase 79 production修正A（strict-first + local bounded fallback `_load_phase155_compatible_history` / `_validate_phase155_terminal_event` 追加）
2. `src/ai_office/engine/persisted_outcome_classification_routing_phase_bridge_continuation.py` — Phase 72 production修正B（同上）
3. `src/ai_office/engine/persisted_terminal_outcome_classification_routing_phase_bridge_reentry.py` — Phase 65 production修正C（同上）
4. `tests/test_persisted_outcome_classification_routing_phase_bridge_cycle_continuation.py` — Phase 79 regression +6
5. `tests/test_persisted_outcome_classification_routing_phase_bridge_continuation.py` — Phase 72 regression +6
6. `tests/test_persisted_terminal_outcome_classification_routing_phase_bridge_reentry.py` — Phase 65 regression +6
7. `tests/test_persisted_outcome_classification_phase79_65_phase155_provenance_compatibility.py` — 新規 real-segment regression（6 cases）
8. `tests/test_persisted_transition_outcome_classification_phase143_128_phase155_provenance_compatibility.py` — Phase 162 regression保守（next-seam proofをPhase 79受理 + Phase 58拒否へ更新、assertion/import-only、+0 cases）
9. `tests/test_persisted_transition_outcome_classification_phase121_107_phase155_provenance_compatibility.py` — Phase 163 regression保守（同上、+0 cases）
10. `tests/test_persisted_outcome_classification_phase100_86_phase155_provenance_compatibility.py` — Phase 164 regression保守（同上、+0 cases）
11. `README.md` — Phase 165 documentation
12. `docs/architecture.md` — Phase 165 architecture documentation

### 非機能範囲（State explicitly）

Phase 165は以下のbehaviorを**一切**追加・変更しない:

- 新しいpublic boundary（新規public関数・新規ルーティング・新規API）を追加しない
- 自動継続（automatic continuation）は行わない
- Phase 144 progression call（`decide_workflow_progression` 系の呼び出し）は行わない
- workflow progression・next-step preparation・start は行わない
- provider / tool 実行は行わない
- retry・loop・schedule・parallel・finalize は行わない
- CLI・GUI behavior は追加・変更しない
- 共有 `terminal_history_contract.py` の意味を広げない（strict contract は不変）
- 新しい request-ID / provider semantics を導入しない（Phase 155 provenance の `request_id=None`・`provider="openai"` を許容するだけ）

### 変更しないもの

- Phase 58 production module（`persisted_terminal_outcome_classification_phase_bridge_reentry.py`）
- `src/ai_office/engine/terminal_history_contract.py`
- Phase 162/163/164 production modules（`persisted_transition_outcome_classification_cycle_handoff_*` ほか）
- 既存テストの削除・rename・skip・xfail・parameter-collapse・弱体化
- エラー分類・quality feedback literal・provider / request-ID semantics
- 実Phase 30 persistence、shared storage/runtime/provider code、CLI / GUI behavior

## Phase 166: Complete Phase-155 Provenance Compatibility across Phase 58 → 51 → 44 → 37 Persisted-Outcome Classification Tail

Phase 166は、persisted-outcome classificationの最終segment（**実Phase 58 → 実Phase 51 → 実Phase 44 → 実Phase 37**）がPhase-155 provenance persisted outcomeを正しく受け渡せるようにする**staged compatibility repair**です。Phase 165で修復したPhase 79 → 72 → 65セグメントの直後にあるtailで、各Phaseはstrict loaderがPhase-155 provenance history（`current_step_index >= 6`、predecessorの空`output_text`）を拒否する場合にのみ、public `load_workflow_execution_history`（`WorkflowExecutionPersistenceTargets`）+ 限定されたPhase-155互換検証へフォールバックします。

```text
Phase 58 (terminal outcome classification phase bridge reentry, final dependency: Phase 51)
    ↓ Phase 51 (terminal outcome classification bridge reentry, final dependency: Phase 44)
    ↓ Phase 44 (terminal outcome classification reentry, final dependency: Phase 37)
Phase 37 (classify persisted execution outcome reentry: 三引数 terminal)
```

### 互換性フォールバック（strict-first + local bounded）

各Phase（58 / 51）のproduction moduleに、**strict-first** のlocal bounded fallbackを実装:

- 既存のstrict loader（`load_strict_terminal_history`）が成功すれば従来どおりstrict経路を利用し、失敗した場合**のみ** public `load_workflow_execution_history` + local compatibility loader（Phase 58: `_load_compatible_terminal_history` / `_validate_compatible_terminal_history` / `_validate_compatible_terminal_event`、Phase 51: `_validate_persistence_result` に同一パターン）を実行
- `current_step_index >= 6` のexact built-in `int` のみ許可（`< 6` は拒否）
- 唯一の緩和は「succeeded predecessorの空`output_text`」のみ（`type(event.output_text) is str`、empty/non-empty可）。`output_text=None` / non-stringは拒否
- provider / request-ID policyは追加しない（Phase 155 provenance の `provider="openai"`・`request_id=None` を許容）
- terminal event semanticsは既存のstrict succeeded-terminal契約を弱めない（succeededは非空`response_id`必須、`allow_empty_success_output`（succeededかつ`current_step_index < len(workflow.steps)`）時のみempty `output_text`許可。failedは`response_id is None`・`output_text is None`・`isinstance(message, str)`（空文字OK）・failure_category一致）
- bytes読込のtry分離: `read_bytes()`の`OSError`は従来どおり`terminal_contract`へ。payload/byte長/suffix検査は不変
- 無効ケースはdownstream dependency call count **zero**とし、分類文字列`terminal_contract`を正確に使用
- storage系エラー（`WorkflowExecutionDataError` / `WorkflowExecutionHistoryInconsistencyError` / `WorkflowExecutionLoadError` / `OSError`）は`_raise("terminal_contract")`へ
- 有効な委譲ではcanonical four-argument delegation（44→37は三引数）、dependency exactly-once、returned outcomeのexact identity、targetsのbyte-for-byte unchanged、retryなしを検証
- **cross-Phase private helper importはしない**（各Phase moduleにlocal実装。public `load_workflow_execution_history` / `WorkflowExecutionPersistenceTargets` のみ共有）
- **Phase 44 / Phase 37は変更しない**（既にPhase-155 provenance historyを受理済み）

### Phase 162/163/164/165 regression保守（+0 cases）

- Phase 162/163/164/165 real-segment test filesのstale next-seam proofを更新（assertion/import-only、collected case増加なし）
- (a) **実Phase 58受理の証明**: 同一persisted historyを実`route_persisted_terminal_outcome_classification_phase_bridge_reentry(...)`に渡し、最終依存Phase 51を決定論的test seamに置換。canonical four-argument identity/order、Phase 51 seam呼び出しちょうど1回、返り値の同一性、no retry、targets byte-for-byte不変をassert
- Phase 162/163/164/165 productionは不変、`terminal_history_contract.py`・Phase 44/37 productionは不変

### Focused regression（+12 cases）

Phase 58 / 51の既存test moduleへ各**+6 cases**を追加します（fixtureはexact Phase-155 provenance: 6-step history "one"〜"six"、terminal "six" index 6、employee = step_id[0]、`events[4]`は`provider="openai"`・`request_id=None`・`output_text=""`）。

- succeeded delegate once / failed delegate once（`message=""`）／multiple earlier empty（step 2・3）delegate once（succeeded / failed）／step 2 `output_text=None` reject（JSON mutation）／step 5 `output_text=1` reject（JSON mutation）
- rejectはzero calls + `terminal_contract`をassert
- inline（non-collected）assertion: fallbackはterminal-success契約を弱めない（terminal `response_id=""` reject、final succeeded `output_text=""` reject）

### Real-tail regression（+6 cases）

新規test file（`tests/test_persisted_terminal_outcome_classification_phase58_37_phase155_provenance_compatibility.py`、**6 collected total**）:

- **実Phase 58 → 実Phase 51 → 実Phase 44 → 実Phase 37**のreal chain（succeeded / failed）: 各実境界を「記録して即次実境界へ委譲」するラッパーで挟み、58→51・51→44はcanonical four-argument identity/order、44→37は三引数`(workflow, state_path, events_path)`、Phase 37生成objectが44/51/58を経て同一objectで返ることをidentityで証明。呼び出し前にpublic storage loader（`load_workflow_execution_history`）でpersisted state/historyを明示的にreload/assert、dependency call count `{phase58: 1, phase51: 1, phase44: 1, phase37: 1}`、両target byte-for-byte不変、retryなしを検証
- multiple earlier empty predecessors（step 2・3）のdelegation（succeeded / failed）
- **negativeルート**: mutation前にintact provenanceをpublic loaderでreload/assertしてから変異し、`terminal_contract`・downstream未呼び出し・targets不変を検証（predecessor `output_text=None` / non-string（`1`））

### Collect invariant

```text
11,612 + 18 = 11,630
```

- Phase 58 test module: **+6 cases**
- Phase 51 test module: **+6 cases**
- Phase 166 real-tail test file: **+6 cases**
- Phase 162/163/164/165 regression保守: **+0 cases**（assertion/import-only）

### 変更範囲（11ファイル）

1. `src/ai_office/engine/persisted_terminal_outcome_classification_phase_bridge_reentry.py` — Phase 58 production修正A（strict-first + local bounded fallback `_load_compatible_terminal_history` / `_validate_compatible_terminal_history` / `_validate_compatible_terminal_event` 追加）
2. `src/ai_office/engine/persisted_terminal_outcome_classification_bridge_reentry.py` — Phase 51 production修正B（`_validate_persistence_result` に同一パターン適用）
3. `tests/test_persisted_terminal_outcome_classification_phase_bridge_reentry.py` — Phase 58 regression +6
4. `tests/test_persisted_terminal_outcome_classification_bridge_reentry.py` — Phase 51 regression +6
5. `tests/test_persisted_terminal_outcome_classification_phase58_37_phase155_provenance_compatibility.py` — 新規 real-tail regression（6 cases）
6. `tests/test_persisted_outcome_classification_phase79_65_phase155_provenance_compatibility.py` — Phase 165 regression保守（next-seam proofを実Phase 58受理 + Phase 51 seam委譲へ更新、assertion/import-only、+0 cases）
7. `tests/test_persisted_outcome_classification_phase100_86_phase155_provenance_compatibility.py` — Phase 164 regression保守（同上、+0 cases）
8. `tests/test_persisted_transition_outcome_classification_phase121_107_phase155_provenance_compatibility.py` — Phase 163 regression保守（同上、+0 cases）
9. `tests/test_persisted_transition_outcome_classification_phase143_128_phase155_provenance_compatibility.py` — Phase 162 regression保守（同上、+0 cases）
10. `README.md` — Phase 166 documentation
11. `docs/architecture.md` — Phase 166 architecture documentation

### 非機能範囲（State explicitly）

Phase 166は以下のbehaviorを**一切**追加・変更しない:

- 新しいpublic boundary（新規public関数・新規ルーティング・新規API）を追加しない
- 自動継続（automatic continuation）は行わない
- Phase 144 progression call（`decide_workflow_progression` 系の呼び出し）は行わない
- workflow progression・next-step preparation・start は行わない
- provider / tool 実行は行わない
- retry・loop・schedule・parallel・finalize は行わない
- CLI・GUI behavior は追加・変更しない
- 共有 `terminal_history_contract.py` の意味を広げない（strict contract は不変）
- 新しい request-ID / provider semantics を導入しない（Phase 155 provenance の `request_id=None`・`provider="openai"` を許容するだけ）

### 変更しないもの

- Phase 44 production module（`persisted_terminal_outcome_classification_routing_reentry.py`）
- Phase 37 production module（`persisted_execution_outcome_reentry.py`）
- `src/ai_office/engine/terminal_history_contract.py`
- Phase 162/163/164/165 production modules（`persisted_transition_outcome_classification_cycle_handoff_*` ほか）
- 既存テストの削除・rename・skip・xfail・parameter-collapse・弱体化
- エラー分類・quality feedback literal・provider / request-ID semantics
- 実Phase 30 persistence、shared storage/runtime/provider code、CLI / GUI behavior

## Phase 167: Repair Phase-155 Provenance Compatibility across Phase 144 → 136 → 129 Classified Persisted-Outcome Progression Segment

Phase 167は、classified persisted-outcome progressionのsegment（**実Phase 144 → 実Phase 136 → 実Phase 129**）が、Phase 155 provenance persisted outcome（`current_step_index >= 6`、predecessorの空`output_text`、immediate predecessorの`request_id=None`）を、次strict seamであるPhase 122へ正しく受け渡せるようにする**staged compatibility repair**です。Phase 166で修復したclassification tailの直後にあるprogression segmentで、各Phaseはstrict loaderがPhase-155 provenance historyを拒否する場合にのみ、public `load_workflow_execution_history`（`WorkflowExecutionPersistenceTargets`）+ 限定されたPhase-155互換検証へフォールバックします。

```text
Phase 144 (classified persisted outcome progression cycle handoff chain bridge outer reentry, final dependency: Phase 136)
    ↓ Phase 136 (classified persisted outcome progression cycle handoff chain bridge reentry, final dependency: Phase 129)
    ↓ Phase 129 (classified persisted outcome progression cycle handoff chain reentry, final dependency: Phase 122)
Phase 122 (next strict seam: 変更しない)
```

### 互換性フォールバック（strict-first + local bounded）

- **Phase 144**: route内で `allow_immediate_none_request_id`（exact `PersistedExecutionOutcome` + exact builtin int `current_step_index >= 6` 限定）を計算し、`_check_terminal` → `_valid_history` → `_valid_predecessor` へ伝搬。immediate predecessorのみ `request_id is None` を許可（`""` はinvalid維持、earlierは非空必須）。`allow_empty_predecessor_output=True` 固定は全routeで維持
- **Phase 136**: Phase 144と同様のNone許可 + persisted-failure direct stop routeの `allow_empty_predecessor_output` を `False`固定 → `(current_step_index >= 6)` に変更（zero-call stop維持、Phase-155空output + immediate-None provenanceを受理）
- **Phase 129**: `phase155_compatible`（exact `PersistedExecutionOutcome` + exact builtin int `current_step_index >= 6`）限定でstrict失敗時の新フォールバック追加。public loader使用、predecessor空output（exact builtin strのみ）許可。`_valid_terminal_history` に `allow_empty_predecessor_output: bool | None = None` オーバーライド引数追加（None=従来どおり派生計算）

### 変更ファイル（正確に9ファイル）

1. `src/ai_office/engine/classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — Phase 144 production
2. `src/ai_office/engine/classified_persisted_outcome_progression_cycle_handoff_chain_bridge_reentry_continuation_boundary.py` — Phase 136 production
3. `src/ai_office/engine/classified_persisted_outcome_progression_cycle_handoff_chain_reentry_continuation_boundary.py` — Phase 129 production
4. `tests/test_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — Phase 144 focused test（+6 cases）
5. `tests/test_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_reentry_continuation_boundary.py` — Phase 136 focused test（+6 cases）
6. `tests/test_classified_persisted_outcome_progression_cycle_handoff_chain_reentry_continuation_boundary.py` — Phase 129 focused test（+6 cases）
7. `tests/test_classified_persisted_outcome_progression_phase143_129_phase155_provenance_compatibility.py` — 新規regression test（+6 cases + inline next-seam proof）
8. `README.md` — Phase 167 documentation
9. `docs/architecture.md` — Phase 167 architecture documentation

### 非機能範囲（State explicitly）

Phase 167は以下のbehaviorを**一切**追加・変更しない:

- 新しいpublic boundary（新規public関数・新規ルーティング・新規API）を追加しない
- 自動継続（automatic continuation）は行わない
- workflow progression・next-step preparation・start は行わない
- provider / tool 実行は行わない
- retry・loop・schedule・parallel・finalize は行わない
- CLI・GUI behavior は追加・変更しない
- 共有 `terminal_history_contract.py` の意味を広げない（strict contract は不変）
- 新しい request-ID / provider semantics を導入しない（Phase 155 provenance の `request_id=None`・`provider="openai"` を許容するだけ）

### 変更しないもの

- Phase 143 production module（`persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py`）
- Phase 122 production module（`classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary.py`）
- Phase 115 production module（`classified_persisted_outcome_progression_cycle_reentry_continuation_boundary.py`）
- `src/ai_office/engine/terminal_history_contract.py`
- Phase 162/163/164/165/166 production modules（`persisted_transition_outcome_classification_*` ほか）
- 既存テストの削除・rename・skip・xfail・parameter-collapse・弱体化
- エラー分類・quality feedback literal・provider / request-ID semantics
- 実Phase 30 persistence、shared storage/runtime/provider code、CLI / GUI behavior

## Phase 168: Repair Phase-155 Provenance Compatibility across Phase 122 → 115 → 108 Classified Persisted-Outcome Progression Segment

Phase 168は、classified persisted-outcome progressionのsegment（**実Phase 122 → 実Phase 115 → 実Phase 108**）が、Phase 155 provenance persisted outcome（`current_step_index >= 6`、predecessorの空`output_text`、immediate predecessorの`request_id=None`・`provider="openai"`）を、次strict seamであるPhase 101へ正しく受け渡せるようにする**staged compatibility repair**です。Phase 167で修復したPhase 144→136→129 segmentの直後にあるprogression segmentで、各Phaseはstrict loader（`load_strict_terminal_history`）がPhase-155 provenance historyを拒否する場合にのみ、public `load_workflow_execution_history`（`WorkflowExecutionPersistenceTargets`）+ 限定されたPhase-155互換検証へフォールバックします。

```text
Phase 122 (classified persisted outcome progression cycle handoff reentry, final dependency: Phase 115)
    ↓ Phase 115 (classified persisted outcome progression cycle reentry, final dependency: Phase 108)
    ↓ Phase 108 (classified persisted outcome progression cycle, final dependency: Phase 101)
Phase 101 (next strict seam: 変更しない)
```

### 互換性フォールバック（strict-first + local bounded）

- **Phase 122 / 115 / 108 共通**: `_validate_terminal` のstrict loadを `try: state, events = load_strict_terminal_history(...)` / `except TerminalHistoryContractError:` で包み、Phase-155互換ケース（exact `PersistedExecutionOutcome` + exact builtin int `current_step_index >= 6`）に限定した `_load_phase155_terminal_history` へフォールバックする。public loader使用（`WorkflowExecutionPersistenceTargets(state_path, events_path)`）、`_valid_phase155_terminal_history` で検証
- **predecessorの空`output_text`のみ緩和**: `type(output_text) is str`（空文字列は許容、`None`・非strはinvalid維持）。immediate predecessorの`request_id=None`・`provider="openai"`は許容（provider / request-ID semanticsは一切追加しない）。terminal succeededの`output_text`非空・`response_id`非空、terminal failedの`failure_category`・`message`はstrict契約を維持
- **post-load identity チェックは共通**: strict / fallback 両経路の後で既存の `final = events[-1]` を起点とするstate.status・workflow_id・current_step_id/index/employee_id・last_failure_category・finalフィールド検証を不変のまま適用
- **Phase 101はstrict seamのまま**: `route_classified_outcome_cycle_closure_continuation_boundary` は変更せず、intact Phase-155 historyを `terminal_contract` で拒否し続ける

### 変更ファイル（正確に10ファイル）

1. `src/ai_office/engine/classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary.py` — Phase 122 production
2. `src/ai_office/engine/classified_persisted_outcome_progression_cycle_reentry_continuation_boundary.py` — Phase 115 production
3. `src/ai_office/engine/classified_persisted_outcome_progression_cycle_continuation_boundary.py` — Phase 108 production
4. `tests/test_classified_persisted_outcome_progression_cycle_handoff_reentry_continuation_boundary.py` — Phase 122 focused test（+6 cases）
5. `tests/test_classified_persisted_outcome_progression_cycle_reentry_continuation_boundary.py` — Phase 115 focused test（+6 cases）
6. `tests/test_classified_persisted_outcome_progression_cycle_continuation_boundary.py` — Phase 108 focused test（+6 cases）
7. `tests/test_classified_persisted_outcome_progression_phase122_108_phase155_provenance_compatibility.py` — 新規regression test（+6 cases + inline Phase 101 strict-seam proof）
8. `tests/test_classified_persisted_outcome_progression_phase143_129_phase155_provenance_compatibility.py` — Phase 167 regression test（inline next-seam proofをacceptance proofへ更新、+0）
9. `README.md` — Phase 168 documentation
10. `docs/architecture.md` — Phase 168 architecture documentation

### 非機能範囲（State explicitly）

Phase 168は以下のbehaviorを**一切**追加・変更しない:

- 新しいpublic boundary（新規public関数・新規ルーティング・新規API）を追加しない
- 自動継続（automatic continuation）は行わない
- workflow progression・next-step preparation・start は行わない
- provider / tool 実行は行わない
- retry・loop・schedule・parallel・finalize は行わない
- CLI・GUI behavior は追加・変更しない
- 共有 `terminal_history_contract.py` の意味を広げない（strict contract は不変）
- 新しい request-ID / provider semantics を導入しない（Phase 155 provenance の `request_id=None`・`provider="openai"` を許容するだけ）

### 変更しないもの

- Phase 143/144/136/129 production modules
- Phase 101 production module（`classified_outcome_cycle_closure_continuation_boundary.py`）— strict seamのまま
- Phase 162/163/164/165/166/167 production modules
- `src/ai_office/engine/terminal_history_contract.py`
- `src/ai_office/engine/__init__.py`
- 既存テストの削除・rename・skip・xfail・parameter-collapse・弱体化
- エラー分類・quality feedback literal・provider / request-ID semantics
- 実Phase 30 persistence、shared storage/runtime/provider code、CLI / GUI behavior

## Phase 169: Repair Phase-155 Provenance Compatibility across Phase 101 → 94 → 87 Classified-Outcome Continuation Segment

Phase 169は、classified-outcome continuationのsegment（**実Phase 101 → 実Phase 94 → 実Phase 87**）が、Phase 155 provenance persisted outcome（`current_step_index >= 6`、predecessorの空`output_text`、immediate predecessorの`request_id=None`・`provider="openai"`）を、次strict seamであるPhase 80へ正しく受け渡せるようにする**staged compatibility repair**です。Phase 168で修復したPhase 122→115→108 segmentの直後にあるcontinuation segmentで、Phase 101とPhase 87はstrict loader（`load_strict_terminal_history`）がPhase-155 provenance historyを拒否する場合にのみ、public `load_workflow_execution_history`（`WorkflowExecutionPersistenceTargets`）+ 限定されたPhase-155互換検証へフォールバックします。

```text
Phase 101 (classified outcome cycle closure continuation boundary, final dependency: Phase 94)
    ↓ Phase 94 (classified outcome dispatch phase bridge cycle reentry, final dependency: Phase 87; productionは変更しない)
    ↓ Phase 87 (classified outcome routing phase bridge cycle reentry, final dependency: Phase 80)
Phase 80 (next strict seam: 変更しない)
```

### 互換性フォールバック（strict-first + local bounded）

- **Phase 101 / 87 共通**: `_validate_terminal` / `_terminal` のstrict loadを `try: ...` / `except TerminalHistoryContractError:` で包み、Phase-155互換ケース（exact `PersistedExecutionOutcome` + exact builtin int `current_step_index >= 6`）に限定した `_load_phase155_terminal_history` へフォールバックする。public loader使用（`WorkflowExecutionPersistenceTargets(state_path, events_path)`）、`_valid_phase155_terminal_history` で検証。`OSError`はstrict経路のまま`terminal_contract`（I/O失敗をcompatibility fallbackの理由にしない）
- **predecessorの空`output_text`のみ緩和**: `type(output_text) is str`（空文字列は許容、`None`・非strはinvalid維持）。predecessorのprovider / request-ID検証は追加しない（shared strict historyはそれらをgateしないため）。terminal succeededの`output_text`非空・`response_id`非空、terminal failedの`failure_category`・`message`（`isinstance(message, str)`、`""`許容）はstrict契約を維持
- **Phase 94 productionは変更しない**: Phase 94は既存アーキテクチャ通りPhase 87のvalidation helpers（`_inputs`/`_terminal`/`_unchanged`/`_progression`ほか）を再利用するため、Phase 87修復後にtransitively compatibleになる。Phase 169ではPhase 94 productionを一切変更せず、これをテストで証明する
- **Phase 80はstrict seamのまま**: `route_classified_outcome_routing_phase_bridge_cycle_continuation` は変更せず、intact Phase-155 historyを `terminal_contract` で拒否し続ける
- **`WorkflowProgressionDecision(workflow_complete)` ルートはstrictのまま**: fallbackはexact `PersistedExecutionOutcome` にのみ適用され、completionルートがpredecessor空output互換を得ることはない

### 変更ファイル（正確に10ファイル）

1. `src/ai_office/engine/classified_outcome_cycle_closure_continuation_boundary.py` — Phase 101 production
2. `tests/test_classified_outcome_cycle_closure_continuation_boundary.py` — Phase 101 focused test（+6 cases）
3. `tests/test_classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation.py` — Phase 94 focused test（+6 cases、productionは無変更）
4. `src/ai_office/engine/classified_outcome_routing_phase_bridge_cycle_reentry_continuation.py` — Phase 87 production
5. `tests/test_classified_outcome_routing_phase_bridge_cycle_reentry_continuation.py` — Phase 87 focused test（+6 cases）
6. `tests/test_classified_outcome_phase101_87_phase155_provenance_compatibility.py` — 新規regression test（+6 cases + inline real Phase 80 strict-seam proof）
7. `tests/test_classified_persisted_outcome_progression_phase122_108_phase155_provenance_compatibility.py` — Phase 168 regression test（inline Phase 101 rejection proofをacceptance proofへ更新、+0）
8. `tests/test_classified_persisted_outcome_progression_phase143_129_phase155_provenance_compatibility.py` — Phase 167 regression test（inline Phase 101 rejection proofをacceptance proofへ更新、+0）
9. `README.md` — Phase 169 documentation
10. `docs/architecture.md` — Phase 169 architecture documentation

### 非機能範囲（State explicitly）

Phase 169は以下のbehaviorを**一切**追加・変更しない:

- 新しいpublic boundary（新規public関数・新規ルーティング・新規API）を追加しない
- 自動継続（automatic continuation）は行わない
- workflow progression・next-step preparation・start は行わない
- provider / tool 実行は行わない
- retry・loop・schedule・parallel・finalize は行わない
- CLI・GUI behavior は追加・変更しない
- 共有 `terminal_history_contract.py` の意味を広げない（strict contract は不変）
- 新しい request-ID / provider semantics を導入しない（Phase 155 provenance の `request_id=None`・`provider="openai"` を許容するだけ）

### 変更しないもの

- Phase 94 production module（`classified_outcome_dispatch_phase_bridge_cycle_reentry_continuation.py`）— 変更しない（transitively compatibleをテストで証明）
- Phase 80 production module（`classified_outcome_routing_phase_bridge_cycle_continuation.py`）— strict seamのまま
- Phase 143/144/136/129/122/115/108 production modules
- Phase 162/163/164/165/166/167/168 production modules（上記2ファイル以外）
- `src/ai_office/engine/terminal_history_contract.py`
- `src/ai_office/engine/__init__.py`
- 既存テストの削除・rename・skip・xfail・parameter-collapse・弱体化
- エラー分類・quality feedback literal・provider / request-ID semantics
- 実Phase 30 persistence、shared storage/runtime/provider code、CLI / GUI behavior

## Phase 170: Repair Phase-155 Provenance Compatibility across Phase 80 → 73 → 59 Classified-Outcome Routing Segment

Phase 170は、classified-outcome routingのsegment（**実Phase 80 → 実Phase 73 → 実Phase 59**）が、Phase 155 provenance persisted outcome（`current_step_index >= 6`、predecessorの空`output_text`、immediate predecessorの`request_id=None`・`provider="openai"`）を、次strict seamであるPhase 52へ正しく受け渡せるようにする**staged compatibility repair**です。Phase 169で修復したPhase 101 → 94 → 87 continuation segmentの直後にあるrouting segmentで、Phase 80・Phase 73・Phase 59はstrict loader（`load_strict_terminal_history`）がPhase-155 provenance historyを拒否する場合にのみ、public `load_workflow_execution_history`（`WorkflowExecutionPersistenceTargets`）+ 限定されたPhase-155互換検証へフォールバックします。

```text
Phase 80 (classified outcome routing phase bridge cycle continuation, final dependency: Phase 73)
    ↓ Phase 73 (classified outcome routing phase bridge continuation, final dependency: Phase 59)
    ↓ Phase 59 (classified persisted outcome routing phase bridge reentry, final dependency: Phase 52)
Phase 52 (next strict seam: 変更しない)
```

### 互換性フォールバック（strict-first + local bounded）

- **Phase 80 / 73 / 59 共通**: `_validate_terminal` のstrict loadを `try: ...` / `except TerminalHistoryContractError:` で包み、Phase-155互換ケース（exact `PersistedExecutionOutcome` + exact builtin int `current_step_index >= 6`）に限定した `_load_phase155_terminal_history` へフォールバックする。public loader使用（`WorkflowExecutionPersistenceTargets(state_path, events_path)`）、`_valid_phase155_terminal_history` / `_valid_phase155_predecessor` / `_valid_phase155_terminal_event` で検証。`TerminalHistoryContractError` の `__cause__` が `WorkflowExecutionLoadError` の場合はfallbackに入らず `terminal_contract`（transient I/O失敗をcompatibility fallbackの理由にしない）、`OSError`もstrict経路のまま `terminal_contract`
- **predecessorの空`output_text`のみ緩和**: `type(output_text) is str`（空文字列は許容、`None`・非strはinvalid維持）。predecessorのprovider / request-ID検証は追加しない（shared strict historyはそれらをgateしないため）。terminal succeededの`output_text`非空・`response_id`非空、terminal failedの`failure_category`・`message`（`isinstance(message, str)`、`""`許容）はstrict契約を維持
- **Phase 52 productionは変更しない**: 次strict seamとして現状を維持し、intact Phase-155 historyを `terminal_contract` で拒否し続ける（inline next-seam proofで証明）
- **`WorkflowProgressionDecision(workflow_complete)` ルートはstrictのまま**: fallbackはexact `PersistedExecutionOutcome` にのみ適用され、completionルートがpredecessor空output互換を得ることはない
- **Phase 59のpersisted-failureは委譲を維持**: Phase 59はfailureもPhase 52へちょうど1回委譲し、同一failureオブジェクトの返却を要求する既存ルーティングを維持する（Phase 80/73のfailureはローカルzero-call stopのまま）

### 変更ファイル（正確に12ファイル）

1. `src/ai_office/engine/classified_outcome_routing_phase_bridge_cycle_continuation.py` — Phase 80 production
2. `src/ai_office/engine/classified_outcome_routing_phase_bridge_continuation.py` — Phase 73 production
3. `src/ai_office/engine/classified_persisted_outcome_routing_phase_bridge_reentry.py` — Phase 59 production
4. `tests/test_classified_outcome_routing_phase_bridge_cycle_continuation.py` — Phase 80 focused test（+6 cases + inline pins）
5. `tests/test_classified_outcome_routing_phase_bridge_continuation.py` — Phase 73 focused test（+6 cases + inline pins）
6. `tests/test_classified_persisted_outcome_routing_phase_bridge_reentry.py` — Phase 59 focused test（+6 cases + inline pins）
7. `tests/test_classified_outcome_phase80_59_phase155_provenance_compatibility.py` — 新規regression test（+6 cases + inline real Phase 52 strict-seam proof）
8. `tests/test_classified_outcome_phase101_87_phase155_provenance_compatibility.py` — Phase 169 regression test（inline real Phase 80 rejection proofをacceptance proofへ更新、+0）
9. `tests/test_classified_persisted_outcome_progression_phase143_129_phase155_provenance_compatibility.py` — Phase 167 regression test（inline real Phase 80 rejection proofをacceptance proofへ更新、+0）
10. `tests/test_classified_persisted_outcome_progression_phase122_108_phase155_provenance_compatibility.py` — Phase 168 regression test（inline real Phase 80 rejection proofをacceptance proofへ更新、+0）
11. `README.md` — Phase 170 documentation
12. `docs/architecture.md` — Phase 170 architecture documentation

### 非機能範囲（State explicitly）

Phase 170は以下のbehaviorを**一切**追加・変更しない:

- 新しいpublic boundary（新規public関数・新規ルーティング・新規API）を追加しない
- 自動継続（automatic continuation）は行わない
- workflow progression・next-step preparation・start は行わない
- provider / tool 実行は行わない
- retry・loop・schedule・parallel・finalize は行わない
- CLI・GUI behavior は追加・変更しない
- 共有 `terminal_history_contract.py` の意味を広げない（strict contract は不変）
- 新しい request-ID / provider semantics を導入しない（Phase 155 provenance の `request_id=None`・`provider="openai"` を許容するだけ）

### 変更しないもの

- Phase 52 production module（`classified_persisted_outcome_routing_bridge_reentry.py`）— strict seamのまま
- Phase 101/94/87 production modules（Phase 169で修復済みのcontinuation segment）
- Phase 143/144/136/129/122/115/108 production modules
- Phase 162/163/164/165/166/167/168/169 production modules
- `src/ai_office/engine/terminal_history_contract.py`
- `src/ai_office/engine/__init__.py`
- 既存テストの削除・rename・skip・xfail・parameter-collapse・弱体化
- エラー分類・quality feedback literal・provider / request-ID semantics
- 実Phase 30 persistence、shared storage/runtime/provider code、CLI / GUI behavior

## Phase 171: Complete Phase-155 Provenance Compatibility across Phase 52 → 45 → 38 Persisted-Outcome Routing Tail

Phase 171は、Phase 155 provenance persisted outcome（`current_step_index >= 6`、predecessorの空`output_text`、immediate predecessorの`request_id=None`・`provider="openai"`）を、**実Phase 52 bridge → 実Phase 45 → 実Phase 38 → 実Phase 37 / 31 → 実Phase 25**の下流実チェーンが無変更で受容することを証明する**final staged compatibility repair**です。Phase 170まで「次strict seam」として変更しなかったPhase 52を、`strict-first + local bounded fallback`で修復し、`Phase 45 → 38 → 37 / 31 → 25`の実ルーティング tailが同一provenanceをそのまま受け渡せることを実チェーンregressionで固定します。

```text
Phase 52 (classified persisted outcome routing bridge reentry, final dependency: Phase 45)
    ↓ Phase 45 (classified persisted outcome routing reentry, final dependency: Phase 38)
    ↓ Phase 38 (persisted execution outcome routing reentry, classification: Phase 37 / progression: Phase 31)
    ↓ Phase 37 (persisted execution outcome classification reentry) / Phase 31 (persisted success progression reentry)
    ↓ Phase 25 (workflow progression)
```

### 互換性フォールバック（strict-first + local bounded）

- **Phase 52のみ修復**: `_validate_terminal` のstrict loadを `try: ...` / `except TerminalHistoryContractError:` で包み、Phase-155互換ケース（exact `PersistedExecutionOutcome` + exact builtin int `current_step_index >= 6`）に限定した `_load_phase155_terminal_history` へフォールバックする。public loader使用（`WorkflowExecutionPersistenceTargets(state_path, events_path)`）、`_valid_phase155_terminal_history` / `_valid_phase155_predecessor` / `_valid_phase155_terminal_event` で検証（Phase 52ローカルに複製、他Phaseからimportしない）
- **transient I/O失敗はfallbackの理由にしない**: `TerminalHistoryContractError` の `__cause__` が `WorkflowExecutionLoadError` の場合は `terminal_contract` のまま（retry readが成功してもfallbackに入らない）。raw `OSError` もstrict経路のまま `terminal_contract`
- **predecessorの空`output_text`のみ緩和**: `type(output_text) is str`（空文字列は許容、`None`・非strはinvalid維持）。predecessorのprovider / request-ID検証は追加しない（Phase 155の`request_id=None`・`provider="openai"`を許容するだけ）
- **terminal検証はstrict維持**: succeededは`response_id`非空str・`output_text`非空str・`message None`。failedは`response_id None`・`output_text None`・failure-category連動・`message`はstr（空文字列許容）
- **completionルートはstrictのまま**: fallbackはexact `PersistedExecutionOutcome` にのみ適用され、`WorkflowProgressionDecision(workflow_complete)` ルートがpredecessor空output互換を得ることはない
- **下流実チェーンは無変更**: 実Phase 45（`_load_terminal_history`はpredecessor `output_text`をgateしない）・実Phase 38（公開Phase 37で再分類し、persisted failureは同一outcomeを返しprogressionを呼ばない）・実Phase 37 / 31 / 25が、同一provenanceを同一object identityで受け渡す

### 変更ファイル（正確に6ファイル）

1. `src/ai_office/engine/classified_persisted_outcome_routing_bridge_reentry.py` — Phase 52 production（strict-first + local bounded fallback）
2. `tests/test_classified_persisted_outcome_routing_bridge_reentry.py` — Phase 52 focused test（+6 cases + inline pins、transient I/O pin含む）
3. `tests/test_classified_persisted_outcome_phase52_25_phase155_provenance_compatibility.py` — 新規regression test（+6 cases、実Phase 143 classify → 実Phase 52 → 実Phase 45 → 38 → 37 / 31 → 25の実チェーン）
4. `tests/test_classified_outcome_phase80_59_phase155_provenance_compatibility.py` — Phase 170 regression test（synthetic seamを実Phase 52 counting wrapperへ置換、inline strict-seam proofを削除、+0・6 collected同名維持）
5. `README.md` — Phase 171 documentation
6. `docs/architecture.md` — Phase 171 architecture documentation

### 非機能範囲（State explicitly）

Phase 171は以下のbehaviorを**一切**追加・変更しない:

- 新しいpublic boundary（新規public関数・新規ルーティング・新規API）を追加しない
- 自動継続（automatic continuation）は行わない
- workflow progression・next-step preparation・start は行わない
- provider / tool 実行は行わない
- retry・loop・schedule・parallel・finalize は行わない
- CLI・GUI behavior は追加・変更しない
- 共有 `terminal_history_contract.py` の意味を広げない（strict contract は不変）
- 新しい request-ID / provider semantics を導入しない（Phase 155 provenance の `request_id=None`・`provider="openai"` を許容するだけ）
- Phase 45 / 38 / 37 / 31 / 25 productionは変更しない

### 変更しないもの

- Phase 45 / 38 / 37 / 31 / 25 production modules
- Phase 59 / 73 / 80 production modules（Phase 170で修復済みのrouting segment）
- Phase 101/94/87、Phase 143/144/136/129/122/115/108 production modules
- Phase 162/163/164/165/166/167/168/169/170 production modules
- `src/ai_office/engine/terminal_history_contract.py`
- `src/ai_office/engine/__init__.py`
- 既存テストの削除・rename・skip・xfail・parameter-collapse・弱体化
- エラー分類・quality feedback literal・provider / request-ID semantics
- 実Phase 30 persistence、shared storage/runtime/provider code、CLI / GUI behavior

## Phase 172: Post-Runtime Persistence → Classification → Progression Orchestration Boundary

Phase 172は、**Phase-155 runtime/stop result を 1 つ受け取り、公開 Phase 161 → Phase 143 → Phase 144 をこの順でちょうど 1 回ずつ合成する最初の明示的な orchestration boundary**です。compatibility repair ではなく、既存の公開境界を直列接続します。

```text
Phase 155 result (StepRuntimeExecutionSuccess / Failure, または stop: WorkflowProgressionDecision / PersistedExecutionOutcome)
    ↓ Phase 161 (runtime-result transition-persistence outer-chain continuation boundary)
    ↓ Phase 143 (persisted transition outcome classification)
    ↓ Phase 144 (classified persisted outcome progression)
    ↓ WorkflowProgressionDecision / PersistedExecutionOutcome
```

### 核心契約（durable commit point）

- **Phase 161 が exact `WorkflowExecutionPersistenceResult` を返した時点で、post-call target bytes を durable commit point とする**
- **Phase 143 / 144 の失敗で pre-Phase161 running 状態へ巻き戻さない**: 補償は committed bytes への復元のみ
- stop 入力（`WorkflowProgressionDecision` / `PersistedExecutionOutcome`）は Phase 161 を 1 回呼び、identity を返して停止（後続 stage は 0 回）
- retry・loop なし、各 stage 最大 1 回

### エラー分類（12 分類）

`result_type` / `workflow_definition` / `state_target` / `event_target` / `target_conflict` / `configuration` / `phase161_contract` / `phase143_contract` / `phase144_contract` / `dependency_error` / `committed_mutation` / `rollback_failure`

- safe error（Phase 161 / 143 / 144 の公開エラー型）は同一 object を identity で re-raise
- 予期しない例外は `dependency_error` に sanitize（detail-safe 固定メッセージ）
- 成功後の target mutation は `committed_mutation`、復元不能は `rollback_failure`

### 変更ファイル（正確に5ファイル）

1. `src/ai_office/engine/runtime_result_to_progression_orchestration_boundary.py` — Phase 172 production（新規）
2. `src/ai_office/engine/__init__.py` — Phase 172 public export（+4 symbols、アルファベット順）
3. `tests/test_runtime_result_to_progression_orchestration_boundary.py` — Phase 172 focused test（focused 18 + real-default A/B/C = 21 cases）
4. `README.md` — Phase 172 documentation
5. `docs/architecture.md` — Phase 172 architecture documentation

### 非機能範囲（State explicitly）

Phase 172は以下のbehaviorを**一切**追加・変更しない:

- 新しい互換性修復（compatibility repair）を追加しない
- 自動継続・workflow progression 自体の実行・next-step preparation・start・provider / tool 実行は行わない
- retry・loop・schedule・parallel・finalize は行わない
- Phase 161 / 143 / 144 production は変更しない（public function + error class のみ import）
- CLI・GUI behavior は追加・変更しない
- 下流（Phase 142 / 135 / 136 / 30 / 37 / 31 / 25 等）を参照しない

### 変更しないもの

- Phase 161 / 143 / 144 production modules
- Phase 155 / 142 / 135 / 136 / 30 / 37 / 31 / 25 production modules
- `src/ai_office/engine/terminal_history_contract.py`
- 既存テストの削除・rename・skip・xfail・parameter-collapse・弱体化
- エラー分類・quality feedback literal・provider / request-ID semantics
- 実Phase 30 persistence、shared storage/runtime/provider code、CLI / GUI behavior

## Phase 173: Post-Runtime → Approved-Preparation Orchestration Boundary

Phase 173は、**Phase 172 の公開 result を、そのまま公開 Phase 145 の approved next-step preparation 境界に合成する、Phase 172 に続く次の integration boundary**です。compatibility repair ではなく、既存の公開境界を直列接続します。**まだ workflow runner ではありません**。

```text
Phase 155 result
    ↓ Phase 172 runtime result → persistence → classification → progression
    ↓ WorkflowProgressionDecision(prepare_next_step | workflow_complete)
    ↓   または exact PersistedExecutionOutcome(persisted_failure)
    ↓ Phase 145 progression → explicitly approved next-step preparation
    ↓ PreparedWorkflowStep
    ↓   または exact stop object (workflow_complete / persisted_failure)
```

### 核心契約（stage ownership / committed continuation）

- **Phase 172 は Phase 161 の durable commit point を所有する**: Phase 173 は Phase 172 stage の失敗・不正戻り値に対して pre-Phase172 への巻き戻しを行わない（既に永続化済みの runtime result を消さない）
- **Phase 172 成功後の target bytes を post-Phase172 committed continuation snapshot とする**
- Phase 145 はその snapshot に対して read-only。失敗時は committed bytes への復元のみ（pre-Phase172 へは戻さない）
- approval / employee は **Phase 172 の前に prevalidate しない**: 完了済み runtime result は approval が欠落・不正でも durable に persist/classify/progress され、検証は Phase 145 の責務
- 各 stage ちょうど 1 回、retry・loop・bypass なし。`PreparedWorkflowStep` または exact stop object で停止し、次の step を start / persist / execute しない

### エラー分類（11 分類）

`result_type` / `workflow_definition` / `state_target` / `event_target` / `target_conflict` / `configuration` / `phase172_contract` / `phase145_contract` / `dependency_error` / `committed_mutation` / `rollback_failure`

- Phase 172 / 145 の既存 safe error は同一 object を identity で re-raise
- 予期しない例外は `dependency_error` に sanitize（detail-safe 固定メッセージ）
- Phase 145 成功後の target mutation は `committed_mutation`、復元不能は `rollback_failure`

### 変更ファイル（正確に5ファイル）

1. `src/ai_office/engine/runtime_result_to_approved_preparation_orchestration_boundary.py` — Phase 173 production（新規）
2. `src/ai_office/engine/__init__.py` — Phase 173 public export（+4 symbols、アルファベット順）
3. `tests/test_runtime_result_to_approved_preparation_orchestration_boundary.py` — Phase 173 focused test（focused 16 + real-default A/B/C/D = 20 cases）
4. `README.md` — Phase 173 documentation
5. `docs/architecture.md` — Phase 173 architecture documentation

### 非機能範囲（State explicitly）

Phase 173は以下のbehaviorを**一切**追加・変更しない:

- Phase 172 / 145 または下流 production を変更しない（public function + error class のみ import）
- 自動 next-step start・start-state persistence・provider / model 呼び出し・tool 実行を行わない
- retry・workflow loop・schedule・parallel・finalize・artifact persistence を行わない
- CLI / GUI behavior・credentials・provider / network / paid API 呼び出しは行わない
- `terminal_history_contract.py` を変更しない

### 変更しないもの

- Phase 172 / 145 / 161 / 143 / 144 / 137 / 129 / 30 / 37 / 31 / 25 / 155 production modules
- 既存テストの削除・rename・skip・xfail・parameter-collapse・弱体化
- shared storage/runtime/provider code、CLI / GUI behavior

## Phase 174: Repair Phase-155 Prepared-step-start Compatibility across Phase 146 → 138 → 131

Phase 174は、Phase 173 `PreparedWorkflowStep(step 7)` が**実 Phase 146 → 実 Phase 138 → 実 Phase 131 → 無変更の Phase 124 → 117 → 110 → 103 → 96 → 89 → 82 → 75 → 68 → 無変更の Phase 61 → 54 → 47 → 40 → 33** を経て exact `PreparedStepExecutionStart(step 7)` に到達するための最小 compatibility repair です。新しい orchestration boundary は追加せず、将来の Phase173→146 boundary も追加しません。

```text
Phase 173 PreparedWorkflowStep(step 7)
    ↓ Phase 146 → 138 → 131（本 Phase で修復）
    ↓ unchanged Phase 124 → 117 → 110 → 103 → 96 → 89 → 82 → 75 → 68
    ↓ unchanged Phase 61 → 54 → 47 → 40 → 33
    ↓ PreparedStepExecutionStart(step 7)
```

### 実測された残存 seam（#360 は superseded）

#360 の preflight は base `cf401996` 上で Phase 47/54/61 の blocker が存在しないことを証明しました（実 Phase 61→54→47→40→33 は canonical history を受理・targets 不変）。commit `0d13f9f` が共有 strict terminal-history contract を非final empty-success 受理に修正済みで、共有契約は predecessor request-ID gate を持ちません。残る local seam は次の3つだけです:

1. **Phase 146** が全 predecessor の `request_id` 非空を要求
2. **Phase 138** が同じ制約を繰り返す
3. **Phase 131** は request_id optional だが predecessor `output_text` 非空を要求

Phase 124→117→110→103→96→89→82→75→68 には同等の gate はありません（fresh audit 済み）。

### Production correction A — Phase 146（prepared route only）

- exact persisted current index `>= 6` に限定
- **immediate predecessor のみ** `request_id is None` を許容
- earlier predecessor の request ID は non-empty exact string のまま
- immediate の `""` と無効型は従来どおり拒否
- index 5 以下は strict 維持
- immediate predecessor provider `"openai"` 必須維持
- 既存の empty predecessor-output 互換性は不変
- stop routes / terminal semantics は不変、Phase146→138 は exactly once・既存 compensation/no-retry 維持

### Production correction B — Phase 138（prepared route only）

同じ bounded immediate-predecessor request-ID compatibility を `PreparedWorkflowStep` のみに適用します。index 5 以下 strict、stop routes 不変、Phase138→131 を exactly once 維持し、Phase 131 を bypass しません。

### Production correction C — Phase 131（prepared route only）

Phase 131 の event shape は既に `request_id=None` を許可するため request-ID restriction は追加しません。exact persisted current index `>= 6` に限定して:

- predecessor success `output_text` は exact built-in `str` のまま空文字を許容（immediate・earlier とも）
- `None` / non-string は拒否維持
- index 5 以下は旧 strict predecessor-output contract 維持
- terminal / history loading は不変、Phase131→124 は exactly once・既存 compensation/no-retry 維持

### 変更ファイル（正確に6ファイル）

1. `src/ai_office/engine/prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py` — Phase 146 production
2. `src/ai_office/engine/prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — Phase 138 production
3. `src/ai_office/engine/prepared_step_start_cycle_handoff_chain_bridge_reentry_continuation_boundary.py` — Phase 131 production
4. `tests/test_prepared_step_start_phase146_138_131_phase155_provenance_compatibility.py` — 新規 +8 compatibility test
5. `README.md` — Phase 174 documentation
6. `docs/architecture.md` — Phase 174 architecture documentation

### 非機能範囲（State explicitly）

Phase 174は以下のbehaviorを**一切**追加・変更しない:

- Phase 124 / 117 / 110 / 103 / 96 / 89 / 82 / 75 / 68 / 61 / 54 / 47 / 40 / 33、Phase 173、`terminal_history_contract.py` を変更しない
- `engine/__init__.py` は変更しない
- 自動 next-step start・start-state persistence・provider / model 呼び出し・tool 実行を行わない
- retry・workflow loop・schedule・parallel・finalize・artifact persistence を行わない
- CLI / GUI behavior・credentials・provider / network / paid API 呼び出しは行わない
- 将来の Phase173→146 orchestration boundary は追加しない（※ Phase 175 で追加されたため、本条は Phase 175 により superseded）

## Phase 175: Post-Runtime → Prepared-Step-Start Orchestration Boundary

Phase 175は、**Phase 173 の公開 result を、そのまま公開 Phase 146 の prepared-step-start chain に合成する、Phase 173 に続く次の integration boundary** です。compatibility repair ではなく、既存の公開境界を直列接続します。**まだ workflow runner ではありません**。

```text
Phase 155 result (StepRuntimeExecutionSuccess / Failure, または stop)
    ↓ Phase 173 post-runtime → persistence → classification → progression → approved preparation
    ↓ PreparedWorkflowStep / exact stop object (workflow_complete / persisted_failure)
    ↓ Phase 146 prepared-step-start chain（146 → 138 → 131 → … → 33）
    ↓ PreparedStepExecutionStart / exact stop object
```

### 核心契約（stage ownership / committed continuation）

- **Phase 173 は Phase 161 の durable commit point を所有する**: Phase 175 は Phase 173 stage の失敗・不正戻り値に対して pre-Phase173 への巻き戻しを行わない（既に永続化済みの runtime result を消さない）
- **Phase 173 成功後の target bytes を post-Phase173 committed continuation snapshot とする**
- Phase 146 はその snapshot に対して read-only。失敗時は committed bytes への復元のみ（pre-Phase173 へは戻さない）
- approval / employee は **Phase 173 の前に prevalidate しない**: 完了済み runtime result は approval が欠落・不正でも durable に persist/classify/progress され、検証は Phase 173 内部の Phase 145 の責務
- 各 stage ちょうど 1 回、retry・loop・bypass なし。`PreparedStepExecutionStart` または exact stop object で停止し、次の step を start / persist / execute しない

### エラー分類（11 分類）

`result_type` / `workflow_definition` / `state_target` / `event_target` / `target_conflict` / `configuration` / `phase173_contract` / `phase146_contract` / `dependency_error` / `committed_mutation` / `rollback_failure`

- Phase 173 stage の既存 safe error（Phase 173 / Phase 172 / Phase 145）は同一 object を identity で re-raise（Phase 146 呼び出し 0 回・write なし）
- Phase 146 stage の既存 safe error（Phase 146 / Phase 138）は committed bytes 復元後に identity で re-raise
- 予期しない例外は `dependency_error` に sanitize（detail-safe 固定メッセージ）、不正戻り値は `phase173_contract` / `phase146_contract`
- 有効戻り値 + target mutation は `committed_mutation`、復元失敗は `rollback_failure`（両 target を 1 回ずつ試行、stage retry なし）

### 変更ファイル（正確に5ファイル）

1. `src/ai_office/engine/runtime_result_to_prepared_step_start_orchestration_boundary.py` — Phase 175 production（新規）
2. `tests/test_runtime_result_to_prepared_step_start_orchestration_boundary.py` — 新規 +20 tests（focused 16 + real-default 4）
3. `src/ai_office/engine/__init__.py` — Phase 175 public exports
4. `README.md` — Phase 175 documentation
5. `docs/architecture.md` — Phase 175 architecture documentation

### 非機能範囲（State explicitly）

Phase 175は以下のbehaviorを**一切**追加・変更しない:

- Phase 146 / 138 / 131 / 124 / 117 / 110 / 103 / 96 / 89 / 82 / 75 / 68 / 61 / 54 / 47 / 40 / 33、Phase 173、`terminal_history_contract.py` を変更しない
- 自動 next-step start・start-state persistence・provider / model 呼び出し・tool 実行を行わない
- retry・workflow loop・schedule・parallel・finalize・artifact persistence を行わない
- CLI / GUI behavior・credentials・provider / network / paid API 呼び出しは行わない

### canonical B/C provenance（prerequisite #365 解決済み）

Phase 175 の B/C（6-step success → `workflow_complete` / 6-step failure → `persisted_failure` の exact identity 保持）は、Issue #363 が指定する **canonical Phase-155 provenance**（直前 step-5 が `provider="openai"` / `output_text=""` / `request_id=None`）のまま実検証しています。prerequisite #365 のマージにより public Phase 146 の stop ルートはこの canonical provenance を受理するため、strict provenance の deviation はありません（詳細は PR 本文参照）。

## Phase 176: Post-Runtime → Prepared Running-State Persistence Orchestration Boundary

Phase 176は、**公開 Phase 175 の結果を、そのまま公開 Phase 147 の prepared-start persistence chain に合成する、Phase 175 に続く次の integration boundary** です。compatibility repair ではなく、既存の公開境界（Phase 175 と #367 で互換性修復済みの Phase 147）を直列接続します。**まだ workflow runner ではありません**。

```text
Phase 155 result (StepRuntimeExecutionSuccess / Failure, または stop)
    ↓ Phase 175 post-runtime → persistence → classification → progression → approved preparation → prepared-step start
    ↓ PreparedStepExecutionStart / exact stop object (workflow_complete / persisted_failure)
    ↓ Phase 147 prepared-start persistence chain（147 → 139 → 132 → … → lower persistence chain）
    ↓ RunningStatePersistenceResult / exact stop object
```

### 核心契約（stage ownership / committed terminal snapshot）

- **Phase 175 は durable runtime-result commit point を所有する**: Phase 176 は Phase 175 stage の失敗・不正戻り値に対して pre-Phase175 への巻き戻しを行わない（既に durable commit 済みの finished runtime result を消さない）
- **Phase 175 成功後の target bytes を post-Phase175 committed terminal snapshot とする**
- Phase 147 は prepared route でのみ state target の変更が許可される。成功した state-only persistence が **新しい durable running-state commit point** になる（event bytes は不変・step-7 runtime event は追加しない）
- Phase 147 失敗時は committed terminal bytes への復元のみ（pre-Phase175 running state へは戻さない）。復元失敗は `rollback_failure`
- approval / employee は **Phase 175 の前に prevalidate しない**: 完了済み runtime result は approval が欠落・不正でも durable に persist/classify/progress され、検証は Phase 175 内部の責務
- 各 stage ちょうど 1 回、retry・loop・bypass なし。`RunningStatePersistenceResult` または exact stop object で停止し、persist 済み step の実行・provider/model 呼び出し・tool 実行・schedule・自動継続はしない

### エラー分類（11 分類）

`result_type` / `workflow_definition` / `state_target` / `event_target` / `target_conflict` / `configuration` / `phase175_contract` / `phase147_contract` / `dependency_error` / `committed_mutation` / `rollback_failure`

- Phase 175 stage の既存 safe error（Phase 175 / 173 / 172 / 145 / 146 / 138 error type）は同一 object を identity で re-raise（Phase 147 呼び出し 0 回・write なし）
- Phase 147 stage の既存 safe error（Phase 147 / 139 error type）は committed bytes 復元後に identity で re-raise
- 予期しない例外は `dependency_error` に sanitize（detail-safe 固定メッセージ）、不正戻り値は `phase175_contract` / `phase147_contract`
- 有効戻り値 + target mutation は `committed_mutation`、復元失敗は `rollback_failure`（両 target を 1 回ずつ試行、stage retry なし）

### 変更ファイル（正確に5ファイル）

1. `src/ai_office/engine/runtime_result_to_prepared_start_persistence_orchestration_boundary.py` — Phase 176 production（新規）
2. `tests/test_runtime_result_to_prepared_start_persistence_orchestration_boundary.py` — 新規 +20 tests（focused 16 + real-default 4）
3. `src/ai_office/engine/__init__.py` — Phase 176 public exports
4. `README.md` — Phase 176 documentation
5. `docs/architecture.md` — Phase 176 architecture documentation

### 非機能範囲（State explicitly）

Phase 176は以下のbehaviorを**一切**追加・変更しない:

- Phase 175 / 147 / 139 / 132 または下流 prepared-start persistence production、Phase 146 / 138 / 131、Phase 173 / 172 / 161 またはその下流チェーン、`terminal_history_contract.py`、`storage/running_state_persistence.py` を変更しない
- 自動 next-step execution・persist 済み step の実行・provider / model 呼び出し・tool 実行・runtime-result persistence（新規 step 分）を行わない
- retry・workflow loop・schedule・parallel・finalize・artifact persistence を行わない
- CLI / GUI behavior・credentials・provider / network / paid API 呼び出しは行わない

## Phase 177: Post-Runtime → Persisted Running Execution Orchestration Boundary

Phase 177は、**公開 Phase 176 の結果（durable running-state persistence）を、そのまま公開 Phase 155 の persisted-running execution chain に合成する、Phase 176 に続く次の integration boundary** です。compatibility repair ではなく、既存の公開境界（Phase 176 と #375 と同じ Phase-155 runtime/stop 入力ファミリーを受け付ける公開 Phase 155）を直列接続します。**workflow runner ではありません**。

```text
finished current-step Phase-155 result (StepRuntimeExecutionSuccess / Failure, または stop)
    ↓ Phase 176 post-runtime → durable persistence → classification → progression → approval/preparation/start
    ↓ capture-only delegating Phase-147 adapter（実 Phase 147 handoff から exact PreparedStepExecutionStart を capture）
    ↓ RunningStatePersistenceResult / exact stop object（workflow_complete / persisted_failure）
    ↓ Phase 155 persisted running execution chain（155 → 141 → 133 → 126 → … → lower chain）
    ↓ StepRuntimeExecutionSuccess / StepRuntimeExecutionFailure / exact stop object
```

### 核心契約（stage ownership / committed running-state snapshot）

- **Phase 176 は durable running-state commit point を所有する**: Phase 177 は Phase 176 stage の失敗・不正戻り値に対して pre-Phase176 への巻き戻しを行わない（既に durable commit 済みの finished runtime result と next running state を消さない）
- **Phase 176 成功後の target bytes を post-Phase176 committed running-state snapshot とする**
- Phase 155 は prepared route でのみちょうど 1 回呼ばれ、next-step runtime execution を実行する。Phase 155 の return 後に target mutation が発生した場合は committed snapshot に復元
- stop 結果（workflow_complete / persisted_failure）は exact identity をそのまま返し、Phase 155 は zero calls
- Phase 147 は capture-only delegating adapter 内でのみ呼ばれる（実 Phase 147 handoff を 1 回実行し、exact `PreparedStepExecutionStart` を capture）。lower boundary は直接呼ばない
- approval / employee / resolved_tools / api_key / execution_approval / transport は **prevalidate しない**: Phase 176 が先に実行され、実行入力の拒否は Phase 155 の責務。Phase 176 所有の preparation 拒否では Phase 155 は zero calls
- 各 stage ちょうど 1 回、retry・loop・bypass なし。ちょうど 1 回の next-step runtime result（成功 / 失敗）または exact stop object で停止し、その runtime result の再永続化・再進行・別ステップ準備・自動継続はしない

### エラー分類（11 分類）

`result_type` / `workflow_definition` / `state_target` / `event_target` / `target_conflict` / `configuration` / `phase176_contract` / `phase155_contract` / `dependency_error` / `committed_mutation` / `rollback_failure`

- Phase 176 stage の既存 safe error（Phase 176 / 175 / 173 / 172 / 145 / 146 / 138 / 147 / 139 error type）は同一 object を identity で re-raise（Phase 155 呼び出し 0 回・write なし）
- Phase 155 stage の既存 safe error（Phase 155 / 141 error type）は committed snapshot への復元後に identity で re-raise
- 予期しない例外は `dependency_error` に sanitize（detail-safe 固定メッセージ）、不正戻り値は `phase176_contract` / `phase155_contract`
- 有効戻り値 + target mutation は `committed_mutation`、復元失敗は `rollback_failure`（両 target を 1 回ずつ試行、stage retry なし）

### 変更ファイル（正確に5ファイル）

1. `src/ai_office/engine/runtime_result_to_persisted_running_execution_orchestration_boundary.py` — Phase 177 production（新規）
2. `tests/test_runtime_result_to_persisted_running_execution_orchestration_boundary.py` — 新規 +20 tests（focused 16 + real-default 4）
3. `src/ai_office/engine/__init__.py` — Phase 177 public exports
4. `README.md` — Phase 177 documentation
5. `docs/architecture.md` — Phase 177 architecture documentation

### 非機能範囲（State explicitly）

Phase 177は以下のbehaviorを**一切**追加・変更しない:

- Phase 176 / 175 / 173 / 172 / 161、Phase 147 / 139 / 132 または下流 prepared-start persistence production、Phase 155 / 141 / 133 / 126 または下流 runtime execution production、`terminal_history_contract.py`、`storage/running_state_persistence.py` を変更しない
- 新しい runtime result の再永続化・2 回目の progression decision・別ステップの preparation / start / persistence cycle を行わない
- retry・自動ループ / 継続・finalize・schedule・parallel・artifact persistence を行わない
- CLI / GUI behavior・credentials・provider / network / paid API 呼び出しは行わない

## Phase 178 prerequisite（Issue #380）: Accumulated Aged None Request-ID Preservation across Persistence / Classification / Progression Entry Layers

Issue #380は、Phase 178（Issue #377）の前提として、**7つの境界すべて**が accumulated aged-None compatibility の対象ですが、**適用ルートは境界ごとに限定**されます。accumulated aged None の許容は、境界時点の current / terminal index が **7以上**（predecessor 履歴が6件以上 = `last_position >= 6`）かつ **predecessor position >= 5** の None request-ID に対して、以下のルートでのみ適用されます:

- **Phase 161 / 142 / 134**: runtime persistence route
- **Phase 143 / 135**: `WorkflowExecutionPersistenceResult` classification route
- **Phase 144 / 136**: `PersistedExecutionOutcome`（`persisted_success`）progression route
- **`workflow_complete` / `persisted_failure` stop route**: aged None を新規に許容しない（既存の immediate-None-only semantics を維持）

### 核心契約

- **accumulated rule**: 境界時点の current / terminal index が **7以上**（`last_position >= 6`）で、**predecessor position >= 5** の None request-ID を許容。`request_id=None` のときは **provider が正確に `"openai"`** であること（新ガード: `if allow_none and event.request_id is None and event.provider != "openai": return False`。non-openai provider の None は従来どおり reject）
- **immediate None は従来どおり**: 直前1件（`position == last_position`）の None 許容は全ルートで維持（`allow_immediate_none_request_id`）
- **許容は新フラグで限定**: `allow_accumulated_none_request_id`（デフォルト False）が有効なのは classification route と `persisted_success` progression route のみ。旧説明のような「`allow_immediate_none_request_id` と独立して常に許容」ではない。**唯一の例外**は Issue #383 の active runtime-failure 経路（Phase 172 が実際に生成した `persisted_failure` に限り、exact default Phase 144 が private opt-in を受けて同じ bounded accumulated rule を適用する）。direct/original `persisted_failure` stop には一切適用しない
- 位置4以前の None request-ID は、`last_position >= 6` でも**依然として reject**（対象外をピン留め）

### ルート別の適用

- **Phase 161 / 142 / 134（runtime persistence chain）**: 実行中（running）persistence route の `_check_predecessor_history` で bounded accumulated provenance を許容（`allow_none = position == last_position or (last_position >= 6 and position >= 5)`）。stop route（`WorkflowProgressionDecision` / `PersistedExecutionOutcome`）はこの検証より手前で return するため accumulated 許容は適用されず、immediate None のみ
- **Phase 143 / 135（persisted classification chain）**: `WorkflowExecutionPersistenceResult` 由来の classification route（`_check_persistence` → `_valid_history`）でのみ `allow_accumulated_none_request_id=True` を有効化
- **Phase 144 / 136（classified progression chain）**: `PersistedExecutionOutcome` かつ `outcome == "persisted_success"` の progression route でのみ `allow_accumulated_none_request_id=True` を有効化。`persisted_failure` / `workflow_complete` の stop route は対象外。**Issue #383 の例外**: Phase 172 の active runtime-failure 経路（exact `StepRuntimeExecutionFailure` → 新規 `persisted_failure` classification）では、exact default Phase 144 のみ private opt-in `_allow_accumulated_none_request_id_for_active_failure=True` を受けて同一の bounded accumulated rule を適用する（direct stop は従来どおり strict）

### 対象7境界（production 修正）

1. `runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py` — Phase 161
2. `runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — Phase 142
3. `runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary.py` — Phase 134
4. `persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — Phase 143
5. `persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary.py` — Phase 135
6. `classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — Phase 144
7. `classified_persisted_outcome_progression_cycle_handoff_chain_bridge_reentry_continuation_boundary.py` — Phase 136

### テスト

- **focused テスト 各4件 × 7ファイル（計+28）**（各モジュールは collected 4件を維持。failure-route / stop の追加検証は collected 数を増やさない inline subcase）:
  1. accumulated None（position 5+6・provider openai）success ルートで委譲1回
  2. **Issue #380 case 2（step8 non-contiguous provenance）**: step5=None / step6=non-empty request ID / step7=None の直後、step8 の境界で delegates ちょうど1回（`test_accumulated_none_step8_noncontiguous_six_request_id_delegates_once`）
  3. position 5 の None + non-openai provider → reject（新ガード）
  4. position 4 の None は依然 reject（`last_position >= 6` でも対象外）をピン留め
- **新規回帰テスト**: `tests/test_phase177_phase172_accumulated_request_id_none_persistence_classification_progression_compatibility.py`（+4件: real A/B/C/D。8-step の step-7 で蓄積 None を実チェーン経由で検証）
- 8-step 用の accumulated セットアップヘルパーを各テストファイルに追加（既存ヘルパーが4〜6ステップのため position 5+6 / step8 に届かない）
- collect 合計: **11,932**（base 11,900 + focused +28 + 新規回帰 +4）

### 変更ファイル（正確に17ファイル）

1. `src/ai_office/engine/runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py` — Phase 161 production
2. `src/ai_office/engine/runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — Phase 142 production
3. `src/ai_office/engine/runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary.py` — Phase 134 production
4. `src/ai_office/engine/persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — Phase 143 production
5. `src/ai_office/engine/persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary.py` — Phase 135 production
6. `src/ai_office/engine/classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — Phase 144 production
7. `src/ai_office/engine/classified_persisted_outcome_progression_cycle_handoff_chain_bridge_reentry_continuation_boundary.py` — Phase 136 production
8. `tests/test_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py` — focused +4
9. `tests/test_runtime_result_transition_persistence_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — focused +4
10. `tests/test_runtime_result_transition_persistence_cycle_handoff_chain_bridge_reentry_continuation_boundary.py` — focused +4
11. `tests/test_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — focused +4
12. `tests/test_persisted_transition_outcome_classification_cycle_handoff_chain_bridge_reentry_continuation_boundary.py` — focused +4
13. `tests/test_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — focused +4
14. `tests/test_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_reentry_continuation_boundary.py` — focused +4
15. `tests/test_phase177_phase172_accumulated_request_id_none_persistence_classification_progression_compatibility.py` — 新規回帰（+4）
16. `README.md` — 本節
17. `docs/architecture.md` — 本 prerequisite のアーキテクチャ文書

### 非機能範囲（State explicitly）

- Phase 178（Issue #377）本体は**実装しない**（本変更は prerequisite のみ）
- 8番目の production 修正・7境界外の production 変更・`__init__.py` export 追加は行わない
- 既存テストの削除・rename・skip・xfail・parameter-collapse・弱体化は行わない
- 位置4以前の None 許容・non-openai の None 許容・provider / network / paid API 呼び出しは行わない

## Phase 178 prerequisite（Issue #383）: Preserve Accumulated Aged None on Active Runtime-Failure Progression without Broadening Direct Stops

Issue #383 は、Issue #380 の accumulated aged-None 保存を **active runtime-failure 経路**（Phase 172 → Phase 161 → Phase 143 → Phase 144）でも成立させる Phase 178 前提修復です。direct/original の `persisted_failure` stop は**一切 broaden しません**。

### 正確な最終ルール

```text
Phase144 persisted_success
  → Issue #380 bounded rule で accumulated aged-None 許容（従来どおり）

Phase172 active runtime failure
  → Phase143 が新規に persisted_failure を分類
  → exact default Phase144 のみ private active-failure opt-in を受ける
  → 同一の bounded accumulated aged-None validation を適用
  → exact persisted_failure を返す（Phase136 は zero-call・no retry）

direct/original persisted_failure stop
  → opt-in なし
  → 既存の immediate-None-only strictness を維持（aged None は terminal_contract のまま）
```

### 実装（production 変更はちょうど2ファイル）

1. **Phase144**（`classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py`）: 公開関数に private keyword-only `_allow_accumulated_none_request_id_for_active_failure: bool = False` を追加（`phase136_function` の後・デフォルト False・有効判定は `is True` のみ）。有効時は既存の bounded accumulated provenance rule（`last_position >= 6`・`position >= 5`・provider が正確に `"openai"`）を `persisted_failure` にも適用し、exact outcome identity を返す。Phase136 は zero-call のまま
2. **Phase172**（`runtime_result_to_progression_orchestration_boundary.py`）: Phase143 の `persisted_failure` classification 後、**入力が exact `StepRuntimeExecutionFailure` かつ `phase144_function is` exact built-in default** のときだけ、Phase144 呼び出しへ private opt-in を渡す。custom injected Phase144 には一切渡さず、従来どおり **4 positional args のみ**（`**kwargs` なしの strict 4引数関数でも TypeError なし・呼び出しはちょうど1回）

### custom injected Phase144 契約（不変）

```text
phase144_function(classified, workflow, state_path, events_path)
```

- 正確に4つの positional 引数
- 新しい keyword は渡さない
- ちょうど1回
- stage order（Phase161 → Phase143 → Phase144）と injected-test 挙動は変更なし

### 明示的に変更しないこと

- Phase136・Phase161・Phase143・Phase177 / 176 / 175 / 173・Phase155・`terminal_history_contract.py`・storage/runtime/invocation/provider/tool・`engine/__init__.py`・CLI / GUI
- accumulated aged-None **terminal stop re-entry** は実装しない（Issue #377 D は matching-terminal-snapshot 契約のまま）
- retry / loop / automatic continuation / finalize / schedule / parallelism / CLI-GUI 挙動は追加しない

### テスト

- **Phase144 focused +2**: (a) private opt-in で bounded accumulated failure provenance 受容（exact persisted_failure identity・Phase136 zero-call・targets bytes unchanged）、(b) default は strict のまま / opt-in も狭い（aged default → `terminal_contract`・position4 None 拒否・non-openai None 拒否・空/非文字列 request-id 拒否・`workflow_complete` 不変・`persisted_success` の Issue #380 挙動不変）
- **Phase172 focused +2**: (a) exact default active runtime failure が opt-in 経由で成功（Phase161 → Phase143 → exact default Phase144 を1回・exact persisted_failure・durable failed target 保持・Phase136/lower 呼び出しなし）、(b) custom injected strict 4引数 Phase144 が TypeError なしで1回呼ばれる（stage order・identity・direct stop は Phase143/144 zero）
- **real regression 更新（+0 collected）**: `test_real_c` を exact Issue #377 C（step5=None + step6=None・openai・step7 決定的 failure）に変更し実 Phase172 経由で `persisted_failure` を受容。同じ collected テスト内の inline subcase で **direct Phase144 default（opt-in なし）では aged step5 None が `terminal_contract` のまま reject** されることを証明

### collect 不変条件

base **11,932** → Phase144 +2 / Phase172 +2 / real regression +0 → **11,936**

### 変更ファイル（正確に7ファイル）

1. `src/ai_office/engine/runtime_result_to_progression_orchestration_boundary.py` — Phase172 routing provenance
2. `tests/test_runtime_result_to_progression_orchestration_boundary.py` — Phase172 focused +2
3. `src/ai_office/engine/classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — Phase144 private opt-in
4. `tests/test_classified_persisted_outcome_progression_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — Phase144 focused +2
5. `tests/test_phase177_phase172_accumulated_request_id_none_persistence_classification_progression_compatibility.py` — real-C を exact Issue #377 C に更新（+0）
6. `README.md` — 本節
7. `docs/architecture.md` — 同一の architecture/ownership 明確化

8番目のファイルが必要になった場合は STOP して報告する。

## Phase 178: Post-Runtime → Persisted Running Execution → Progression Orchestration Boundary

Phase 178 は、**公開 Phase 177 の結果（post-runtime persisted running execution）を、そのまま公開 Phase 172 の post-runtime progression boundary に合成する、Phase 177 に続く次の integration boundary** です。compatibility repair ではなく、既存の公開境界（Phase 177 と Phase 172）を直列接続し、それぞれを**ちょうど 1 回ずつ**呼びます。**workflow runner ではありません**。

```text
finished current-step Phase-155 runtime result (StepRuntimeExecutionSuccess / Failure, または stop)
    ↓ Phase 177 post-runtime → persisted running execution（Phase 176 → capture Phase 147 → Phase 155）
    ↓ StepRuntimeExecutionSuccess / Failure / exact stop object（workflow_complete / persisted_failure）
    ↓ Phase 172 post-runtime → persistence → classification → progression（Phase 161 → Phase 143 → Phase 144）
    ↓ WorkflowProgressionDecision（prepare_next_step / workflow_complete）または PersistedExecutionOutcome（persisted_failure）
```

### 核心契約（stage ownership / thin durable proof / no outer rollback）

- **Phase 177 は durable running-state commit point を所有する**: Phase 178 は Phase 177 stage の失敗・不正戻り値に対して pre-Phase177 への巻き戻しを行わない（post-Phase177 running snapshot を消さない）
- **Phase 177 成功後の target bytes を post-Phase177 committed running snapshot とする**
- **stop 入力（exact `workflow_complete` / `persisted_failure`）は Phase 172 を zero calls で素通り**: Phase 177 が exact identity で返した stop object をそのまま返し、target bytes が不変であることを確認するだけ（変更検知時は `phase177_contract` で fail、restore はしない）
- **runtime ルートは Phase 177 出力を thin 検証した後に Phase 172 へ委譲**: Phase 177 出力は post-Phase177 running snapshot（status `running`・current step/index/employee が一致）と整合する exact `StepRuntimeExecutionSuccess` / `Failure` であること、committed history は `step_index - 1` 件の workflow-linked succeeded events であることを確認。Phase 177 の public runtime-result validator semantics（`is_valid_step_runtime_execution_result`）が authoritative
- **Phase 172 呼び出しは 4 positional のみ**（`phase172_function(value, workflow, state_path, events_path)`。keyword-only はデフォルトに委譲）: `value` は Phase 177 の出力（実行済み step の runtime result）であり、入力 `result` ではない
- **Phase 172 は durable terminal commit point を所有する**: Phase 178 は Phase 172 stage の失敗・不正戻り値に対して restore を行わない
- **thin durable target proof**: Phase 172 実行後、post-Phase177 running event bytes が prefix として byte-for-byte 保存され、ちょうど 1 件の terminal event（`serialize_runtime_step_event_jsonl` と一致）だけが追加され、final state と terminal event が Phase 177 出力の step / workflow に正確にリンクしていることを確認（Phase 161 / 143 / 144 を full reimplement しない）
- **progression proof**: success 非最終 → exact `prepare_next_step`（reason `next_step_available`・next は `workflow.steps[step_index]`）、success 最終 → exact `workflow_complete`（next 3 fields None・reason `last_step_succeeded`）、failure → exact `persisted_failure`（`failure_category == invocation.category`）
- 各 stage ちょうど 1 回、retry・loop・bypass・自動継続なし。返された decision / outcome を超える finalize はしない

### エラー分類（9 分類）

`result_type` / `workflow_definition` / `state_target` / `event_target` / `target_conflict` / `configuration` / `phase177_contract` / `phase172_contract` / `dependency_error`

- Phase 177 stage の既存 safe error（Phase 176 / 175 / 173 / 172 / 145 / 146 / 138 / 147 / 139 / 155 / 141 error type + Phase 177 CompatibilityError）は同一 object を identity で re-raise（Phase 172 呼び出し 0 回・write なし）
- Phase 172 stage の既存 safe error（Phase 161 / 143 / 144 error type + Phase 172 CompatibilityError）は同一 object を identity で re-raise（no outer rollback）
- 予期しない例外は `dependency_error` に sanitize（detail-safe 固定メッセージ）、不正戻り値・不正 durable target は `phase177_contract` / `phase172_contract`
- stop 入力の narrowing: `prepare_next_step` decision / `persisted_success` outcome は `result_type` で reject（stop 入力は exact `workflow_complete` / `persisted_failure` のみ）

### 変更ファイル（正確に5ファイル）

1. `src/ai_office/engine/runtime_result_to_persisted_running_execution_progression_orchestration_boundary.py` — Phase 178 production（新規）
2. `tests/test_runtime_result_to_persisted_running_execution_progression_orchestration_boundary.py` — 新規 +20 tests（focused 16 + real-default 4）
3. `src/ai_office/engine/__init__.py` — Phase 178 public exports
4. `README.md` — 本節
5. `docs/architecture.md` — Phase 178 architecture documentation

### 非機能範囲（State explicitly）

Phase 178 は以下の behavior を**一切**追加・変更しない:

- Phase 177 / 176 / 175 / 173 / 172 / 161 / 143 / 144 / 155 / 147 / 139 / 141 / 138 / 146 / 145 の production を変更しない（public function + error class のみ import）
- Phase 161 / 143 / 144 を full reimplement しない。provider-response parser を追加しない
- 新しい runtime result の再永続化・2 回目の progression decision・別ステップの preparation / start / persistence cycle を行わない
- retry・自動ループ / 継続・finalize・schedule・parallel・artifact persistence を行わない
- CLI / GUI behavior・credentials・provider / network / paid API 呼び出しは行わない（synthetic transport のみ）

## Phase 179 prerequisite（Issue #386）: Preserve Accumulated Aged None Request-ID through Approved-Preparation Entry Layers

Issue #386 は、Issue #380 / #383 で保存した accumulated aged-None request-ID の証明を、**approved-preparation エントリ層**（Phase 145 outer-chain / Phase 137 outer）まで延長する Phase 179 前提修復です。実 Phase 177 → 実 Phase 172 が生成する step-8 の `prepare_next_step` decision は、step-5 / step-6 に `request_id=None`（provider=`"openai"`）の predecessor 履歴を伴います。従来この証明は **approved-preparation エントリ層** で非 immediate predecessor の None request-ID が `terminal_contract` で reject され、次の phase へ渡せませんでした。

### 正確な最終ルール

- **prepare route 限定**: 新フラグ `allow_accumulated_openai_none`（デフォルト False）は **prepare route のみ** 有効（`allow_accumulated_openai_none=not stop`）。`workflow_complete` / `persisted_failure` の stop route では `not stop == False` のため aged None を新規に許容しない（immediate-None-only semantics 維持）
- **accumulated rule**: `allow_accumulated_openai_none and event.request_id is None and position >= 5 and _exact_string(event.provider, "openai") and state.current_step_index >= 7` のときのみ None request-ID を許容
  - provider が正確に `"openai"` であること（non-openai provider の None は従来どおり reject）
  - position >= 5（位置4以前の None は依然 reject）
  - `current_step_index >= 7`
- **immediate None は従来どおり**: 直前1件（`position == len(prior_steps)`・index >= 6）の None 許容は維持
- **対象は 2 production のみ**: Phase 145（outer-chain）と Phase 137（outer）。Phase 130 以下・Phase 161 / 143 / 144 / 136 etc. は変更しない

### 対象2境界（production 修正）

1. `progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py` — Phase 145
2. `progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — Phase 137

### テスト

- **focused テスト 各2件 × 2ファイル（計+4）**: accumulated openai None（position 5+6・provider openai）prepare route で委譲1回 + 狭さ（non-openai None reject・position 4 Nothing reject）
- **新規実回帰テスト**: `tests/test_phase178_phase145_accumulated_request_id_none_approved_preparation_compatibility.py`（+4件: A/B/C/D。A: real Phase 178 public boundary を synthetic successful transport で実行し step5/6 request_id=None + provider=openai の accumulated 状態から exact prepare_next_step(step8) を生成、その exact result を real Phase 145 デフォルト chain（real Phase 137 → Phase 130/lower）に渡し exact PreparedWorkflowStep(step8) を得る・transport はた度1回・step8 start/persist/execute ゼロ・terminal bytes unchanged。B: non-contiguous accumulated 対照（position5 None/openai・position6 非空 request_id）で real Phase 145 チェーンが step-8 まで成功。C: inline strict prepare 否定的（position4 None reject・position5 None + non-openai reject・target bytes unchanged）。D: stop route の exact identity + bytes unchanged 維持（canonical workflow_complete / persisted_failure）+ aged-None stop reject（stop 意味論を拡張しない））

### collect 不変条件

base **11,956**（Phase 178 完了時）→ focused +4 + 実回帰 +4 → **11,964**

### 変更ファイル（正確に7ファイル）

1. `src/ai_office/engine/progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py` — Phase 145 production
2. `src/ai_office/engine/progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — Phase 137 production
3. `tests/test_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py` — Phase 145 focused +2
4. `tests/test_progression_to_approved_preparation_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — Phase 137 focused +2
5. `tests/test_phase178_phase145_accumulated_request_id_none_approved_preparation_compatibility.py` — 新規実回帰 +4
6. `README.md` — 本節
7. `docs/architecture.md` — 本 prerequisite のアーキテクチャ文書

### 非機能範囲（State explicitly）

- **Phase 179 本体は実装しない**（本変更は prerequisite のみ）
- 8番目の production 変更・3境界目以降の production 修正・`__init__.py` export 追加は行わない
- stop route の aged None 許容・position 4 以前の None 許容・non-openai の None 許容は行わない
- 既存テストの削除・rename・skip・xfail・parameter-collapse・弱体化は行わない
- provider / network / paid API 呼び出しは行わない（synthetic transport のみ）

## Phase 179: Post-Runtime → Persisted Running Execution → Approved-Preparation Orchestration Boundary

Phase 179 は、**Phase 178 の結果（persisted running execution → progression）を、そのまま公開 Phase 145 の approved-preparation 境界（prepare-next-step 経路のみ）に合成する、Phase 178 に続く integration boundary** です。compatibility repair ではなく、既存の公開境界（Phase 178 と Phase 145）を直列接続します。**workflow runner ではありません**。

```text
finished current-step Phase-178 runtime result (StepRuntimeExecutionSuccess / Failure, または stop)
    ↓ Phase 178 post-runtime → persisted running execution → progression（Phase 177 → Phase 172）
    ↓ WorkflowProgressionDecision（prepare_next_step / workflow_complete）または PersistedExecutionOutcome（persisted_failure）
    ↓ （prepare_next_step のときのみ）Phase 145 approved-preparation（第2の next_preparation_approval / next_employee を使用）
    ↓ PreparedWorkflowStep（次の step の準備済み）または exact stop object
```

### 核心契約（追跡可能性 / 停止ゼロ呼び出し / 第2ペア分離 / no readvance）

- **Phase 178 を 10 positional でちょうど 1 回呼ぶ**（keyword-only はデフォルトへ委譲）: Phase 178 は自身の preparation / execution 入力について **authoritative**。Phase 179 は Phase 178 入力・第2ペアを **prevalidation しない**
- **Phase 145 は prepare_next_step のときだけ呼ぶ（6 positional）**: 第2の `next_preparation_approval` / `next_employee`（第1ペアとは別物）をそのまま渡す
- **stop ルート（original または runtime 経由の `workflow_complete` / `persisted_failure`）は Phase 145 zero-call**: exact identity で返し、target bytes 不変を確認するだけ
- **Phase 178 出力の thin validation**: 型が decision / outcome であること、`result` / `workflow` / 永続 snapshot と整合すること（不整合は `phase178_contract`。Phase 179 自身は追加 write / pre-Phase178 rollback をしない）
- **committed snapshot は post-Phase178 bytes**: 補償は pre-Phase178 へ巻き戻さない。Phase 145 が成功 target を不正変更した場合は **committed bytes のみ**へ restore
- **Phase 145 safe error は identity re-raise**: 予期しない例外は `dependency_error` に sanitize、restore 失敗は `rollback_failure`、Phase 145 不正戻り値は `phase145_contract`
- **no readvance**: Phase 145 は step を実行・永続化しない。返された `PreparedWorkflowStep` を超える finalize はしない。state / events は post-Phase178 committed のまま

### エラー分類（11 分類）

`result_type` / `workflow_definition` / `state_target` / `event_target` / `target_conflict` / `configuration` / `phase178_contract` / `phase145_contract` / `dependency_error` / `committed_mutation` / `rollback_failure`

- Phase 178 stage の既存 safe error（`_SAFE_PHASE178_ERRORS`）は同一 object を identity で re-raise（Phase 145 呼び出し 0 回・Phase 179 自身は追加 write / pre-Phase178 rollback なし）
- Phase 145 stage の safe error（Phase 145・Phase 137 CompatibilityError 等）は identity で re-raise（committed へ restore。Phase 179 自身は追加 write をしない）
- 予期しない例外は `dependency_error`、Phase 145 不正戻り値は `phase145_contract`、restore 不能は `rollback_failure`、committed 不変違反は `committed_mutation`
- stop 入力の narrowing: `prepare_next_step` decision / `persisted_success` outcome は `result_type` で reject（stop は exact `workflow_complete` / `persisted_failure` のみ）

### 変更ファイル（正確に5ファイル）

1. `src/ai_office/engine/runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary.py` — Phase 179 production（新規）
2. `tests/test_runtime_result_to_persisted_running_execution_progression_approved_preparation_orchestration_boundary.py` — 新規 +20 focused tests
3. `src/ai_office/engine/__init__.py` — Phase 179 public exports
4. `README.md` — 本節
5. `docs/architecture.md` — Phase 179 architecture documentation

### 非機能範囲（State explicitly）

Phase 179 は以下の behavior を**一切**追加・変更しない:

- Phase 178 / 177 / 176 / 175 / 173 / 172 / 161 / 143 / 144 / 155 / 147 / 137 / 130 / 145 の production を変更しない（public function + error class のみ import）
- 新しい runtime result の再永続化・2 回目の progression decision・別ステップの start / persist / execute cycle を行わない
- retry・自動ループ / 継続・finalize・schedule・parallel・artifact persistence を行わない
- CLI / GUI behavior・credentials・provider / network / paid API 呼び出しは行わない（synthetic transport のみ）

### collect 不変条件

base **11,964**（Issue #386 完了時）→ focused +20 → **11,984**

## Phase 180 prerequisite（Issue #390）: Preserve Accumulated Aged None Request-ID through Prepared-Step-Start Entry Layers

Issue #390 は、Issue #386 で approved-preparation エントリ層（Phase 145 / Phase 137）まで延長した accumulated aged-None request-ID の証明を、**prepared-step-start エントリ層**（Phase 146 outer-chain / Phase 138 outer）まで延長する Phase 180 前提修復です。実 Phase 179 が生成する `PreparedWorkflowStep`（次の step の準備済み）は、straight の persisted snapshot に step-5 / step-6 の `request_id=None`（provider=`"openai"`）の aged predecessor 履歴を伴います。従来この証明は **prepared-step-start エントリ層** で非 immediate predecessor の None request-ID が `terminal_contract` で reject され、prepared-step start へ roll できませんでした。

### 正確な最終ルール

- **prepare route 限定**: 新フラグ `allow_accumulated_openai_none`（デフォルト False）は **prepare route のみ** 有効（`allow_accumulated_openai_none=True` を prepared-step-start の prepare 分岐に適用）。`workflow_complete` / `persisted_failure` の stop route では本フラグを渡さない（False のまま）ため aged None を新規に許容しない（immediate-None-only semantics 維持）
- **accumulated rule**: `allow_accumulated_openai_none and event.request_id is None and position >= 5 and _exact_string(event.provider, "openai") and state.current_step_index >= 7` のときのみ None request-ID を許容
  - provider が正確に `"openai"` であること（non-openai provider の None は従来どおり reject）
  - position >= 5（位置4以前の None は依然 reject）
  - `current_step_index >= 7`
- **immediate None は従来どおり**: 直前1件（`allow_missing_immediate_request_id`・index >= 6）の None 許容は維持
- **対象は 2 production のみ**: Phase 146（outer-chain）と Phase 138（outer）。Phase 131 以下・Phase 179・共有 contract は変更しない

### 対象2境界（production 修正）

1. `prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py` — Phase 146
2. `prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — Phase 138

### テスト

- **新規実回帰テスト**: `tests/test_phase179_prepared_step_start_accumulated_request_id_none_compatibility.py`（+8）。実 Phase 179 で canonical step-8 `PreparedWorkflowStep` を生成し、実 Phase 146 / 実 Phase 138 → 不変の Phase 131 以下へ通し、exact `PreparedStepExecutionStart`(step 8) を返す（post-Phase179 committed bytes 不変・step 8 persistence/execution 0・synthetic transport の step 7 execution exactly once）。accumulated aged-None（step-5 / step-6・provider openai・`current_step_index >= 7`）を prepare route で受容。non-contiguous（position 5 のみ None）も受容。strict 否定的（position 4 None / position 5 non-openai / `request_id=""` / wrong-type / current step 6）を保持。stop route の exact identity + bytes 不変維持、および aged-None stop reject（stop 意味論を拡張しない）

### collect 不変条件

base **11,984**（Phase 179 完了時）→ 実回帰 +8 → **11,992**

### 変更ファイル（正確に5ファイル）

1. `src/ai_office/engine/prepared_step_start_cycle_handoff_chain_bridge_outer_chain_reentry_continuation_boundary.py` — Phase 146 production
2. `src/ai_office/engine/prepared_step_start_cycle_handoff_chain_bridge_outer_reentry_continuation_boundary.py` — Phase 138 production
3. `tests/test_phase179_prepared_step_start_accumulated_request_id_none_compatibility.py` — 新規実回帰 +8
4. `README.md` — 本節
5. `docs/architecture.md` — Phase 180 prerequisite architecture documentation

5ファイルを超える変更・3境界目以降の production 修正が必要になった場合は STOP して報告する。

## Phase 180: Post-Runtime → Persisted Running Execution → Prepared-Step-Start Orchestration Boundary

Phase 180 は、**公開 Phase 179 と公開 Phase 146 を直列接続する integration boundary** です。既存の公開 Phase 179（post-runtime → persisted running execution → progression → approved-preparation）をそのまま呼び、その返した exact `PreparedWorkflowStep` を公開 Phase 146（prepared-step-start）に渡して exact `PreparedStepExecutionStart` を 1 つ得ます。**Phase 175 の substitute ではなく**、Phase 179 の resultado を起点に 1 段だけ prepared-step start へ延長します。

```text
original runtime result / exact stop input
    ↓ Phase 179（12 positional、ちょうど1回）
    → PreparedWorkflowStep（次の step の準備済み）
        ↓ Phase 146（5 positional、ちょうど1回）… next_employee を渡す
        ↓ PreparedStepExecutionStart（proposed immutable value のみ）
    → workflow_complete
        ↓ exact stop return（Phase 146 zero-call）
    → persisted_failure
        ↓ exact stop return（Phase 146 zero-call）
```

### 核心契約

- **Phase 179 を 12 positional・kwargs なしでちょうど 1 回呼ぶ**: 最初の 12 引数は canonical Phase179 call shape。operational/approval/employee 入力（`next_employee` 含む）は **prevalidation しない**（Phase 179 が authoritative）
- **Phase 146 は exact `PreparedWorkflowStep` のときだけ 5 positional でちょうど 1 回呼ぶ**: `(prepared, workflow, next_employee, state_path, events_path)`。**同じ `next_employee`** を渡す。第1の `employee` を silent reuse しない、第3の employee を導入しない
- **stop ルート（`workflow_complete` / `persisted_failure`）は Phase 146 zero-call**: exact Phase 179 stop object を identity で返し、target bytes 不変を確認するだけ
- **employee 所有権の分離**: `employee` は Phase178/177 が prepare/persist/execute した step（例 step7）に属し、`next_employee` は Phase179/145 が prepare した次の step（例 step8）に属する。Phase 180 は **`next_employee`** を Phase 146 に渡す
- **Phase 179 stage は追加 write / pre-Phase179 rollback をしない**: 認識済み safe Phase179 error は同一 object を identity で re-raise（Phase 146 zero-call・rollback なし）。予期しない例外は `dependency_error` に sanitize
- **committed snapshot は post-Phase179 bytes**: Phase 146 の mutation / 安全エラー / 不正戻り値の補償は **post-Phase179 committed bytes のみ**へ restore。pre-Phase179 へ巻き戻さない
- **戻り値は proposed のみ**: Phase 180 は返された `PreparedStepExecutionStart` の running state を**永続化せず**、prepared step を**実行しない**。retry / loop / 自動継続 / finalize / schedule / parallel を一切行わない
- **Issue #390 の accumulated aged-None 互換を再利用**: Phase 146 / Phase 138 の stop-route 意味論を拡張しない（stop は `next_employee` 検証を行わない）

### エラー分類（11 分類）

`result_type` / `workflow_definition` / `state_target` / `event_target` / `target_conflict` / `configuration` / `phase179_contract` / `phase146_contract` / `dependency_error` / `committed_mutation` / `rollback_failure`

### 変更ファイル（正確に5ファイル）

1. `src/ai_office/engine/runtime_result_to_persisted_running_execution_progression_prepared_step_start_orchestration_boundary.py` — Phase 180 production（新規）
2. `tests/test_runtime_result_to_persisted_running_execution_progression_prepared_step_start_orchestration_boundary.py` — 新規 +20 focused tests
3. `src/ai_office/engine/__init__.py` — Phase 180 public exports
4. `README.md` — 本節
5. `docs/architecture.md` — Phase 180 architecture documentation

### collect 不変条件

base **11,992**（Issue #390 完了時）→ focused +20 → **12,012**

## Issue #394: Phase 181 prerequisite compatibility repair

Issue #394 is a narrow prerequisite repair, not the Phase 181 implementation. The
real Phase 180 path can age a previously valid built-in OpenAI
`request_id=None` predecessor behind later successful steps. The prepared-start
persistence routes of Phase 147 and Phase 139 now preserve the bounded rule
already established by the prior entry layers:

- `request_id is None` is additionally accepted only when `position >= 5`,
  `provider == "openai"` as an exact built-in string, and persisted
  `current_step_index >= 7`.
- Positions 1–4, non-OpenAI providers, empty strings, and wrong request-ID types
  remain strict. Existing immediate-predecessor `None` compatibility remains.
- The expansion is limited to the PreparedStepExecutionStart route. Stop routes
  remain unchanged and read-only.
- Phase 132 and lower persistence, the shared terminal-history contract, and
  Phase 181 itself are unchanged. No provider/tool execution, retry, automatic
  continuation, or step execution is added; validation uses synthetic transport
  only.


## Phase 181: Post-Runtime → Persisted Running Execution → Prepared-Start Persistence Orchestration Boundary

Phase 181 composes public Phase 180 → public Phase 147 only. Only the exact
`PreparedStepExecutionStart` returned by Phase 180 enters Phase 147; exact
`workflow_complete` and `persisted_failure` stop objects return directly with
Phase 147 zero-call. The Phase-180-produced start belongs to `next_employee`,
which is passed unchanged to Phase 147. Phase 180's prior durable work is never
rolled back. Phase 147 success establishes the new durable running-state commit,
and Phase 147 failure compensation restores only the post-Phase-180 committed
predecessor snapshot. Phase 181 stops after the exact
`RunningStatePersistenceResult`; it does not execute the persisted step and adds
no retry, automatic continuation, finalize, schedule, loop, parallel, CLI, or
GUI behavior.

## Phase 182: Post-Runtime → Persisted Running Execution → One Runtime Result

Phase 182 composes public Phase 181 with public Phase 155. It captures the
exact `PreparedStepExecutionStart` transparently from Phase 181's real public
Phase-147 handoff and never reconstructs a replacement start. Phase 181's
first execution context remains distinct from the explicit second
`next_resolved_tools` / `next_api_key` / `next_execution_approval` /
`next_transport` context used by Phase 155.

Phase 181's durable running-state commit is never rolled back to a
pre-Phase-181 snapshot. Phase 155 is read-only relative to that committed
running snapshot; its failures or target mutations compensate only to the
post-Phase-181 committed bytes. `workflow_complete` and `persisted_failure`
stop outputs bypass Phase 155. For a prepared route, Phase 182 returns the
exact one-step runtime result and stops: it does not persist or progress that
result, retry, loop, automatically continue, finalize, schedule, parallelize,
or add CLI/GUI behavior.

## Phase 183: Post-Runtime → Persisted Running Execution → Progression

Phase 183 composes the public Phase 182 one-step runtime boundary with the
public Phase 172 runtime-result persistence/classification/progression boundary:

```text
original runtime result / exact stop input
    ↓ Phase 182 (one persisted continuation step, or exact stop)
StepRuntimeExecutionSuccess / StepRuntimeExecutionFailure
    ↓ Phase 172 (persist → classify → progress)
WorkflowProgressionDecision / PersistedExecutionOutcome
    ↓ STOP
```

The Phase-182 call uses its canonical sixteen positional arguments exactly once.
Only an exact Phase-182 runtime result enters Phase 172, using exactly four
positional arguments exactly once. An exact `workflow_complete` or
`persisted_failure` returned by Phase 182 is returned by identity; Phase 172 is
not called. Phase 183 does not add rollback or write compensation around either
owned boundary: Phase 182's running snapshot and Phase 172's terminal effects
remain owned by those phases.

For the canonical eight-step path, step 7 and step 8 execute once through Phase
182, then Phase 172 persists step 8 and returns `workflow_complete` or
`persisted_failure`. For a nine-step workflow, Phase 172 returns
`prepare_next_step(step 9)` and Phase 183 stops without preparing, starting,
persisting, or executing step 9. There is no retry, loop, automatic
continuation, second progression, finalize, schedule, parallel execution,
provider/network call, CLI, or GUI behavior.

The boundary validates exact result/stop types, workflow and step linkage,
post-Phase-182 running history, and the byte-preserving Phase-172 terminal
append. Stop validation requires a final-step `workflow_complete` and canonical
workflow-linked events; malformed state/event evidence is classified as
`phase182_contract` or `phase172_contract`. Its local safe error family exposes
only the fixed classifications `result_type`, `workflow_definition`,
`state_target`, `event_target`, `target_conflict`, `configuration`,
`phase182_contract`, `phase172_contract`, and `dependency_error`.

Requirement coverage is kept in the focused 20-test module: public API and
source audit; exact call shapes and identity; strict input and safe-error
handling; stop zero-call routes; durable snapshot/event linkage; malformed
output rejection; canonical eight-step success/failure; and nine-step
prepare-decision stop behavior. Synthetic transports are used exclusively.

## Phase 184: Post-Runtime → Persisted Running Execution → Approved Preparation

Phase 184 composes the public Phase 183 boundary with the existing public
Phase 145 approved-preparation boundary. It preserves Phase 183's canonical
sixteen positional inputs and adds only the distinct
`following_preparation_approval` / `following_employee` pair for the step
identified by Phase 183 as following:

```text
runtime result / exact stop input
    ↓ Phase 183
prepare_next_step(step 9) ──→ Phase 145(following approval, following employee)
                                  ↓
                              PreparedWorkflowStep(step 9) → STOP
workflow_complete / persisted_failure ──→ exact identity stop
```

Phase 145 is called exactly once only for an exact `prepare_next_step`
decision. Phase 183 `workflow_complete` and `persisted_failure` outputs return
directly with zero Phase 145 calls, and the following pair is not validated on
those routes. The first step-7 context, the second step-8 `next_*` context,
and the new step-9 following approval/employee context have distinct
ownership; Phase 145 receives the following pair only.

The bounded accumulated provenance compatibility established by Issue #386 is
reused rather than reimplemented. Phase 183 owns its durable writes. If Phase
145 mutates or fails after that commit, Phase 184 compensates only to the
exact post-Phase-183 state/event snapshot. It stops at the exact
`PreparedWorkflowStep(step 9)` and does not start, persist, or execute step 9.
No retry, loop, automatic continuation, finalization, scheduling, parallelism,
provider/network call, CLI, or GUI behavior is added.

## Phase 185: Post-Runtime → Persisted Running Execution → Prepared-Step-Start

Phase 185 composes only public Phase 184 followed by public Phase 146. The
exact `PreparedWorkflowStep(step 9)` from Phase 184 is the only value that
enters Phase 146, together with the same `following_employee` that owns step 9.
`workflow_complete` and `persisted_failure` are exact identity stop routes and
make zero Phase 146 calls. The accumulated provenance compatibility for aged
OpenAI `request_id=None` values established by Issue #390 is reused through
Phase 146/138; it is not reimplemented here.

After Phase 184 commits, Phase 185 compensates a Phase 146 mutation or failure
only to the post-Phase-184 state/event snapshot. It returns the proposed exact
`PreparedStepExecutionStart(step 9)` and stops: step 9 running state is not
persisted, step 9 is not executed, and no retry, loop, or automatic continuation
is added.

## Phase 186: Post-Runtime → Persisted Running Execution → Prepared-Start Persistence

Phase 186 composes only public Phase 185 followed by public Phase 147. Exact
`workflow_complete` and `persisted_failure` outputs from Phase 185 are returned
by identity with zero Phase 147 calls. Only an exact
`PreparedStepExecutionStart(step 9)` enters Phase 147, with the unchanged
`following_employee` that owns step 9. A successful Phase 147 call returns the
exact `RunningStatePersistenceResult` after persisting step 9 as `running`, with
steps 1–8 completed and no step 9 event.

Phase 185 owns all prior durable work. Phase 186 never prevalidates its
operational inputs or restores its pre-call bytes. Once Phase 185 returns a
valid prepared start, Phase 147 failures or invalid mutations compensate only
to the post-Phase-185 snapshot. Phase 186 stops there: it does not execute step
9 and adds no retry, loop, automatic continuation, finalization, scheduling,
parallelism, provider/network/paid API, CLI, or GUI behavior.

## Phase 187: Post-Runtime → Persisted Running Execution → Step-9 Runtime Result

Phase 187 composes public Phase 186 with a capture-only public Phase 147
adapter and then public Phase 155. Phase 186 receives its exact eighteen
positional arguments and only the `phase147_function` keyword. The adapter
preserves the exact `PreparedStepExecutionStart(step 9)` identity and calls
Phase 147 in its canonical five-argument form. Terminal outputs are returned
unchanged with zero capture and Phase 155 calls. A valid running persistence
result invokes Phase 155 exactly once with only the step-9 following context and
then stops at the exact step-9 runtime success or failure. The step-9 result is
not persisted, classified, progressed, retried, or automatically continued.

## Phase 188: Post-Runtime → Step-9 Persistence and Progression

Phase 188 composes the public Phase 187 step-9 runtime boundary with the
public Phase 172 runtime-result persistence/classification/progression boundary:

```text
initial runtime result / exact stop
        ↓
      Phase 187 (22 positional arguments, exactly once)
        ├─ workflow_complete / persisted_failure → exact identity stop
        └─ exact StepRuntimeExecutionSuccess/Failure(step 9)
                              ↓
                    Phase 172 (4 positional arguments, exactly once)
                              ↓
      workflow_complete / persisted_failure / prepare_next_step(step 10)
                              ↓ STOP
```

The first 22 arguments retain Phase 187's canonical positional order. Phase 187
is called once without keyword arguments. Only an exact step-9 runtime result
enters Phase 172, which is called once as `(result, workflow, state_path,
events_path)` without keyword arguments. The exact Phase-172 progression object
is returned unchanged. Original and Phase-187-generated terminal stops bypass
Phase 172 and are returned by identity; Phase 187-owned durable bytes are not
rolled back.

Phase 188 does not reimplement persistence, classification, or progression and
does not call Phase 161, 143, 144, or 145 directly. Phase 172 remains the sole
owner of step-9 durable persistence and its bounded provenance rules: aged
OpenAI `request_id=None` and older non-OpenAI events with a non-empty request ID
remain valid, while early or non-OpenAI `None` values remain rejected. The
boundary stops at Phase 172's result: a ten-step workflow may return
`prepare_next_step(step 10)`, but step 10 is never prepared, started, persisted,
or executed.

The Phase-188 focused module contains exactly 20 tests covering the public API,
exact call shapes and identities, stop zero-call behavior, real nine-/ten-step
success and failure, provenance compatibility, safe and unexpected errors,
malformed outputs, durable snapshots, and no-readvance/source audits. Synthetic
transports are used exclusively. No retry, loop, automatic continuation,
finalization, scheduling, parallelism, provider/network/paid API, CLI, or GUI
behavior is added.

## Phase 190: One-Step Approved Workflow Continuation Cycle

Phase 190 provides the reusable public
`route_approved_workflow_continuation_cycle` boundary. One call consumes one
current `prepare_next_step` decision and one next-step context, then executes
the exact stage order below at most once:

```text
WorkflowProgressionDecision(prepare_next_step)
  → Phase 145 approved preparation
  → Phase 146 prepared-step start
  → Phase 147 running-state persistence
  → Phase 155 one runtime execution
  → Phase 172 runtime-result persistence/classification/progression
  → exact next decision or persisted_failure → STOP
```

`workflow_complete` and `persisted_failure` inputs are exact identity terminal
stops: all five stages receive zero calls and operational context is ignored.
The preparation approval, employee, tools, credential, execution approval, and
transport belong only to this one next step; a later `prepare_next_step` must
be continued by a new explicit Phase-190 call.

Phase 145 and 146 failures or malformed/mutating results compensate to the
prior terminal snapshot. Phase 147 failures compensate to that same prior
snapshot; after a valid Phase-147 running commit, Phase 155 failures or
malformed/mutating results compensate only to the committed running snapshot.
Phase 172 owns its runtime-result durable commit, so Phase 190 performs no
destructive outer rollback after invoking it. No stage is retried, looped,
recursively or automatically continued; scheduling, parallelism, finalization,
provider/network/paid API, CLI, and GUI behavior remain outside this boundary.

The focused Phase-190 regression module contains exactly 20 tests, and uses
synthetic transports exclusively.

## Phase 192: Bounded Explicit-Context Workflow Runner

Phase 192 exposes `route_bounded_approved_workflow_continuation`, a bounded
runner over the public Phase-190 cycle. The caller supplies an exact built-in
tuple of frozen `ApprovedWorkflowContinuationContext` values; each tuple
element contains only the six values needed by one Phase-190 call:
preparation approval, employee, resolved tools, API key, execution approval,
and transport. The runner performs no lookup, approval generation, transport
creation, retry, recursion, or unbounded continuation.

For an exact `prepare_next_step` input, one tuple element permits exactly one
canonical ten-positional-argument Phase-190 call. Results are validated at the
thin outer seam, terminal `workflow_complete` / `persisted_failure` results
are returned by identity, and later contexts are not consumed. If the tuple
is exhausted while the result is still `prepare_next_step`, that exact result
is returned by identity. An empty tuple therefore performs no call and leaves
durable targets untouched. Terminal inputs bypass all operational validation.

The runner does not snapshot, roll back, compensate, or write state/events;
Phase 190 and its lower public stages retain ownership of durable behavior and
their recognized safe errors. Runner-owned input and seam failures expose only
safe local classifications. The focused Phase-192 suite contains exactly 20
tests and uses deterministic synthetic transports only. Retry, recursion,
unbounded loops, parallel execution, scheduling, finalization,
provider/network/paid API, CLI, and GUI behavior remain outside this phase.

## Phase 208: Explicit Fresh Workflow Step-1 Bootstrap Boundary

Phase 208 exposes
`route_approved_workflow_fresh_start(workflow, state_path, events_path, context, *, running_persistence_function=persist_prepared_running_state, execution_function=execute_persisted_start_openai_step, phase172_function=route_runtime_result_to_progression_orchestration_boundary)`.
It is the explicit fresh-entry boundary for exactly one step-1 execution. Both
durable targets must be nonexistent, and the caller supplies the exact frozen
`ApprovedWorkflowBootstrapContext` containing the distinct
`InitialStepPreparationApproval`, step-1 employee, resolved tools, API key,
execution approval, and transport.

The boundary creates the canonical ready state and empty event log as a pair,
then strictly loads them back. Accepted ready initialization is the first
durable commit. The explicit step-1 approval is separate from the
predecessor-bound next-step approval used by Phase 190/192. Phase 208 then
constructs the existing `PreparedWorkflowStep` and
`PreparedStepExecutionStart` models directly, persists the running state once
as the second durable commit, and performs exactly one
`execute_persisted_start_openai_step` call. That execution owner does not
persist its runtime result.

The exact runtime result is handed once to Phase 172, which owns the terminal
state/event persistence, classification, and progression commit. Phase 208
returns the exact Phase-172 result and stops; it does not prepare or execute a
later step. Phase 192 remains a separate caller action for any bounded
continuation.

This boundary adds no retry, generated context, hidden approval or lookup,
scheduler, loop, automatic continuation, parallel execution, or provider/API
call of its own. It is not a full automatic workflow runner.

## Phase 210: Fresh Workflow Bounded Top-Level Runner

Phase 210 exposes
`route_approved_fresh_workflow_bounded(workflow, state_path, events_path, bootstrap_context, continuation_contexts, *, fresh_start_function=route_approved_workflow_fresh_start, bounded_continuation_function=route_bounded_approved_workflow_continuation)`.
It is the explicit top-level entry for a fresh Phase-208 step-1 start followed
by at most one Phase-192 bounded continuation handoff. The caller supplies the
frozen Phase-208 bootstrap context and a finite built-in tuple of exact frozen
`ApprovedWorkflowContinuationContext` values.

Before Phase 208, the boundary performs only shallow structure checks: the
continuation container must be an exact built-in `tuple`, every element must be
an exact `ApprovedWorkflowContinuationContext`, and both injected dependencies
must be callable. Approval, employee, tool, API-key, execution-approval, and
transport semantics remain owned by Phase 208 and Phase 192/190. No context,
approval, employee, tool, key, or transport is generated or looked up.

Phase 208 is called once with its canonical four positional arguments. A valid
terminal `workflow_complete` or `persisted_failure` is returned by exact object
identity and Phase 192 is not called. Only a valid `prepare_next_step` result
enters Phase 192, exactly once, with the canonical five positional arguments;
Phase 192 alone owns the finite supplied-context loop and Phase-190 calls. The
valid Phase-192 result is likewise returned by exact identity, and the boundary
stops at tuple exhaustion or a lower terminal result.

Phase 208 owns step-1 fresh-target initialization and ready/running/terminal
durable commits; Phase 192/190 own later durable transitions and recognized
safe errors. Phase 210 performs no state/event write, rollback, retry,
recursion, unbounded loop, automatic continuation, scheduler, finalizer,
parallel work, CLI/GUI behavior, or provider/network/paid API call. Persisted
resume/reentry remains a separate future contract.

## Phase 212: Persisted-Terminal Bounded Top-Level Resume

Phase 212 adds the explicit persisted-terminal operation
`route_persisted_terminal_workflow_bounded(workflow, state_path, events_path, continuation_contexts, *, classification_function=classify_persisted_execution_outcome_reentry, routing_function=route_persisted_execution_outcome_reentry, bounded_continuation_function=route_bounded_approved_workflow_continuation)`.
There are now two deliberately separate top-level operations:

```text
fresh bounded start
  → route_approved_fresh_workflow_bounded(...)

existing persisted-terminal target
  → route_persisted_terminal_workflow_bounded(...)
```

The persisted-terminal operation reads the authoritative target through Phase
37 exactly once, routes that exact outcome through Phase 38 exactly once, and
preserves the Phase-38-owned duplicate read-only Phase-37 reclassification.
A persisted failure is an identity-preserving terminal stop; final persisted
success is routed through Phase 31 and is also an identity-preserving terminal
stop. Only `prepare_next_step` validates the caller-supplied exact tuple of
`ApprovedWorkflowContinuationContext` values and calls the public Phase-192
bounded runner once.

Continuation configuration is deferred until it is relevant. Terminal failure
and final success do not validate or consume contexts and do not require a
bounded continuation dependency. Persisted `ready` and `running` states are
not automatically replayed: an in-progress persisted state does not prove
whether an external provider side effect completed before process death.
Phase 37/38/31 remain read-only owners; once Phase 192 begins, Phase 192/190
and lower stages own durable changes and Phase 212 performs no outer rollback.
There is no retry, generated input, loop, recursion, scheduler, finalizer,
CLI/GUI, or provider/network/paid API behavior in this boundary, and it never
automatically selects fresh versus persisted-terminal resume.

## 明示承認付きワークフロー実行（Phase 214）

`workflows start` と `workflows continue` は、定義と既存の公開実行境界を
組み合わせた、1 step 限定の operator-facing CLI です。実行前に必ず
preview を表示し、その出力を確認してから、同じ step を実行するための
値を明示的に返します。

```bash
# preview: 状態・イベント・API key・provider transportを変更しない
ai-office workflows start research-and-summarize \
  --state-path state.json --events-path events.json --preview-only

# execution: previewから次の4値をそのまま指定する
ai-office workflows start research-and-summarize \
  --state-path state.json --events-path events.json \
  --approve-preparation --approve-execution \
  --approved-by operator --approval-id approval-20260904-001 \
  --expected-step-id research --expected-step-index 1 \
  --expected-employee-id general-researcher \
  --expected-request-fingerprint <previewのrequest_fingerprint>

# step 1成功後は、同じpreview-first手順で次の1 stepだけcontinueする
ai-office workflows continue research-and-summarize \
  --state-path state.json --events-path events.json --preview-only
```

executionでは、`expected-step-id`、`expected-step-index`、
`expected-employee-id`、`expected-request-fingerprint` の4値が、再計算した
現在のpreviewと完全一致しなければなりません。欠落または不一致の場合は、
preparation approval、paid execution approval、`OPENAI_API_KEY` の読込、
provider transportのいずれも行わずに拒否します。approvalの実行者とIDも
callerが明示的に指定する必要があり、既定値や自動生成はありません。

1つのコマンドが実行できるprovider stepは最大1つです。自動継続、retry、
backoffはなく、成功後は必ず停止します。`OPENAI_API_KEY` は実際のexecution
routeで必要になった場合にだけ読み込まれ、previewでは不要です。最終結果の
CLI出力はstepの状態遷移など安全なmetadataに限定され、credentialやraw
provider payloadは表示しません。

永続化された `ready` / `running` state は、外部provider side effectの有無を
判定できないため、`continue` が自動再実行しません。これらは別途 recovery
または manual investigation が必要です。`continue` は persisted terminal
routeを再検証してから次の1 stepを実行するため、CLI preview後に保存状態が
変化した場合も stale execution を防ぎます。

## Phase 216: 直前step outputのrestart-safe handoff

成功した step の出力は、既存の `events.jsonl` に保存された
`RuntimeStepEvent.output_text` を唯一の authoritative source とします。
`workflows continue` と下位の実行境界は、再起動後も同じ履歴から直前の成功
step **1件だけ**を再構成し、provider非依存の `UpstreamStepOutput` として次の
requestへ渡します。

```text
step N-1 succeeded output
  → authoritative RuntimeStepEvent.output_text
  → restart-safe immediate-predecessor reconstruction
  → structured UpstreamStepOutput
  → exact task input preview
  → fingerprint / explicit approval
  → Phase190 authoritative pre-persistence revalidation
  → Phase147 running persistence
  → Phase155 provider execution (最大1回)
  → terminal persistence
  → STOP
```

upstream output は untrusted task/user data であり、employee の
`system_instructions` には決して注入しません。task input は deterministic
canonical JSON として明示的に preview に表示され、source provenance、元の
text、digest、request fingerprint が operator approval に結び付けられます。
CLI の preview/expected-value 検査とは別に、Phase190 は Phase147 の running
state 永続化前に最新の terminal history、upstream tuple、execution approval
fingerprint を再検証します。履歴が変化した場合は provider call や running
state 永続化を行わず、既存の state/events を保ちます。

Policy A（直前の成功出力のみ）を採用し、fan-in、DAG、workflow 定義への
dataflow schema、retry、conversation continuation、automatic continuation は
追加しません。fresh な step 1 の upstream は空 tuple のままで、Phase214 の
provider input と fingerprint の互換性を維持します。

## Phase 218: restart-safeなworkflow result inspection

永続化済みworkflowの現在のbusiness resultを、プロセス再起動後に
read-onlyで確認するには、次を使用します。

```bash
ai-office workflows result research-and-summarize \
  --state-path state.json \
  --events-path events.jsonl

# 定義ディレクトリを明示する場合
ai-office workflows result research-and-summarize \
  --state-path state.json \
  --events-path events.jsonl \
  --directory path/to/workflows \
  --employees-directory path/to/employees
```

`--state-path` と `--events-path` は必須で、定義ディレクトリの既定値は
`workflows` / `employees` です。このコマンドはworkflowとemployee定義を
検証して対象workflowを選択した後、Phase37 → Phase38のcanonicalな永続
terminal routeを読み取り、strictな`load_workflow_execution_history`で
履歴を再読込します。CLIはstep数からprogressionを推測せず、最後のterminal
eventをcurrent stateとPhase38のresult identityへ照合してから、1個の決定的な
JSON objectだけを出力します。

成功時の`output.output_text`は、最後のsuccessful stepに保存された値を
trim、normalize、要約、連結せず、そのまま返します。途中stepの成功では
current stepのoutputとPhase38が返した次stepの`id` / `index` /
`employee_id`を返し、workflow完了時は最終successful stepのoutputだけを
business resultとします。以前のstepのoutputは履歴上の監査データとして
残りますが、結果へ集約しません。空文字、空白、改行、Unicode、JSON-likeな
文字列、instruction-likeな文字列、長い文字列もuntrusted dataとして無加工で
扱います。成功outputには、そのsource provenance（workflow/step/index/
employee）とexact outputから作ったcanonical SHA-256 digestも含まれます。

終了コードは次のとおりです。

```text
0  persisted success（prepare_next_step または workflow_complete）
1  persisted_failure（JSONを出力し、output は null）
2  不正な定義・履歴・workflow identity、または recovery/investigationが必要
```

`ready` / `running` stateは、過去のeventから結果を推測したりproviderを
再実行したりせず、`Error: persisted workflow state requires recovery or
investigation`として終了コード2になります。破損・不一致のstate/eventも
修復せず、安全なエラーとして終了します。

result inspectionはstate/eventsを変更しません。API key、preparation/
execution approval、provider、retry、replay、automatic continuation、
recovery、repairを行わず、`OPENAI_API_KEY`も要求しません。provider、
request_id、response_id、failure event message、credential、approval、
raw provider payloadはresult JSONへ出力しません。

## Phase 271: publication-result の read-only projection inspection

Phase 270 が作成する再生成 publication projection を、provider-freeに確認
するには、readiness recordとresult recordのパスを明示して次を実行します。

```bash
ai-office workflows publication-result \
  --readiness-record-path path/to/readiness-record.json \
  --result-path path/to/result-record.json
```

両パスは必須で、既定artifact、workflow discovery、state/events lookup、
再生成、retry、fallback、adoption、export、publication side effectはありません。
コマンドは既存のPhase 270 `project_publication_regeneration_output`を一度だけ
呼び出し、その返却projectionを決定的なJSON一行へ変換します。`ready`では
保存されたbusiness textを改変せずに返し、終了コードは0です。

`insufficient_evidence`、`stale_or_inconsistent`、`result_failure`では、
business textとdigestを`null`にしたprojectionを返して終了コード1になります。
証拠が不正・破損・不一致の場合は通常のJSONを出力せず、安全な固定エラーを
stderrへ出して終了コード2になります。既存の`workflows result`は元のworkflow
execution lineageを読む契約のままで、再生成されたpublication outputへ切り替わり
ません。どのケースでもprovider、network、credential、clock、書込みは行いません。

## Phase 273: publication-export の provider-free CLI boundary

Phase 272のready projectionを、callerが指定した新しいファイルへ明示的に
exportするには、次を使用します。

```bash
ai-office workflows publication-export \
  --readiness-record-path path/to/readiness-record.json \
  --result-path path/to/result-record.json \
  --output-path path/to/export.txt
```

3つのパスはすべて必須で、既定値、workflow IDからの推測、directory discovery、
hidden export directoryはありません。CLIはパスをそのまま既存のPhase 272
`export_publication_regeneration_output(...)`へ一度だけ渡し、preflight、ready判定、
exact UTF-8 bytesのcreate-only書込み、flush、file/parent-directory fsyncはPhase 272
だけが行います。外部publication、provider/network、credential、再生成、retry、
fallback、automatic continuationは行いません。

成功時はreceiptの安全なidentityとbyte lengthだけを含むcompact JSONを1行出力し、
終了コード0です。output path、business text、provider failure、timestamp、credential、
receipt/manifestは出力・永続化しません。not-publishableは固定stderrと終了コード1、
永続化結果がambiguousな場合は専用の固定stderrと終了コード2、それ以外の拒否や
予期しない依存失敗は固定invalidエラーと終了コード2になります。既存の
`workflows publication-result`はread-only projection inspectionのまま変更されません。

## Phase 274: publication export receipt のcanonical durable evidence

Phase 272 がすでに返した正確な
`PublicationRegenerationExportReceipt` を、callerが明示したreceipt sidecarへ
provider-freeに永続化・strict reloadするengine boundaryを追加しています。
Phase 274はexportを再実行せず、Phase 270 projectionやPhase 267/268/269の
loaderを呼ばず、export済みbusiness outputの検査・reconciliationも行いません。
CLI、workflow state/events、既存lineage、provider/network、credential、clock、
retry、fallback、automatic continuationは追加・変更されません。

receiptは次の8キーだけを、`ensure_ascii=False`、compact separators、sorted keys、
`allow_nan=False`でcanonical JSON化します。

```text
schema_version
regeneration_id
projection_sha256
readiness_record_sha256
result_record_sha256
source_audit_sha256
business_output_sha256
output_byte_length
```

canonical JSONのUTF-8 bytesと、そのbytesのSHA-256 digestをin-memory helperで
取得できます。明示pathのsidecarはparentを作成せずexclusive createし、write、
flush、file fsync、close、parent-directory fsyncの全てが完了した後だけ成功します。
同一canonical bytesの既存targetだけはread-onlyに再fsyncするidempotent再保存を
許可し、異なるbytes、directory、symlink、short write、durability ambiguityは
既存artifactを変更・削除・修復せず固定detail-safe errorで拒否します。

strict loaderはUTF-8、JSON、duplicate key、非標準constant、完全な8-key set、
Phase 272 receiptのモデル検証、canonical byte-for-byte一致を順に確認します。
receipt sidecarはbusiness-output path/text、provider payload、credentials、
timestamp、randomness、metadataを保存せず、export fileが後から存在するか・一致
するかの証明は将来の明示reconciliation phaseに委譲します。

## Phase 275: publication export receipt と明示 output file の read-only reconciliation

Phase 274 が永続化した canonical receipt sidecar と、callerが明示した output
fileを比較するprovider-freeなengine boundaryを追加しています。
`reconcile_publication_regeneration_export(...)` はPhase 274の
`load_publication_regeneration_export_receipt(...)`を一度だけ使い、同じloaded
receiptを`publication_regeneration_export_receipt_digest(...)`へ渡してreceiptの
identityを固定します。receipt JSONを直接parseしたり、Phase 272 export、Phase 270
projection、Phase 267/268/269 loader、provider/runtime boundaryを呼び出したりは
しません。

output fileはcaller指定のexact `Path`としてread-onlyに一度だけ観測します。
directory、symlink、special file、permissionやその他のread failureは固定の
detail-safe errorです。存在しないfileは正常な`missing` result、読み取れた
bytesがreceiptのSHA-256とbyte lengthの両方を満たす場合だけ`matched`、それ以外は
観測したdigestとlengthを含む`content_mismatch`になります。bytesはdecode・改行変換・
正規化せず、結果はfrozenなin-memory modelだけで、reconciliation resultやsidecarを
永続化しません。repair、adopt、overwrite、retry、fallback、re-export、CLI変更、
workflow state/events等のlineage mutationはありません。

## Phase 276: publication export reconciliation のcanonical durable evidence

Phase 275がすでに導出した
`PublicationRegenerationExportReconciliation`を、再reconciliationせず、Phase 274
receipt sidecarやexport fileも再読込せずに、callerが明示したevidence pathへ
provider-freeに永続化・strict reloadするengine boundaryを追加しています。
`matched`、`missing`、`content_mismatch`のstatusと、expected/observed digest・
byte lengthを再解釈せずそのまま保存します。`missing`では両observed fieldをJSON
`null`として保存します。

canonical evidenceは8-keyのcompact JSONを`ensure_ascii=False`でUTF-8化し、その
exact bytesのSHA-256 digestを取得できます。parent directoryは作成せず、explicit
`Path`へのexclusive create、write、flush、file fsync、close、parent-directory
fsyncが成功した場合だけ完了します。同一canonical bytesの既存targetだけは
rewriteなしでfile/parent fsyncするidempotent再保存を許可し、異なるbytes、破損、
directory、symlink、special file、durability ambiguityは固定detail-safe errorで
拒否します。ambiguous failure後のartifactは保持し、retry、repair、overwrite、
adoption、rename、deleteは行いません。

strict loaderはduplicate key、非標準JSON constant、invalid UTF-8、missing/extra
key、非canonical JSON、Phase 275 modelの不正なstatus・digest・length・cross-field
組合せを拒否します。receipt/outputの再観測、CLI変更、reconciliation resultの
再計算、workflow state/events等のlineage mutation、provider/network/credential/
clock accessはありません。

## Phase 277: publication reconciliation evidence の read-only CLI

Phase 276が永続化したreconciliation-evidence sidecarを、明示されたpathから
provider-freeにstrict loadして確認するread-only CLI commandを追加しています。
commandは次の1つだけです。

```bash
ai-office workflows publication-reconciliation-evidence \
  --evidence-path path/to/reconciliation-evidence.json
```

`--evidence-path`は必須で、既定値、discovery、scanning、workflow-derived pathは
ありません。CLIはcallerが指定した`Path`をPhase 276の
`load_publication_regeneration_export_reconciliation(...)`へ一度だけ渡し、strict
loaderが返した同じmodel objectを
`publication_regeneration_export_reconciliation_digest(...)`へ一度だけ渡します。
sidecar JSONをCLIで再parseしたり、Phase 275 reconciliation、Phase 274 receipt、
export output、Phase 272 export、Phase 270 projectionを再実行・再観測したりは
しません。

validな`matched`、`missing`、`content_mismatch`では、`operation`、Phase 276の
全model fields、`evidence_sha256`だけを含むexact ten-key deterministic JSONを1行出力
します。`matched`は終了コード0、`missing`と`content_mismatch`は同じJSONを出力して
終了コード1です。invalid・tampered・unreadable evidence、unsafe target、wrong
dependency return type、digest failure、unexpected exceptionはstdoutを空にし、
固定detail-safe stderrと終了コード2だけを返します。path、raw evidence bytes、
business output、exception detail、digest failure detailは出力しません。

このcommandはfilesystem mutation、repair、retry、fallback、persistence、workflow
state/events・audit・claim・readiness・resultの変更、provider/runtime/network/
credential実行、publication permissionの推測を行いません。既存の
`publication-result`、`publication-export`、`result`、`start`、`continue`の契約も
変更しません。

## Phase 278: matched reconciliation evidence からの external publication plan

Phase 278は、将来のexternal publicationに向けたprovider-freeなplanning boundary
だけを追加します。`ExternalPublicationTarget`は、exact schema version、明示された
lowercase provider slug、callerが指定したtrim済み・non-secretなdestination identity
だけを持つfrozen targetです。providerのallowlist、URL discovery、DNS、credential、
environment、provider/network呼び出しはありません。destination IDのcontrol
character、型、長さ、余分な空白は拒否し、identityを変更するnormalizationは行いません。

targetのcanonical identityは、`schema_version`、`provider`、`destination_id`の
exact three keysを、`ensure_ascii=False`、sorted keys、compact separators、
`allow_nan=False`でJSON化し、exact UTF-8 bytesへSHA-256を適用します。
`build_external_publication_plan(...)`はcallerが明示したPhase 276 evidence `Path`を
strict loaderへ一度だけ渡し、返されたexact
`PublicationRegenerationExportReconciliation`が`matched`である場合だけ、同じ
objectをevidence digest helperへ一度だけ渡します。`missing`と
`content_mismatch`はfail closedで、planを返しません。

生成される`ExternalPublicationPlan`は、regeneration ID、reconciliation evidence
digest、receipt digest、expected business-output digest/byte length、provider、
publication target digestだけを保持します。raw business output、output path、
evidence path、destination text、credential、provider payload、timestamp、random IDは
含みません。planもtargetも永続化せず、output file・Phase 274 receiptを再読込せず、
Phase 275 reconciliation、Phase 272 export、Phase 270 projectionを呼びません。
planのcanonical JSONはexact eight keysのdeterministic UTF-8/SHA-256 identityです。
`validate_external_publication_plan(...)`はcachedなlineageを信頼せず、freshな明示
inputsから同じplanning contractを再導出して一致しないplanを拒否します。

このphaseではhuman approval、one-use claim、external publication、CLI command、
retry、repair、fallback、automatic continuation、workflow state/events/audit/readiness/
result mutationを追加しません。既存のPhase 274/275/276/277のreceipt、reconciliation、
evidence、CLI契約は変更されません。

## Phase 279: exact external publication plan への human approval binding

Phase 279は、Phase 278がすでにcallerから受け取った exact
`ExternalPublicationPlan` に対するprovider-freeなhuman approval boundaryだけを
追加します。`ExternalPublicationApproval`はfrozenな4-field valueで、
`approved=True`、canonical plan digest、callerが明示した`approved_by`、
`approval_id`だけを保持します。approval metadataはexactなbuiltin `str`、non-empty、
trim済み、最大256文字で、Unicodeの`Cc`/`Cs`を拒否します。値のnormalization、生成、
推測、timestamp、random IDの追加はありません。

`approve_external_publication(...)`はexactな`ExternalPublicationPlan`を受け取り、
既存plan invariantとmetadataを検証した後、同じcaller-supplied plan objectを既存の
`external_publication_plan_digest(...)`へ一度だけ渡します。lowercase 64-hexのdigest
だけをapprovalへ保存し、Phase 276 evidenceの再読込、Phase 278 planの再構築・fresh
revalidation、receipt/outputの読込は行いません。`validate_external_publication_approval`
もexactなplanとapprovalを検証し、同じplan objectでdigestを一度だけ再計算して、保存済み
digestとの一致をfail closedで確認します。provider、destination、evidence、receipt、
business outputのidentityはplan digestを通じてtransitively bindingされます。

approvalのcanonical identityは、exact four keys
`approved`、`approved_by`、`approval_id`、`publication_plan_sha256`を、
`ensure_ascii=False`、sorted keys、compact separators、`allow_nan=False`でJSON化し、
trailing newlineなしのexact UTF-8 bytesへSHA-256を適用します。fixed
`ExternalPublicationApprovalError`と`ExternalPublicationFailureDetail`は、plan type、
approval type、metadata、digest、binding、serialization、validationの分類だけを
detail-safeに返し、ID、approver、digest、path、destination、business data、内部例外を
漏らしません。

このphaseではapprovalを永続化せず、one-use claim、consumption key、external
publication、provider/runtime/network/credential access、filesystem mutation、CLI
command、workflow state/events/audit/readiness/result mutation、retry、repair、fallback、
automatic continuationを追加しません。Ready化、merge、Issue closeは後続の人間レビュー
まで行いません。

## Phase 280: external publication approval の durable one-use claim

Phase 280は、exactなPhase 278 `ExternalPublicationPlan`とPhase 279
`ExternalPublicationApproval`から、将来のexternal executionに先行するprovider-freeな
write-ahead claim markerだけを作成します。`ExternalPublicationAttemptClaim`はfrozenな
14-field identityで、planの再構成に必要なregeneration ID、reconciliation evidence /
receipt / business-output / publication-targetの各digestとoutput byte length、approval
digest、approval metadata、`state="claimed"`を保持します。destination text、raw output、
credential、path、timestamp、random ID、provider response、execution resultは保存しません。

`external_publication_consumption_key(approval)`は、exactなbuiltin
`ExternalPublicationApproval`の`approval_id.encode("utf-8")`へSHA-256を一度適用した
lowercase 64-hexを返します。同じcaller-supplied ledger directoryでは、同じapproval ID
のmarker pathを必ず同じ`<consumption_key>.json`へ束ねます。markerが既に存在する場合は、
内容が同一でも破損していてもread/compare/adopt/repair/overwriteせず、固定メッセージの
`ExternalPublicationAttemptAlreadyConsumedError`を直ちに返します。

ledger directoryはcallerが明示した、既存かつ非symlinkのexact `Path` directoryだけを
受け付けます。欠落directoryの自動作成、default pathのdiscovery、aliasのresolveは行い
ません。claim constructionは既存plan invariantとapproval bindingだけを検証し、Phase 276
evidenceを再読込せず、Phase 278 planをevidence/targetから再構築せず、receipt/output/
reconciliation/export/projectionを読まず、provider/runtime/network/credential/environment/
clock/random/UUIDへアクセスしません。

claim persistenceはcanonical JSON bytesをmutation前に確定し、authoritative marker自身を
exclusive createして、`write → full-write check → flush → file fsync → close → parent
directory fsync`の順で耐久化します。exclusive create成功後のwrite、flush、file fsync、
close、directory fsyncのどの失敗も`ambiguous`として扱い、markerを削除・truncate・repair・
retryせず保持します。その後の再試行はmarker bytesを読まずにalready-consumedとなります。

`load_external_publication_attempt_claim(...)`はexactなregular non-symlink `Path`からbytesを
一度だけ読み、strict UTF-8、duplicate-key拒否、NaN/Infinity拒否、exact 14-key set、全構造
validation、plan/approval/consumption-keyの内部binding、canonical byte-for-byte equalityを
検証します。loaderはnormalization、repair、rewrite、retryを行いません。

このphaseはexternal publication、provider/runtime/network/credential実行、CLI command、
workflow state/events/audit/readiness/result mutation、別approval file、retry、fallback、
automatic continuation、Ready化、merge、Issue closeを追加しません。次の明示phaseがclaim
後のexternal executionを所有します。

## Phase 281: durable claim 後の external publication execution

Phase 281は、exactなPhase 278 `ExternalPublicationPlan`、Phase 279
`ExternalPublicationApproval`、Phase 276 reconciliation evidence path、Phase 278の
explicit target、export済みbusiness-output file、caller-supplied transportを、次の固定順序で
一度だけ扱います。

`Plan -> Human Approval -> fresh execution preflight -> durable one-use Claim -> transport once -> in-memory result`

execution moduleはまず`validate_external_publication_plan(...)`をexactなplan、evidence
path、targetで一度だけ呼び、次に`validate_external_publication_approval(...)`をexactなplanと
approvalで一度だけ呼びます。その後、Phase 280 claimとclaim digestの期待identityをin-memory
で一度だけ構築します。output pathはcaller-supplied exact `Path`であること、regularかつ非symlink
であることを確認してからbytesを一度だけ読み、plan指定のbyte lengthとSHA-256へ照合します。
このverified bytes objectはdecode/re-encode、normalize、再読込せず、そのままtransportへ渡します。
transportがcallableであることもclaim作成前に確認します。

全preflight成功後、`claim_external_publication_attempt(...)`をtransport直前にexactly once
呼びます。Phase 281はpre-existing claimをloadしてexecutionするのではなく、この境界自身が
Phase 280のwrite-ahead claim作成を所有します。claim errorは既存のfixed/detail-safe errorの
まま伝播し、malformedまたは期待値と異なるclaim returnでもtransportは呼びません。successful
claimをread back、delete、repair、reset、replace、compensateしません。

claim成功後はexact target objectとverified bytes objectをtransportへexactly once渡します。
transport exceptionまたは不正・subclass・attribute-compatibleなreceiptは、外部処理が既に
行われた可能性があるため、fixed `external publication execution outcome is ambiguous`
errorで停止し、approvalをconsumed状態のままclaimとともに保持します。retry、fallback、
automatic continuation、新しいclaimやapprovalの作成は行いません。validな
`ExternalPublicationTransportReceipt`のpublication ID
だけを使い、secret-freeなfrozen `ExternalPublicationExecutionResult`をin-memoryで返します。
このphaseではexecution result/receipt/evidenceを永続化せず、execution moduleでenvironmentや
credentialをloadせず、CLI commandも追加・変更しません。testsはfake transportのみを使います。

## Phase 282: external publication execution result の canonical durable evidence

Phase 282は、Phase 281で既に導出された exact な
`ExternalPublicationExecutionResult`を、その11 fields自身としてcanonical durable
evidenceへ保存します。wrapper、timestamp、generated ID、random ID、path、approval metadata、
destination、raw output、provider raw response、credential、追加identityは保存しません。
control sequenceは次のとおりです。

`external transport once -> Phase 281 in-memory published result -> Phase 282 canonical durable execution evidence`

canonical JSONはexact 11-key objectを`ensure_ascii=False`、compact separators、sorted keys、
`allow_nan=False`で生成し、UTF-8 bytesへ変換します。BOM、trailing newline、余分なwhitespaceは
ありません。Unicodeのpublication IDはnormalizeせず、canonical bytesのSHA-256 digest helperは
そのexact bytesに適用します。serializer、bytes、digest、persistenceの各public boundaryは
exactな`ExternalPublicationExecutionResult`だけを受け付け、subclass、lookalike、mapping、
coercion、malformed forged instanceを拒否します。

`persist_external_publication_execution_result(...)`はcaller-supplied exact `Path`と既存parent
directoryだけを使い、targetを`xb`でexclusive createします。write、full-write check、flush、
file fsync、close、parent-directory fsyncの順序を守ります。exclusive create後のshort write、
flush、file fsync、close、directory fsync failureはambiguousとして扱い、artifactを削除・
truncate・repair・rewrite・retryせず保持します。既存targetがcanonical bytesと完全一致する
場合だけidempotent successとし、rewriteせずfile/parentをfsyncします。異なるbytes、または
semanticには同じでもnoncanonicalなbytesはconflictです。

`load_external_publication_execution_result(...)`はread-onlyのstrict loaderです。exact `Path`の
regular non-symlink targetからbytesを最大1回だけ読み、strict UTF-8、duplicate key拒否、
NaN/Infinity/-Infinity拒否、exact 11-key set、exact result再構築、canonical byte-for-byte
一致を確認します。BOM、whitespace、key order、escaped表現、trailing newline、extra/missing
keys、型・digest・schema・statusの不正は拒否し、loaderはrepair、rewrite、normalization、retry
を行いません。

このphaseはPhase 281 execution、Phase 280 claim作成・load、Phase 278以前のplan/approval/
reconciliation/receipt/export/projection/output、transport/provider/network/credential/
environment、clock/random/UUIDへアクセスしません。executionを再実行せず、external stateを
reconcileせず、workflow/audit/readiness/result lineageを変更せず、CLI commandも追加・変更
しません。execution-result evidenceは、このphaseの明示的なcaller callによってのみ作成され、
後続phaseが明示的に組み合わせるまでcanonical durable recordに限定されます。

## Phase 283: durable claim と execution evidence の read-only lineage reconciliation

Phase 283は、既存のstrict durable recordを2つだけ読み込んで、同じpublication attempt
lineageに属するかをprovider-freeに観測します。control sequenceは次のとおりです。

```text
Phase 280 durable one-use claim ───────┐
                                       ├─> Phase 283 read-only lineage reconciliation
Phase 282 durable execution evidence ─┘
```

`reconcile_external_publication_execution(...)`は、callerが渡したexactな`Path`をそのまま
strict Phase 280 claim loaderとstrict Phase 282 execution-evidence loaderへ渡し、それぞれの
loaderを一度だけ呼びます。各loaderが返したexact object identityのまま対応するdigest helper
も一度だけ呼び、claim digest、execution-evidence digest、8つの共有immutable lineage binding
をcanonical orderで比較します。sidecar JSONをこのmodule自身がopen/readすること、値を再構築
すること、`publication_id`やapproval metadataをlineageへ追加することはありません。

完全一致だけが`status="matched"`となります。validなdurable record同士の不一致は例外では
なく、`status="lineage_mismatch"`とsafeなfield nameだけの`mismatched_fields`を返します。
field nameは`publication_attempt_claim_sha256`、`regeneration_id`、
`publication_plan_sha256`、`publication_approval_sha256`、`business_output_sha256`、
`output_byte_length`、`provider`、`publication_target_sha256`の順で、重複や値の漏えいは
ありません。`matched`は、strict durable execution evidenceがexactなstrict durable claimに
束縛され、共有する全immutable lineage fieldが一致したことだけを意味します。結果はfrozenな
in-memory observationであり、永続化しません。

これはlocal durable lineageの一致だけを検証し、providerの現在のexternal stateを証明
しません。Phase 283はpublication execution、claim作成、execution evidence persistence、
repair、retry、fallback、automatic continuation、provider/network/credential/environment/
clock/random/UUID/socket access、filesystem mutation、output file read、workflow stateや
CLI behaviorの変更を行いません。`lineage_mismatch`はvalidな観測であり、execution retryの
signalではありません。

## Phase 284: external publication execution reconciliation の canonical durable evidence

Phase 284は、Phase 283ですでに導出された exactな
`ExternalPublicationExecutionReconciliation` だけを、同じ5 fields自身として
canonical durable evidenceへ保存します。control sequenceは次のとおりです。

```text
Plan
 ↓
Human Approval
 ↓
Durable one-use Claim
 ↓
External Execution
 ↓
Durable Execution Evidence
 ↓
Read-only Lineage Reconciliation
 ↓
Canonical Durable Reconciliation Evidence
```

保存するJSONは`schema_version`、`claim_sha256`、`execution_evidence_sha256`、
`status`、`mismatched_fields`だけのexact 5-key objectです。wrapper、timestamp、
generated ID、provider state、publication ID、approval metadata、raw output、path、
credentialは追加しません。`mismatched_fields`はPhase 283のcanonical tuple orderを
そのままJSON arrayへ変換します。canonical JSONはcompactなsorted-key JSON、
`ensure_ascii=False`、`allow_nan=False`、BOMなし、trailing newlineなしのexact UTF-8
bytesであり、digestはそのbytesのSHA-256です。

serializer、bytes、digest、persistenceはexact runtime typeの
`ExternalPublicationExecutionReconciliation`だけを受け付け、subclass、lookalike、
mapping、coercion、malformed forged instanceをfail-closedで拒否します。persistenceは
caller-supplied exact `Path`、既存parent directory、exclusive `xb` create、full write
check、flush、file fsync、close、parent-directory fsyncだけを使います。exclusive create
後のshort write、write、flush、file fsync、close、directory open/fsync/close failureは
ambiguousとしてartifactを保持し、delete、truncate、repair、rewrite、retry、fallbackを
行いません。既存bytesがcanonical bytesと完全一致する場合だけfile/parent durabilityを
確認してidempotent successとし、異なるbytesやsemanticには同じnoncanonical bytesはconflict
です。

strict loaderはtargetを一度だけ読み、strict UTF-8、duplicate key、NaN/Infinity、BOM、
exact key set、exact model invariants、canonical byte-for-byte equalityを検証します。
loaderはread-onlyで、repairやrewriteをしません。Phase 284はPhase 283 reconciliationを
再実行せず、Phase 280 claim、Phase 282 execution evidence、Phase 281 execution、Phase 278
以前のboundary、provider/network/credential/environment/clock/random/UUID/socketへアクセス
しません。`lineage_mismatch` evidenceもvalidなdurable observationであり、retry signalでは
ありません。これによりexternal-publication audit chainはdurably closedとなり、次の作業は
新しいlower-level publication evidence boundaryではなくhigher-level orchestrationへ移ります。

## Phase 285: approved external publication の execution evidence orchestration

Phase 285は、Phase 284でlower audit chainが閉じた後の最初の上位
external-publication orchestrationです。既存のpublic boundaryを再実装せず、Phase 282のfresh
execution-evidence target preflight、Phase 281のapproved external publication execution、Phase
282のcanonical durable execution-evidence persistenceだけを次の固定順序で組み合わせます。

```text
External Publication Orchestration (Phase 285)

fresh Phase 282 evidence target preflight
        ↓
Phase 281
  approval + durable one-use claim + external transport
        ↓
Phase 282
  canonical durable execution evidence
        ↓
stop and return exact execution result
```

`preflight_external_publication_execution_result_path(path)`はPhase 282のpublic read-only
helperです。exact concrete `Path`、既存のparent directory、regular non-symlink targetを
確認し、既存targetを`target_exists`として拒否します。missing targetだけをvalidとしますが、
pathをreserve/create/write/mkdirせず、target contentsも読みません。したがってpreflight成功は
evidence pathの予約ではなく、後続persistenceとのfilesystem raceをなくしません。raceが発生
した場合のPhase 282 persistence errorがauthoritativeです。

成功経路は、preflightをexactly once、`execute_approved_external_publication(...)`をexactly
once、返されたexact `ExternalPublicationExecutionResult`のsnapshot、
`persist_external_publication_execution_result(...)`をexactly onceの順序で実行します。
persistenceにはPhase 281が返した同一object identityだけを渡し、returnはexact `None`だけを
受理します。persistence中のresult mutationはfail-closedに検出し、成功時はPhase 281のresult
objectを同一identityのまま返します。Phase 283 reconciliation、Phase 284 reconciliation
evidence persistence/load/digest、Phase 282 loader/digestの直接利用は行いません。

Phase 281/Phase 280/Phase 282のknown errorは同じerror objectのまま伝播します。claim作成後、
transportがambiguousになった後、またはpublication後のPhase 282 persistenceがconflict/
ambiguousになった後にretry、fallback、claimのdelete/reset/repair、evidenceのdelete/repair/
rewrite、provider stateからの結果推測、自動継続は行いません。unknown dependency exception
とorchestration-owned contract failureだけをfixed/detail-safeなPhase 285 errorへ変換します。

Phase 285はprovider/network/credential/environment/clock/random/UUID/socketへ直接アクセスせず、
fake transportによる呼び出しだけをtestsで検証します。CLI/GUIは変更しません。Phase 286では、
新しいlower-level evidence modelを追加するのではなく、durable post-execution reconciliation
closureを上位からcomposeすることが予定されます。

## Phase 286: durable external publication reconciliation closure orchestration

Phase 286は、Phase 283のread-only execution reconciliationとPhase 284のcanonical durable
reconciliation evidence persistenceを組み合わせる、provider-freeなpost-execution closureです。
Phase 285のexecution orchestration、Phase 281のexternal execution、Phase 280のclaim操作を
呼び出さず、すでにdurableなPhase 280 claimとPhase 282 execution evidenceから開始します。

```text
Post-execution Reconciliation Closure (Phase 286)

durable Phase 280 claim + durable Phase 282 execution evidence
        ↓
Phase 283 read-only reconciliation
        ↓
matched | lineage_mismatch
        ↓
Phase 284 canonical durable reconciliation evidence
        ↓
stop and return exact reconciliation result
```

`reconcile_and_persist_external_publication_execution()`の正常経路は、Phase 283
`reconcile_external_publication_execution()`をexactly once、exact caller `claim_path`と
`execution_evidence_path`で呼び出し、exactな`ExternalPublicationExecutionReconciliation`の
全5 field（`schema_version`、`claim_sha256`、`execution_evidence_sha256`、`status`、
`mismatched_fields`）をsnapshotします。その後、Phase 284
`persist_external_publication_execution_reconciliation()`をexactly once、exact callerの
`reconciliation_evidence_path`とPhase 283が返した同一objectで呼び出します。persistenceの
returnはexact `None`だけを受け付け、5 fieldが変化していないことを確認した後、Phase 283の
reconciliation objectを同じidentityのまま返します。

`matched`と`lineage_mismatch`はどちらもpersist可能なvalid observationです。
`lineage_mismatch`はretry signal、execution failure、publication許可、repair理由ではなく、
監査可能な観測としてPhase 284へ一度だけ保存します。Phase 286にはPhase 284用のfresh-target
preflightを追加しません。既存canonical evidenceとのidentical-bytes再実行はPhase 284の
既存idempotent recoveryに任せ、differing/noncanonical targetのconflictおよびambiguous
persistenceはPhase 284のauthoritative errorとして、そのerror object identityのまま伝播します。

Phase 283のknown reconciliation errorとPhase 284のknown evidence/conflict/ambiguous errorは
同じerror objectのまま伝播します。unknown dependency exception、wrong result、non-`None`
persistence return、persistence中のresult mutationだけを、固定message/detail-safeなPhase 286
orchestration error（`configuration`、`reconciliation_contract`、`persistence_contract`、
`result_mutation`、`dependency_error`）としてfail-closedに扱います。どの失敗後にもretry、
fallback、repair、delete、rewrite、compensation、Phase 283再実行、provider state inference、
automatic continuationは行いません。

Phase 286自身は、Phase 280 claim、Phase 282 persistence/digest/serializer、Phase 284
loader/digest/serializer、sidecarの直接open/read/write、business output、plan rebuild、
approval revalidation、provider/network/credential/environment/clock/random/UUID/socket/
subprocessへアクセスしません。CLI/GUI変更もありません。Phase 284 evidenceがdurableになった
後の次の設計段階では、Phase 285 + Phase 286を明示的な大きなoperationとしてcomposeするか、
job/workflow-facing orchestrationへ外側に進むかを評価します。このIssueでは次Phaseを自動開始
しません。

## Phase 287: durable approval lineage からの reconciliation closure resumable handoff

Phase 287は、Phase 285のexternal side effectが成功した可能性がある一方で、後続の
Phase 286 closureだけを再開する必要がある場合のprovider-freeなrecovery/handoff boundary
です。fresh publication boundaryではないため、Phase 285、Phase 281、transport/providerを
呼び出しません。

```text
Phase 287 resumable post-execution handoff

exact ExternalPublicationApproval + existing ledger directory
        ↓
Phase 280 deterministic consumption key / canonical claim path
        ↓
Phase 286 provider-free reconciliation closure
        ↓
matched | lineage_mismatch
        ↓
durable reconciliation evidence
```

`resume_external_publication_reconciliation_closure(...)`は、exactなcaller approvalの
runtime typeを確認してから、公開済みPhase 280 helperを次の順序で各一度だけ呼びます。

1. `external_publication_consumption_key(approval)`へ同じapproval objectを渡す。
2. lowercase 64-hexのbuiltin `str`だけを受け付け、返された同じkey objectを
   `external_publication_attempt_claim_path(ledger_directory, consumption_key)`へ渡す。
3. exact concrete `Path`だけを受け付け、返された同じclaim path objectとcallerの
   `execution_evidence_path`、`reconciliation_evidence_path`を
   `reconcile_and_persist_external_publication_execution(...)`へ一度だけ渡す。
4. exact `ExternalPublicationExecutionReconciliation`の5 fieldsを再検証し、同じPhase 286
   result object identityを返す。

Phase 280のmarker filename/path convention、Phase 283のcomparison、Phase 284のevidence
persistenceは再実装しません。claimをcreate/load/reset/delete/repairせず、sidecarを直接
open/read/writeせず、plan rebuildやapproval revalidationも行いません。`matched`と
`lineage_mismatch`はともに正常returnです。mismatch、conflict、ambiguous、missing、corrupt
の下位エラーはfresh publication retryを許可せず、authoritativeなPhase 280/286 errorを
そのまま伝播します。

同じapproval、claim、execution evidence、reconciliation targetでPhase 287を再実行すると、
Phase 286/284の既存identical-bytes idempotent recoveryを通じて成功できます。predecessor
sidecarは変更されず、provider実行もありません。Phase 287はCLI/GUI、schedule、retry、
fallback、compensation、automatic continuationを追加しません。次の設計段階では、fresh
execute + resumable closureを含むhigher-level operation/job-facing contractを評価しますが、
このIssueから自動開始しません。

## Phase 288: explicit fresh-vs-resume external publication operation dispatch

Phase 288は、job/workflow-facing callerが`fresh`と`resume`のどちらを実行するかを
明示的に選ぶruntime dispatch contractです。durableなjob stateを実装したり、filesystemや
claim/evidenceの状態からmodeを推測したりはしません。freshとresumeは同じopaque operationの
retry modeではなく、異なる意味を持つ明示的なoperationです。

```text
Explicit operation selection

ExternalPublicationFreshOperationRequest
        ↓
Phase 285 exactly once
        ↓
published + durable execution evidence

ExternalPublicationResumeOperationRequest
        ↓
Phase 287 exactly once
        ↓
matched | lineage_mismatch + durable reconciliation evidence
```

`ExternalPublicationFreshOperationRequest`は、
`execution_evidence_path`、`plan_reconciliation_evidence_path`、`output_path`、
`ledger_directory`、`plan`、`approval`、`target`、`transport`を保持します。
`plan_reconciliation_evidence_path`は、Phase 285/278がfresh publication前にplanを
再検証するための既存artifactです。

`ExternalPublicationResumeOperationRequest`は、`ledger_directory`、`approval`、
`execution_evidence_path`、`execution_reconciliation_evidence_path`を保持します。
`execution_reconciliation_evidence_path`は、Phase 287/286/284がpost-execution lineage
closureを再開するための別artifactです。この2つのpath名は意図的に統合しません。
両requestはexact frozen dataclassのruntime envelopeであり、永続化・serialization・
filesystem access・lower-phase policy duplicationを行いません。

`run_external_publication_operation(...)`はexact request runtime typeだけでrouteを選び、
freshではPhase 285をexactly once、resumeではPhase 287をexactly once呼びます。selected
routeへrequest内のcaller objectを同じidentityで渡し、freshではexact
`ExternalPublicationExecutionResult`、resumeではexact
`ExternalPublicationExecutionReconciliation`だけを受け付け、既存model contractで全fieldを
再検証した後、lower boundaryの同じresult object identityを返します。

fresh成功後にPhase 287を自動実行せず、fresh失敗後にresumeへfallbackせず、resume失敗後に
freshへfallbackしません。`matched`と`lineage_mismatch`はどちらもresumeの正常returnです。
下位のknown errorは同じerror object identityで伝播し、unexpected selected-dependency
exceptionだけを固定detail-safeなPhase 288 errorへ変換します。unused dependencyのmalformed
valueはselected routeに影響しません。

Phase 288はPhase 280〜284/286を直接呼ばず、claim path/consumption keyを導出せず、sidecarを
直接open/read/writeせず、provider/transportをPhase 285以外から呼びません。retry、fallback、
compensation、automatic continuation、provider state inference、credential/environment/
clock/random/UUID/socket/subprocess/network access、CLI/GUI変更もありません。futureの
higher-level durable job lifecycleがfresh attempt後に明示的なresumeを指示することは評価対象
ですが、このIssueではそのlifecycleを実装せず、自動開始もしません。

## Phase 289: durable explicit external-publication operation intent evidence

Phase 289は、Phase 288のruntime operation choiceを、job/workflow-facing
lifecycleが後で参照できる最初のdurable building blockとして記録します。保存するのは
callerがその時点で明示的に選択した`fresh`または`resume`と、exactなapproval/plan
lineageのdigestだけです。Phase 288のruntime request、transport、credential、path、
timestamp、UUID、provider state、exception text、その他のruntime objectは保存しません。

```text
Phase 288 runtime choice
        fresh | resume
              ↓
Phase 289 durable explicit intent
              ↓
canonical immutable intent sidecar
```

`build_external_publication_operation_intent(...)`は、exact
`ExternalPublicationApproval`を既存のpublic approval digest contractで再検証し、
`external_publication_approval_digest(approval)`をcaller object identityのまま一度だけ
呼び出します。approvalが保持する`publication_plan_sha256`はそのままintentへbindし、
planやevidenceを再構築・再読込しません。`fresh`から`resume`への変換や、claims、execution
evidence、reconciliation evidence、provider state、previous errorからのroute推測も行い
ません。

intent sidecarは、`operation`、`publication_approval_sha256`、
`publication_plan_sha256`、`schema_version`だけを含むUTF-8 compact canonical JSONです。
sort keys、非ASCII保持、non-standard JSON constant拒否、duplicate key拒否、strictな
four-key検証、canonical byte再検証を適用します。sidecarはimmutableで、同一canonical
bytesの再保存だけをdurable idempotent successとして認め、異なるbytesにはconflictを
返します。新規作成はexclusive creation、全bytesのwrite、flush、file fsync、parent
directory fsyncを順に行い、作成後の不確実な失敗ではartifactを保持します。

Phase 289はintentを実行せず、Phase 288、Phase 285、Phase 287、provider/transportを
呼び出しません。persisted `fresh` intentは「その時点でcallerがfreshを選択した」という
記録に過ぎず、freshの繰り返し実行を安全にするpermissionやexecution-start markerでは
ありません。persisted `resume` intentもexplicit resume intentの記録に過ぎません。
Phase 289は別artifactの存在・不在をpublish permissionとして解釈せず、fresh attemptの
曖昧な外部副作用後にresumeへ自動chainしません。次の設計段階では、intentがPhase 288の
executionを駆動する前に、ambiguousなfresh attemptをreplayできないcrash-safeな
consumption/one-way state transition boundaryを評価する必要があります。

## Phase 290: crash-safe external-publication operation start acquisition fence

Phase 290は、Phase 289のdurable explicit intentをstrictにloadし、そのexact intent
digestとapproval/plan lineageを別のimmutable start markerへbindします。

```text
Phase 289 durable intent
              ↓ strict load
exact ExternalPublicationOperationIntent
              ↓ exact intent digest
Phase 290 exclusive start acquisition
              ↓
       acquired | already_acquired
```

`ExternalPublicationOperationStart`は、schema、intent digest、approval/plan digest、
explicitな`fresh`または`resume`、`started` stateだけを持つexact frozen modelです。
canonical markerは6-key compact UTF-8 JSONで、strict loaderはduplicate key、非標準
constant、malformed field、extra/missing key、non-canonical bytesを拒否します。

start targetの新規作成はexclusive create、全bytesのwrite、flush、file fsync、close、
parent-directory fsyncの順に完了した場合だけ`acquired`を返します。exact markerが既に
存在してdurabilityを再検証できた場合は`already_acquired`を返します。`already_acquired`
は新しい実行許可ではなく、freshを自動replayしてはいけません。conflict、partial marker、
non-canonical markerはfail closedし、既存artifactをoverwrite、delete、rename、repair、
retryしません。

Phase 290はPhase 280のpublication claimを複製する境界ではなく、Phase 289 intentの
上位operation lifecycle fenceです。Phase 289 intentはimmutableでauditableなまま保持し、
Phase 290自身はPhase 288、Phase 285、Phase 287、provider、transportを実行しません。
post-createのwrite/flush/fsync/close/directory-fsyncがambiguousに失敗した場合もmarkerを
保持して`acquired`を返さず、後続のexact marker確認は`already_acquired`に限ります。
`fresh`と`resume`を相互変換せず、次の設計段階では、同じinvocationの新規`acquired`結果
だけからPhase 288を呼ぶthin execution handoffを評価します。

## Phase 291: same-invocation acquired-start external publication handoff

Phase 291は、Phase 290のcrash-safe start fenceを越えた直後のthinなexecution handoffです。
callerが渡した/reconstructしたacquisition objectは実行許可として受け取りません。Phase 291
自身がPhase 290をexactly once呼び、そのsame-invocationの新規`acquired`結果だけから
Phase 288をexactly once呼びます。

```text
Phase 289 durable intent
              ↓
Phase 291 request preflight
              ↓
Phase 290 acquire exactly once
        ↓ acquired            ↓ already_acquired
Phase 288 exactly once   zero-call stop
```

`phase290_function`のdefaultはpublicな
`acquire_external_publication_operation_start`、`phase288_function`のdefaultはpublicな
`run_external_publication_operation`です。Phase 288のruntime requestを受ける前に、
`intent_path`/`start_path`のexact concrete `Path`、requestのexact runtime type、fresh request
のexact path/plan/approval/target/callable transport、resume requestのexact path/approval、
およびauthoritativeなapproval↔plan validationとapproval digest、plan binding digest
（builtin lowercase 64-hex）を検証します。malformedなrequestでdurable start fenceを
消費しません。このpreflightはoutput/evidence fileを読まず、providerを呼びません。

Phase 290の戻り値はexact runtime `ExternalPublicationOperationStartAcquisition`でなければ
ならず、embedded `ExternalPublicationOperationStart`のschema、intent/approval/plan digest、
operation、`started` stateを再検証します。さらにstart lineageがpreflight済みrequestと一致
することを要求します（operation一致、approval digest一致、validated plan digest一致、
intent digestがlowercase 64-hex）。mismatchはfixed/detail-safe Phase 291 errorとなり、
Phase 288はzero-callのままです。

`already_acquired`はrestart/no-replayのstop routeです。Phase 288 call countは0で、同一の
acquisition objectをidentityでそのまま返し、fresh→resume変換、lower evidence/provider
stateのinspection、Phase 290のretry、markerのmutation/delete/repair、別markerの作成を
行いません。また、これをexternal operationの成功として扱いません。

`acquired`の場合だけ、lineage検証通過後に元のrequest objectをidentityでPhase 288へ
exactly once渡します。freshはexact `ExternalPublicationExecutionResult`、resumeはexact
`ExternalPublicationExecutionReconciliation`を要求し、Phase 288の戻り値objectをidentityで
返します。Phase 288がknown authoritative errorをraiseした場合は同一objectをidentityで
propagateし、malformed/wrong/subclass resultの場合はfail closedします（second callなし）。
このときPhase 290 start markerはauthoritativeなまま保持され、削除・rewriteしません。

Phase 291はretry、fallback、automatic recovery、fresh↔resume変換、outcome inference、
lifecycle looping、Phase 285/287/provider/transportの直接呼び出しを行いません。Phase 291
自身は新しいdurable artifactを所有せず、Phase 280 claim、Phase 282 evidence、Phase 284
reconciliation evidence、Phase 289/290 persistenceを複製しません。CLI/GUI変更もありません。
次の設計段階は、このno-replay boundaryが証明された後にだけ、explicitなpost-handoff
durable lifecycle/recovery stateを評価します。

## Phase 292: durable post-handoff lifecycle outcome evidence

Phase 292は、Phase 291のsame-invocation handoffの上に、append-onlyでimmutableな
lifecycle sidecarを1つ追加します。Phase 291の**normal returnだけ**をdurable lifecycle
outcomeへ変換し、それ以外は記録しません。

```text
Phase 289 durable intent
        ↓
Phase 290 crash-safe start fence
        ↓
Phase 291 same-invocation handoff
        ↓ normal return only
Phase 292 durable lifecycle outcome
   ├─ completed
   └─ recovery_required
```

Phase 291実行前に、Phase 292はexactな`intent_path` / `start_path` /
`lifecycle_outcome_path`のplatform `Path`、exact request runtime type、注入dependencyの
callable性、lifecycle targetのparent/shape（mutationなし）、および既存sidecar比較に
必要な最小限のrequest lineage（exact approval runtime type、public canonical approval
digestを1回、exact plan digest、request typeから導出するexpected operation）だけを
preflightします。output/evidence/provider stateは読みません。

lifecycle sidecarはそれ自身がidempotency boundaryです。exactなlifecycle outcomeが
既に存在する場合、Phase 292はsidecarを1回strict-loadし、Phase 290 start markerを1回
strict-loadし、exact start digestを1回計算し、operation/approval/plan/start-digestの
lineageと全cross-field invariantを検証して、そのloaded objectをそのまま返します。この
ときPhase 291のcall countは**0**です。validな既存`recovery_required` recordはそのまま
返され、retry permissionとして使われません。malformed/conflicting/noncanonicalな
sidecarはfail closedとなり、Phase 291はzero-callです。

lifecycle outcomeが存在しない場合、Phase 292はcallerのexactな`intent_path` /
`start_path` / request identityでPhase 291をexactly once呼び、Phase 290 startを
strict-loadし、start digestを1回計算してstart lineageを再検証したうえで、exactな
Phase 291 returnのruntime type/valueだけで分類します。

- fresh → durable startとapproval/plan digestが一致するexact
  `ExternalPublicationExecutionResult(status="published")` は
  `completed / execution_result`；
- resume → exact `ExternalPublicationExecutionReconciliation(status="matched")`は
  `completed / reconciliation`；
- resumeの`lineage_mismatch`は`recovery_required / reconciliation`；
- 埋め込みstartがstrict-loaded durable startと一致するexact
  `ExternalPublicationOperationStartAcquisition(status="already_acquired")`は
  `recovery_required / none`（result digestはnull）。

`already_acquired`は必ず`recovery_required`となり、`completed`にはなりません。
`recovery_required`はautomatic replayを停止すべきというdurable knowledgeであり、failure
classificationでもretry authorizationでもありません。Phase 290 markerがreplay fenceの
ままであり、Phase 292はpost-handoff knowledgeのみを記録します。

Phase 291が例外をraiseした場合、Phase 292はknown authoritative errorを同一object
identityでpropagateし、sidecarを作成しません。unexpected exceptionはfixed/detail-safeな
`dependency_error`となり、lifecycle targetはabsentのままです。Phase 291の例外から
durable lifecycle stateを推測することはなく、Phase 290 start markerが存在するだけで
`recovery_required`を書くこともありません。exception-sideのdurable ambiguity
classificationは後続のexplicit phaseに属します。

完全なlifecycle outcomeはlifecycle targetのmutation前にderive/validateされ、Phase 292の
append-only persistence helperでexactly once永続化されます。exclusive create、full
canonical write、flush、file fsync、safe close、parent-directory fsyncの順です。同一bytes
はidempotentなdurable success、different/partial/noncanonical bytesはfixed conflictであり、
overwrite/truncate/delete/rename-overは行いません。exclusive create後のuncertain failureは
ambiguousとなり、artifactは保持されcleanupもretryも行いません。後からexactなretained
bytesはidempotentにacceptされ、partial/different bytesはfail closedします。Phase 292は
Phase 291や自身のpersistenceをretryせず、Phase 290 start markerをrollbackせず、
fresh↔resume変換も行いません。

canonical JSONはexactly 8 key（`operation`、`operation_start_sha256`、
`publication_approval_sha256`、`publication_plan_sha256`、`result_kind`、
`result_sha256`、`schema_version`、`state`）で、compact UTF-8、`sort_keys=True`、
`ensure_ascii=False`、`allow_nan=False`、strict duplicate-key rejection、非標準JSON
定数のrejectionを持ちます。loaderはexact modelとcanonical byte equalityを要求するため、
semantically equivalentでもnoncanonicalなwhitespace/orderはrejectされ、digestはexact
canonical bytesに対するdeterministic SHA-256です。timestamp、clock value、UUID、
randomness、hostname、PID、caller path、provider credential、transport object、
exception text、approval ID/name、mutable runtime valueは含みません。

Phase 292はPhase 288/290/285/287境界やprovider/transportを直接呼ばず、Phase 280 claimや
Phase 282/284 evidenceをretry判断のためにinspectしません。fresh↔resume変換、
`recovery_required`からのfresh replay、Phase 289 intent / Phase 290 startのdelete/repair、
lifecycle outcomeのoverwrite/repair、Phase 291 / lifecycle persistenceのretry、lifecycle
persistence失敗時のstart marker rollback、automatic continuation/schedule/loop/parallelizeは
行いません。CLI/GUI変更もありません。次の設計段階は、freshのno-replay fenceを弱めずに
`recovery_required`をconsumeするexplicitなoperator/recovery decision boundaryを評価します。

## Phase 293: durable explicit recovery decision evidence

Phase 293は、Phase 292のdurable lifecycle outcomeの上に、append-onlyでimmutableな
**explicit operator recovery decision** boundaryを1つ追加します。消費するのはexactな
Phase 292 lifecycle outcomeで`state`が`recovery_required`のものだけです。Phase 293は
recoveryを実行しません。

```text
Phase 292 recovery_required
        ↓
Phase 293 explicit operator decision
   ├─ stop
   └─ authorize_resume_preparation
        ↓
durable append-only recovery decision evidence
```

allowed decisionは2つだけです。

- `stop`: このrecovery pathを自動actionなしで終了します。Phase 293はdecisionを記録する
  だけで、provider call、resume preparation、Phase 291/292 execution、fresh replayの
  いずれも行いません。
- `authorize_resume_preparation`: このexactなrecovery decisionにbindされた新しいexplicit
  resume-operation lineageを**将来のphaseが**準備することだけをauthorizeします。provider
  callの実行許可でも、Phase 291/292の呼び出し許可でも、Phase 290 start markerの新規取得
  許可でも、Phase 289 resume intentの作成許可でも、automatic reconciliationの許可でも
  fresh replayの許可でもありません。resumeが可能であることを保証もしません。

`completed`のPhase 292 outcomeはPhase 293に入りません。**recovery kindはcallerから受け取らず**、
strict-loadしたPhase 292 lifecycle modelとexactなPhase 290 start lineageだけからderiveします。

- `recovery_required` + `result_kind` none + `result_sha256` None → `already_acquired`
  （source operationはfresh/resumeいずれも可）；
- `resume` + `recovery_required` + `result_kind` reconciliation + exact result digest →
  `reconciliation_mismatch`；
- その他のlifecycleの組み合わせはinvalidです。

Phase 293はまず、exactな`lifecycle_outcome_path` / `start_path` /
`recovery_decision_path`のplatform `Path`、exactな`decision`値（`stop` /
`authorize_resume_preparation`）、explicitな`decided_by` / `decision_id` operator metadata、
注入dependencyのcallable性、decision targetのparent/shape（mutationなし）をpreflightします。
caller-suppliedのlifecycle/start model、recovery_kind、predecessor digest、Phase 291/292
return objectはauthorityとして受け取りません。ambient identityやdefault decisionも使いません。

preflight後、Phase 293はcallerのexactなpath identityでPhase 292 lifecycle outcomeを
exactly once strict-loadし、exact runtime typeとcross-field invariantを再検証し、
`state`が`recovery_required`であることを要求して`completed`をrejectし、recovery kindを
lifecycle modelだけからderiveします。次にlifecycle digestをexactly once計算し
（lowercase 64-hex必須）、Phase 290 startをexactly once strict-loadし、start digestを
exactly once計算したうえで、startのoperation/approval/plan lineageとcomputed start digestが
lifecycleと一致することを要求します。Phase 289 intentのload、Phase 280 claim、Phase 282
execution evidence、Phase 284 reconciliation evidence、provider state、output、credentialの
inspectは行いません。

decisionはvalidated predecessor lineage + derived recovery kind + explicit decision +
explicit `decided_by` + explicit `decision_id`だけから構築されます。decision targetが
既に存在する場合、Phase 293はsidecarを1回strict-loadし、exact runtime decisionと
lifecycle/start/approval/plan/source/recovery lineageおよびrequested decision/metadataの
完全一致を要求し、一致すればそのloaded objectをそのまま（identityで）返します。差分が
あればfixed conflictとなり、既存bytesは変更されません。1つのdecision pathは1つの
immutableなoperator decisionであり、Phase 293はそれをrevise/supersedeしません。targetが
absentの場合はconstructed decisionをexactly once永続化し、durable success後にそのexact
objectを返します。retryは行いません。

canonical JSONはexactly 11 key（`decision`、`decided_by`、`decision_id`、
`lifecycle_outcome_sha256`、`operation_start_sha256`、`publication_approval_sha256`、
`publication_plan_sha256`、`recovery_kind`、`schema_version`、`source_operation`、
`state`）で、compact UTF-8、`sort_keys=True`、`ensure_ascii=False`、`allow_nan=False`、
strict duplicate-key rejection、非標準JSON定数のrejectionを持ちます。loaderはexact modelと
canonical byte equalityを要求するため、semantically equivalentでもnoncanonicalな
whitespace/orderはrejectされ、digestはexact canonical bytesに対するdeterministic
SHA-256です。

append-only persistence helperはexclusive create → full canonical write → flush →
file fsync → safe close → parent-directory fsyncの順で永続化します。同一bytesはidempotentな
durable success、different/partial/noncanonical bytesはfixed conflictであり、
overwrite/truncate/delete/rename-over/repairは行いません。exclusive create後のuncertain
failure（write/flush/file-fsync/close/dir-fsync）はambiguousとなり、artifactは保持され
cleanupもretryもrewriteも行いません。後からexactなretained bytesはidempotentにacceptされ、
partial/different bytesはfail closedします。

Phase 293はPhase 292 orchestration、Phase 291、Phase 290 acquisition、Phase 288/287/285、
provider/transport/networkを呼ばず、Phase 289 intentやPhase 290 start markerを作らず、
lower claim/evidence/provider stateをinspectせず、recovery kindをlifecycle+start lineage
以外から推測せず、freshをresumeの実行へ変換せず、`authorize_resume_preparation`を実行許可と
みなしません。timestamp、random、UUID、ambient identityの生成や、environment/credential/
socket/subprocess access、automatic continuation/schedule/loop/parallelizeも行いません。
CLI/GUI変更もありません。将来のPhase 294はexactな`authorize_resume_preparation` evidenceを
消費して新しいexplicit resume lineageを準備できます。`stop`はterminalなno-action routeの
ままです。

## Phase 294: durable recovery-authorized resume preparation evidence

Phase 294は、Phase 293のdurable explicit recovery decisionの上に、append-onlyでimmutableな
**recovery-authorized resume-lineage preparation evidence** boundaryを1つ追加します。消費するのは
exactなPhase 293 decisionで`decision`が`authorize_resume_preparation`のものだけであり、その背後にある
Phase 292 lifecycle outcomeとPhase 290 startのprovenanceを完全に再検証したうえで、将来の明示的な
phaseが新しいresume lineageを準備してよいことだけをdurablyに記録します。Phase 294は実行しません。

```text
Phase 292 recovery_required
        ↓
Phase 293 authorize_resume_preparation
        ↓
Phase 294 durable resume-lineage preparation
        ↓
future explicit intent-construction phase
```

```text
Phase 293 stop → Phase 294 zero preparation / reject
```

authorization provenanceは決してlifecycle、start、caller argumentから推測されません。explicitな
recovery authorizationを持つのはexactなPhase 293 decisionだけであり、そのdigestがpreparation recordに
bindされます。`stop`はpreparationに到達せず、target mutationの前にrejectされます。

`prepared`が意味するのは次だけです。

> exactなPhase 293 `authorize_resume_preparation` decisionとそのexactなrecovery provenanceが検証され、
> 将来のtarget operation `resume`にdurably bindされた。

これは、resume intentが既に存在すること、resumeが開始されたこと、provider/transportが自動実行され
よいこと、Phase 290 acquisitionが自動でauthorizeされたこと、Phase 291/292を自動で呼んでよいこと、
freshをreplayしてよいこと、resumeが成功することを、いずれも意味しません。

Phase 294はまず、exactな`recovery_decision_path` / `lifecycle_outcome_path` / `start_path` /
`resume_preparation_path`のplatform `Path`、注入dependencyのcallable性、preparation targetの
parent/shape（mutationなし）をpreflightします。Phase 289 intent targetのcreate/readは行いません。
caller-suppliedのdecision/lifecycle/start object、recovery_kind、predecessor digest、target operation、
operation intent、approval objectはauthorityとして受け取りません。

preflight後、Phase 294はcallerのexactなpath identityでPhase 293 decisionをexactly once strict-loadし、
exact runtime typeとcross-field invariantを再検証し、`state`が`decided`であることを要求したうえで
`decision`が`authorize_resume_preparation`であることを要求します（`stop`はpreparation target mutation
前にrejectされ、他のfieldからtarget permissionを推測しません）。次にdecision digestをexactly once
計算し（lowercase 64-hex必須）、Phase 292 lifecycle outcomeをexactly once strict-loadしてexact runtime
typeとcross-field invariantを再検証し、`state`が`recovery_required`であることを要求して`completed`を
rejectし、legacy Phase 293 semanticsどおりにrecovery kindをlifecycleからderiveし（`result_kind` none +
`result_sha256` None → `already_acquired`、`resume` recovery_required reconciliation + exact result
digest → `reconciliation_mismatch`）、lifecycle digestをexactly once計算します。その後、computed
lifecycle digestがdecisionの`lifecycle_outcome_sha256`と一致し、approval/plan digest、operation、
derived recovery kindがdecisionと一致することを要求します。最後にPhase 290 startをexactly once
strict-loadし、start digestをexactly once計算したうえで、start digestがdecisionとlifecycleの
`operation_start_sha256`の両方に一致し、operation/approval/plan lineageがdecisionとlifecycleの両方に
一致することを要求します。Phase 289 intentのload、Phase 280 claim、Phase 282/284 evidence、provider
state、output、credentialのinspectは行いません。

preparationはvalidated durable provenanceだけから構築されます。`recovery_decision_sha256`、
`lifecycle_outcome_sha256`、`operation_start_sha256`はexactにcomputedなdigest、approval/plan digestは
exactにvalidatedなlineage、`source_operation`はexactにvalidatedなsource、`recovery_kind`はexactに
derived/validatedなkindであり、`target_operation`は常にexactに`resume`、`state`は常にexactに
`prepared`です。ambient valueは使いません。

canonical JSONはexactly 10 key（`lifecycle_outcome_sha256`、`operation_start_sha256`、
`publication_approval_sha256`、`publication_plan_sha256`、`recovery_decision_sha256`、
`recovery_kind`、`schema_version`、`source_operation`、`state`、`target_operation`）で、compact
UTF-8、`sort_keys=True`、`ensure_ascii=False`、`allow_nan=False`、strict duplicate-key rejection、
非標準JSON定数のrejectionを持ちます。loaderはexact modelとcanonical byte equalityを要求するため、
semantically equivalentでもnoncanonicalなwhitespace/orderはrejectされ、digestはexact canonical
bytesに対するdeterministic SHA-256です。

append-only persistence helperはexclusive create → full canonical write → flush → file fsync →
safe close → parent-directory fsyncの順で永続化します。同一bytesはidempotentなdurable success、
different/partial/noncanonical bytesはfixed conflictであり、overwrite/truncate/delete/rename-over/
repairは行いません。exclusive create後のuncertain failure（write/flush/file-fsync/close/dir-fsync）は
ambiguousとなり、artifactは保持されcleanupもretryもrewriteも行いません。後からexactなretained bytesは
idempotentにacceptされ、partial/different bytesはfail closedします。preparation targetが既に存在する
場合、Phase 294はsidecarを1回strict-loadし、exact runtime preparation modelとexactにconstructedな
recordの完全一致を要求し、一致すればそのloaded objectをそのまま（identityで）返します。差分が
あればfixed conflictとなり、既存bytesは変更されません。

Phase 294はPhase 293/292 orchestration、Phase 291、Phase 290 acquisition、Phase 288/287/285、
provider/transport/networkを呼ばず、Phase 289 intentや新しいPhase 290 start markerを作らず、
lower claim/evidence/provider stateをinspectせず、exactなPhase 293 decisionなしにlifecycle/startから
authorizationを推測せず、freshをreplayせず、resumeを実行せず、`prepared`を実行/start許可とみなしません。
predecessor/preparation artifactのoverwrite/delete/repair、dependency/persistenceのretry、timestamp/
random/UUID/ambient identityの生成、environment/credential/socket/subprocess access、automatic
continuation/schedule/loop/parallelizeも行いません。CLI/GUI変更もありません。knownなpredecessor error
（Phase 293 decision、Phase 292 lifecycle、Phase 290 startのloader/digest family）はexact object
identityのまま伝播し、unexpectedなdependency exceptionはdetail-safeな`dependency_error`にsanitize
されます。

future explicit phase（たとえばPhase 295）はこのexactなpreparation evidenceを使って、recovery-decision
provenanceを保持したまま新しいPhase 289 resume intentを構築/永続化できます。Phase 294自身はそれを
行いません。

## Phase 295: recovery-bound resume intent materialization

Phase 295は、Phase 294のdurableなrecovery resume preparationの上に、append-onlyでimmutableな
**recovery resume-intent provenance binding**を1つ追加し、そのbindingがauthorizeするexactな
Phase 289 `resume` operation intentをmaterializeします。Phase 295はPhase 290 start markerを
取得せず、resumeを実行せず、reconciliationもprovider呼び出しも行いません。

新しいrecovery authorityは「durableなPhase 294 preparation + Phase 295 binding」です。
Phase 289 resume-intent digest **単独**はrecovery authorizationではありません。

```text
Phase 294 prepared
    |
Phase 295 binding authorized
    |
exact Phase 289 resume intent materialized
    |
future Phase 296 explicit start acquisition
```

### identity rule

Phase 289 `ExternalPublicationOperationIntent`はapproval digest / plan digest / operationだけを
保持するため、同じapproval/planを使った過去のresume intentとbyte/digest単位で同一になり得ます。
したがってPhase 295はPhase 289 intent digest単独を新しいrecovery authorizationの証明として
扱いません。recovery provenanceは、exactなPhase 294 preparation digestと期待されるPhase 289
intent digestを含む**別個のPhase 295 durable binding**が担います。

### crash-ordering rule

Phase 295はPhase 289 intentのmaterialize/acceptより**先に**bindingをpersist/resolveします。
bindingは「このexactなrecovery preparationが、このexactな期待resume intent identityを
authorizeする」ことだけを意味するため、intentより先に安全に存在できます。binding durabilityの
後・intent durabilityの前にcrashした場合、後続のPhase 295 invocationはexactなbindingから
recoverしてintentをmaterialize/verifyできます。intent persistenceがambiguousな場合もbindingが
authoritativeなまま残り、後からexactにretainされたintent bytesはacceptされ、partial/different
bytesはfail closedします。

### binding model

bindingはexactなfrozen modelで、`schema_version` / `resume_preparation_sha256` /
`recovery_decision_sha256` / `publication_approval_sha256` / `publication_plan_sha256` /
`operation_intent_sha256` / `source_operation` / `recovery_kind` / `operation` / `state`を持ちます。
digestはexactなbuiltin lowercase 64-hex、enumはexactなbuiltin文字列、`operation`はexactly
`resume`、`state`はexactly `authorized`、`recovery_kind == "reconciliation_mismatch"`は
`source_operation == "resume"`を要求します。timestamp、generated UUID、randomness、hostname、
PID、caller path、credential、provider/transport object、exception text、mutable runtime valueは
保持しません。

`authorized`が意味するのは「このexactなPhase 294 preparationが、exactな期待Phase 289 resume
intent identityをauthorizeする」ことだけです。intentがdurably materialize済みであること、startを
取得済みであること、provider実行がauthorize済みであること、reconciliationが完了していること、
freshがreplay可能であることは意味しません。bindingはcrash後にintent materialization前の状態で
単独にdurably存在し得るため、この区別が重要です。

### canonical helpers

public helperとして
`serialize_external_publication_recovery_resume_intent_binding_canonical`、
`external_publication_recovery_resume_intent_binding_canonical_bytes`、
`external_publication_recovery_resume_intent_binding_digest`、
`load_external_publication_recovery_resume_intent_binding`、
`persist_external_publication_recovery_resume_intent_binding`を追加します。canonical JSONは
exactly 10 keysで、compact UTF-8、sorted keys、`ensure_ascii=False`、`allow_nan=False`、
duplicate-key rejection、non-standard constant rejection、exact key set、strict reconstruction、
canonical byte equality、deterministic SHA-256を使います。semantically equivalentでも
noncanonicalなbytesは拒否されます。

### persistence

binding persistenceは既存のappend-only exclusive durable patternに従います。exactなconcrete
platform `Path`、parentが存在しdirectoryであること、symlink/directory/non-regular targetの拒否、
作成前のcanonical bytes、exclusive create、full write、flush、file fsync、close、
parent-directory fsyncです。existing exact bytesはidempotent success、existing
different/partial/noncanonical bytesはfixed conflictで変更されません。exclusive creation後の
write/short-write/flush/file-fsync/close/dir-fsyncでのuncertaintyはambiguousとして分類し、
artifactをretainし、cleanup・retry・rewriteを行いません。

### Phase 289 intent construction rule

Phase 295は期待される`ExternalPublicationOperationIntent`を、strict-loadしたPhase 294
preparationの`publication_approval_sha256` / `publication_plan_sha256`とexactな
`operation="resume"`だけから構築します。Phase 295はapproval objectをauthorityとして受け取る
権限がないため、`build_external_publication_operation_intent()`を要求も呼び出しもしません。
Phase 289のmodelとschemaは変更しません。

### public orchestration API

`materialize_and_bind_external_publication_recovery_resume_intent`はkeyword-onlyで
`resume_preparation_path` / `resume_intent_binding_path` / `resume_intent_path`と、exactなpublic
helperをdefaultに持つinjected dependency
（`preparation_loader` / `preparation_digest_function` / `intent_loader` /
`intent_digest_function` / `intent_persist_function`）を取ります。caller-suppliedのpreparation
object、preparation digest、approval object、operation intent object、intent digest、recovery
decision object、source operation、recovery kind、target operationはauthorityとして受け取りません。

### required ordering

preflightで3つのpathがexactなconcrete platform `Path`でありpairwise distinctであること、全
injected dependencyがcallableであること、binding targetとintent targetのparent/shapeを
mutationなしで検証します。start-marker pathはこのAPIに存在せず、provider/network/credential
accessもありません。既存のregular intent targetはそれ自体ではauthorizationではなく、exactな
Phase 295 bindingがdurably resolveされた後にのみacceptされ得ます。

次にPhase 294 preparationをcallerのexactなpath identityでexactly once strict-loadし、exactな
runtime modelとローカルで再検証した全field/cross-field invariant（exact schema、exact lowercase
digest、exact `fresh|resume`、exact `already_acquired|reconciliation_mismatch`、
`reconciliation_mismatch`は`source_operation == "resume"`を要求、`target_operation`はexactly
`resume`、`state`はexactly `prepared`）を要求します。preparation digestはexactなloaded object
identityでexactly once計算し、exactなlowercase 64-hexを要求します。knownなPhase 294 errorは
exact object identityのまま伝播し、unexpectedなhelper errorはfixed/detail-safeな
`dependency_error`になります。

続いて期待されるPhase 289 resume intentをpreparationのapproval/plan lineageとexactなoperation
`resume`だけから構築し、exactなmodel/schema/approval digest/plan digest/operationをローカルで
再検証します。intent digestはinjected public helperをexactなconstructed object identityで
exactly once呼び出して計算します。

bindingはcomputedなPhase 294 preparation digest、preparationのrecovery decision digest、
approval/plan digest、computedな期待Phase 289 intent digest、preparationのsource operation、
recovery kind、operation `resume`、state `authorized`だけから構築し、ambient valueを使いません。

**bindingのresolve/persistはPhase 289 intentのload/persist attemptより先に完了します。** binding
targetが存在する場合、exactly once strict-loadし、exact runtime bindingとexact equalityを要求
します。差分はfixed conflictでbindingは変更されません。binding targetがabsentの場合はconstructed
bindingをexactly once persistし、retryしません。binding persistenceがambiguous/failedの場合は
即座に停止し、Phase 289 intent loader/persistのcall countはzeroで、intent mutationもありません。

binding durabilityが確立した後にのみPhase 289 resume intentをmaterialize/verifyします。intent
targetが存在する場合はexactly once strict-loadし、exact modelとexact expected intent equality、
operation `resume`を要求し、mismatch/corruptはfail closedで、overwrite/repair/deleteしません。
absentの場合は`intent_persist_function`をexactなpath/object identityでexactly once呼び出し、
retryしません。knownなPhase 289 errorはexact object identityのまま伝播し、unexpected errorは
detail-safeにsanitizeされます。intent materializationが後で失敗/ambiguousになってもPhase 295
bindingはretainedされます。

returnはexactにresolvedしたbinding objectです。existing exact bindingの場合はexactな
loader-returned object identity、newly persisted bindingの場合はexactなconstructed binding
identityを返します。Phase 289 intentがstrict-loadされてexactかつequalであるか、durably
persistedされた後にのみ成功を返し、bindingだけ存在してintentのexact/durableがこのinvocationで
証明できない場合にsuccessを返しません。

### forbidden behavior

Phase 295はPhase 289 intent単独をrecovery authorizationとして扱わず、binding durability確立前に
intentを作成/persistせず、Phase 294/293/292/291 orchestrationを呼ばず、Phase 290 acquisitionや
start marker作成を行わず、Phase 288/287/285、provider/transport/networkを呼ばず、approval/plan/
provider/target/runtime objectをauthorityとして受け取らず、freshをreplayせず、resumeを実行せず、
Phase 280/282/284のlower evidence/provider stateをinspectせず、binding/preparation/intent
artifactのoverwrite/delete/repairやpersistenceのretryを行わず、timestamp/random/UUID/ambient
identityを生成せず、environment/credentials/socket/subprocessにaccessせず、auto-continue/
schedule/loop/parallelizeせず、CLI/GUIを追加/変更しません。

### next phase

future Phase 296は、**新しいexplicit start marker path**を取得する前にexactなPhase 295 bindingと
exactなbound Phase 289 intentを要求しなければならず、intent単独からauthorityを推測しては
なりません。Phase 295自身はPhase 290 start markerを作成せず、実行/reconciliation/provider作業を
行わず、fresh no-replayを維持します。

## Phase 296: recovery-bound resume start authorization evidence

Phase 296は、Phase 295のdurableなrecovery resume intent bindingとそのexactなbound Phase 289
`resume` operation intentの上に、append-onlyでimmutableな**recovery resume start authorization
evidence**を追加します。このrecordは、将来のsame-invocationな実行境界が、exactなexpected Phase 290
resume start identityを、将来のcanonical start target経由でstart acquisitionを試みることを
authorizeするだけです。

```text
Phase 295 durable binding + exact bound Phase 289 resume intent
        |
        v
Phase 296 strict provenance validation
        |
        v
durable recovery resume start authorization
        |
        v
future Phase 297 only:
validate authorization -> derive one deterministic start target
-> Phase 291 owns Phase 290 acquisition + execution handoff in same invocation
```

Phase 296はPhase 290 acquisitionを呼ばず、start markerを作成・load・persistせず、
caller-supplied `start_path`を受け取りません。authorization targetが存在する場合はstrict-load
されたexactなrecordのみをidentityで返し、差分はfixed conflictとして既存bytesを変更しません。

`state="authorized"`が意味するのは次の一点だけです。

> exactなPhase 295 recovery bindingとexactなbound Phase 289 resume intentが、将来の1回のattempt
> に対して、exactなexpected Phase 290 resume start identityを将来のcanonical start target経由で
> acquireすることをauthorizeする。

`authorized`は`acquired`ではありません。start markerの存在、acquisitionの成功、`acquired`の
reconstruction、future same-invocation handoff外でのprovider実行許可、resume実行、
reconciliation完了、fresh replay許可のいずれも意味しません。

### canonical authorization target contract

Phase 296のauthorization target自体が、exactなPhase 295 bindingに対してcanonicalでなければ
なりません。`resume_intent_binding_path`だけでは不十分で、同じbindingを別parentのauthorization
pathにmaterializeできると、future Phase 297で複数のstart targetを作れてしまいます。

Phase 296はbindingをstrict-load・local revalidate・digestした後、**intent loaderを呼ぶ前**に、
callerの`resume_start_authorization_path`が次のderived canonical targetとexactに一致することのみを
許可します。

```text
resume_intent_binding_path.parent
/
("external-publication-recovery-resume-start-authorization-"
 + binding_digest
 + ".json")
```

filename違い／parent違いは`authorization_path` classificationでfail closedし、intent
loader/digest、start digest、persistenceはいずれもzero-callで、artifactはunchangedです。
normalization/resolve/symlink-following equivalenceは行わず、exactなconcrete `Path` equalityのみを
受け付け、directory作成やartifactのrelocate/copyも行いません。これにより同じexact bindingから
2つの有効なPhase 296 authorizationを作ることはできません。

### future deterministic start target contract

Phase 296はstart markerを作成しませんが、future Phase 297のruleを固定します。authoritativeな
namespaceはexactなPhase 295 binding parentです。exactな`resume_intent_binding_path`、exactな
loaded Phase 296 authorization、exactなcomputed Phase 296 authorization digestが与えられたとき、
future Phase 297が導出できる唯一のstart targetは次です。

```text
resume_intent_binding_path.parent
/
("external-publication-recovery-resume-start-"
 + authorization_digest
 + ".json")
```

Phase 296はauthorization sidecar自体をexactなbinding parent内のcanonical pathに固定するため、
`resume_start_authorization_path.parent`はvalidation後にはequivalentになりますが、contractと
helper/testsはnamespace authorityを曖昧にしないため`resume_intent_binding_path.parent`を明示的に
使います。これにより1つのPhase 296 authorizationは、そのauthoritativeなbinding parent内の1つの
canonical start targetにのみ対応します。future Phase 297は任意のcaller-supplied start pathを
受け取ってはなりません。

### public API

`authorize_and_persist_external_publication_recovery_resume_start`はkeyword-onlyで、3つのexactな
concrete platform `Path`（`resume_intent_binding_path`、`resume_intent_path`、
`resume_start_authorization_path`）と、exactなpublic helperをdefaultに持つinjected callable
（`binding_loader`、`binding_digest_function`、`intent_loader`、`intent_digest_function`、
`start_digest_function`）を取ります。caller-suppliedなbinding object/digest、intent
object/digest、approval object、preparation/decision object、expected start object/digest、
source operation、recovery kind、start pathのいずれもauthorityとして受け取りません。

Phase 296はbindingを**必ずintentより先に**strict-load・local revalidate・digestし、intent単独を
recovery authorityとして扱いません。expected Phase 290 start objectはexactなvalidated lineage
からのみin-memoryで構築され、exactなpublic Phase 290 `external_publication_operation_start_digest`
helperで1回だけdigestされます。Phase 296はPhase 290 start objectをpersist・load・acquireせず、
Phase 288/291/292/293/294/295 orchestrationを呼ばず、provider/transport/network作業を行いません。

persistenceはestablished append-only exclusive patternに従います。canonical bytesを先に
derive/validateし、new targetはexclusive create → full write → flush → file fsync → safe close →
parent-directory fsyncの全durability step成功後にのみ成功とします。exactに同一の既存bytesは
idempotentなdurable success、異なる/partial/noncanonicalなbytesはfixed conflictとして既存bytesを
変更しません。exclusive creation後のwrite/short-write/flush/file-fsync/close/dir-fsyncでの不確実性は
ambiguousとし、artifactをretainしてcleanup・retry・rewriteを行いません。

### next phase

future Phase 297は、Phase 296 authorizationをvalidateし、上記のdeterministic start targetを導出
したうえで、exactなPhase 291 handoffを呼び、Phase 290 acquisitionとPhase 288 execution handoffを
**同一invocation内**で完結させなければなりません。Phase 290 `acquired`はpersist/reconstructして
後続実行authorityとして扱ってはならず、intent単独は依然として不十分なrecovery authorityであり、
fresh no-replayは維持されます。

## Phase 297: recovery-bound resume start handoff

Phase 297は、Phase 296のdurableなrecovery resume start authorizationを消費し、exactなexpected
Phase 290 resume start targetを導出したうえで、既存のpublicなPhase 291 same-invocation handoffを
**exactly once**呼ぶ、strictなrecovery resume start handoff boundaryです。Phase 297はPhase 290の
start fenceを越える最初のrecovery専用境界ですが、越え方はPhase 291への委譲だけです。

```text
Phase 296 durable start authorization
        |
Phase 297 strict recovery resume handoff
        |
canonical start path
        |
Phase 291
  |-- Phase290 acquired ----------> Phase288 resume -> reconciliation
  '-- Phase290 already_acquired --> stop
```

Phase 297はauthorization pathとstart pathの両方を内部導出し、callerはどちらも渡しません。
callerが渡せるのはexactなPhase 295 binding path、exactなbound Phase 289 resume intent path、
そして1つのexactなruntime `ExternalPublicationResumeOperationRequest`だけです。

### 導出規則

binding load/validation/digest後、authorization pathはexactなbinding path parentとexactなcomputed
binding digestから導出します。

```text
resume_intent_binding_path.parent
/
("external-publication-recovery-resume-start-authorization-"
 + binding_digest
 + ".json")
```

Phase 296 authorizationのstrict-load/local revalidation/digest後、start pathは同じexactなbinding
path parentとexactなcomputed authorization digestから導出します。

```text
resume_intent_binding_path.parent
/
("external-publication-recovery-resume-start-"
 + authorization_digest
 + ".json")
```

どちらもnormalization、`.resolve()`、`realpath`、`normpath`、`samefile`、symlink-following
等価性、ambientなdirectory discoveryを使いません。Phase 297はこのmarker自体を作成せず、exactな
derived `Path` objectをPhase 291へ渡すだけです。acquireするのはPhase 291経由のPhase 290だけです。

### validation順序

Phase 297はexact Phase 295 binding → exact Phase 296 authorization → exact Phase 289 resume
intent → exact runtime resume requestの順に検証します。authorizationはbinding lineage（binding
digest、preparation/decision/intent/approval/plan digest、source operation、recovery kind、
operation、state）と照合され、intentはbindingとauthorizationの両方に照合されます。expected
Phase 290 start identityはvalidatedなintent lineageからのみin-memoryで再構築し、そのdigestが
Phase 296の`expected_operation_start_sha256`と一致することを要求します。request側ではexactな
approval typeとmetadata、approval digest、plan digestをbinding・authorization・intentと照合します。
不一致はintent load前またはPhase 291前にfail closedとなります。

### Phase 291委譲

Phase 297は`intent_path` / derived `start_path` / exact caller `request`だけをkeywordで渡し、
Phase 291をexactly once呼びます。`phase290_function` / `phase288_function`は注入せず、Phase 291に
自身のdefault Phase 290/288依存を所有させます。retry、fallback、automatic continuationは行いません。

戻り値はexactな`ExternalPublicationOperationStartAcquisition`（statusが`already_acquired`の場合のみ）
またはexactな`ExternalPublicationExecutionReconciliation`だけで、どちらもPhase 291が返した同一object
identityをそのまま返します。Phase 291が`acquired`を返すことはなく（新規acquire時はPhase 288へdispatch
してresume reconciliationを返す）、`acquired`のacquisitionやfresh execution result、subclass、
lookalike、malformed modelは拒否します。

### crash semantics

Phase 297は`acquired`を再構築せず、Phase 290を直接呼ばず、Phase 291を1回だけ呼びます。過去の
invocationがmarkerをacquireした後にresume resultを返す前にcrashした場合、後続のPhase 297 invocationは
Phase 291の`already_acquired` stop resultを受け取ります。`already_acquired`は実行authorityではなく
stop resultであり、新規実行許可として再解釈してはなりません。markerのcleanup/repair/retryは行いません。

Phase 297は自身のdurable outcome sidecarを追加せず、Phase 296/295/294/293/292 orchestration、
Phase 290/288/287/285の直接呼び出し、fresh requestの受理・dispatch、transport/provider objectの受理、
fresh-vs-resume推測、artifactのoverwrite/delete/repair、time/random/UUID/environment/socket/
subprocessの使用、auto-continue/schedule/loop/parallelize、CLI/GUI変更を行いません。realな
provider/network/credential/paid API callは0です。

### public API

`run_external_publication_recovery_resume_start_handoff`はkeyword-onlyで、exactなconcrete platform
`Path`（`resume_intent_binding_path`、`resume_intent_path`）と1つのexactなruntime
`ExternalPublicationResumeOperationRequest`、およびexactなpublic helperをdefaultに持つinjected
callable（`binding_loader`、`binding_digest_function`、`authorization_loader`、
`authorization_digest_function`、`intent_loader`、`intent_digest_function`、
`approval_digest_function`、`start_digest_function`、`phase291_function`）を取ります。
caller-suppliedなauthorization path、start path、binding/authorization/intent/start objectやdigest、
source operation、recovery kind、operation、acquisition result、Phase 290/288 functionのいずれも
authorityとして受け取りません。

### next phase

future Phase 298は、Phase 297のnormal returnをpersist/classifyしつつ、Phase 296 authorization
provenanceを保持しなければなりません。Phase 298は本Issueでは着手しません。

## Phase 298: durable recovery resume outcome evidence

Phase 298は、Phase 297の上にappend-onlyでimmutableな**durable recovery resume outcome**を追加
します。Phase 296のrecovery resume start authorization lineageを消費し、canonicalなPhase 290
start targetとcanonicalなPhase 298 outcome targetを内部導出し、existing exact outcomeをzero-Phase297の
idempotent fast pathとして使い、outcomeが無い場合のみpublic Phase 297 handoffをexactly once呼び、
normal returnのみを分類し、exactなdurable Phase 290 start markerをstrict-load/bindして、Phase 296
authorization provenanceを保持するoutcomeを1つpersistします。

```text
Phase 296 authorization
        |
Phase 297 same-invocation resume handoff
        |
Phase 298 durable recovery resume outcome
   |-- completed / reconciliation
   |-- recovery_required / reconciliation
   '-- recovery_required / none
```

Phase 298はPhase 292のgenericな`ExternalPublicationOperationLifecycleOutcome`をcanonical artifact
として再利用しません。Phase 292 modelはoperation start digest、approval digest、plan digest、
operation、state、result kind/digestを持ちますが、Phase 296のrecovery-start-authorization provenanceを
持たないためです。Phase 298はpublicなmodel/digest規約は再利用してよいものの、Phase 292
orchestrationを呼びません。

### outcome model

`ExternalPublicationRecoveryResumeOutcome`はfrozenでsecret-freeなmodelで、
`resume_start_authorization_sha256`（Phase 296 authorization digest全体へのcommit）、
`resume_intent_binding_sha256`、`operation_intent_sha256`、`operation_start_sha256`（durable
Phase 290 start marker digest）、approval/plan digest、継承した`source_operation` / `recovery_kind`、
`operation="resume"`、classified `state` / `result_kind` / `result_sha256`を持ちます。

許可されるstate/result組み合わせはexactly 3つです。

```text
completed          / reconciliation / exact digest  -- Phase 297 matched
recovery_required  / reconciliation / exact digest  -- Phase 297 lineage_mismatch
recovery_required  / none           / None          -- Phase 297 already_acquired stop
```

`completed + none`、`none + non-None digest`、`reconciliation + None`、その他のstate/result kindは
拒否されます。`recovery_kind == "reconciliation_mismatch"`は`source_operation == "resume"`を要求します。

`already_acquired`は過去の成功を意味しません。Phase 298は不確実性として
`recovery_required / none / None`を記録し、start markerの存在から失われたreconciliation resultを
推測しません。

### canonical paths

binding load/validation/digest後、authorization pathはexactなbinding parent + binding digestから
導出します。authorization strict-load/validation/digest後、start pathとoutcome pathは同じexactな
binding parent + authorization digestから導出します。

```text
external-publication-recovery-resume-start-authorization-<binding digest>.json
external-publication-recovery-resume-start-<authorization digest>.json
external-publication-recovery-resume-outcome-<authorization digest>.json
```

callerはこの3つのpathをいずれも渡せません。normalization、`.resolve()`、`realpath`、`normpath`、
`abspath`、`samefile`、symlink-following等価性、ambient-directory discoveryは使いません。exactな
binding parentがauthoritativeなnamespaceです。outcome pathはbinding parent + authorization digestで
一意に固定されます。

### validation順序

exact Phase 295 binding → Phase 296 authorization → Phase 289 intent → expected start → runtime
requestの順に検証します。bindingとauthorizationはそれぞれexact load exactly once、local
revalidation、exact object identityでのdigest exactly onceを行います。authorizationはbinding lineageの
全項目（binding/preparation/decision/intent/approval/plan digest、source operation、recovery kind、
resume operation、authorized state）と照合されます。intentはbindingとauthorizationの両方に照合され、
expected Phase 290 start identityはin-memoryで再構築してdigestがauthorization
`expected_operation_start_sha256`と一致することを要求します。request approval/plan lineageも
binding・authorization・intentと照合されます。outcomeが無い場合のみpublic Phase 297をexactly once
呼び、Phase 297へ渡すのは`resume_intent_binding_path` / `resume_intent_path` / exact caller `request`
のみです。Phase 291 / 290 / 288 / 287 / 285やPhase 292 orchestrationを直接呼びません。

Phase 297のnormal return後、canonicalなdurable start markerをstrict-load・local validation・
digest exactly onceし、digestがauthorization.expected start digestと一致し、startのintent/approval/
plan/operationがauthorization lineageと一致することを要求します。start markerがabsent/malformed/
mismatchならfail closedとなりPhase 298 outcomeは作成されません。

### 分類

Phase 297 returnはexactな`ExternalPublicationOperationStartAcquisition`（statusがbuiltin `str`で
exactly `already_acquired`、embedded startがstrict-loaded durable startとexactly equal）またはexactな
`ExternalPublicationExecutionReconciliation`（statusが`matched`または`lineage_mismatch`）のみ受理
します。`acquired`、fresh execution result、subclass、lookalike、malformed model、arbitrary objectは
拒否します。reconciliation digestはexact Phase 297-returned object identityでexactly once計算します。

### existing outcome fast path

canonical outcome targetが既に存在する場合、Phase 297 call countは**0**です。exact Phase 298 outcomeを
exactly once strict-loadし、binding + authorization + intent + request lineageと照合し、canonical
Phase 290 start markerをstrict-load・digestしてauthorization expected start digestとoutcome
`operation_start_sha256`の両方に一致することを要求し、loader-returned object identityをそのまま返します。
fast pathではreconciliation digest helperを呼びません。malformed/conflicting/noncanonical outcome、
あるいはmissing/mismatched startはPhase 297 zero-callのままfail closedとなります。

### persistenceとcrash semantics

persistenceはestablished append-only exclusive patternに従います。new targetはexclusive create →
full write → flush → file fsync → safe close → parent-directory fsyncの全durability step成功後にのみ
成功とします。exactに同一の既存bytesはidempotentなdurable success、異なる/partial/noncanonicalな
bytesはfixed conflictとして既存bytesを変更しません。exclusive creation後の不確実性はambiguousとし、
artifactをretainしてcleanup・retry・rewrite・deleteを行いません。後続のexact artifactはfast pathで
受理され、partial/differentなretained artifactはfail closedとなります。

Phase 297がnormal returnした後にPhase 298 outcomeのpersist前にcrashした場合、後続のPhase 298
invocationはoutcomeが無いためlineageを再検証してPhase 297を1回呼びます。canonical start markerが既に
存在するためPhase 297/291は`already_acquired`を返しPhase 288 executionをreplayしません。Phase 298は
`recovery_required / none / None`をpersistします。これは意図的であり、失われたprior reconciliation
resultをstart markerの存在から推測してはなりません。

Phase 297が例外をraiseした場合、Phase 298 outcomeはpersistされず、retryも行いません。known predecessor
errorは同一object identityで伝播し、unexpected dependency errorはdetail-safeな`dependency_error`へ
sanitizeされます。

Phase 298はauthorization/start/outcome pathをcallerから受け取らず、Phase 296/295 orchestration、
Phase 294/293/292 orchestration、Phase 291/290/288/287/285の直接呼び出し、Phase 297へのlower
dependency注入、fresh requestの受理、fresh-vs-resume推測、already-acquired operationのreplay、
`acquired`のreconstruct/persist、`already_acquired`の成功扱い、失われたreconciliation resultの推測、
binding/auth/intent/start/outcome artifactのoverwrite/delete/repair、Phase 297やpersistenceのretry、
time/random/UUID/environment/socket/subprocessの使用、auto-continue/schedule/loop/parallelize、
CLI/GUI変更を行いません。realなprovider/network/credential/paid API callは0です。

### public API

`run_and_persist_external_publication_recovery_resume_outcome`はkeyword-onlyで、exactなconcrete
platform `Path`（`resume_intent_binding_path`、`resume_intent_path`）と1つのexactなruntime
`ExternalPublicationResumeOperationRequest`、およびexactなpublic helperをdefaultに持つinjected
callable（`binding_loader`、`binding_digest_function`、`authorization_loader`、
`authorization_digest_function`、`intent_loader`、`intent_digest_function`、
`approval_digest_function`、`expected_start_digest_function`、`start_loader`、
`start_digest_function`、`reconciliation_digest_function`、`phase297_function`）を取ります。
`expected_start_digest_function`はPhase 297前のin-memory expected start identityを、
`start_digest_function`はPhase 297後／outcome用のstrict-loaded durable markerをdigestするという
異なるsemantic roleを持ちますが、defaultは両方とも同一のexactなpublic helperを指します。

### next phase

future Phase 299は、`completed`をterminal stop/closureへ、`recovery_required/reconciliation`を
explicitな新recovery decision pathへ、`recovery_required/none`をexplicitなhuman recovery
decision pathへrouteすることがあります。Phase 299のrouting boundaryは以下で定義します。

## Phase 299: recovery resume outcome routing boundary

Phase 299は、Phase 298のdurable recovery resume outcomeをstrictに読み取り、current recovery
reasonをrouteする**read-only boundary**です。callerが渡すのはexactなPhase 295 binding pathだけであり、
Phase 296 authorization path、Phase 298 outcome path、Phase 290 start pathはbinding parentとcomputed
digestから内部でcanonicalに導出します。pathのnormalization、`resolve()`、`realpath`、`normpath`、
`abspath`、`samefile`、symlink-following equivalence、ambient directory discoveryは行いません。

```text
Phase 298 durable outcome
        |
Phase 299 read-only routing
   |-- completed -----------------> terminal exact outcome
   |-- recovery_required/recon ---> decision required (current mismatch)
   '-- recovery_required/none ----> decision required (current already-acquired)
```

Phase 298の`recovery_kind`は、resume attemptをauthorizeしたprevious-cycle provenanceです。新しい
current decisionのreasonとは限らないため、Phase 299は`previous_recovery_kind`としてその値を保持し、
current `recovery_kind`をPhase 298の現在のstate/resultからのみ導出します。したがって
`recovery_required / reconciliation`は常に`reconciliation_mismatch`、`recovery_required / none`は
常に`already_acquired`です。previous valueをcurrent valueへコピーしません。

Phase 299はbinding、authorization、outcome、startをそれぞれexactly once strict-loadし、loader-returned
objectを独立にpublic model reconstructionしてlocal revalidateします。各digest helperも必要な段階で
exactly once、exact object identityに対して呼びます。authorization、outcome、startのcomplete lineage
を検証し、lineage mismatch、malformed digest、subclass、lookalike、forged exact modelはfail closed
します。authoritativeなPhase 295/296/298/290 errorはexact object identityで伝播し、unexpected
exceptionはfixed detail-safeなrouting errorへsanitizeします。

`completed / reconciliation`ではoutcome digest helperを呼ばず、loaderが返したPhase 298 outcome objectを
同一identityのままterminal returnします。recovery routeでは一度だけoutcome digestを計算し、
`ExternalPublicationRecoveryResumeDecisionRequired`をin-memoryで構築します。このmodelはfrozenで、
完全なlowercase SHA-256 digest、previous/current recovery kind、state/result couplingだけを持ち、path、
provider、transport、credential、runtime request、operator metadata、timestamp、UUID、randomness、
hostname、PID、exception textを持ちません。Phase 299ではこのdecision-required modelをpersistしません。

Phase 299はPhase 298/297/293/291以下のorchestrationを呼ばず、execution、replay、retry、fallback、
automatic continuation、scheduler、loop、parallelizationを行いません。CLI/GUI変更はなく、real provider、
network、credential、paid API callは0です。将来のPhase 300は、exact Phase 298 outcome digest、durable
start digest、current Phase 299 recovery kindにboundした明示的operator decisionをpersistできますが、
Phase 293 generic Phase 292-bound decision recordをauthorityとして再利用してはなりません。

## Phase 300: durable recovery-resume operator decision

Phase 300は、Phase 299のread-only routeの上に、recovery-resume専用のappend-onlyでimmutableな
**explicit operator decision** boundaryを追加します。callerが渡せるのは、exactなPhase 295
`resume_intent_binding_path`、`decision`（`stop` または `authorize_resume_preparation`）、
`decided_by`、`decision_id`だけです。Phase 299の`DecisionRequired`、Phase 298 outcome、
authorization、binding digest、start、approval、plan、result、decision pathはcallerから受け取りません。

```text
Phase 299
   |
   +-- exact Phase 298 completed outcome
   |      -> decision_not_required / zero persistence
   |
   '-- exact DecisionRequired
          |
          +-- stop
          |      -> durable Phase 300 decision
          |
          '-- authorize_resume_preparation
                 -> durable Phase 300 decision
                 -> future preparation authorization only
```

Phase 300はpublic Phase 299 `route_external_publication_recovery_resume_outcome`をexactly once、
`resume_intent_binding_path`だけで呼びます。lower dependency overrideは渡しません。Phase 299が返す
completed Phase 298 outcomeはpublic model reconstructionでlocal revalidateした後、固定分類
`decision_not_required`としてrejectし、digest計算・decision construction・decision persistenceを
行いません。exactな`ExternalPublicationRecoveryResumeDecisionRequired`だけを受理し、同じpublic
modelを再構築してlocal revalidateします。Phase 300はcurrent recovery kindを再計算せず、Phase 299が
導出したcurrent valueをauthorityとしてそのまま保持します。したがって、previous
`already_acquired` → current `reconciliation_mismatch` と、previous `reconciliation_mismatch` →
current `already_acquired` の両方をdurable decisionに保持できます。

durable decisionは、exactなPhase 298 outcome digest、Phase 296 start-authorization digest、Phase 295
binding digest、operation-intent digest、durable Phase 290 start digest、publication approval/plan digest、
previous/current recovery kind、current result kind/digest、operator choice/metadataを保持します。
canonical targetはbinding parentとPhase 298 outcome digestから内部で
`external-publication-recovery-resume-decision-<outcome-digest>.json`として導出され、callerは上書き
できません。従って同じPhase 298 outcomeに対するdecisionはone-shotです。同じdecisionの再実行は
existing canonical bytesをstrict-loadしてloader-returned objectをidentityで返し、decision、
`decided_by`、`decision_id`のいずれかが変わればfixed conflictとして既存bytesを変更しません。

decision modelはexact runtime typeのfrozen dataclassで、canonical JSONはexactly 18 keys、compact UTF-8、
sorted keys、`ensure_ascii=False`、`allow_nan=False`、duplicate-key/non-standard-constant rejection、
strict reconstruction、canonical byte equality、deterministic SHA-256を持ちます。operator metadataは
空文字、前後空白、Unicode `Cc`/`Cs`、256文字超過、subclassを拒否します。modelとsidecarはpath、
provider、transport、credential、timestamp、UUID、randomness、hostname、PID、exception textを保持しません。

persistenceはexclusive create → full write → flush → file fsync → safe close → parent-directory fsyncの
順です。既存の同一bytesはdurable idempotent success、different/partial/noncanonical bytesはfixed
conflictで、overwrite、delete、cleanup、rewrite、retryを行いません。exclusive create後のwrite、
flush、file-fsync、close、parent-fsyncの不確実な失敗は`ambiguous`としてartifactを保持します。

`stop`はterminal no-action decisionです。`authorize_resume_preparation`は将来のphaseがこのexactな
Phase 300 decisionにbindした新しいpreparation lineageを作ることだけをauthorizeし、execution許可、
intent materialization、start acquisition、provider call、reconciliation、automatic continuationを
意味しません。Phase 300自身はpreparation、intent、start、executionを行わず、Phase 293 generic decision
を再利用しません。Phase 298/297/294/293/291/290 acquisition/288以下を直接呼ばず、CLI/GUI変更もなく、
real provider/network/credential/paid API callは0です。future Phase 301はexactな
`authorize_resume_preparation`だけをconsumeし、`stop`では停止しなければなりません。

## Phase 301: recovery-resume decision-bound preparation

Phase 301 adds one durable, append-only preparation boundary above the exact
Phase 300 recovery-resume operator decision. Its caller supplies only the
exact Phase 295 `resume_intent_binding_path`; Phase 299 is called exactly once
to recover the canonical current recovery provenance, and the Phase 300
decision path is derived internally from the Phase 299 outcome digest.

```text
Phase 298 durable outcome
        |
Phase 299 current recovery routing
        |
Phase 300 durable operator decision
        |
        +-- stop ------------------------> terminal / zero Phase 301 persistence
        |
        '-- authorize_resume_preparation
                     |
                     v
Phase 301 decision-bound preparation
                     |
                     '-- future lineage materialization only
```

Phase 301 accepts only an exact Phase 299 `DecisionRequired` result after local
public-model reconstruction. A completed Phase 298 route is
`preparation_not_required` and performs zero Phase 300 decision-loader calls,
decision-digest calls, or Phase 301 persistence. A Phase 300 `stop` decision is
`preparation_not_authorized` and performs zero decision-digest calls and zero
Phase 301 persistence. Only the exact
`authorize_resume_preparation` decision is accepted.

The complete Phase 299 → Phase 300 provenance is compared field by field
before the Phase 300 decision digest is computed. The digest is computed
exactly once from the original strict-loader-returned Phase 300 object and is
bound directly to the new preparation. Phase 301 never recalculates the
current recovery kind: both `already_acquired` → `reconciliation_mismatch`
and `reconciliation_mismatch` → `already_acquired` remain authoritative.

The Phase 301 model and canonical JSON contain exactly seventeen fields and no
raw paths, provider/transport objects, credentials, timestamps, UUIDs,
randomness, hostnames, PIDs, or exception text. Persistence uses exclusive
create, full write, flush, file fsync, safe close, and parent-directory fsync.
Identical existing canonical bytes are idempotent and return the strict-loaded
object identity; partial, noncanonical, or different existing bytes are
rejected without changing the artifact. Ambiguous post-create failures retain
the artifact and never clean up, retry, or rewrite it.

`prepared` means only that the exact Phase 300 decision authorized one future
attempt to materialize a new recovery-resume lineage bound to this exact
preparation. It is not operation-intent existence, start authorization, start
acquisition, execution authority, retry permission, or automatic continuation.
Phase 294 preparation is not reused as authority, no Phase 294/300
orchestration is called, no lower acquisition/execution boundary is called, and
real provider, network, credential, and paid API calls remain zero. A future
Phase 302 may consume only an exact Phase 301 preparation and must preserve the
Phase 298/299/300 provenance without executing.

## Phase 302: recovery-resume preparation-intent binding

Phase 302 adds the durable boundary between the exact Phase 301 preparation
and the exact Phase 289 `resume` operation intent. The caller supplies only
the existing Phase 295 `resume_intent_binding_path`, which is used as the
namespace anchor; Phase 299 is called exactly once and every later path is
derived from the returned lineage digests.

```text
Phase 300 durable operator decision
        |
Phase 301 decision-bound preparation
        |
Phase 302 preparation-intent binding  <-- new recovery authority
        |
exact Phase 289 resume intent
        |
future start authorization only
```

Phase 302 accepts only an exact Phase 299 `DecisionRequired` route. A
completed route is terminal (`materialization_not_required`) and performs zero
Phase 300/301/302/289 work. A Phase 300 `stop` is terminal
(`materialization_not_authorized`). Only `authorize_resume_preparation` may
continue. The Phase 299 → Phase 300 lineage is validated before the Phase 300
digest; the complete Phase 300 → Phase 301 lineage is validated before the
Phase 301 digest. Every predecessor digest is computed exactly once against
the exact object returned by its loader.

The frozen Phase 302 record has exactly thirteen fields and is rooted directly
in the Phase 301 preparation digest. Its `authorized` state means only that
this exact preparation authorizes this exact expected Phase 289 `resume`
intent identity. A next resume intent may be byte- and digest-identical to a
prior resume intent when its approval and plan are unchanged. Intent identity
alone is therefore not new recovery authority; the Phase 301 preparation
digest plus the new Phase 302 binding disambiguate the recovery cycle.

The Phase 302 binding is resolved or durably persisted first. Only after that
binding is exact and durable may the content-addressed Phase 289 intent be
strict-loaded or persisted. Existing exact intent bytes are accepted only
after this binding-first step; partial, noncanonical, or different binding or
intent bytes fail closed without overwrite, cleanup, retry, or repair. Binding
and intent persistence use exclusive create, full write, flush, file fsync,
safe close, and parent-directory fsync; uncertain post-create failures retain
the artifact and are classified `ambiguous`.

Phase 302 creates no start authorization, acquires no start marker, executes
or reconciles no publication, and makes no provider, transport, network,
credential, or paid API call. It does not reuse the Phase 295 binding as new
authority, call Phase 300/301 orchestration, add CLI/GUI behavior, or begin
Phase 303. A future Phase 303 may consume only the exact Phase 302 binding and
exact Phase 289 intent to create a new recovery-specific start authorization;
it must not acquire a start or execute.

## Phase 303: recovery-resume decision-preparation start authorization

Phase 303 adds the durable authorization boundary above the exact Phase 302
preparation-intent binding. The caller supplies only the exact Phase 302
binding path. Phase 303 strict-loads and locally reconstructs that binding
exactly once, computes its digest once from the loader-returned object, derives
the content-addressed Phase 289 intent path from the binding, and strict-loads
the exact bound intent once. The intent digest is computed once from that exact
loader-returned object and its digest, approval, plan, and `resume` operation
must match the Phase 302 binding.

```text
Phase 302 durable preparation-intent binding
        |
exact Phase 289 resume intent
        |
expected Phase 290 start identity (in memory only)
        |
Phase 303 durable start authorization  <-- new recovery authority
        |
future Phase 304 canonical start handoff/acquisition
```

The expected Phase 290 start is constructed and locally reconstructed in
memory only. Its public digest helper is called exactly once with that exact
constructed object. Phase 303 never loads, persists, or acquires a Phase 290
start marker. The new fifteen-field authorization binds the Phase 302 binding
digest, preparation digest, recovery-decision digest, exact intent digest,
expected-start digest, approval and plan digests, and all source/recovery/result
provenance. The authorization path is derived internally from the Phase 302
binding digest, so an intent or start digest repeated in another cycle cannot
collapse the cycle identity.

An existing exact authorization is strict-loaded and returned by the exact
loaded-object identity. A different, partial, noncanonical, corrupt, or
non-regular artifact fails closed without overwrite, cleanup, repair, retry, or
rewrite. New authorization persistence uses exclusive create, full write,
flush, file fsync, safe close, and parent-directory fsync. Ambiguous failures
retain the artifact and perform no second attempt.

Phase 303 calls no Phase 299/300/301/302 orchestration, no old Phase 295/296/
297/298 orchestration, and no Phase 290 acquisition. It performs no execution,
reconciliation, provider, transport, network, credential, or paid API work and
adds no CLI/GUI behavior. `authorized` means only that this exact Phase 302
binding plus its exact bound Phase 289 resume intent authorize one future
attempt to acquire the exact expected Phase 290 resume-start identity. Phase
304 must derive its future start target from the Phase 303 authorization digest;
Phase 304 is not started here.

## Phase 304: strict recovery-resume start acquisition handoff

Phase 304 is the narrow handoff from one exact Phase 303 durable start
authorization to the Phase 290 exclusive start-acquisition fence. The caller
supplies only the exact Phase 303 authorization path. The boundary strict-loads
that authorization once, independently reconstructs it, verifies its exact
canonical filename, and computes its digest exactly once from the original
loader-returned object. It derives the exact bound Phase 289 intent path from
the authorization, strict-loads and reconstructs the intent once, computes its
digest exactly once from the original loader-returned object, and checks the
intent, approval, plan, and `resume` operation lineage before any start call.

```text
Phase 303 durable authorization
        |
Phase 304 strict acquisition handoff
        |
Phase 290 exclusive start marker
        |
 acquired / already_acquired
        |
       STOP
```

The expected `ExternalPublicationOperationStart` is constructed in memory
only and its digest is computed exactly once from that exact constructed
identity. The digest must equal the authorization's expected-start digest.
Only then does Phase 304 derive
`external-publication-recovery-resume-start-<Phase303 authorization digest>.json`
and call Phase 290 exactly once with only `intent_path` and `start_path`.
Phase 290 owns all exclusive-create and durable-marker semantics; Phase 304
does not inspect, load, persist, repair, or retry the start target.

Both exact Phase 290 results, `acquired` and `already_acquired`, are returned
unchanged by object identity and are terminal for this phase. `acquired` only
proves that this invocation won the durable start fence; `already_acquired`
never becomes fresh execution authority. Phase 304 adds no sidecar, calls no
Phase 291/297/288 or execution/reconciliation boundary, and performs no
provider, transport, network, credential, or paid API work. A future Phase 305
may consume the exact acquisition result only through a new explicit boundary.

## Phase 305: same-invocation recovery-resume start acquisition routing

Phase 305 is the read-only, non-persisted routing boundary above the exact
Phase 304 acquisition handoff. Its only caller input is the exact Phase 303
`start_authorization_path`; Phase 304 is called exactly once with that same
`Path` object and exactly the keyword `start_authorization_path`. No caller
may supply an acquisition, status, route, intent/start path, authorization
object or digest, request, provider, transport, credential, execution, or
reconciliation dependency.

```text
Phase 303 durable authorization
        |
Phase 304 exclusive acquisition
        |
Phase 305 in-memory routing
        |
        +-- acquired
        |       -> fresh_start_acquired
        |              |
        |             STOP at Phase 305
        |
        '-- already_acquired
                -> recovery_required
                       |
                      STOP
```

Phase 305 accepts only the exact
`ExternalPublicationOperationStartAcquisition` returned by Phase 304. It
independently reconstructs the acquisition and its exact contained
`ExternalPublicationOperationStart`, requires an exact builtin acquisition
status, and couples `acquired` only to `fresh_start_acquired` and
`already_acquired` only to `recovery_required`. The returned frozen,
three-field route keeps the exact Phase 304 acquisition object by identity.

`fresh_start_acquired` means only that this exact Phase 304 call chain won the
Phase 290 durable start fence in this invocation. It is an in-memory route,
not execution permission, and it is never serialized, digested, loaded, or
persisted. A later invocation over the same durable lineage receives
`already_acquired` from Phase 304 and is routed to `recovery_required`;
marker existence is never inspected or used to reconstruct fresh authority.

Phase 305 calls no Phase 290, Phase 291, old Phase 297, Phase 288, execution,
reconciliation, provider, transport, network, credential, or runtime boundary.
It performs no retry, fallback, cleanup, automatic continuation, path
normalization, or CLI/GUI work. Unexpected Phase 304 exceptions become a
fixed, detail-safe `dependency_error`; known Phase 304 errors preserve exact
object identity. A future Phase 306 must call Phase 305 in the same invocation
and must not accept a caller-constructed route as authority. Phase 306 is not
started here.

## Phase 306: same-invocation recovery-resume reconciliation handoff

Phase 306 is the explicit handoff above Phase 305. It binds the exact runtime
`ExternalPublicationResumeOperationRequest` to the durable Phase 303 start
authorization before Phase 305 may create the Phase 290 start marker. The
caller supplies only the exact `start_authorization_path` and exact resume
request; route, acquisition, authorization, intent/start paths, provider,
transport, and reconciliation values are not caller authority.

```text
Phase 303 durable authorization
        |
Phase 306 request preflight / lineage bind
        |
Phase 305 same-invocation acquisition routing
        |
        +-- recovery_required
        |       -> exact Phase 305 route
        |       -> Phase 288 zero-call
        |       -> STOP
        |
        '-- fresh_start_acquired
                -> Phase 288 exact resume request once
                -> Phase 287 provider-free reconciliation closure
                -> exact reconciliation
                -> STOP
```

Before Phase 305, Phase 306 rejects fresh requests, Path subclasses and
malformed request paths, independently reconstructs the exact approval,
strict-loads Phase 303 authorization exactly once, checks its canonical
filename, computes the request approval digest exactly once over the original
approval identity, and binds both approval and plan digests to the
authorization. Any request or lineage mismatch therefore burns no start
fence. Phase 305 is then called exactly once with only the original
`start_authorization_path` object.

Phase 306 independently reconstructs the exact Phase 305 route, acquisition,
and start. It computes the start digest once over the exact returned start and
checks the complete Phase 303/request lineage. `recovery_required` returns the
exact Phase 305 route unchanged and never calls Phase 288. Only an exact
same-invocation `fresh_start_acquired` route calls Phase 288 once, positionally
with the exact caller request and without lower-dependency overrides. The
exact `ExternalPublicationExecutionReconciliation` returned by Phase 288 is
returned unchanged; Phase 306 does not classify or persist it.

If Phase 288 fails after Phase 305 acquired the marker, the known error is
propagated unchanged, the marker is retained, and there is no retry. A later
Phase 306 invocation receives `recovery_required` from Phase 305 and makes
zero Phase 288 calls. Phase 306 does not load or inspect the marker, call
Phase 290/291/old Phase 297/287/285 directly, perform fresh provider
publication, normalize paths, add CLI/GUI behavior, or use ambient
time/random/environment/network state. A future Phase 307 may explicitly
classify or persist the exact reconciliation result; it is not started here.
