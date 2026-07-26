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
 OpenAI Responses HTTP Response Boundary
                       ↓
 success response | API error response | invalid response error
                       ↓
 OpenAI Responses Output Text Boundary
                       ↓
       OpenAIResponsesOutputText
                       ↓
 Model Invocation Result Boundary
                       ↓
 ModelInvocationSuccess | ModelInvocationFailure
                       ↓
 Explicit Paid-Execution Approval Boundary
                       ↓
 Guarded OpenAI Provider Execution Boundary
                       ↓
 Single-Step Runtime Execution Result Boundary
                       ↓
 Pure Workflow State Transition and Runtime Event Boundary
                       ↓
    Compensatable State and Event Persistence Boundary
                       ↓
 Strict State and Event Loading Boundary
                       ↓
      future controlled progression and replay validation

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
| `invocation` | `StepExecutionRequest` を、モデル・system instructions・task instructions・allowed toolsだけからなるprovider非依存の不変なモデル呼び出し要求へ変換し、provider固有の安全なoutcomeをfuture runtime向けの不変なsuccess/failure結果へ正規化する。明示的paid-execution approvalは、provider、caller metadata、入力に束縛した決定的fingerprintだけを持ち、credential、HTTP、response、保存、時刻、policyを扱わない。planning上のworkflow、step、employee文脈は持ち込まず、prompt結合、provider固有message形式、AI実行、retry policy、state mutationは扱わない。 |
| `providers.openai` | `ModelInvocationRequest` をOpenAI Responses API用の不変な実行前情報 `OpenAIResponsesRequest` へ純粋に変換する。`system_instructions` は `instructions`、`task_instructions` は `input` に対応し、文字列を結合・加工しない。`allowed_tool_names` は定義順の未解決tool名である。OpenAI Responses Tool Adapterは解決済み`ToolDefinition`を静的な`OpenAIResponsesFunctionTool`へ変換し、OpenAI Responses Payload Adapterは基本request情報と解決済みtool schemaを`OpenAIResponsesPayload`へ統合する。Dictionary Payload AdapterはそれをJSON互換Python辞書へ、JSON Serializerはその辞書を決定的なJSON文字列へ、HTTP Request Template Builderは非秘密headerを持つ未認証templateへ変換する。Authentication Boundaryは明示入力のAPI keyをBearer headerへ付加し、Environment Acquisition Boundaryだけが`OPENAI_API_KEY`を明示mappingまたはprocess environmentから取得する。HTTPS Transport Boundaryは認証済みrequestを1回だけ送信し、Response Boundaryはraw responseを不変successまたはAPI-error dataへ分類し、Output Text Boundaryはsuccess responseから対応するmessage output textだけを不変text結果へ抽出する。credential persistenceやruntime処理は扱わない。 |
| `engine` | 定義済みの状態遷移、検証、再試行を決定的に管理する。 |
| `runtime` | すでに準備済みかつ承認済みの単一step inputを、既存guarded OpenAI executionへ一度だけ委譲し、workflow/step/employee identityとprovider-independent resultを不変dataとして返す。completed resultを明示`running` stateと検証して、不変next stateと1つのsafe runtime eventへ純粋に変換する。next-step選択、event/artifact保存、retry、tool実行は扱わない。 |
| `storage` | completed transitionのnext stateを決定的JSONへ、runtime eventを決定的JSONLへ保存する。両targetの事前bytesを捕捉し、handled partial failure時には両targetを補償復元する。load/replay、locks、crash recovery、workflow進行は扱わない。 |
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
- OpenAI Responses HTTP Response Boundaryは、raw response bodyをUTF-8で1回decodeしJSONを1回parseして、2xxを最小検証済みsuccess responseへ、非2xxを最小検証済みAPI-error responseへ分類する。payloadは完全に保持し再帰的に不変化し、最初の`x-request-id`を取得する。completed非2xxを例外化せず、無効なbodyや契約は安全なinvalid-response errorにする。usage、tool call、retry、persistence、Runtimeは扱わない。
- OpenAI Responses Output Text Boundaryは、`OpenAIResponsesSuccessResponse.output`を順に走査して、`message`の`content`にある`output_text`の文字列だけを加工せず不変の`OpenAIResponsesOutputText`へ抽出する。対応しないitemは無視し、対応すると主張するmessage/content構造が不正な場合はpayloadやtextを露出しない安全なinvalid-output errorにする。raw response、JSON decode・parse、API error、credentials、HTTP通信、usage、tool call、persistence、Runtimeは扱わない。
- Model Invocation Result Boundaryは、provider固有の安全なsuccess output、API error、transport error、invalid-response error、invalid-output error、invalid execution input、approval failureを、provider-independent かつ不変の`ModelInvocationSuccess`または`ModelInvocationFailure`へ正規化する。failure categoryは`api_error`、`transport_error`、`invalid_response`、`invalid_output`、`invalid_request`、`approval_required`だけであり、retryabilityやtransient/permanentを推測しない。raw payload、request body、headers、credential、exception internals、usage、tool、persistence、runtime stateは保持・解釈しない。
- Explicit Paid-Execution Approval Boundaryは、caller suppliedな不変`ModelInvocationExecutionApproval`を、provider、non-empty caller metadata、現在の`ModelInvocationRequest`とresolved `ToolDefinition` tupleから再計算するSHA-256 fingerprintへ束縛して検証する。fingerprintはmodel、system/task instructions、ordered allowed tool names、ordered tool definitionsとparameter定義を含み、API key、authorization、HTTP body、environment、response、時刻、random値を含まない。false、provider mismatch、stale fingerprint、空metadataは例外詳細を露出しない`approval_required`として正規化される。自動approval、approval persistence、expiration、role/policy、CLI paid executionは扱わない。
- Guarded OpenAI Provider Execution Boundaryは、明示的な`ModelInvocationRequest`、位置順まで一致する解決済み`ToolDefinition` tuple、明示的な`OpenAIApiKey`、明示的なapprovalを入力にして、tool一致、approval検証、既存のrequest、tool schema、payload、JSON、HTTP template、authentication、HTTPS transport、response、output、result正規化の境界を順に一度だけ合成する。tool一致失敗はapproval前に安全な`invalid_request`結果へ、approval失敗は認証・通信前に安全な`approval_required`結果へ正規化し、expected provider-specific safe errorだけを正規化する。環境credential取得、retry、tool実行、usage、persistence、runtime state、CLI API実行は扱わない。
- Single-Step Runtime Execution Result Boundaryは、明示的な`StepExecutionRequest`、既存の`ModelInvocationRequest`、resolved tool tuple、approvalからなる不変inputを受け取る。model、employee instructions、step instructions、allowed toolsが既存のrequest生成契約どおり完全一致することを最初に検証し、不一致は詳細を露出しない`invalid_request` runtime failureへ正規化する。整合する入力だけを既存guarded OpenAI executionへ一度だけ委譲し、workflow ID、step ID、既存の1始まりstep index、employee IDと既存`ModelInvocationSuccess`または`ModelInvocationFailure`を不変wrapperとして保持する。API keyはinput/resultに保存しない。state transition、next-step selection、event/artifact保存、retry、tool実行、agent loop、CLI paid executionは扱わない。future state-transition boundaryがこのresultを消費する。
- Pure Workflow State Transition and Runtime Event Boundaryは、completed `StepRuntimeExecutionResult`と明示的`running` `WorkflowExecutionState`のworkflow ID、step ID、既存の1始まりstep index、employee IDを完全一致で検証する純粋boundaryである。不一致は詳細を露出しないsafe input errorになる。successは`running -> succeeded`としてcurrent step IDを一度だけappendし、failureは`running -> failed`としてcompleted IDsを保持してfailure categoryを記録する。どちらもidentity、safe provider metadata、empty output textを含むevent dataを不変に返す。ここでの`succeeded`は単一step execution stateの完了であり、multi-step workflow final completionではない。timestamp、event ID、persistence、next-step selection、provider呼出、retry、tool execution、agent loop、CLI paid executionは扱わず、future persistence boundaryがstateとeventをatomicまたはcompensatableに保存する。
- Compensatable State and Event Persistence Boundaryは、明示`WorkflowExecutionTransition`のnext stateを決定的UTF-8 JSONへ、runtime eventを1件の決定的UTF-8 JSONL recordへ保存する。workflow/step/employee identity、status、event type、distinct file targetを検証し、変更前に両targetの存在と正確なbytesを捕捉する。state replacement後のevent appendを行い、handled filesystem failureではevents、stateの決定順で両targetを元のbytesへ復元し、元は存在しなかったtargetを削除する。rollbackの一方が失敗しても両方を試み、safe rollback errorへ分類する。これはin-process compensationでありcrash-safe cross-file transaction、fsync保証、locks、concurrency、load/replay、next-step orchestration、provider呼出、retry、tool execution、CLI paid executionは扱わない。
- Strict State and Event Loading Boundaryは、caller suppliedなPhase 23 target pathsからstate JSONとevent JSONLをread-onlyで読み、UTF-8、duplicate key、完全なfield集合、型、有限値、event意味制約を厳格に検証してPhase 22の不変`WorkflowExecutionState`と順序付き`RuntimeStepEvent` tupleへ再構築する。JSONLのblank record、未知field、欠損field、partial final recordは拒否する。regular fileを指すsymbolic linkは許可し、directory targetは拒否する。終端stateは最後のevent、identity、status、failure category、completed stepと照合し、安全なdata/load/inconsistency errorだけを返す。空event fileは明示的に`ready`または`running` stateに限り許可する。repair、migration、書込み、resume、next-step selection、retry、tool/provider実行、CLI paid execution、GUIは扱わない。Phase 21 execution、Phase 22 transition、Phase 23 persistence、Phase 24 loadingはすべて別の明示呼出であり、後続boundaryだけがhuman-approvedなcontrolled next-step preparationを判断できる。
- Pure Workflow Progression Decision Boundaryは、検証済み`WorkflowDefinition`とPhase 24の`LoadedWorkflowExecutionHistory`だけを入力に、persisted stateのworkflow ID、current step index/ID/employee、completed step IDと定義順序を現在のworkflow definitionへ照合する純粋boundaryである。staleまたは互換性のない履歴は、IDやinstructionなどを露出しない安全なcompatibility errorとして拒否する。`ready`と`running`は`not_progressable`、`failed`は`stopped_failed`、成功した終端stepは`workflow_complete`、成功した非終端stepは定義順の直後の一stepだけを示す`prepare_next_step`を返す。不変decisionはstateを変更せず、request構築、approval作成、provider/tool実行、persistence、automatic resume、retry、scheduler、CLI paid execution、GUIを行わない。Phase 21 execution、Phase 22 transition、Phase 23 persistence、Phase 24 loading、Phase 25 decisionは別の明示呼出であり、後続boundaryだけが明示承認済みdecisionを次step preparation requestへ変換できる。
- Approved Next-Step Preparation Boundaryは、Phase 25の`prepare_next_step` decision、明示的かつcurrent/next step identityへ完全に束縛されたapproval、正確なvalidated employee definitionを入力に、workflow/history/decision/approval/employeeの互換性を再検証する純粋boundaryである。返す不変`PreparedWorkflowStep`はworkflow/step/employee identity、1始まりindex、employee instructions、step instructions、model、順序を保持したallowed tool namesだけを含む。stale decision、別stepのapproval、employee mismatchは安全なerrorとして拒否する。provider request/payload、credential lookup、tool resolution/execution、state mutation、runtime event、persistence、retry、automatic continuation、CLI paid execution、GUIは扱わない。Phase 21 execution、Phase 22 transition、Phase 23 persistence、Phase 24 loading、Phase 25 decision、Phase 26 preparationは別の明示呼出であり、後続boundaryだけがprepared dataをcontrolled execution requestとrunning-state transitionへ変換できる。
- Pure Prepared-Step Execution Start Boundaryは、Phase 26の`PreparedWorkflowStep`とloaded historyを入力に、success state、workflow ID、1始まりの直後step index、必要なrequest dataを再検証して、provider-independent execution requestとproposed `running` stateを不変に返す。completed step IDsは正確に保持しfailure categoryはclearする。provider execution、credential、tool resolution、event、persistence、retry、automatic continuation、CLI paid execution、GUIは扱わず、後続boundaryがrunning stateを明示保存してからPhase 21を明示実行する。
- Explicit Running-State Persistence Boundaryは、Phase 27のproposed `running` stateだけをcaller suppliedなstate targetへ既存の決定的state JSON contractで安全に置換保存する。保存完了後にのみcallerがPhase 21を明示実行できる。start eventは作成せずruntime event fileを変更しない。provider execution、Phase 22 completed-result transition、completion/failure persistence、retry、automatic continuationは別の明示boundaryに保つ。
- Persisted-Start Single-Step Execution Boundaryは、Phase 27の`PreparedStepExecutionStart`、明示state target、検証済み`WorkflowDefinition`と正確な`EmployeeDefinition`、resolved tools、credential、approvalを入力にする。in-memory contractを検証してから既存の厳格state parserでtargetをread-onlyに読み、`running` status、failure categoryなし、Phase 27 stateとの完全一致を確認する。さらにworkflow ID、1-based current step index、current step ID、current employee IDをworkflow definitionへ照合する。確認後だけworkflow/step表示名をdefinitionから取得して既存の必須`StepExecutionRequest`を構築し、同一`ModelInvocationRequest`を既存Phase 21 `execute_openai_runtime_step()`へ一度だけ渡す。結果state/eventを保存せず、retry、自動継続、paid CLI、GUIも行わない。順序はPhase 25 decision → Phase 26 preparation → Phase 27 proposed start → Phase 28 running-state persistence → Phase 29 persisted-state verification + one Phase 21 call → later Phase 22 transition + Phase 23 completion/failure persistenceである。
- Executed-Step Transition Persistence Boundaryは、既存Phase 21の`StepRuntimeExecutionSuccess`または`StepRuntimeExecutionFailure`、明示state target、明示runtime-event targetを入力にする。strict state-only loaderでcurrent stateをread-onlyに再読込し、`running` status、failure categoryなし、workflow/step/index/employee identityを確認してから、既存Phase 22 `transition_workflow_execution_from_step_result()`を一度だけ呼ぶ。returned `WorkflowExecutionTransition`を再構築せず互換性確認し、既存Phase 23 `persist_workflow_execution_transition()`へ一度だけ渡して最終stateと一つのeventを補償付きで保存する。provider、credential、approval、tool解決、retry、progression、次step準備・実行、自動継続、paid CLI、GUIは扱わない。順序はPhase 25 decision → Phase 26 preparation → Phase 27 proposed start → Phase 28 running-state persistence → Phase 29 one Phase 21 call → Phase 30 reload running state + one Phase 22 transition + one Phase 23 persistence → later explicit progression decisionである。
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

`employees/` と `workflows/` はテキスト定義の配置場所であり、定義の読込・検証とCLIによる確認を提供する。`planning/` は検証済み定義から実行計画と、1 step分の構造化実行要求を生成する。`invocation/` は実行要求からprovider非依存のモデル呼び出し要求を生成する。`tools/` は未解決名を静的な`ToolDefinition`へ解決する。`providers/openai/` はモデル呼び出し要求をOpenAI固有の実行前モデルへ、解決済みtool定義を静的function tool schemaモデルへ、payloadをJSON互換Python辞書と決定的なJSON文字列へ、未認証HTTP request templateと認証済みtemplateへ変換し、`OPENAI_API_KEY`を限定された環境取得境界でだけ取得して、認証済みrequestを1回だけHTTPS送信してraw responseを返す。Response Boundaryはcompleted responseを不変dataへ分類し、Output Text Boundaryはsuccess responseから対応するtextだけを抽出する。OpenAI Runtimeは今後のPhaseで扱う。
