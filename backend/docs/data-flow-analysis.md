# Terraform Data Flow Analysis (3차)

이 분석은 **값이 영향을 줄 수 있는 정적 경로**를 저장합니다. 값·조건·함수를 평가하지 않으며 flow 자체로 Finding을 생성하지 않습니다. Terraform 실행, provider 실행, 외부 module 다운로드, 실제 secret 값 탐지, taint severity는 수행하지 않습니다. 4차의 [Risk Correlation](risk-correlation.md)은 별도 규칙으로 이 graph와 Checkov/Source Trust를 결합합니다.

## 기존 graph의 확장

```text
Collector → Checkov → Parser → Entity / Reference → DependencyGraphBuilder
                                                    ├ SourceTrustAnalyzer → Findings
                                                    └ DataFlowAnalyzer
                                                        → DataFlowGraphBuilder
                                                        → 기존 GraphEdge 저장
                                                        → Flow / Trace API
```

`ModuleAnalyzer[Result]`의 진입점은 유지하며 결과 타입을 generic으로 확장했습니다. SourceTrustAnalyzer는 기존 Finding 목록을, DataFlowAnalyzer는 edge·summary·diagnostics를 반환합니다. StaticAnalysisService는 이미 파싱된 graph를 전달하므로 파일을 다시 파싱하지 않습니다. dependency edge의 중복 제거가 서로 다른 module output 참조를 합칠 수 있으므로 builder는 완전한 Reference 목록도 전달합니다.

`data_flow`는 독립 stage입니다. flow 실패는 Checkov, Source Trust, dependency graph를 버리지 않습니다. Source Trust 실패 시에도 flow를 실행합니다. 부분 파싱·동적 iterator·집계 수준의 loop 처리·분석 한도는 diagnostics와 PARTIAL 상태로 드러납니다. 기존 Checkov 전체 실패 처리 정책은 유지합니다.

## 방향과 logical node

Dependency는 소비자 → 참조 대상입니다. Flow는 값의 제공자 → 소비 attribute입니다.

```text
Dependency: aws_instance.web ─READS_LOCAL→ local.bootstrap
Flow:       local.bootstrap ─SINKS_TO_RESOURCE_ATTRIBUTE→ aws_instance.web.user_data
```

새 node 테이블은 없습니다. 기존 ParsedEntity UUID를 중심으로 logical address와 attribute path를 GraphEdge.metadata의 `source`, `target`에 저장합니다. endpoint에는 entity UUID/address/type, module_path, file_path, line_start/end, attribute_path, resolved/reason, external/opaque, source/sink category가 들어갑니다. source range는 원래 선언의 정확한 범위이며 attribute 자체의 범위를 추정해서 만들지 않습니다.

예:

- `var.bootstrap`, `local.encoded`
- `aws_security_group.web.id`, `aws_instance.web.user_data`
- `data.aws_ssm_parameter.secret.value`
- `module.compute.var.user_data`, `module.compute.output.instance_id`, `module.compute.instance_id`

`GraphEdge.source_entity_id`/`target_entity_id`는 선언에 연결합니다. 선언을 못 찾으면 null이며 logical address는 남습니다. 선언이 있는 resource의 computed attribute는 declaration 관점에서 resolved이며 런타임 값이 알려졌다는 뜻은 아닙니다. 각 flow에는 `graph_kind=data_flow`, `flow_schema_version=1`을 저장합니다.

## Edge와 cross-module 연결

| 타입 | 제공자 → 소비자 |
|---|---|
| PROPAGATES_THROUGH_LOCAL | reference → local value |
| SINKS_TO_RESOURCE_ATTRIBUTE | reference → resource attribute |
| FLOWS_TO | reference → data source configuration attribute |
| PASSES_TO_MODULE_INPUT | caller expression reference → child variable |
| PASSES_TO_OUTPUT | reference → output declaration |
| RETURNS_FROM_MODULE_OUTPUT | child output declaration → public module output alias |
| DERIVED_FROM | 확장 가능한 타입으로 예약; 이번 builder는 생성하지 않음 |

```text
var.bootstrap
 → local.encoded
 → module.compute.var.user_data
 → module.compute.aws_instance.web.user_data
```

