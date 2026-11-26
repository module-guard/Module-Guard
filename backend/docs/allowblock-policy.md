# 허용/차단 목록 정책 (이슈 #47 Part A)

운영자가 수집·게시 대상을 허용(ALLOW)하거나 차단(BLOCK)하는 목록의 규칙입니다.
Top10 랭킹은 이 문서의 범위가 아닙니다.

## 1. 대상 단위

자유 형식 글롭/정규식은 지원하지 않습니다. 대상은 아래 세 가지뿐입니다.

| `target_kind` | `target_key` | 정규화 |
|---|---|---|
| `repository` | 플랫폼 Repository UUID | 소문자 UUID, 존재하는 저장소여야 함 |
| `github_repository` | `https://github.com/owner/repository` | `normalize_github_url()` (소문자, `.git` 제거) |
| `github_owner` | GitHub owner/org | 소문자, `[a-z0-9-]` 최대 39자 |

## 2. 우선순위

- 활성 항목: 해제되지 않았고 (`expires_at` 이 없거나 `expires_at > 현재(UTC)`) 인 항목.
  만료 시각 경계에서는 이미 만료로 봅니다.
- 활성 BLOCK이 ALLOW보다 우선합니다 (BLOCK이 ALLOW보다 우선).
- ALLOW는 기존 파일/비밀값/라이선스/크기/스캔 검사를 건너뛰지 않습니다.
  닫힌 허용 목록(allowlist)이 아니므로 목록에 없는 대상도 기존 규칙대로 처리됩니다.
- 이미 저장된 버전은 삭제·다운로드 차단·홈 숨김을 하지 않습니다 (소급 적용 없음).
  이후의 각 수락 검사 지점에서만 적용됩니다.

## 3. 만료와 해제

- 같은 (`target_kind`, `target_key`, `effect`) 의 해제되지 않은 항목은 하나만 둘 수 있습니다
  (부분 유니크 인덱스).
- 만료된 채 남은 항목은 같은 대상을 다시 등록할 때 같은 트랜잭션에서 `EXPIRE` 이벤트와 함께
  닫히고 새 항목이 들어갑니다.
- 해제는 항목을 지우지 않고 `revoked_*` 필드를 채우고 `REVOKE` 이벤트를 남깁니다.

## 4. 적용 지점

모든 지점은 `AllowBlockService` 의 하나의 매처를 호출합니다. 차단되면 `SourceBlockedError`
(403, 코드 `SourceBlocked`, 고정 문구)를 냅니다. 내부 사유는 응답에 나가지 않습니다.

| 위치 | 시점 |
|---|---|
| `PublishService.prepare_github` | URL 정규화 직후, 스테이징 전 |
| `PublishService.prepare_registry` | 수집기 안의 백킹 GitHub 검사 + `_finish_source` 재검사 |
| `PublishService._finish_source` | 봉인(`_finish`) 전에 `repository_url or source_url` 재검사 |
| `PublishService.register` | 세션 시작 직후, 같은 이름 저장소 `_discard` 전 |
| `PublishService.register_version` | 저장소 존재 확인 직후 |
| `PublishService._start_version` | `ModuleVersion` 생성 직전, 커밋·토큰 소비 전 (마지막 공통 관문) |
| `SourceRouter.collect` / `GitHubCollector.collect` | git 실행 전 |
| `TerraformRegistryCollector.collect` | 백킹 소스 확인 후 클론 전 |
| `ScanService` | 기본 라우터 주입 + 직접 `_collect()` 직전 |
| `seeds.mirrors.MirrorSeeder._one` | `unchanged` 조기 반환 전 (dry-run 포함) |
| `seeds.collection.BatchCollector._one` | 저장된 체크포인트 결과를 쓰기 전에 현재 정책 확인 |

배치 수집에서 정책 차단은 `blocked` 분류이며 일시 오류가 아닙니다. 차단 결과는 체크포인트에
고정되지 않으므로, 차단이 풀리면 다음 실행에서 다시 시도됩니다.

## 5. 감사 이력

`allowblock_events` 는 추가만 가능합니다 (수정/삭제 API 없음). 항목과 이벤트는 항상 같은
트랜잭션에서 기록되며, 이벤트에는 사유, 증거 URL, 항목 스냅샷이 들어갑니다.

## 6. 관리자 권한과 호출 제한

- `ADMIN_LOGINS` 설정(소문자 비교)에 있는 로그인만 관리자입니다. 시스템 계정은 거부됩니다.
- 쓰기 API는 `Origin` 검증 + 관리자 + 시간당 `ALLOWBLOCK_WRITES_PER_HOUR` (기본 30) 제한.
  제한기는 기존 `HourlyLimiter` 와 같이 프로세스 단위이며 클러스터 전체 한도가 아닙니다.

## 7. 공개 범위

- 공개 목록/저장소 정책 조회는 `repository_visible_to(viewer)` 를 페이지네이션 전에 적용합니다.
  보이지 않는 저장소는 존재하지 않는 것과 똑같이 404 입니다.
- 공개 응답에는 내부 `reason`, `evidence_urls`, 행위자, 감사 이력이 없습니다. 효과/상태/
  `public_message`/만료만 나가며, 메시지가 없으면 고정 문구를 씁니다.
- 뷰어별 응답은 `Cache-Control: private, no-store`.
