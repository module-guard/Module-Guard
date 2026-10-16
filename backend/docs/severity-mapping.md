# 심각도 매핑과 권장 조치 (Checkov)

ModuleGuard는 Checkov를 `--skip-download`로 실행합니다. 이 모드에서는 Prisma Cloud 메타데이터를 받지 않으므로 대부분의 탐지에 `severity`와 `guideline`이 비어 있습니다. 비어 있는 값만 ModuleGuard가 채웁니다.

| 항목 | 코드 | 출처 표시 (`Finding.raw_data`) |
|---|---|---|
| 심각도 | `app/scanners/severity_overrides.py` `SEVERITY_OVERRIDES` | `"severity_source": "moduleguard"` |
| 권장 조치 | `app/scanners/remediation.py` `REMEDIATIONS` | `"guideline_source": "moduleguard"` |

Finding/Scan 스키마와 DB는 바뀌지 않습니다. 채운 값은 기존 `severity`, `guideline` 컬럼에 들어가고 출처는 `raw_data`에만 남습니다.

## 우선순위

1. **Checkov가 준 값이 항상 우선합니다.** `severity`가 유효한 등급이면 그대로 쓰고 `severity_source`를 붙이지 않습니다. `guideline`이 비어 있지 않은 문자열이면 그대로 둡니다.
2. 비어 있을 때만 표의 값을 씁니다. 공백뿐인 guideline도 비어 있는 것으로 봅니다.
3. 표에 없는 규칙은 `UNKNOWN`·guideline 없음으로 남습니다. 추측하지 않습니다.

## 심각도 기준

`severity_overrides.py` 상단 docstring과 같습니다.

| 등급 | 기준 | 예 |
|---|---|---|
| HIGH | 그대로 배포하면 리소스가 직접 노출되거나 권한이 넓어짐 | 관리자급 IAM(CKV_AWS_62/63), 0.0.0.0/0 인바운드, 공개 ACL, 인증 없는 Lambda URL |
| MEDIUM | 보호 장치 누락 (암호화, 로깅, 인증 강도, 범위 제한) | 저장 암호화, 로그 내보내기, 비밀번호 정책, 시크릿 교체 |
| LOW | 운영 위생, 복원력, 관리 편의 | 백업·Multi-AZ·모니터링, 태그, 설명 누락 |
| CRITICAL | **지정하지 않음.** 모듈은 대부분의 리소스를 변수로 켜고 끄므로 정적 규칙 하나로 실제 노출을 판단할 수 없습니다. 상관 탐지(TFCR) 전용입니다. | |

고정된 Checkov 3.3.26(`requirements-scanner.txt`) 기준으로 모든 규칙의 이름·대상 리소스를 `checkov --list`로 확인했고, CKV_AWS_62/63과 이름만으로 의미가 모호한 규칙(CKV_AWS_133, 211, 304, 382, 283, 258, 45, 93)은 구현 코드를 열어 실패 조건을 확인했습니다. 줄마다 근거 주석을 달았습니다. `tests/unit/test_severity_overrides.py`가 표 전체를 다시 적어 두므로 의도하지 않은 수정은 테스트에서 실패합니다.

### CKV_AWS_62 / CKV_AWS_63과 TFCR001

TFCR001은 정확히 매핑된 HIGH/CRITICAL `CKV_AWS_62`·`CKV_AWS_63`이 있을 때 CRITICAL로 올라갑니다(`app/correlation/scoring.py`의 `PRIVILEGED_IAM_RULES`, [risk-correlation.md](risk-correlation.md)). 이전에는 두 규칙이 매핑에 없어 항상 UNKNOWN이었고 이 상향이 동작하지 않았습니다.

- 대상: `aws_iam_role_policy`, `aws_iam_user_policy`, `aws_iam_group_policy`, `aws_iam_policy`, `aws_ssoadmin_permission_set_inline_policy`의 `policy`/`inline_policy`
- CKV_AWS_62: `Effect = Allow`이고 `Action`에 `"*"`, `Resource`에 `"*"`가 함께 있는 문장 (전체 관리자)
- CKV_AWS_63: `Effect = Allow`이고 `Action`에 `"*"`가 있는 문장 (리소스 무관)
- 둘 다 **HIGH**입니다. 같은 의미의 `data "aws_iam_policy_document"` 규칙(CKV_AWS_1, CKV_AWS_49)도 HIGH로 지정했지만 TFCR001 allowlist에는 넣지 않았습니다(상관 규칙은 수정하지 않음).
- 실제 Checkov로 관리자 정책을 검사해 두 규칙이 HIGH + 권장 조치로 나오는지, 그리고 Checkov가 심각도를 보내지 않는 CKV_AWS_62로 파이프라인 전체를 돌렸을 때 TFCR001이 CRITICAL이 되는지 `tests/integration/test_checkov.py`가 확인합니다. 같은 입력으로 이전 `main`에서는 CKV_AWS_62 UNKNOWN, TFCR001 HIGH였습니다.

