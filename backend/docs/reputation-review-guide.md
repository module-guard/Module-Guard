# 평판 근거 독립 평가 워크시트 (#52, 정책 evidence-v1)

이 문서는 `reputation-cohort.json`의 `reviewer_a` / `reviewer_b`를 채우는 두 평가자를 위한 지침이다.
두 값은 **서로 독립한 사람의 판단**이어야 하며 도구나 다른 평가자가 대신 채우지 않는다.
기계로 재현 가능한 부분(표본 선정, 근거 스냅샷, digest, 결측률·민감도·결정성)은 이미 끝났고
`python scripts/reputation_cohort.py validate | analyze`로 확인한다. 이 문서는 사람 단계만 다룬다.

## 1. 독립성 규칙

1. 평가자 A, B는 각자 별도 브랜치(또는 로컬 사본)에서 작업한다. 상대의 브랜치·파일·메모를 열지 않는다.
2. 판단 전에 도구 출력(`analyze`, 앱의 평판 화면, `evaluate()` 결과)을 보지 않는다. 입력은 아래 두 가지뿐이다.
   - `reputation-cohort-evidence/slot-NN.json` — 수집한 근거 스냅샷(고정 commit의 파일 경로, 공개 응답 필드).
   - 공개 원본 — GitHub 저장소 페이지, Terraform Registry 모듈 페이지(스냅샷의 `commit`·`tag`·`stratum_facts.repo` 참고).
3. 한 슬롯의 판단을 끝내기 전에 다른 슬롯의 결과를 수정하지 않는다. 기입 후 수정이 필요하면
   커밋 메시지에 이유를 남기고 기존 값을 지우지 않는다.
4. 두 사람 모두 끝난 뒤에만 한 브랜치에 합치고 `disagreement`를 기록한다(§5).
5. 평가 집합(`split: evaluation`)의 불일치로 정책이나 표본을 고치지 않는다. 정책 보정은 보정 집합
   (`calibration`)의 결과로만 제안하고, 새 정책 버전과 새 평가 집합으로 다시 검증한다.

## 2. 기입 형식

`reviewer_a` / `reviewer_b`는 `metric_id`를 키로 하는 객체이다. 기입하지 않은 슬롯은 `null`로 둔다.

```json
{
  "security_policy_file": {"status": "confirmed", "reason": null},
  "pushed_at": {"status": "not_applicable", "reason": "NOT_GITHUB_SOURCE"}
}
```

`metric_id`는 10개이다: `security_policy_file`, `readme_file`, `test_files`, `commit`, `archived`,
`pushed_at`, `created_at`, `stargazers_count`, `registry_published_at`, `registry_downloads`
(`reputation_service.evaluate()`의 metric id). 모든 항목을 채울 필요는 없지만,
**두 사람이 모두 기입한 항목만** 일치도 계산에 들어간다.

## 3. 판단 상태

정책 문서의 상태 어휘를 그대로 쓴다. 평가자는 "원본을 보면 이 항목이 어떤 상태로 기록되어야 하는가"를 적는다.
점수나 좋고 나쁨을 적지 않는다.

| 상태 | 쓰는 경우 |
| --- | --- |
| `confirmed` | 원본에서 값을 확인할 수 있다. 값이 `false`·`0`이어도 확인된 것이다. |
| `insufficient` | 확인하려 했으나 원본 자료가 모자라 판단할 수 없다(예: 파일 목록이 없다). |
| `unverified` | 확인할 수 있는 출처가 있으나 아직 확인하지 않았거나 확인하지 못했다. |
| `failed` | 출처에 접근했지만 오류·제한으로 값을 얻지 못했다. |
| `not_applicable` | 이 모듈 출처에는 해당 항목이 존재하지 않는다(예: Registry 출처의 GitHub 필드). |
| `stale` | 확인은 되었으나 수집 후 7일이 지나 최신이라 볼 수 없다. |

## 4. 축별 판단 기준

| 축 | metric_id | 판단 기준 |
| --- | --- | --- |
| 보안 운영 | `security_policy_file` | 고정 commit 파일 목록에 `SECURITY.md` 또는 `.github/SECURITY.md`(대소문자 무시)가 **있는지 없는지** 확인되면 `confirmed`. 목록이 없으면 `insufficient`. 내용의 질은 보지 않는다. |
| 문서 | `readme_file` | 파일명이 `readme`로 시작하는 파일이 목록에 있는지 확인되면 `confirmed`. |
| 테스트 | `test_files` | `tests/` 아래 파일 또는 `*.tftest.hcl`의 유무가 확인되면 `confirmed`. 테스트 통과·품질은 판단하지 않는다. |
| 출처 추적 | `commit` | 스냅샷의 `commit`이 원본 저장소에 실제로 존재하면 `confirmed`. |
| 유지보수 | `archived`, `pushed_at`, `created_at` | GitHub 공개 응답에 필드가 있고 원본 페이지와 일치하면 `confirmed`. 모듈 출처가 GitHub가 아니면 `not_applicable`. 오래된 pushed_at을 관리 중단으로 해석하지 않는다. |
| 출처 추적 | `registry_published_at` | Registry 출처에서 값이 확인되면 `confirmed`, GitHub 출처이면 `not_applicable`. |
| 인기도 | `stargazers_count`, `registry_downloads` | 숫자가 원본과 일치하면 `confirmed`. 안전성이나 품질로 해석하지 않으며 점수에 쓰지 않는다. |