출력은 다음처럼 연결됩니다.

```text
module.compute.aws_instance.web.id
 → module.compute.output.instance_id
 → module.compute.instance_id
 → aws_eip.ip.instance
```

Module path → variable/output 인덱스로 실제 child 선언을 연결합니다. 다단계 local module과 같은 directory를 여러 이름으로 호출하는 경우도 별도 주소를 유지합니다. cycle/경로 제한은 기존 parser 정책을 따릅니다. literal module input은 source node가 아니므로 별도 flow를 만들지 않습니다. 출력 alias는 literal output에도 생성됩니다.

## 지원 expression과 정밀도

- Variable/local/resource/data/module 참조, 중첩 expression, interpolation, 알려지지 않은 함수 포함 함수 인자, 조건식, collection, splat을 정적으로 읽습니다.
- `transformations`에는 함수 이름만 기록하며 실행하지 않습니다. 조건식은 양쪽 branch와 조건 참조 모두 potential flow이며 `conditional=true`입니다.
- Collection index와 nested block의 정적 index는 logical address에서 일관되게 집계합니다. 예: `aws_instance.web.root_block_device.encrypted`. 선언 attribute path의 index는 edge.attribute_name에 남깁니다. 원래 index는 `traversal`과 전체 `expression`에 보존합니다. 같은 source/target의 중복 edge는 합칠 수 있습니다.
- Map/object는 **aggregate 수준**입니다. `var.region → local.config`, `local.config → resource.attribute`로 연결하고 `referenced_attribute`, 원본 expression을 남깁니다. field-sensitive 분석이 아니므로 특정 field만의 영향이라고 주장하지 않습니다.
- for expression은 collection source를 추적합니다. `for subnet in aws_subnet.example : subnet.id`는 `aws_subnet.example → local.ids`이며 iterator의 `.id` projection을 해석하지 않습니다. `COLLECTION_FLOW_AGGREGATED` diagnostic을 남깁니다.
- Dynamic block의 iterator/content instance 흐름은 생성하지 않고 `DYNAMIC_ITERATOR_FLOW_UNRESOLVED` diagnostic을 남깁니다. 나머지 정적 attribute는 계속 처리합니다.
- `depends_on`, provider binding, count/for_each, lifecycle은 값 공급 edge가 아닙니다. 인스턴스 expansion과 제어 의존성은 포함하지 않습니다.
- configured resource input → provider-computed resource output 관계를 임의로 만들지 않습니다. provider runtime semantics는 분석하지 않습니다.
- parser 범위는 root `.tf` + 도달 가능한 local module입니다. `.tf.json`은 Checkov만 지원하며 graph parser는 diagnostic을 남깁니다.

## External / unresolved 경계

외부 Registry/Git/HTTP module은 다운로드하지 않습니다.

```text
var.config → module.external.var.config  [opaque boundary]
module.external.subnet_id → aws_instance.web.subnet_id
```

외부 input과 output 사이에 edge를 만들지 않습니다. 그 내부 연관성은 알 수 없기 때문입니다. 외부 endpoint/edge는 `opaque=true`, `external=true`, `resolved=false`, `reason=external_module`을 기록합니다. module source/type/expansion, boundary entity UUID/address도 보존하여 Source Trust Finding과 연결할 수 있습니다.

누락 선언·local module input/output은 `undeclared_reference`, `unresolved_module_input`, `unresolved_module_output`으로 기록합니다. unresolved source에 가짜 소유 entity를 붙이지 않습니다. 미지원 dynamic flow는 저장 edge가 아니라 diagnostics이므로 unresolved edge 개수에 포함되지 않습니다. 따라서 unresolved=0은 분석이 완전하다는 뜻이 아닙니다.

## Source / Sink classification

FlowSourceClassifier는 교체 가능한 분류 계층입니다.

