# 평판 근거 표본 검증 보고서 (#52, 정책 evidence-v1)

> **상태: 기계 검증 완료, 두 평가자 독립 판단 대기.** 이 보고서는 정확도·타당도를 주장하지 않는다.
> 총점은 계속 공개하지 않는다(`not_independently_validated`). 근거는 `reputation-policy.md`의
> "총점 공개 결정"에 있다.

## 1. 재현 방법

모든 수치는 커밋된 `reputation-cohort.json`과 `reputation-cohort-evidence/`에서 오프라인으로 재현된다.

```powershell
cd backend
python scripts/reputation_cohort.py validate
python scripts/reputation_cohort.py analyze
```

`validate`는 30슬롯 층화(공급자 5 × 규모 2 × 상태 3), 보정/평가 분리, 프로젝트 중복 없음, 고정 commit의
`.tf` 파일 수·생성·보관·push 시각에서 다시 계산한 규모·상태, `evidence_digest` 재계산을 검사한다.
아래 JSON은 `analyze` 출력 그대로이며 `tests/unit/test_reputation_cohort.py`가 현재 스크립트 출력과
일치하는지 매번 확인한다.

## 2. 표본 구성

수집 시각 2026-11-19T02:21:56Z, 정책 `evidence-v1`. 각 슬롯의 근거는 고정 commit(수집 시점 기본 브랜치
HEAD)의 파일 목록과 GitHub·Registry 공개 응답의 허용 필드이다. 층화 규칙은 `reputation-review-guide.md` §6.

| 슬롯 | 공급자 | 규모 | 상태 | 집합 | 저장소/모듈 | commit | tag |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | aws | small | new | evaluation | github.com/andreswebs/terraform-aws-ecr-repository | `4bd6889257` | 0.1.0 |
| 2 | aws | small | active | calibration | github.com/terraform-aws-modules/terraform-aws-sns | `4538b7e208` | v7.2.0 |
| 3 | aws | small | archived | evaluation | github.com/trussworks/terraform-aws-ecs-cluster | `7b1ac66c1b` | - |
| 4 | aws | large | new | calibration | github.com/terraform-aws-modules/terraform-aws-wafv2 | `36eceb918a` | v2.1.0 |
| 5 | aws | large | active | evaluation | registry.terraform.io/modules/terraform-aws-modules/cloudwatch/aws | `f6dff9f79a` | v6.0.0 |
| 6 | aws | large | archived | calibration | github.com/fpco/terraform-aws-foundation | `78c5fefa88` | - |
| 7 | azurerm | small | new | evaluation | github.com/hoolser/terraform-azurerm-keycloak-aca | `12368a2c71` | - |
| 8 | azurerm | small | active | calibration | github.com/Azure-Terraform/terraform-azurerm-virtual-network | `3074b01cd7` | v9.0.0 |
| 9 | azurerm | small | archived | evaluation | github.com/Azure/terraform-azurerm-naming | `2359079c7a` | 0.4.4 |
| 10 | azurerm | large | new | calibration | github.com/dis-way/terraform-azurerm-dis-modules | `d35b761d66` | - |
| 11 | azurerm | large | active | evaluation | github.com/Azure/terraform-azurerm-avm-ptn-alz | `91ccf41254` | - |
| 12 | azurerm | large | archived | calibration | github.com/Azure/terraform-azurerm-aks | `58ffc7c71a` | 11.7.0 |
| 13 | google | small | new | evaluation | github.com/GoogleCloudPlatform/terraform-google-cloud-composer | `dd0207685b` | - |
| 14 | google | small | active | calibration | registry.terraform.io/modules/terraform-google-modules/cloud-storage/google | `c7effc0be0` | v12.4.1 |
| 15 | google | small | archived | evaluation | github.com/terraform-google-modules/terraform-google-endpoints-dns | `b2e057ef19` | v3.0.1 |
| 16 | google | large | new | calibration | github.com/GoogleCloudPlatform/terraform-google-firebase | `cc9293eea6` | - |
| 17 | google | large | active | evaluation | github.com/terraform-google-modules/terraform-google-project-factory | `eeda8a6ece` | v18.4.0 |
| 18 | google | large | archived | calibration | github.com/forseti-security/terraform-google-forseti | `5d73e3a75b` | - |
| 19 | kubernetes | small | new | evaluation | github.com/controlplaneio-fluxcd/terraform-kubernetes-flux-operator-bootstrap | `5075409811` | v0.10.0 |
| 20 | kubernetes | small | active | calibration | github.com/harness/terraform-kubernetes-harness-delegate | `993aaef52d` | 0.3.0 |
| 21 | kubernetes | small | archived | evaluation | github.com/cookielab/terraform-kubernetes-metrics-server | `1e0dda42c0` | 0.11.2 |
| 22 | kubernetes | large | new | calibration | github.com/fabn/terraform-kubernetes-formation | `e2b3d8ee16` | v0.18.0 |
| 23 | kubernetes | large | active | evaluation | registry.terraform.io/modules/particuleio/addons/kubernetes | `3c63dcce04` | - |
| 24 | kubernetes | large | archived | calibration | github.com/oracle/terraform-kubernetes-installer | `3a4952f2cb` | - |
| 25 | other | small | new | evaluation | github.com/marcuwynu23/terraform-cloudflare-turnstile | `ad7aa87508` | - |
| 26 | other | small | active | calibration | github.com/adinhodovic/terraform-cloudflare-maintenance | `c79f28e5f1` | - |
| 27 | other | small | archived | evaluation | github.com/bluk/terraform-cloudflare-fastmail-dns | `6150ccdac9` | v0.7.0 |
| 28 | other | large | new | calibration | github.com/5xxeu/terraform-cloudflare-ezdns | `06d1dcb7d1` | v3.0.0 |
| 29 | other | large | active | evaluation | github.com/oracle-terraform-modules/terraform-oci-oke | `412ec395e0` | - |
| 30 | other | large | archived | calibration | github.com/oracle-quickstart/terraform-oci-oke-quickstart | `c07a068da7` | - |

