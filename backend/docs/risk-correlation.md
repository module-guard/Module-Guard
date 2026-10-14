# Risk Correlation Engine (4차)

Correlation은 개별 검사 결과와 코드 구조를 결합한 **잠재적 영향 경로**를 설명합니다. 실제 공격·악성 코드·credential leak의 증명이 아니며 repository 안에 Finding이 함께 존재한다는 이유만으로 생성하지 않습니다. Flow 자체는 계속 관찰 graph이며, 명시적인 TFCR 규칙을 만족하는 경우만 scanner=`correlation` Finding이 됩니다.

## Architecture와 수명

```text
Checkov → normalized findings (UUID)
Parser → Entity → Dependency Graph → Source Trust findings (UUID)
                         → Data Flow
                              ↓
CorrelationContext: scan 단위 index와 bounded graph search
                              ↓
CorrelationEngine → CorrelationRule 8개 → severity / confidence / suppression
                              ↓
기존 Finding.raw_data JSONB + 동일 scan GraphEdge → REST API
```

`app/correlation/domain.py`, `context.py`, `matcher.py`, `rules.py`, `scoring.py`, `engine.py`로 역할을 분리했습니다. 새 규칙은 `CorrelationRule.evaluate(context)`를 구현해 Engine의 rule 목록에 추가합니다. 규칙은 DB를 조회하지 않습니다.

Context는 entity address/UUID, scanner/rule/resource별 Finding, module boundary, provider binding, source/sink category, forward/reverse adjacency를 한 번 구성합니다. Path 검색 결과는 모든 규칙이 공유합니다. `app/graphs/search.py`의 `FlowPathSearch`는 기존 Flow Trace API와 Correlation에서 공용으로 사용하며 API를 내부 호출하지 않습니다.

ScanService의 기존 status enum은 유지하고 `analysis.stages.correlation`을 추가했습니다. Engine 예외는 이 stage만 FAILED로 처리하고 기존 결과를 저장합니다. 개별 rule 실패는 나머지를 계속 실행하며 PARTIAL입니다. Graph 실패 시 correlation은 SKIPPED입니다. 선행 Checkov/정적 분석이 PARTIAL/FAILED이면 생성 가능한 근거만 사용하고 correlation coverage도 PARTIAL로 표시합니다. Finding과 graph는 기존 최종 트랜잭션에서 함께 저장합니다.

## 규칙

| Rule | 조건 | Severity 정책 |
|---|---|---|
| TFCR001 | TFSC003/004 module output → IAM_POLICY | HIGH; 정확히 매핑된 HIGH/CRITICAL privileged IAM Checkov가 있으면 CRITICAL |
| TFCR002 | TFSC003/004 module output → CODE_EXECUTION | CRITICAL |
| TFCR003 | secret 값 attribute → CODE_EXECUTION | HIGH |
| TFCR004 | secret 값 attribute → ENVIRONMENT | MEDIUM, potential exposure |
| TFCR005 | mutable module output → resource attribute + 같은 resource/attribute의 Checkov | 가중치 기반, 매핑 품질에 따라 상한 적용 |
| TFCR006 | full SHA로 고정된 외부 Git + TFSC006 → IAM_POLICY/CODE_EXECUTION/CREDENTIAL/SECRET_STORAGE | LOW |
| TFCR007 | TFSC008/009 provider ← 실제 USES_PROVIDER edge ← HIGH/CRITICAL Checkov resource | MEDIUM, 인과관계 주장 없음 |
| TFCR008 | 고정된 SHA가 아닌 opaque external output → sensitive sink | IAM_POLICY/CODE_EXECUTION/CREDENTIAL HIGH, 그 외 MEDIUM |

TFCR001의 privileged IAM allowlist는 Checkov `CKV_AWS_62`, `CKV_AWS_63`입니다. 다른 rule이나 UNKNOWN severity만으로 CRITICAL로 승격하지 않습니다. 실제 설치된 Checkov rule 구현의 대상 resource/의미를 확인했습니다. Rule metadata가 변하면 allowlist를 검토해야 합니다.

TFCR005는 evaluated attribute 정보가 없을 때 resource 수준 관련성을 허용하되, `checkov_mappings.level=resource`로 명시하고 dependency가 해당 문제의 원인이라고 주장하지 않습니다. evaluated_keys가 제공되면 해당 sink attribute와 구조적으로 일치해야 합니다.

TFCR006과 TFCR008의 pinned 예외는 full commit SHA 형태에 대한 정적 분류입니다. Commit 존재·안전성을 검증하지 않습니다. Tag는 branch로 취급하지 않으며 mutable 규칙 001/002/005를 생성하지 않습니다. 외부 구현이 opaque인 tag/Registry/HTTP dependency에는 008이 독립적으로 적용될 수 있습니다.

