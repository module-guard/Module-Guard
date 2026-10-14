# ModuleGuard 방향 전환 계획 — "Terraform 모듈의 GitHub"

> 정한 것: 모듈은 **ModuleGuard에 저장**, 로그인은 **GitHub**, 커스텀은 **변수 설정 + 사용 코드 생성**, 일정은 **2주**.
> 이 문서는 Claude Code가 단계별로 따라 할 수 있게 쓴 설계서다. `[결정]` 표시는 시작 전에 정해야 할 것이고, 괄호 안에 기본안을 적었다.

---

## 1. 한 줄 정의

**Terraform 모듈을 올리고, 보안 검사 결과를 확인하고, 내 저장소로 가져가 바로 쓰는 곳.**

지금까지는 "검사 서버의 관리 화면"이었다. 앞으로는 검사가 **모듈 페이지 안의 한 탭**으로 들어간다. 사용자가 보는 중심은 대시보드가 아니라 **모듈**과 **사람(프로필)**이다.

| 지금 | 바뀐 뒤 |
|---|---|
| 대시보드: 전체 탐지 통계 | 홈: 내 모듈과 그 보안 상태 |
| 새 검사: URL 넣고 검사 | 올리기: GitHub 주소나 zip → 자동 검사 → 내 모듈로 등록 |
| 모듈 목록: 검사한 모듈 | 탐색: 모든 사람의 공개 모듈 (검색·정렬·별표) |
| 검사 결과 페이지 | 모듈 페이지의 "보안" 탭 |
| (없음) | 프로필, 가져오기(fork), 사용 코드 생성, 별표 |

---

## 2. 시연 시나리오 (2주 뒤 이게 끊김 없이 돌아가야 한다)

1. 로그인하지 않은 상태로 `/explore`에 들어가 `vpc`를 검색한다. 결과 카드에 보안 상태 배지와 별 수, 가져간 수가 보인다.
2. 모듈 페이지에서 README, 입력 변수, 보안 탭을 확인한다. `주의 N종`(또는 `주요 위험 없음`) 배지와 심각도별 분포가 보인다.
3. **GitHub로 로그인**한다.
4. **내 저장소로 가져오기**를 누른다. 가져오기 전 확인 창에 보안 요약이 나온다. → `/@내아이디/terraform-aws-vpc`가 생기고 "원본: @terraform-aws-modules-mirror/terraform-aws-vpc"가 표시된다.
5. **사용하기** 탭에서 변수(cidr, 이름, NAT 여부 등)를 폼에 채운다 → `main.tf` 사용 코드가 생기면 복사하거나 zip으로 내려받는다.
6. **올리기**에서 내 GitHub 저장소 주소(또는 zip)를 넣는다 → 검사가 진행되는 모습이 보이고 → 내 모듈로 공개된다.
7. 내 프로필 `/@내아이디`에 올린 모듈과 가져온 모듈이 보인다.

---

## 3. 화면 구조

| 주소 | 화면 | 로그인 |
|---|---|---|
| `/` | 랜딩 (지금 것 유지, 섹션 문구만 "찾기 → 확인 → 가져가기"로 바꾼다) | 불필요 |
| `/explore` | 탐색: 검색, 정렬(인기 · 최신 · 별), 필터(provider: aws/gcp/azure) | 불필요 |
| `/@{user}` | 프로필: 아바타, 이름, 올린 모듈, 가져온 모듈, 별표한 모듈 | 불필요 |
| `/@{user}/{module}` | 모듈 페이지 (아래 탭) | 불필요 |
| `/@{user}/{module}?tab=use` | 사용하기: 변수 폼 → 코드 생성 | 불필요 (가져오기만 로그인 필요) |
| `/@{user}/{module}/scans/{id}` | 지금의 검사 결과 화면을 그대로 재사용 | 불필요 |
| `/new` | 올리기: GitHub 주소 / zip 업로드 | 필요 |
| `/home` | 로그인 후 첫 화면: 내 모듈 목록과 보안 상태, 최근 활동 | 필요 |
| `/settings` | 프로필 표시 이름, 로그아웃, 계정 삭제 | 필요 |
| `/dev/*` | 개발 검증 모드 (그대로) | — |

**모듈 페이지 탭:** `개요`(README 렌더링, 설명, provider, 원본 표시) · `사용하기`(변수 폼 + 코드) · `입력·출력`(variables/outputs 표) · `보안`(최신 버전 검사 요약 → 상세 링크) · `버전`(버전별 검사 상태)