- 후보 선정은 무작위가 아니라 목적 표집이다. 슬롯 2, 4, 5, 14, 17 다섯 개는 #46 후보 풀
  (`seeds/candidates.json`·`mirrors.json`)에서, 나머지는 GitHub 공개 검색과 Registry 공개 API로 찾았다.
  실제 사용 분포를 대표하지 않는다. aws 슬롯 1, 3, 6은 풀에 신생 소규모·보관 저장소가 없어
  (풀의 aws 저장소는 모두 보관되지 않았고 신생은 wafv2 하나로 대규모) 풀 밖 공개 검색으로 채웠다.
- Registry URL 슬롯은 3개(5, 14, 23)이다. GitHub URL 27개와 근거 구성이 다르다(§3.3).
- `new` 슬롯은 대부분 별이 거의 없는 신생 저장소이다. `other`는 cloudflare·oci 두 공급자만 포함한다.
- 고정 commit이 태그와 일치하는 슬롯만 `tag`가 있다. 태그 없는 항목은 HEAD commit으로 고정했다.
  Registry URL 슬롯 중 5와 14는 태그가 있으나 23은 태그가 없는 HEAD이다. 이 슬롯들의 `published_at`·
  `downloads`는 Registry의 최신 공개 버전 값이고 파일 목록은 수집 시점 기본 브랜치 HEAD라서 시점이
  다를 수 있다.

## 3. 지금 측정된 값

`analyze` 출력(수치는 모두 이 블록에서 가져온다).