Source classifier가 SECRET_SOURCE라고 표시한 모든 attribute를 무조건 secret 값으로 취급하지 않습니다. 실제 후보는 다음과 같습니다.

- aws_ssm_parameter.value
- aws_secretsmanager_secret_version.secret_string / secret_binary
- azurerm_key_vault_secret.value
- google_secret_manager_secret_version.secret_data

name/id/arn과 aws_secretsmanager_secret의 metadata만으로 003/004를 생성하지 않습니다. 실제 secret 내용을 탐지하거나 읽지 않으며, 이 분류도 값이 존재한다는 runtime 증거는 아닙니다.

## Evidence와 UUID

NormalizedFinding에 UUID를 생성하고 그대로 DB에 저장합니다. 이 additive 변경으로 저장 전 Correlation이 원본 Finding UUID를 안정적으로 참조합니다. 새로운 DB table/column/migration은 없으며 Alembic head는 0003입니다.

`Finding.raw_data`의 핵심 필드:

```json
{
  "schema_version": 1,
  "source": "module.policy_source.policy",
  "sink": "aws_iam_role_policy.admin.policy",
  "source_entity": "module.policy_source",
  "sink_entity": "aws_iam_role_policy.admin",
  "source_attribute": "policy",
  "sink_attribute": "policy",
  "source_finding_ids": ["source-trust-finding-uuid"],
  "related_finding_ids": ["checkov-finding-uuid"],
  "flow_edge_ids": ["ordered-edge-uuid"],
  "dependency_edge_ids": [],
  "flow_path": ["module.policy_source.policy", "local.policy", "aws_iam_role_policy.admin.policy"],
  "confidence": "MEDIUM",
  "path_quality": "OPAQUE"
}
```

추가로 source/sink endpoint(file/line, entity UUID, module hierarchy, opaque boundary), Checkov mapping method/confidence/attribute, score components, rule version, dedup key, suppressed rule IDs, attack_path를 저장합니다. TFCR007은 flow 경로 대신 resource → provider configuration → required provider의 dependency edge UUID를 저장합니다.

Detail API는 동일 scan 범위에서 원본 Source Trust/Checkov Finding과 graph edge를 batch load하여 반환합니다. Edge에는 expression/transformation metadata가 있으므로 UI에서 원래 경로를 재구성할 수 있습니다. 삭제/과거 migration 때문에 사라진 근거는 `missing_edge_ids`, `missing_finding_ids`로 표시하며 다른 scan에서 대신 찾지 않습니다. Evidence UUID는 JSONB 참조이므로 별도 FK는 없고 scan pipeline과 API scope로 일관성을 유지합니다.

## Severity와 Confidence

Severity는 가능한 영향의 크기, confidence는 정적 연결 근거의 확실성입니다. 높은 severity가 실제 공격의 확정성을 의미하지 않습니다.

경량 scoring은 mutable dependency(+3), sink criticality(0~4), Source Trust severity(0~4), Checkov severity(0~4), opaque(+1), 직접 resolved path(+1)를 기록합니다. 기본 mapping은 11 이상 CRITICAL, 7 이상 HIGH, 3 이상 MEDIUM, 그 외 LOW이며 rule별 override가 우선합니다. 001의 명시적 escalation 조건, 003 HIGH, 004 MEDIUM, 안전 pin 006 LOW, dependency 007 MEDIUM은 보수적인 정책입니다. 이는 module 전체 trust/risk score가 아닙니다.

Confidence:

- RESOLVED path + 정확한 mapping: HIGH
- OPAQUE boundary, conditional potential path, 또는 위치 기반 MEDIUM mapping: MEDIUM
- 모호한 resource mapping: 거부. LOW mapping을 근거로 high-risk 결과를 생성하지 않음
- MEDIUM Checkov mapping에 의존하는 CRITICAL은 HIGH로 제한

Secret→environment는 정상적인 패턴일 수도 있으므로 **potential secret exposure surface**로 설명합니다. Provider rule은 variability 증가를 설명하며 provider version이 Checkov 문제를 일으켰다고 단정하지 않습니다. 설명과 remediation은 코드 템플릿이며 LLM을 사용하지 않습니다.

## Resource / module mapping

Checkov matcher:

1. Instance index를 선언 주소로 정규화한 exact match. File/range가 있으면 일치도 확인합니다.
2. Checkov가 module path를 생략했을 때만 local resource address + 정확한 file path + 가능한 line overlap으로 후보를 좁힙니다.
3. 후보가 유일하면 MEDIUM. 같은 local module directory가 여러 instance에서 호출돼 모호하면 거부합니다.
4. 명시적으로 다른 module path를 가진 Finding은 suffix만으로 연결하지 않습니다. Substring matching은 없습니다.