**상단 내비 (사이드바 대신):** 로고 · 탐색 · 검색창 · `올리기` 버튼 · 아바타(메뉴: 내 프로필 / 홈 / 설정 / 로그아웃). GitHub처럼 상단 바 하나로 간다. 지금의 사이드바는 없앤다.

**지금 화면들의 처리:** 대시보드 → `/home`으로 축소, 검사 기록 → 모듈 페이지 `버전` 탭과 `/home`의 최근 활동으로 흡수, 모듈 목록 → `/explore`로 대체, 새 검사 → `/new`로 대체.

---

## 4. 데이터 모델

기존 검사 파이프라인(`modules`, `scans`, `findings`, 그래프·흐름 테이블)은 **그대로 두고**, 사람과 저장소를 위한 테이블을 위에 얹는다.

```
users
  id, github_id (unique), login (unique), name, avatar_url, created_at

repositories            -- 사용자에게는 "모듈"로 보이는 단위. GitHub 저장소 1개 = 모듈 1개
  id, owner_id → users, name (owner 안에서 unique, slug)
  description, provider (aws/gcp/azurerm…), visibility (public | private)
  forked_from_id → repositories (nullable)     -- 가져온 경우 원본
  origin_type (GITHUB | UPLOAD | FORK), origin_url (nullable)
  star_count, fork_count (캐시 값), created_at, updated_at

module_versions         -- 올리거나 다시 가져올 때마다 하나씩. 내용은 바뀌지 않음(immutable)
  id, repository_id → repositories, version ("1.0.0" 또는 "v3" 자동 번호)
  snapshot_sha256, size_bytes, file_count, commit_hash (GitHub 출처일 때)
  readme (text), variables (json), outputs (json), license (spdx, nullable)
  scan_id → scans (nullable), created_at

stars
  user_id, repository_id (복합 PK), created_at
```

- **스냅샷 저장:** 지금은 검사가 끝나면 작업 폴더를 지운다(`WorkspaceManager`). 앞으로는 수집한 코드를 `tar.gz`로 묶어 `/data/snapshots/{sha256}.tar.gz`에 보관한다. 같은 내용이면 같은 파일을 쓰도록(content-addressed) 한다. Docker 볼륨을 하나 추가한다.
- **가져오기(fork) = 행만 복사:** 새 `repositories` 행 하나와, 원본 최신 버전과 **같은 스냅샷을 가리키는** `module_versions` 행 하나를 만든다. 파일 복사가 없어서 빠르고, 검사도 다시 할 필요가 없다(같은 `scan_id`를 연결한다). 나중에 파일 편집을 넣을 때 그때 새 스냅샷을 만든다.
- **변수 정보:** 파서가 이미 `VARIABLE`과 `OUTPUT` 엔티티를 뽑고 있다. 검사가 끝날 때 루트 모듈의 variable(이름 · type · default · description · 필수 여부)과 output을 `module_versions.variables / outputs`에 저장한다. 사용하기 탭의 폼이 이 값으로 만들어진다.
- **기존 `scans` 연결:** `scans`에 `module_version_id`(nullable)를 추가한다. 기존 `modules` 테이블은 검사 파이프라인 내부용으로 남겨 두고, 개발 모드와 기존 데이터는 계속 동작하게 한다.

---

## 5. 기능별 설계

### 5.1 GitHub 로그인
- GitHub OAuth App을 만든다. scope는 **`read:user`만** 쓴다. 저장소 쓰기 권한은 필요 없다(코드는 ModuleGuard에 저장하니까).
- **백엔드(FastAPI)가 OAuth를 처리**한다: `/api/auth/github/login` → GitHub → `/api/auth/github/callback` → 세션 쿠키 발급(`HttpOnly`, `Secure`(운영), `SameSite=Lax`) → 프런트로 이동.
- `state` 값으로 CSRF를 막는다. 데이터를 바꾸는 요청(POST/DELETE)은 로그인 확인과 함께 `Origin` 헤더를 검사한다.
- 프런트는 `credentials: "include"`로 API를 부르고, CORS는 프런트 주소만 허용한다(credentials를 쓰므로 `*`는 안 된다).
- `GET /api/me`로 로그인 상태를 확인한다.
- 비밀값(`GITHUB_CLIENT_ID/SECRET`, `SESSION_SECRET`)은 `.env`로 받고 `.env.example`에만 이름을 적는다.