- USER_INPUT: variable. `sensitive = true`인 variable도 USER_INPUT으로 유지하며, endpoint에 `sensitive_input=true`를 별도로 남깁니다(그 외 endpoint는 false). child module variable(`module.x.var.y`)도 선언 기준으로 같은 표시를 받습니다.
- LOCAL_VALUE: local
- MODULE_OUTPUT: module/output
- RESOURCE_ATTRIBUTE: resource
- DATA_SOURCE: 일반 data source
- SECRET_SOURCE: aws_ssm_parameter, aws_secretsmanager_secret, aws_secretsmanager_secret_version, azurerm_key_vault_secret, google_secret_manager_secret_version
- UNKNOWN: 미해결 또는 지원하지 않는 source

SECRET_SOURCE는 resource type 기반의 후보 분류이며 실제 secret의 존재나 속성별 민감도를 검증하지 않습니다. Source category를 전체 trace에 전파하거나 taint로 계산하지 않습니다.

SensitiveSinkRule 목록으로 확장합니다. Attribute 이름은 각 provider 스키마(hashicorp/aws, google, azurerm)의 실제 인자 이름이며, nested block(`aws_security_group.ingress`)은 root attribute 이름으로 매칭합니다(`ingress[0].cidr_blocks` → `ingress`).

| Category | Resource.attribute |
|---|---|
| CODE_EXECUTION | aws_instance.user_data, aws_instance.user_data_base64, aws_launch_template.user_data, aws_launch_configuration.user_data, google_compute_instance.metadata_startup_script, azurerm_linux_virtual_machine.custom_data |
| NETWORK | aws_security_group.ingress, aws_security_group.egress, aws_security_group_rule.cidr_blocks, aws_vpc_security_group_ingress_rule.cidr_ipv4, aws_db_instance.publicly_accessible, aws_instance.associate_public_ip_address, google_compute_firewall.source_ranges, azurerm_network_security_rule.source_address_prefix |
| CREDENTIAL | aws_db_instance.password, aws_rds_cluster.master_password, aws_docdb_cluster.master_password, aws_redshift_cluster.master_password, aws_elasticache_replication_group.auth_token, azurerm_linux_virtual_machine.admin_password, google_sql_user.password |
| IAM_POLICY | aws_iam_policy.policy, aws_iam_role_policy.policy, aws_iam_role.assume_role_policy, aws_iam_user_policy.policy, aws_iam_group_policy.policy, aws_s3_bucket_policy.policy, aws_kms_key.policy |
| ENVIRONMENT | aws_lambda_function.environment |
| COMMAND | aws_ecs_task_definition.container_definitions |
| SECRET_STORAGE | aws_ssm_parameter.value, aws_secretsmanager_secret_version.secret_string |

나머지는 UNKNOWN입니다. 분류는 관찰 metadata이고 vulnerability 판단은 아닙니다. 예를 들어 `aws_security_group.ingress`가 NETWORK라는 것은 값이 네트워크 노출 설정에 도달한다는 뜻이지 그 규칙이 열려 있다는 뜻이 아닙니다.

Correlation(TFCR006/008)은 CREDENTIAL 등 sink category를 사용하므로 sink 규칙이 늘면 해당 경로가 있는 module에서 correlation Finding이 정당하게 늘 수 있습니다. NETWORK는 현재 correlation 규칙의 sensitive 집합에 포함되지 않습니다. 분류 예시는 `tests/fixtures/data_flow/sensitive_sinks/main.tf`에 있습니다.

## API

```bash
curl 'http://localhost:8000/api/scans/SCAN_ID/flows?limit=20&offset=0'
curl 'http://localhost:8000/api/scans/SCAN_ID/flows?entity_type=RESOURCE&edge_type=SINKS_TO_RESOURCE_ATTRIBUTE'
curl 'http://localhost:8000/api/scans/SCAN_ID/flows/aws_instance.web'
curl --get 'http://localhost:8000/api/scans/SCAN_ID/flow-trace' \
  --data-urlencode 'from=var.bootstrap' \
  --data-urlencode 'to=module.compute.aws_instance.web.user_data' \
  --data-urlencode 'max_depth=10'
```