Source Trust는 exact resource address, 존재하는 entity UUID, module_path, 제공된 location을 확인합니다. Nested `module.a.module.external`도 같은 정책입니다. TFCR007도 실제 provider dependency edge만 사용하며 이름 prefix만으로 같은 provider라고 판단하지 않습니다. Provider inheritance를 추정해서 없는 edge를 만들지 않습니다.

## Path quality / False-positive 억제

- RESOLVED: 각 endpoint가 선언에 연결됨. Computed resource 값의 runtime resolution을 의미하지 않습니다.
- OPAQUE: 존재하는 외부 module 선언의 output 경계만 불투명하고 이후 경로는 연결됨. Confidence는 MEDIUM입니다.
- PARTIAL: loop aggregate 또는 field projection 집계로 세부 영향 경로가 불명확함. 이번 규칙은 생성하지 않습니다.
- UNRESOLVED: 누락 선언/input/output 등의 불연속. 이번 규칙은 생성하지 않습니다.

조건식은 potential 영향으로 유지하고 confidence를 낮춥니다. 외부 module input과 output 사이의 내부 edge를 발명하지 않습니다. Source Trust + Checkov가 같은 repository에만 존재하는 경우, 다른 resource/module/provider인 경우, Checkov attribute가 sink와 다른 경우에는 결합하지 않습니다.

## Dedup / priority

기본 우선순위: 002 → 001 → 003 → 004 → 005 → 008 → 006 → 007.

Candidate는 resolved 우선·짧은 path 우선으로 정렬합니다. 같은 source logical address + sink logical address에는 가장 구체적인 규칙 하나만 남깁니다. 저장 dedup key는 rule/source/sink/related Checkov rule이며 alternate path나 같은 Checkov의 여러 관찰로 Finding을 늘리지 않습니다. 나머지 규칙은 suppressed_rule_ids, summary.suppressed로 기록합니다. Pinned source는 008 적용에서 제외하여 낮은 위험도의 006이 유지됩니다. 같은 endpoint pair의 모든 대안 경로를 evidence에 저장하지는 않습니다.

## API / Dashboard

```bash
curl 'http://localhost:8000/api/scans/SCAN_ID/correlations?severity=HIGH&limit=20&offset=0'
curl --get 'http://localhost:8000/api/scans/SCAN_ID/correlations' \
  --data-urlencode 'source=module.policy_source.policy' \
  --data-urlencode 'sink=aws_iam_role_policy.admin.policy' \
  --data-urlencode 'confidence=MEDIUM'
curl 'http://localhost:8000/api/scans/SCAN_ID/correlations/FINDING_ID'
curl 'http://localhost:8000/api/scans/SCAN_ID/findings?scanner=correlation'
```

List는 기존 FindingResponse 배열이며 severity/rule_id/source/sink/confidence/limit/offset을 지원합니다. source/sink는 exact logical address입니다. Detail은 `{finding, evidence, attack_path}`이며 다른 scan의 Finding UUID는 404입니다.

`attack_path`는 SOURCE_TRUST → FLOW → SINK → CHECKOV, provider 규칙은 SOURCE_TRUST → DEPENDENCY → CHECKOV 형태입니다. 명칭은 UI 계약이며 실제 공격 발생의 선언이 아닙니다.

기존 Scan/Dashboard 전체 Finding 집계에 correlation을 포함합니다. Dashboard의 `scanner_breakdown`과 `correlated_risks`로 별도 확인할 수 있습니다. 기존대로 COMPLETED scan만 dashboard finding 집계에 포함하며 재검사 결과는 별도 관찰로 계산합니다. 원본 findings와 correlation을 더한 총합은 독립 취약점 개수가 아닙니다.

`analysis.correlation`에는 rules_evaluated/findings/severity counts/paths_evaluated/traversal_steps/suppressed/truncated/duration_ms/rule_errors를 추가합니다. 과거 scan에는 null일 수 있습니다.

## 성능 / 한도

기존 MAX_FLOW_DEPTH=30, MAX_FLOW_PATHS=20, MAX_FLOW_SEARCH_STEPS=100000을 재사용합니다. Correlation에서는 traversal budget이 **전체 source에 걸친 scan 단위**이고 paths는 source당 상한입니다. Cycle-safe한 simple path만 탐색하며 상한 도달 시 summary.truncated와 PARTIAL로 표시합니다. Truncation 때문에 0 findings가 전체 위험 부재의 증명은 아닙니다.

