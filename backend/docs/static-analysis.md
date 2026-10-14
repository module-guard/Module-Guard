# Terraform Dependency Graph / Source Trust (2차)

2차 구현은 구성 보안 검사에 **선언 Entity, 정적 참조 Graph, Source Trust 규칙**을 추가했습니다. 3차에서는 [정적 Data Flow 분석](data-flow-analysis.md)을 추가했습니다. 4차에서는 [근거 기반 Risk Correlation](risk-correlation.md)을 추가했습니다. taint, 실행 시 값 평가, trust score, 악성 행동 판정은 실행하지 않습니다.

## 파이프라인과 기존 인터페이스

```text
SourceCollector → workspace → Terraform discovery
  → CheckovScanner → FindingNormalizer
  → TerraformParser → TerraformEntity / TerraformParseResult
  → DependencyGraphBuilder → DependencyGraph
  → SourceTrustAnalyzer(ModuleAnalyzer) → NormalizedFinding
  → GraphRepository + FindingRepository → PostgreSQL → REST API
```

`ScanDispatcher`와 `ScanService.run(scan_id)` 진입점은 유지됩니다. `StaticAnalysisService`가 파싱·그래프·trust 단계별 결과를 격리하며, ScanService가 전체 수명과 트랜잭션을 관리합니다. `ModuleAnalyzer.analyze(path)`의 기존 진입점에 선택적인 `parse_result`, `graph` keyword를 추가했습니다. 파이프라인에서는 이미 계산한 결과를 전달하므로 분석기에서 다시 파일을 읽지 않습니다.

역할별 구현:

| 역할 | 파일 |
|---|---|
| 교체 가능한 parser interface / domain | `app/parsers/base.py`, `domain.py` |
| python-hcl2 syntax tree → 선언 Entity | `app/parsers/hcl.py` |
| expression syntax tree → Reference | `app/parsers/references.py` |
| Node / Edge / Reference 모델 | `app/graphs/domain.py` |
| 주소 lookup과 dependency edge 구성 | `app/graphs/builder.py` |
| Source / Git ref / version 분류 | `app/analyzers/classifiers.py` |
| TFSC 규칙과 Finding 생성 | `app/analyzers/source_trust.py` |
| 단계별 실행 및 summary | `app/services/analysis_service.py` |
| 저장·조회 | `app/repositories/graph_repository.py`, `app/services/graph_service.py` |
| API | `app/api/routes/graphs.py` |

## Parser와 semantic identity

파서 범위는 **repository root 디렉터리의 `.tf` 파일과, 이 root에서 실제로 호출하는 로컬 모듈**입니다. `examples/` 등 호출되지 않은 디렉터리를 별도 root처럼 합치지 않습니다. Checkov는 종전과 같이 제외 경로를 뺀 repository 전체를 검사하므로 두 분석의 coverage는 다릅니다.

지원 선언은 ROOT_MODULE, MODULE, RESOURCE, DATA, VARIABLE, OUTPUT, LOCAL, PROVIDER, REQUIRED_PROVIDER입니다. `terraform` 블록의 속성과 source range는 소유 root/module의 `metadata.terraform_configurations`에 보존합니다. `required_providers`의 각 항목은 독립 Entity입니다. provider alias는 `provider.aws.west`처럼 별도 주소를 갖습니다.

- `attributes`는 attribute path → **원래 HCL expression 문자열**입니다. 예: `subnet_id: "module.vpc.private_subnets[0]"`, `ingress[0].cidr_blocks: "var.cidrs"`.
- `file_path`는 repository 상대 경로이며 source range는 Lark의 실제 token 위치에서 얻습니다. 합성 root에는 line을 만들지 않습니다.
- Entity UUID는 scan별 식별자입니다. 같은 scan의 선언 주소는 UNIQUE입니다. 서로 다른 scan을 비교할 때는 address 및 module path/commit을 함께 사용해야 합니다.
- 예: `module.network.module.subnet.aws_subnet.private`, `metadata.module_path = ["network", "subnet"]`.
- 같은 real directory를 여러 이름으로 호출하면 syntax는 한 번만 파싱하고 호출별 Entity를 생성합니다. ancestry real path set으로 cycle을 차단하며, 전역 cache를 단순 visited skip으로 사용해 호출 주소를 잃지 않습니다.
- source가 `./` 또는 `../`인 경우만 로컬 재귀 탐색합니다. root 밖, symlink, excluded directory, cycle, 누락 경로는 진단과 boundary module Entity로 남기며 읽지 않습니다.
- 파일 단위 파싱 오류·크기 초과는 해당 파일만 제외합니다. syntax가 깨진 파일 내부의 블록 복구는 지원하지 않습니다. `.tf.json`은 기존 Checkov 검사 대상으로 유지하되 새 그래프 파서는 지원하지 않으며 진단으로 알립니다.