- `/flows`: source/target는 정확한 logical address, edge_type, entity_type(양쪽 endpoint 중 하나) 필터. limit=1..100, offset≥0. 필터·정렬·limit/offset·COUNT는 DB에서 수행합니다(`app/repositories/flow_repository.py`). summary는 **필터 후·페이지 적용 전** 개수이며 nodes는 반환 edge의 endpoint만 포함합니다. 정렬은 기존과 같이 source_entity_id, edge_type, target_address, id 순입니다.
- `/flows/{entity_address}`: 정확한 entity 또는 logical address의 직접 upstream/downstream edge. 전이 경로는 trace API를 사용합니다. 각 방향 최대 1000개, 잘리면 truncated=true. `nodes`는 반환된 upstream·downstream edge 양 끝 노드(address 기준 중복 제거)입니다. 존재하는 flow 없는 entity는 빈 목록, 없는 entity/scan은 404입니다.
- `/flow-trace`: cycle-safe simple paths. from/to는 logical address이며 자동 suffix/prefix 확장을 하지 않습니다. 최대 depth 기본10/상한30, 최대 path20, 탐색 step100000. 설정이 더 작으면 적용합니다. `path_edge_ids`로 각 경로의 근거 edge id를, `path_edges`로 같은 순서·길이의 edge 전체(type/expression/metadata 포함)를 제공합니다. `path_edges[i][j]`는 `paths[i][j]` → `paths[i][j+1]` edge입니다. depth/path/step 한도에 닿으면 truncated=true이며 found=false도 경로 부재의 증명이 아닐 수 있습니다.
- `FlowNode`(세 API 공통)는 기존 `metadata`(endpoint 원본)를 그대로 두고, 그래프 렌더링용으로 다음 값을 endpoint에서 꺼내 채웁니다: `source_category`, `sink_category`(str|null), `opaque`, `external`(기본 false), `resolved`(기본 true), `sensitive_input`(기본 false). 이 필드가 생기기 전에 저장된 scan은 endpoint에 없는 값이 기본값으로 채워집니다.

```json
{
  "found": true,
  "paths": [[
    {"id": "var.bootstrap", "address": "var.bootstrap", "entity_type": "VARIABLE",
     "source_category": "USER_INPUT", "sink_category": "UNKNOWN",
     "opaque": false, "external": false, "resolved": true, "sensitive_input": false,
     "metadata": {"...": "endpoint 원본"}},
    {"id": "local.encoded", "...": "..."}
  ]],
  "path_edge_ids": [["5f0c...", "..."]],
  "path_edges": [[
    {"id": "5f0c...", "source": "var.bootstrap", "target": "local.encoded",
     "type": "PROPAGATES_THROUGH_LOCAL", "expression": "base64encode(var.bootstrap)",
     "metadata": {"...": "..."}}
  ]],
  "truncated": false,
  "explored_steps": 4,
  "max_depth": 10
}
```
- 기존 `/graph`는 dependency graph만 반환합니다. `analysis.edges`는 DB에 저장된 전체 edge 수이며 `analysis.dependency_edges`가 dependency 개수입니다. `analysis.data_flow`에는 flows/cross_module_flows/opaque_external_flows/unresolved_flows를 추가합니다. 이전 scan에는 data_flow가 null일 수 있습니다.
- 빈/과거 scan은 빈 flow 응답을 반환합니다. source trust/checkov Finding API와 summary는 flow 수에 영향을 받지 않습니다.

API와 동일한 response 모델로 생성한 fixture 예제:

- [basic-data-flow.json](../examples/basic-data-flow.json)
- [cross-module-flow.json](../examples/cross-module-flow.json)
- [external-module-flow.json](../examples/external-module-flow.json)

재생성: backend 디렉터리에서 `.venv/bin/python -m scripts.generate_flow_examples`. Checkov를 실행하지 않는 정적 fixture 예제이며 실제 DB 저장 후 FlowService 응답을 직렬화합니다.

## DB / 실행 / 검증

Migration `0003_data_flow`는 기존 graph_edges.source_entity_id를 nullable로 바꿉니다. 같은 scan FK, 기존 entity/edge index는 유지합니다. 새로운 table은 없습니다. downgrade는 3차 flow edge만 제거하고 기존 dependency edge·entity·Module/Scan/Finding을 보존합니다. Scan.analysis는 당시 분석의 historical summary이므로 downgrade 후 flow API의 현재 저장 개수와 다를 수 있습니다.