`status`만 판단하지 말고, `confirmed`일 때 **값**이 원본과 다르면 기입 대신 §5에 따라 메모를 남긴다
(값 불일치는 도구 오류 후보이므로 일치도 계산과 별도로 본다).

### 누락 사유 코드

`status`가 `confirmed`가 아니면 `reason`에 아래 코드 하나를 적는다. 확인된 항목은 `null`이다.

| 코드 | 의미 |
| --- | --- |
| `NO_MANIFEST` | 파일 목록(manifest)이 없다. |
| `NOT_GITHUB_SOURCE` | 출처가 GitHub가 아니어서 GitHub 필드가 없다. |
| `NOT_REGISTRY_SOURCE` | 출처가 Registry가 아니어서 Registry 필드가 없다. |
| `FIELD_ABSENT` | 응답에 해당 필드가 없다. |
| `RATE_LIMITED` | 요청 한도(403/429)로 얻지 못했다. |
| `NOT_FOUND` | 출처가 없다(404, 삭제·이전). |
| `NOT_PUBLIC` | 공개 저장소가 아니다. |
| `SOURCE_UNAVAILABLE` | 서버 오류·통신 오류·잘못된 응답. |
| `OTHER` | 위에 없다. 메모에 설명을 남긴다. |

## 5. 불일치 기록

두 평가자가 끝난 뒤 한 사람이 합쳐 슬롯별 `disagreement`를 채운다. 값이 같으면 `null`로 둔다.
다르면 항목별로 원자료와 정책을 대조해 다음을 적는다.

```json
{
  "readme_file": {
    "a": "confirmed", "b": "insufficient", "tool": "confirmed",
    "cause": "B가 스냅샷 파일 목록을 열지 않았다 | 정책 문구 모호 | 원본 변경 | 평가자 실수 | 기타",
    "resolution": "스냅샷 확인 후 B 기입 오류로 분류. 정책 변경 없음"
  }
}
```

원인은 (a) 평가자 실수, (b) 정책·판단 기준 모호, (c) 수집 근거 문제로 나눈다. (b)와 (c)만 정책 개선 후보이며
평가 집합에서 나온 것은 후속 이슈로 올리고 이번 집합을 다시 쓰지 않는다.

## 6. 표본 층화 규칙 (`validate`가 검증)

- **상태**: `archived` = GitHub `archived: true`. `new` = 보관되지 않았고 `collected_at` 기준 365일 이내 생성.
  `active` = 보관되지 않았고 365일보다 오래전에 생성되었으며 `collected_at` 기준 180일 이내 push.
- **규모**: 고정 commit 트리의 `.tf` 파일 수. `small` ≤ 20, `large` ≥ 50. 21–49개는 표본에서 제외한다.
- **공급자**: 저장소 이름의 `terraform-<aws|azurerm|google|kubernetes>-` 규칙. 네 이름이 모두 없으면 `other`.
- **고정 commit**: 수집 시점 기본 브랜치의 HEAD. 같은 commit이 태그와 일치하면 `tag`에 기록하고 아니면 `null`.
- **중복 금지**: 소문자 `owner/repo`가 30개 전체에서 유일하다. Registry URL은 `source`의 저장소로 환산한다.
- Registry URL 슬롯은 3개(5, 14, 23)이며, 이때 GitHub 필드는 평가 입력에 없어 `not_applicable`이 된다.
  층화 사실(`stratum_facts`)은 `source` 저장소의 GitHub 응답에서 따로 보관한다.

## 7. 끝난 뒤

```powershell
python scripts/reputation_cohort.py validate   # 기입 형식·상태·사유 코드 검사
python scripts/reputation_cohort.py analyze    # reviewers.axes[*].disagreement_pct / kappa
```

kappa가 정의되지 않으면(`null`) 기대 일치도가 1이거나 쌍이 없는 경우이다. 이때는 불일치율만 보고한다.
결과를 `reputation-cohort-report.md`에 옮기고 `reputation-policy.md`의 총점 공개 기준(§ 총점 공개 결정)과 대조한다.