### 5.2 올리기 (`/new`)
- **GitHub 주소:** 기존 수집기(`collectors/github.py`)를 그대로 쓴다 → 스냅샷 저장 → 검사 → 저장소와 버전 생성.
- **zip 업로드:** 새로 만든다. 업로드는 공격 통로가 되니까 다음을 반드시 지킨다.
  - 크기 제한(압축 10MB, 해제 50MB, 파일 1,000개)
  - 경로 탈출 차단(`../`, 절대 경로), 심볼릭 링크 거부
  - 허용 확장자: `.tf .tf.json .tftpl .md .json .yaml .yml .tfvars.example` + `LICENSE`
  - `.terraform/`, `*.tfstate`, `*.tfvars`(실제 값이 들어 있을 수 있음)는 거부하고, 거부한 이유를 화면에 보여 준다.
  - 지금처럼 Terraform은 실행하지 않는다.
- **비밀값 검사:** Checkov의 secrets 검사(`--framework secrets`)를 함께 돌린다. 키나 토큰이 발견되면 **공개를 막고** 해당 줄을 보여 준다. 그 밖의 탐지는 공개를 막지 않고 배지로만 표시한다.
- 올리는 화면에서 이름, 설명, 공개 여부를 받는다. 검사가 진행되는 동안 지금의 "분석 진행" UI를 보여 준다.

### 5.3 탐색 (`/explore`)
- 공개 저장소만 보여 준다. 검색은 이름과 설명에 대한 `ILIKE`로 충분하다(2주 안에 검색 엔진은 넣지 않는다).
- 카드 구성: `@owner/name` · 설명 한 줄 · provider · 보안 배지 · ★ 수 · 가져간 수 · 갱신 시각
- 정렬: 인기(별 + 가져간 수) · 최신 · 이름

### 5.4 보안 배지 — 일반 사용자에게 보여 주는 방식
- 사내 대시보드처럼 숫자를 늘어놓지 않는다. **세 단계**로 보여 준다.
  - `주요 위험 없음` (회색 방패) — Critical/High 0
  - `주의 N` (노랑) — High N
  - `위험 N` (빨강) — Critical N
- "안전" 같은 단정적인 말은 쓰지 않는다. 배지 옆에 항상 `검사: v1.2 · 3일 전`을 붙인다.
- **주의:** 지금은 탐지의 대부분이 심각도 미분류라서, 배지가 실제보다 좋게 나온다. `UI_CLEANUP_2.md` B항의 심각도 지정(자주 나오는 Checkov 규칙)을 **이 계획의 1주차에 같이 한다**. 이번 방향에서는 배지가 첫인상이므로 필수다.

### 5.5 가져오기 (fork)
- 버튼: `내 저장소로 가져오기`. 로그인이 필요하다.
- 확인 창에 원본의 보안 요약과 라이선스를 보여 준다.
- 이름이 겹치면 `vpc-1`처럼 바꿔 제안한다. 가져온 저장소는 **기본 비공개**로 한다(개인 커스텀 용도). `[결정]` (기본안: 비공개)
- 원본의 `fork_count`를 올린다. 모듈 페이지 상단에 `원본: @owner/name` 링크를 단다.
- 원본에 새 버전이 나오면 가져간 쪽에 `원본에 새 버전이 있어요`를 표시한다(선택, 시간이 남으면).

### 5.6 사용하기 (변수 설정 + 코드 생성)
- `module_versions.variables`로 폼을 만든다. 필수 변수는 위에, 기본값이 있는 변수는 `고급 설정`으로 접는다. 타입별 입력: string, number, bool은 토글, list와 map은 텍스트 영역(HCL/JSON).
- 결과 코드:
  ```hcl
  module "vpc" {
    source = "./modules/vpc"   # 내려받은 zip을 풀어 둘 위치
    name   = "my-vpc"
    cidr   = "10.0.0.0/16"
  }
  ```
  - **zip 다운로드:** 스냅샷과 생성된 `main.tf` 예시를 하나의 zip으로 묶어 준다. 2주 안에 확실히 되는 방식이다.
  - 원본이 GitHub이고 수정하지 않은 버전이면 `source = "git::https://github.com/…?ref=<commit>"`도 함께 보여 준다.
- 값 검증은 타입 수준까지만 한다. 폼 값은 로그인한 사용자일 때 저장소별로 기억한다(선택).

### 5.7 프로필 · 별표 · 홈
- `/@{user}`: GitHub 아바타와 이름, 탭(`모듈` · `가져온 모듈` · `별표`)
- 별표: 토글 API 하나와 `star_count` 캐시
- `/home`: 내 모듈 목록 + 각 모듈의 보안 배지 + "검사가 필요한 모듈"(검사 실패, 오래됨) + 최근 활동 5개. 지금 대시보드의 위험도 분포는 내 모듈 기준으로 작게 남긴다.