## Reference / Edge

단순 문자열 정규식 대신 HCL syntax tree의 traversal을 분석합니다. `"var.fake"` 같은 literal은 참조로 만들지 않고 interpolation 내부 참조는 추출합니다. `for`/template 반복 변수와 dynamic iterator의 lexical scope를 구분합니다. index/splat은 정의 주소로 축약하되 원래 expression과 traversal을 보존합니다. `count`/`for_each`의 실제 instance 수나 key는 평가하지 않습니다.

Edge 방향은 **사용하는 선언 → 의존 대상**입니다.

| Edge type | 의미 |
|---|---|
| CALLS_MODULE | root/parent module → module call |
| USES_PROVIDER | provider 사용/requirement 연결, 명시 alias 및 module providers mapping |
| READS_VARIABLE | variable 참조 |
| READS_LOCAL | local 참조 |
| READS_DATA | data source 참조 |
| CONSUMES_MODULE_OUTPUT | output을 소비하는 선언 → module call |
| REFERENCES | resource attribute / module output 참조 |
| RESOURCE_DEPENDS_ON | resource 선언에 대한 암시적 의존 |
| EXPLICIT_DEPENDS_ON | `depends_on`에서 명시한 의존 |

각 Edge는 UUID, source/target Entity UUID, target_address, expression, attribute_name, metadata를 갖습니다. metadata에 `resolved`, `traversal`, `target_attribute` 등을 보존합니다. 실제 local output이 있으면 REFERENCES edge를 `module.network.output.subnet_id`에 연결합니다. 다운로드하지 않은 외부 module output은 `module.vpc.private_subnets` 같은 unresolved 주소로 남깁니다.

없는 variable/resource/provider를 선언한 것으로 꾸미지 않습니다. DB에서 unresolved target ID는 NULL입니다. `/graph` 응답은 시각화 편의를 위해 `unresolved:<address>` ID의 **합성 UNRESOLVED 노드**를 추가하므로 모든 edge endpoint를 바로 그릴 수 있습니다. 이 노드는 `parsed_entities`에 저장되지 않으며 `analysis.entities`에도 포함되지 않습니다.

Provider configuration과 requirement 선언은 구분됩니다. 명시 `provider = aws.west`, `providers = { aws = aws.west }`를 연결하고 resource type prefix 기반 provider 추정에는 `binding=inferred_from_resource_type`를 표시합니다. 전체 Terraform provider inheritance/alias validation을 구현한 것은 아닙니다. `/dependencies.providers` 및 provider trust 규칙은 선언된 `required_providers`를 기준으로 합니다. 선언 없이 추정한 provider 사용은 Graph의 unresolved edge로 확인할 수 있습니다.

## Source / version / ref 분류

- Source: REGISTRY, GITHUB, GIT, LOCAL, HTTP, UNKNOWN. public/private registry 주소와 registry subdirectory 형태를 구분합니다. 이 분류는 **수집기 지원 확대를 의미하지 않습니다**. POST source collector는 계속 공개 GitHub URL만 실제 수집합니다.
- Version: EXACT, BOUNDED, BROAD, UNBOUNDED, MISSING, UNKNOWN. `5.1.0`은 EXACT, `~> 5.0`/`>= 1.0, < 2.0`은 BOUNDED, `>= 1.0`은 BROAD, `*`/`>= 0.0.0`은 UNBOUNDED입니다. version 표현식은 평가하지 않고 UNKNOWN으로 남깁니다. 제약 조건의 충돌/만족 가능성까지 계산하지 않습니다.
- Git ref: MISSING, BRANCH, TAG, COMMIT_SHA, UNKNOWN. 전체 40/64 hex SHA만 immutable 형식으로 취급합니다. 짧은 SHA나 임의 이름은 UNKNOWN입니다. 알려진 branch 이름/명시 refs/heads, version-like tag/refs/tags는 **heuristic**이며 원격 Git 조회로 존재나 유형을 검증하지 않습니다. TAG도 mutable합니다.