## 측정: terraform-aws-modules

API와 같은 인자(`CheckovScanner`)로 7개 모듈을 검사하고, 저장된 보고서를 정규화해 `finding_scope()`로 모듈 본체(scope=MODULE)와 예제를 나눴습니다. 기준선은 이 브랜치 이전 `main`의 매핑(45개 규칙)입니다.

| 모듈 | 커밋 | 날짜 | 태그 |
|---|---|---|---|
| vpc | b3abd6d | 2026-09-18 | v6.7.3 |
| eks | 91b8a8d | 2026-10-08 | (태그 없음) |
| s3-bucket | 9afa4fc | 2026-10-11 | v5.17.0 |
| rds | 175da04 | 2026-09-18 | v7.2.2 |
| iam | da8e6a8 | 2026-10-04 | (태그 없음) |
| security-group | b3c1b2e | 2026-08-06 | (태그 없음) |
| lambda | 851d209 | 2026-10-07 | (태그 없음) |

### UNKNOWN 비율 (scope=MODULE)

| 모듈 | 전체 탐지 | 모듈 본체 | UNKNOWN 이전 | UNKNOWN 이후 |
|---|---:|---:|---:|---:|
| eks | 98 | 23 | 0 (0.0%) | 0 (0.0%) |
| iam | 320 | 35 | 2 (5.7%) | 0 (0.0%) |
| lambda | 113 | 6 | 4 (66.7%) | 0 (0.0%) |
| rds | 131 | 89 | 78 (87.6%) | 0 (0.0%) |
| s3-bucket | 172 | 133 | 2 (1.5%) | 0 (0.0%) |
| security-group | 11 | 7 | 0 (0.0%) | 0 (0.0%) |
| vpc | 46 | 21 | 0 (0.0%) | 0 (0.0%) |
| **합계** | **891** | **314** | **86 (27.4%)** | **0 (0.0%)** |

예제·테스트까지 포함한 전체 탐지 기준으로는 169/891(19.0%) → 0/891입니다. 이 7개 모듈에서 나온 규칙은 81개이며 모두 표에 있습니다. 다른 모듈에서는 표에 없는 규칙이 UNKNOWN으로 남을 수 있습니다.

### 기준선에서 UNKNOWN이던 규칙 (모듈 본체, 빈도순)

| 규칙 | 건수 | 전체 scope | 지정 |
|---|---:|---:|---|
| CKV_AWS_353 RDS Performance Insights | 13 | 13 | LOW |
| CKV_AWS_293 RDS 삭제 보호 | 13 | 13 | LOW |
| CKV_AWS_118 RDS Enhanced Monitoring | 13 | 13 | LOW |
| CKV_AWS_157 RDS Multi-AZ | 10 | 10 | LOW |
| CKV_AWS_304 Secrets Manager 90일 교체 | 8 | 8 | MEDIUM |
| CKV_AWS_129 RDS 로그 내보내기 | 8 | 8 | MEDIUM |
| CKV_AWS_16 RDS 저장 암호화 | 4 | 4 | MEDIUM |
| CKV_AWS_133 RDS 자동 백업 | 3 | 3 | LOW |
| CKV_AWS_226 RDS 마이너 자동 업그레이드 | 2 | 2 | LOW |
| CKV_AWS_211 RDS CA 인증서 | 2 | 2 | LOW |
| CKV2_AWS_60 RDS 스냅샷 태그 복사 | 2 | 2 | LOW |
| CKV_AWS_15 비밀번호 정책 대문자 | 1 | 1 | MEDIUM |
| CKV_AWS_11 비밀번호 정책 소문자 | 1 | 1 | MEDIUM |
| CKV_AWS_50 Lambda X-Ray | 1 | 32 | LOW |
| CKV_AWS_51 ECR 태그 불변 | 1 | 2 | MEDIUM |
| CKV_AWS_163 ECR 푸시 시 스캔 | 1 | 2 | MEDIUM |
| CKV_AWS_136 ECR KMS 암호화 | 1 | 2 | MEDIUM |
| CKV_AWS_18 S3 액세스 로깅 | 1 | 1 | MEDIUM |
| CKV2_AWS_61 S3 수명 주기 | 1 | 1 | LOW |

예제 쪽에서만 UNKNOWN이던 규칙(CKV_AWS_258 Lambda URL 31건 등)도 함께 지정했습니다. 그 밖에 이번 측정에서는 나오지 않았지만 같은 계열이라 의미가 분명한 규칙(IAM 권한 상승·자격 증명 노출 계열, 보안 그룹 0.0.0.0/0 인바운드, 공개 S3 ACL/정책, RDS 공개 접근 등)을 추가해 총 110개입니다.