---

## 6. 2주 일정

| 일 | 작업 | 끝났다는 기준 |
|---|---|---|
| 1–2 | 데이터 모델과 마이그레이션, 스냅샷 저장(파이프라인 수정), 변수/출력 추출 | 기존 검사가 그대로 돌고, 스냅샷과 variables가 저장된다 |
| 3 | GitHub 로그인, 세션, `/api/me`, 상단 내비와 아바타 메뉴 | 로그인/로그아웃 e2e 통과 |
| 4–5 | 올리기(GitHub 주소 + zip, 업로드 보안, secrets 검사) | 악성 zip(경로 탈출, 심볼릭 링크, 용량 초과) 테스트 통과 |
| 6–7 | 모듈 페이지(개요/README · 입력·출력 · 보안 · 버전), 보안 배지, **심각도 지정** | eks 모듈이 README와 함께 보이고 배지가 맞다 |
| 8–9 | 탐색, 가져오기(fork), 별표, 프로필 | 시연 시나리오 1~4, 7 동작 |
| 10–11 | 사용하기(변수 폼 → 코드, zip 다운로드) | 내려받은 zip으로 `terraform init && terraform validate` 통과 |
| 12 | **시드 데이터**: `terraform-aws-modules`의 인기 모듈 10~15개를 공식 미러 계정(`@terraform-aws-modules`, "미러" 표시)으로 올린다 | 탐색 화면이 비어 보이지 않는다 |
| 13 | 랜딩 문구와 이미지 교체, `/home` 정리, 지금 페이지들 제거 | 사이드바와 옛 화면이 남아 있지 않다 |
| 14 | 시연 시나리오 e2e, 반복 실행(`--repeat-each=5`), 리허설 | 시나리오 전체가 5번 연속 통과 |

- **시드 데이터 라이선스:** terraform-aws-modules는 Apache-2.0이다. 다시 배포할 수 있지만 `LICENSE`를 스냅샷에 그대로 두고, 모듈 페이지에 라이선스와 원본 링크를 표시한다. 라이선스가 없는 저장소는 시드로 쓰지 않는다.

---

## 7. 이번 2주에 하지 않는 것

웹에서 파일 직접 수정 · GitHub로 내보내기 · 조직/팀 · 댓글/이슈/PR · 알림 · 비공개 저장소 공유 · Terraform이 직접 `source = "moduleguard.dev/…"`로 받아 가는 **레지스트리 프로토콜** · 결제

> 레지스트리 프로토콜은 발표 뒤 첫 번째로 추천하는 기능이다. `/.well-known/terraform.json`, `GET /v1/modules/{ns}/{name}/{provider}/versions`, `GET …/{version}/download`(→ `X-Terraform-Get` 헤더로 스냅샷 주소 반환) 정도만 있으면 되고, 스냅샷 구조가 이미 그 형태라서 붙이기 쉽다.

---

## 8. 지금까지 한 작업과의 관계

- **그대로 살린다:** 검사 파이프라인, 분석 탭(→ 모듈 페이지 보안 상세), 랜딩, 로고, 상태 점, 색·배지 규칙, 상대 시간, e2e 분리 스택
- **바뀐다:** 사이드바 → 상단 내비, 대시보드 → `/home`, 모듈 목록·새 검사·검사 기록 → 탐색·올리기·버전 탭
- **`UI_CLEANUP_2.md`:** B(심각도 지정)는 6–7일차에 같이 한다. A(탐지 묶기)와 C(분석 탭 정리)는 보안 상세 화면의 품질 문제라서, 일정이 남으면 12일차 이후에, 아니면 발표 뒤에 한다. D와 E는 화면이 사라지므로 하지 않는다.

---

## 9. 시작 전에 정할 것

- `[결정]` **용어:** 사용자 화면에서 "모듈"과 "저장소" 중 무엇으로 부를지. (기본안: 하나하나는 **모듈**, 내 모듈 모음은 **내 저장소**. 버튼은 `내 저장소로 가져오기`)
- `[결정]` 가져온 모듈의 기본 공개 범위 (기본안: 비공개)
- `[결정]` 검사에서 Critical이 나온 모듈의 공개를 막을지 (기본안: 막지 않고 배지로 표시. 비밀값이 탐지됐을 때만 막는다)
- `[결정]` 서비스 주소(로고 옆 표기, OAuth 콜백 주소). 개발 중에는 `http://localhost:3000`