분류는 [Terraform module sources](https://developer.hashicorp.com/terraform/language/modules/sources)와 [reference 의미](https://developer.hashicorp.com/terraform/language/expressions/references)를 참고한 정적 모델입니다. Terraform interpreter와 동일한 runtime semantics를 보장하는 모델은 아닙니다.

## Source Trust 규칙

모든 결과는 기존 Finding에 `scanner="source_trust"`로 저장됩니다. INFO는 취약점 판정이 아닌 dependency surface 관측입니다.

| ID | 이름 | Severity |
|---|---|---|
| TFSC001 | MISSING_MODULE_VERSION | MEDIUM |
| TFSC002 | BROAD_MODULE_VERSION | MEDIUM |
| TFSC003 | UNPINNED_GIT_MODULE | HIGH |
| TFSC004 | BRANCH_REFERENCED_GIT_MODULE | HIGH |
| TFSC005 | TAG_REFERENCED_GIT_MODULE | LOW |
| TFSC006 | EXTERNAL_GIT_MODULE | INFO |
| TFSC007 | MISSING_PROVIDER_VERSION | MEDIUM |
| TFSC008 | BROAD_PROVIDER_VERSION | MEDIUM |
| TFSC009 | UNBOUNDED_PROVIDER_VERSION | HIGH |
| TFSC010 | EXTERNAL_PROVIDER_SOURCE | INFO |
| TFSC011 | MISSING_PROVIDER_SOURCE | MEDIUM |

TFSC011은 요청된 provider source 누락 검사를 위한 추가 규칙입니다. 원격 module/provider를 다운로드하거나 publisher 평판 점수를 산출하지 않습니다. UNKNOWN ref를 임의로 branch라고 단정하지 않으며, commit pinning도 악성 여부·신뢰성 인증을 의미하지 않습니다.

## DB와 마이그레이션

`alembic/versions/0002_dependency_graph.py`는 다음만 추가합니다.

- `scans.analysis JSONB NULL`: 기존 scan은 NULL 유지.
- `parsed_entities`: UUID PK, scan FK, entity_type/address/name/resource_type, file/line, attributes JSONB, metadata JSONB, created_at. scan/type/address/resource_type index, `(scan_id,address)` UNIQUE.
- `graph_edges`: UUID PK, scan FK, source/target Entity, nullable target_address/target ID, edge_type, expression, attribute_name, metadata JSONB, created_at. scan/source/target/type index.
- composite FK로 다른 scan의 Entity에 Edge가 연결되는 것을 DB에서도 막습니다. 삭제 cascade는 scan 범위에 적용됩니다.
- `edge_type`은 VARCHAR이므로 향후 FLOWS_TO 등의 타입 추가에 enum migration이 필요하지 않습니다.
- Entity를 한 번 flush하고 Edge 및 Finding과 최종 Scan 결과를 한 트랜잭션에 저장합니다. Entity/Edge마다 commit하지 않습니다. UUID를 미리 부여하므로 참조 연결을 위해 DB lookup을 반복하지 않습니다.

```sh
cd backend
.venv/bin/alembic upgrade head
.venv/bin/alembic current
# 1차 schema로 복귀: graph 데이터만 제거되며 기존 Module/Scan/Finding은 보존
.venv/bin/alembic downgrade 0001
```

## 단계별 상태와 호환성

Scan의 기존 상태 enum과 응답 필드는 유지합니다. 새 nullable `analysis` 필드에 `stages.checkov/parse/graph/source_trust/data_flow/persistence` 상태를 기록합니다. 상태는 PENDING, RUNNING, COMPLETED, PARTIAL, FAILED, SKIPPED입니다.

- Checkov 성공 + graph/parser/analyzer 실패: 유효 Checkov Finding을 보존하고 전체 Scan은 COMPLETED, `partial_result=true`입니다.
- Checkov 실패 + static 분석 성공: **기존 실패 의미를 보존해 전체 Scan은 FAILED**지만, Entity/Graph/Source Trust Finding과 단계별 결과는 저장·조회할 수 있습니다.
- 일부 파일 실패: 유효 파일을 분석하고 PARTIAL. 모든 `.tf` 파일 파싱 실패: parse FAILED, 후속 단계 SKIPPED.
- 그래프 구성 실패 시 파싱된 Entity는 보존합니다. Source Trust 실패 시 Graph는 보존합니다.
- 안전한 Checkov 실행을 위한 기존 local module path preflight는 유지합니다. preflight 파싱이 실패하거나 source 경계가 불확실하면 Checkov 실행은 차단하지만 독립 static 분석은 계속합니다.
- DB 저장 실패는 전체 Scan FAILED이며 저장 transaction은 rollback됩니다. 정상 종료/오류 모두 workspace 정리 정책을 유지합니다.
- 기존 finding summary는 **Checkov + Source Trust의 합계**입니다. Checkov 단독 결과는 `scanner=checkov` 필터로 조회합니다. 기존 dashboard 정책상 FAILED scan의 finding은 dashboard 누적 합계에 포함하지 않습니다.
- unresolved 참조와 외부 module boundary는 예상된 분석 범위이므로 그 자체로 parser 실패를 의미하지 않습니다.

`analysis.max_observed_module_depth`는 root=0으로 보이는 module call hierarchy의 최대 깊이입니다. 외부 module이 있으면 `external_transitive_depth_unknown=true`입니다. 이는 완전한 외부 공급망 깊이를 아는 것으로 해석하면 안 됩니다. `.analysis.entities`는 실제 선언 수, `.unresolved_references`는 target ID가 없는 **edge 수**입니다.

## 새 API

| API | Query |
|---|---|
| `GET /api/scans/{id}/entities` | entity_type, resource_type, limit, offset |
| `GET /api/scans/{id}/graph` | 전체 저장 graph + unresolved 표시 노드 |
| `GET /api/scans/{id}/dependencies` | module / required-provider dependency summary |
| 기존 `GET /api/scans/{id}/findings` | `scanner` filter 추가 |
| 기존 `GET /api/scans/{id}` | nullable `analysis` summary 추가 |

기존 scan에는 graph/엔티티 목록이 비어 있고 analysis는 NULL입니다. 실행 중에는 완료 전이므로 아직 목록이 비어 있을 수 있습니다. 모르는 scan UUID는 404입니다. Entity 목록은 기존 pagination 규칙(기본 20, 최대 100)을 사용합니다. Graph 전체 응답은 아래 생성 상한의 영향을 받습니다.

```sh
SCAN_ID='<scan UUID>'
curl -sS "http://localhost:8000/api/scans/$SCAN_ID/entities?entity_type=MODULE&limit=100"
curl -sS "http://localhost:8000/api/scans/$SCAN_ID/graph"
curl -sS "http://localhost:8000/api/scans/$SCAN_ID/dependencies"
curl -sS "http://localhost:8000/api/scans/$SCAN_ID/findings?scanner=source_trust"
```

프론트엔드의 `src/lib/api.ts`에 `api.entities`, `api.graph`, `api.dependencies`와 응답 타입을 추가했습니다. 기존 화면의 demo mode와 mock data는 유지됩니다.

## 실제 fixture 결과 예시

`tests/fixtures/dependency_graph/basic/main.tf`를 파싱해 생성한 결과 일부:

```text
root ──CALLS_MODULE──> module.vpc
root ──USES_PROVIDER──> required_provider.aws
module.vpc ──READS_LOCAL──> local.name
local.name ──READS_VARIABLE──> var.region
module.vpc ──USES_PROVIDER──> provider.aws.west
aws_instance.web ──CONSUMES_MODULE_OUTPUT──> module.vpc
aws_instance.web ──REFERENCES──> module.vpc.private_subnets (unresolved external output)
aws_instance.web ──READS_DATA──> data.aws_ami.ubuntu
aws_instance.web ──EXPLICIT_DEPENDS_ON──> aws_security_group.web
```

`source = "git::https://github.com/example/terraform-module.git?ref=main"`은 TFSC004(HIGH)와 TFSC006(INFO)를 생성합니다. 전자는 mutable branch, 후자는 외부 Git dependency 관측을 각각 나타냅니다. `source_trust/provider_versions` fixture에서는 TFSC007/008/009/010/011이 생성됩니다.

생성한 JSON 예제는 `examples/basic-graph.json`, `examples/branch-source-trust.json`에 있습니다. 이 파일은 fixture 정적 분석 결과이며 실제 Checkov 결과를 포함하지 않습니다.

## 검증과 제한

```sh
cd backend
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/terraform_intelligence \
  .venv/bin/pytest tests/integration/test_postgres.py -q
# 저장소 루트
npm run typecheck
docker compose up --build
```

MVP 회귀 테스트, 실제 Checkov fixture 3개, source/ref/version 분류, lexical reference 추출, module hierarchy·cycle·cache, unresolved target, partial/failure 분리, API, migration 왕복·기존 데이터 보존을 테스트합니다. PostgreSQL 테스트는 고유한 임시 schema만 만들고 삭제하며, JSONB와 cross-scan FK도 확인합니다.

기본 상한은 `MAX_ANALYSIS_ENTITIES=20000`, `MAX_ANALYSIS_EDGES=100000`, `MAX_LOCAL_MODULE_DEPTH=32`, `MAX_TERRAFORM_FILE_BYTES=2097152`입니다. 초과는 진단과 부분 결과로 표시합니다. index 안전성을 위해 Entity 주소는 UTF-8 2048 bytes까지 저장합니다.

지금 없는 기능은 taint tracking, Terraform runtime evaluation, runtime 공격 판정, trust scoring, malicious behavior analysis입니다. provider binary나 외부 module을 다운로드하지 않으며 terraform init/plan/apply는 실행하지 않습니다. 상한은 완전한 CPU/time sandbox를 대체하지 않습니다. 기존 in-process queue의 강제 종료 복구·인증 제한도 유지됩니다.

3차 Data Flow Analyzer는 Entity UUID/address/module_path, 원래 attributes와 file range, Reference.expression/attribute_path/target_attribute를 재사용하며 값 공급 방향의 flow edge를 추가했습니다. 자세한 연결 의미와 제한은 [Data Flow 문서](data-flow-analysis.md)를 참고하세요. 현재 dependency edge를 실제 값 흐름이나 taint 증거로 간주하지 않습니다.