Entity/Finding/adjacency index는 선형으로 만들고 규칙들은 준비된 candidate만 재사용합니다. 모든 simple path 열거는 일반적으로 비쌀 수 있어 depth/path/work 한도를 적용합니다. Correlation list JSONB filter는 기존 scan index를 활용하지만 별도 JSONB expression index는 추가하지 않았습니다.

## 예제 / 실행 / 검증

실제 저장 후 Detail service로 생성한 API 응답 예제:

- [correlation-iam.json](../examples/correlation-iam.json): mock normalized Checkov + mutable IAM 경로
- [correlation-user-data.json](../examples/correlation-user-data.json): mutable bootstrap 경로
- [correlation-opaque.json](../examples/correlation-opaque.json): Registry output → environment

backend에서 `.venv/bin/python -m scripts.generate_correlation_examples`로 재생성합니다. IAM 예제의 Checkov HIGH는 명시적 테스트 fixture이며 실제 CLI의 severity를 임의로 승격한 결과가 아닙니다.

```bash
docker compose up --build
cd backend
.venv/bin/pytest tests -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/terraform_intelligence \
  .venv/bin/pytest tests/integration/test_postgres.py -q
# repository root
npm run typecheck
```

PostgreSQL 테스트는 고유 임시 schema에서 migration upgrade/downgrade/re-upgrade, JSONB filters, correlation evidence UUID 연결, 원본 Finding 보존을 확인합니다. 기존 Checkov와 API/flow 회귀 테스트도 유지합니다.

## 현재 제한과 다음 단계

정적 potential influence 분석입니다. Terraform/runtime/provider 실행, 외부 module 다운로드, external 내부 흐름, 실제 secret 탐지, taint severity 모델, reputation/maintainer 신호, historical diff, malicious change 탐지, Trusted 판정/전체 risk score, graph UI는 구현하지 않았습니다. 모호한 alias·미지원 expression·집계 field path에서는 false negative를 감수하며 결과를 억제합니다. Checkov severity UNKNOWN은 그대로 두므로 TFCR007/001 escalation 조건을 만족하지 않을 수 있습니다.

다음 단계는 CorrelationRule, Context index, shared path search, versioned evidence, UUID provenance, attack_path, mapping confidence와 score breakdown을 재사용할 수 있습니다. 새로운 attack-pattern rule은 독립된 근거 predicate를 추가해야 하며 기존 Finding 수를 늘리기 위한 repository-level 조인은 허용하지 않습니다.

## 이번 구현의 검증 기록

- 전체 pytest: **270 passed, 1 skipped**. Skip은 기본 실행에서 TEST_DATABASE_URL을 지정하지 않은 PostgreSQL 테스트이며 별도 실행은 **1 passed**였습니다. 기존 TestClient/httpx deprecation warning 1개가 남습니다.
- Ruff, formatter, TypeScript compile, Docker build 통과. PostgreSQL에서 기존 Alembic head=0003 upgrade/downgrade/re-upgrade와 correlation JSONB 필터·원본 UUID/evidence 연결을 검증했습니다.
- 실제 VPC scan `40fffa99-0f3c-4a1f-b63c-654619023422`, commit `b3abd6df2ecf052451a361ed55b8f06f8742a795`: **481 entities / 1,466 dependency edges / 947 flow edges / Checkov 46 / Source Trust 1 / Correlation 0**.
- Correlation API도 빈 배열을 반환했습니다. 이 root에는 외부 module 출력 source가 없고 Checkov의 severity는 UNKNOWN이므로 provider rule의 HIGH/CRITICAL 조건도 충족하지 않았습니다. 기존 flow 진단으로 stage coverage는 PARTIAL, rule error/truncation은 없습니다.
- 전체 scan 104,277 ms 중 로그상 Checkov 구간은 약 101초였습니다. 저장 결과를 메모리 domain model로 load한 뒤 별도 측정한 Context index 생성은 34.973 ms, 8개 rule 평가는 0.449 ms, 합계 35.422 ms였습니다. DB load·clone·Checkov는 이 correlation microbenchmark에 포함하지 않습니다. 단일 환경의 0-candidate 실행이므로 모든 repository의 시간 보장은 아닙니다. `analysis.correlation.duration_ms`는 context 생성 이후 Engine 평가 시간을 정수 ms로 기록합니다.
- Mutable IAM fixture + 정확한 CKV_AWS_62 HIGH는 **TFCR001 CRITICAL / confidence MEDIUM / OPAQUE** 한 건으로 suppression됩니다. source와 Checkov가 repository에만 공존하는 unrelated fixture는 **0건**입니다.