<!-- analyze:start -->
```json
{
  "policy_version": "evidence-v1",
  "composition": {
    "provider": {
      "aws": 6,
      "azurerm": 6,
      "google": 6,
      "kubernetes": 6,
      "other": 6
    },
    "size": {
      "small": 15,
      "large": 15
    },
    "state": {
      "new": 10,
      "active": 10,
      "archived": 10
    },
    "split": {
      "evaluation": 15,
      "calibration": 15
    }
  },
  "source_form": {
    "github": 27,
    "registry": 3
  },
  "by_state": {
    "new": {
      "slots": 10,
      "missing_rate_pct": 0.0,
      "not_applicable_rate_pct": 12.5,
      "mean_coverage_pct": {
        "security": 100.0,
        "maintenance": 100.0,
        "provenance": 100.0,
        "documentation": 100.0,
        "tests": 100.0
      },
      "file_absent_pct": {
        "security_policy_file": 60.0,
        "readme_file": 0.0,
        "test_files": 70.0
      }
    },
    "active": {
      "slots": 10,
      "missing_rate_pct": 0.0,
      "not_applicable_rate_pct": 20.0,
      "mean_coverage_pct": {
        "security": 100.0,
        "maintenance": 70.0,
        "provenance": 100.0,
        "documentation": 100.0,
        "tests": 100.0
      },
      "file_absent_pct": {
        "security_policy_file": 90.0,
        "readme_file": 0.0,
        "test_files": 80.0
      }
    },
    "archived": {
      "slots": 10,
      "missing_rate_pct": 0.0,
      "not_applicable_rate_pct": 12.5,
      "mean_coverage_pct": {
        "security": 100.0,
        "maintenance": 100.0,
        "provenance": 100.0,
        "documentation": 100.0,
        "tests": 100.0
      },
      "file_absent_pct": {
        "security_policy_file": 70.0,
        "readme_file": 0.0,
        "test_files": 70.0
      }
    }
  },
  "by_size": {
    "small": {
      "slots": 15,
      "missing_rate_pct": 0.0,
      "not_applicable_rate_pct": 14.17,
      "mean_coverage_pct": {
        "security": 100.0,
        "maintenance": 93.33,
        "provenance": 100.0,
        "documentation": 100.0,
        "tests": 100.0
      },
      "file_absent_pct": {
        "security_policy_file": 80.0,
        "readme_file": 0.0,
        "test_files": 80.0
      }
    },
    "large": {
      "slots": 15,
      "missing_rate_pct": 0.0,
      "not_applicable_rate_pct": 15.83,
      "mean_coverage_pct": {
        "security": 100.0,
        "maintenance": 86.67,
        "provenance": 100.0,
        "documentation": 100.0,
        "tests": 100.0
      },
      "file_absent_pct": {
        "security_policy_file": 66.67,
        "readme_file": 0.0,
        "test_files": 66.67
      }
    }
  },
  "by_source_form": {
    "github": {
      "slots": 27,
      "missing_rate_pct": 0.0,
      "not_applicable_rate_pct": 12.5,
      "mean_coverage_pct": {
        "security": 100.0,
        "maintenance": 100.0,
        "provenance": 100.0,
        "documentation": 100.0,
        "tests": 100.0
      },
      "file_absent_pct": {
        "security_policy_file": 70.37,
        "readme_file": 0.0,
        "test_files": 70.37
      }
    },
    "registry": {
      "slots": 3,
      "missing_rate_pct": 0.0,
      "not_applicable_rate_pct": 37.5,
      "mean_coverage_pct": {
        "security": 100.0,
        "maintenance": 0.0,
        "provenance": 100.0,
        "documentation": 100.0,
        "tests": 100.0
      },
      "file_absent_pct": {
        "security_policy_file": 100.0,
        "readme_file": 0.0,
        "test_files": 100.0
      }
    }
  },
  "coverage_zero_without_applicable_metrics": [
    {
      "slot": 5,
      "axis": "maintenance"
    },
    {
      "slot": 14,
      "axis": "maintenance"
    },
    {
      "slot": 23,
      "axis": "maintenance"
    }
  ],
  "popularity_sensitivity": {
    "slots_with_popularity_values": 30,
    "slots_with_changed_non_popularity_output": [],
    "invariant_holds": true,
    "note": "coverage ignores popularity by construction; this checks for regressions only"
  },
  "determinism": {
    "same_input_same_output_and_digest": true
  },
  "reviewers": {
    "status": "pending",
    "reason": "no slot has both reviewer_a and reviewer_b"
  }
}
```
<!-- analyze:end -->

### 3.1 결정성

30/30 슬롯에서 같은 스냅샷을 두 번 평가해도 결과가 같고, `evidence_digest`가 스냅샷에서 다시 계산되어
일치한다(`determinism.same_input_same_output_and_digest: true`). 같은 digest와 같은 정책에서 같은 결과가
나온다는 회귀 근거이다. 시각은 입력(`collected_at`)이며 현재 시각을 읽지 않는다.

### 3.2 인기 조작 민감도

별 수·다운로드를 0과 10억으로 바꿔 다시 평가해도 30/30 슬롯에서 충족률과 인기도 외 모든 지표가
그대로였다. 이것은 충족률이 **구조상** 인기도를 읽지 않기 때문이며 경험적 발견이 아니다.
이 검사는 나중에 인기도가 계산에 섞이는 회귀를 막는 용도이다. 조작할 수 없다는 뜻이 아니다. 파일 존재
지표는 악의적 파일 추가로 조작할 수 있다(정책 문서).