> 참고: 작업 배경에 적힌 "terraform-aws-eks 109건 중 98건 UNKNOWN"은 그보다 적은 매핑(34개) 기준으로 보고된 수치이며 여기서 재현하지 않았습니다. 이 브랜치 직전 `main`(45개)과 위 eks 커밋 기준으로는 98건 중 1건만 UNKNOWN이었고, 남은 UNKNOWN은 주로 rds·lambda 계열이었습니다.

### 다시 측정하는 방법

`backend/`에서 별도 venv의 Checkov로 실행합니다(README의 `.venv-checkov`).

```python
from pathlib import Path
from app.core.config import Settings
from app.scanners.checkov import CheckovScanner
from app.scanners.normalizer import CheckovNormalizer, SeverityMapper
from app.scanners.scope import MODULE, finding_scope
from app.scanners.severity_overrides import SEVERITY_OVERRIDES

settings = Settings(
    checkov_binary=str(Path(".venv-checkov/bin/checkov").resolve()),
    checkov_timeout_seconds=1800,
    max_process_output_mb=500,
    _env_file=None,
)
# 모듈 사본. 부모 디렉터리에 checkov-config.yml이 생깁니다.
target = Path("/path/to/work/vpc/module")
result = CheckovScanner(settings).scan(target)
findings = CheckovNormalizer(SeverityMapper(SEVERITY_OVERRIDES)).normalize(result, target).findings
body = [f for f in findings if finding_scope(f.file_path, f.raw_data) == MODULE]
unknown = [f for f in body if f.severity == "UNKNOWN"]
```

모듈별 `result.raw_data`를 JSON으로 저장해 두면 매핑을 바꿀 때 Checkov를 다시 돌리지 않고 집계만 다시 할 수 있습니다. Checkov 버전을 올리면 규칙 구현이 바뀌었는지 다시 확인하고 이 표를 갱신하세요.

## 권장 조치 문구

- `REMEDIATIONS`: `rule_id` → 한국어 1~2문장. 바꿀 Terraform 인자를 가능한 한 직접 적습니다.
- 위 측정의 전체 scope 빈도 상위 50개 규칙과 `SEVERITY_OVERRIDES`의 모든 규칙에 문구가 있습니다(`tests/unit/test_remediation.py`).
- `CheckovNormalizer`는 Checkov `guideline`이 비어 있을 때만 이 문구를 넣고 `raw_data.guideline_source = "moduleguard"`를 남깁니다. 심각도 지정과는 독립입니다. Checkov가 심각도를 준 탐지라도 guideline이 비어 있으면 문구를 채웁니다.
- 생성자에 `remediations={}`를 넘기면 채우지 않습니다.

## 이미 저장된 검사에 적용 (reapply)

매핑·문구는 새 검사에만 자동으로 적용됩니다. 기존 검사에는 `app/scanners/reapply.py`를 실행합니다. `backend/`에서 `.env`의 `DATABASE_URL`을 사용하며, 마이그레이션이 최신이어야 합니다(`alembic upgrade head`).

```sh
cd backend
.venv/bin/python -m app.scanners.reapply             # dry run: 바뀔 검사와 건수만 출력
.venv/bin/python -m app.scanners.reapply --apply     # 실제 반영
.venv/bin/python -m app.scanners.reapply --database-url postgresql+psycopg://...  # 다른 DB
```

Docker로 띄운 경우: `docker compose exec api python -m app.scanners.reapply --apply`.

하는 일:

1. **심각도**: `scanner = checkov`이고 저장된 심각도가 `UNKNOWN`이거나 ModuleGuard가 지정했던(`severity_source = moduleguard`) 탐지만 현재 표로 다시 계산합니다. Checkov가 준 심각도는 건드리지 않습니다.
2. **권장 조치**: `scanner = checkov`이고 guideline이 비어 있거나 ModuleGuard가 채웠던(`guideline_source = moduleguard`) 탐지만 현재 문구로 채우거나 갱신합니다. 표에서 빠진 규칙이면 ModuleGuard가 채운 문구를 지웁니다. Checkov가 준 guideline은 건드리지 않습니다.
3. **scope와 집계**: 모듈 본체/예제 분류를 다시 하고 검사의 등급별 건수·`scope_summary`를 다시 씁니다.
4. **버전 배지**: `refresh_rule_counts`로 모듈 버전의 CRITICAL/HIGH 규칙 수를 다시 계산합니다.

여러 번 실행해도 결과가 같습니다(두 번째 실행은 변경 0건).

### 하지 않는 일: 상관 탐지는 다시 계산하지 않습니다

reapply는 **저장된 Checkov 탐지의 심각도(와 guideline·집계)만** 바꿉니다. 상관 탐지(TFCR001~008)는 검사 당시의 Checkov 심각도로 계산되어 저장되며 reapply가 다시 평가하지 않습니다. 예를 들어 CKV_AWS_62가 UNKNOWN이던 시점에 저장된 TFCR001은 reapply 후에도 HIGH로 남습니다. 새 매핑으로 TFCR001 CRITICAL 상향 등을 반영하려면 해당 모듈을 **다시 검사**해야 합니다.