```bash
docker compose up --build
# 로컬 backend 환경
cd backend
.venv/bin/alembic upgrade head
.venv/bin/pytest tests -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
# PostgreSQL 테스트는 별도 임시 schema에서 downgrade/re-upgrade까지 검증합니다.
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/terraform_intelligence \
  .venv/bin/pytest tests/integration/test_postgres.py -q
# /flows SQL 필터·페이지네이션을 PostgreSQL(JSONB)에서 기존 방식과 비교
TEST_DATABASE_URL=... .venv/bin/pytest -m postgres -q
# frontend 루트
npm run typecheck
```

## 성능과 다음 단계

Address→entity, entity→reference, module_path→variable/output 인덱스를 사용합니다. 함수/조건 구조는 unique expression별로 한 번 읽습니다. Entity/edge는 batch로 저장하고 flow 조회는 edge마다 DB query를 하지 않습니다. `/flows`는 JSON(SQLite)/JSONB(PostgreSQL) metadata 필터와 limit/offset, COUNT·집계를 DB에서 수행하므로 한 scan의 flow edge 전체를 메모리에 올리지 않습니다. JSON 접근은 SQLAlchemy JSON accessor(SQLite `JSON_EXTRACT`, PostgreSQL `->>`/`#>>`)로 두 DB에서 동일하게 동작합니다. 별도 JSONB index는 추가하지 않았습니다(migration 없음). Entity/trace API는 경로 탐색을 위해 한 scan의 flow edge를 한 번 load합니다. Trace는 adjacency index와 depth/path/step budget으로 시간·메모리를 제한합니다. `MAX_FLOW_EDGES=100000`, `MAX_FLOW_PATHS=20`, `MAX_FLOW_DEPTH=30`, `MAX_FLOW_SEARCH_STEPS=100000`을 설정할 수 있습니다.

4차 Correlation 단계는 endpoint의 entity UUID, source range, module hierarchy, module boundary, source/sink category, expression/transformations, potential/opaque/unresolved 상태와 path edge UUID를 재사용할 수 있습니다. full evaluator, runtime resolution, 외부 module 내부 경로, field-sensitive flow, instance expansion, taint 전파·severity, trust 점수, 공격 시나리오 Finding, graph UI는 이번 범위에 없습니다.

## 실제 VPC repository 검증

공개 `terraform-aws-modules/terraform-aws-vpc`의 commit `b3abd6df2ecf052451a361ed55b8f06f8742a795`에서 481 entities, 1,466 dependency edges, 947 data flow edges를 생성했습니다. unresolved와 opaque external flow는 각각 0이며 root module이 child module을 호출하지 않아 cross-module flow도 0입니다. Cross-module 경로는 별도 local/nested module fixture와 PostgreSQL/API trace 테스트로 검증합니다.

Docker에서 별도 정적 benchmark를 실행한 측정값은 parser 2,476 ms, dependency graph 278 ms, data flow 189 ms였습니다. 단일 환경·실행의 관측치이며 전체 clone/Checkov/DB 저장 시간을 포함하지 않습니다. 재측정은 `python -m scripts.benchmark_data_flow`로 가능합니다. Flow 분석의 추가 시간은 이 실행에서 parser+dependency 대비 약 7%였습니다.

Dynamic iterator 진단 5개와 collection 집계 진단 1개가 있으므로 data_flow stage는 PARTIAL입니다. 0 unresolved는 이러한 생략된 흐름이 없다는 의미가 아닙니다. 기존 Checkov Finding 46개와 Source Trust Finding 1개는 그대로 유지됐습니다.

최종 Docker API 스캔 ID는 `c9543cc7-4038-432d-90f5-ffd5a58bcc38`이며 전체 clone/Checkov/static/저장 시간은 84,759 ms였습니다. `/graph`의 dependency 1,466개, `/flows` summary의 947개 및 모든 반환 edge의 node endpoint를 확인했고 `var.name → output.name` trace도 조회했습니다. 전체 pytest 230 passed/1 skipped(별도 DB 설정), 별도 PostgreSQL integration 1 passed, migration upgrade/downgrade/re-upgrade, Ruff/formatter, TypeScript compile, Docker build를 통과했습니다. 기존 Starlette TestClient의 httpx deprecation warning 1개는 남아 있습니다.