### 3.3 결측률과 신규·소규모 편향

- 적용 가능한 인기도 외 지표 중 `insufficient`/`unverified`/`failed` 비율(`missing_rate_pct`)은 모든
  집단에서 0%였다. **수집이 30건 모두 성공했기 때문**이다. 요청 한도·출처 오류 같은 실패 경로는 실제
  자료로 측정되지 않았고(단위·통합 테스트만 있음) 이 0%를 신규·소규모 프로젝트에 결측이 없다는 근거로
  쓸 수 없다.
- 상태·규모별 충족률 차이는 대부분 구조에서 나온다. 보안·문서·테스트·출처 축은 모든 슬롯에서 100%이다.
  이 축들은 manifest가 있으면 값(`false` 포함)이 `confirmed`가 되는 지표 하나뿐이어서 충족률이
  관측 가능성만 말하고 품질을 구별하지 못한다.
- 유지보수 축 충족률은 `active` 집단에서 70%로 낮고 `small`은 93.33%, `large`는 86.67%이다.
  원인은 신규·소규모 프로젝트의 활동 부족이 아니라 Registry URL 슬롯 3개(5, 14, 23)이다. 이 슬롯은
  GitHub 필드가 모두 `not_applicable`이어서 유지보수 축에 해당 지표가 없는데 충족률이 **0**으로
  표시된다(`coverage_zero_without_applicable_metrics` 3건). "근거 없음/해당 없음"과 "확인 실패"가
  같은 0으로 보이는 한계이며, 정책의 "누락·해당 없음을 0점과 구분" 원칙과 충돌한다. 서비스 코드
  변경이 필요해 이번 이슈에서 고치지 않았다(후속).
- 파일 존재 지표(`file_absent_pct`)는 상태·규모별로 크게 다르다. `SECURITY.md` 없음은 new 60%, active 90%,
  archived 70%, small 80%, large 66.67%이고 테스트 파일 없음도 비슷하다. README는 모두 있다(0%).
  성숙한 `active` 모듈도 대부분 `SECURITY.md`가 없어 이 지표는 성숙도를 구별하지 못한다. 조직 수준
  `SECURITY.md`(저장소 트리 밖)를 인식하지 못하는 한계도 있다(미측정, 설계상 한계).

## 4. 아직 측정하지 못한 값 (pending)

| 항목 | 상태 | 필요한 입력 |
| --- | --- | --- |
| 축별 불일치율 | `pending` | `reviewer_a`·`reviewer_b` 둘 다 기입 |
| Cohen's kappa (축별 범주형) | `pending` | 위와 같음 |
| 불일치 원인 분류(`disagreement`) | `pending` | 두 평가자 기입 후 원자료·정책과 대조 |

`analyze`는 두 평가자 값이 없으면 `reviewers.status: "pending"`을 출력하고 값을 추정하지 않는다.
kappa는 두 평가자의 일관성을 재는 값이며 지표 타당도 자체를 증명하지 않는다.

## 5. 총점 공개 여부 결정

**공개하지 않는다.** 이유는 (1) 독립 평가자 일치도가 측정되지 않았고, (2) 측정된 범위에서도 충족률이
관측 가능성이어서 품질·안전을 구별하지 못하며, (3) 해당 없음이 0으로 표시되는 한계가 남아 있기 때문이다.
공개 재검토의 명시적 기준은 `reputation-policy.md` "총점 공개 결정"에 있다. 그 기준이 충족되기 전에는 정확도를
주장하지 않는다.

## 6. 다른 담당자에게 요청할 변경

- (프론트) `src/components/reputation-evidence.tsx` 문구를 현재 상태에 맞게 갱신한다.
  제안: "표본 수집은 끝났지만 두 평가자의 독립 검증이 진행 중이어서 총점을 제공하지 않습니다. 평판은
  서명 실패나 중대한 보안 위험을 상쇄하지 않습니다."
- (백엔드 `reputation_service`·스키마) 적용 가능한 지표가 없는 축의 충족률을 0이 아니라 구분되는 값으로
  표시한다(`schemas/reputation.py`는 필드 추가만, 기본값 필수). 이 이슈에서는 수정하지 않았다.
