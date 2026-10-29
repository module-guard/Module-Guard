# SHA-256 모듈 무결성 검증

## 기존 흐름과 변경 범위

기존 GitHub 수집기는 실제 HEAD Commit을 기록하며, Registry 수집기는 Registry 메타데이터에서 GitHub 저장소와 버전을 확인한 후 해당 태그를 가져온다. 게시 준비 단계는 허용 파일 정책과 비밀정보 검사를 적용한다. `SnapshotStore`는 정렬된 엔트리, 정규화된 소유자·시간·권한으로 결정적 `tar.gz`를 저장하고 그 압축 바이트의 SHA-256을 주소로 사용한다. 기존 `Scan.module_version_id`와 `ModuleVersion.scan_id`를 그대로 재사용한다.

이번 변경은 기존 스냅샷 구현을 확장한다. Git Commit, 파일 Manifest SHA-256, 원본 tar.gz SHA-256, 최종 ZIP SHA-256은 서로 다른 값이다. Registry의 원본 GitHub URL과 해석된 버전도 기록한다. 현재 수집기가 지원하는 대상은 저장소 루트이며 `module_path`는 `.`이다. 임의 하위 모듈 선택 기능을 새로 추가하지 않았다.

## 기준 Manifest와 분석

- 게시 준비 단계에서 허용된 파일의 실제 바이트를 읽어 기준 Manifest를 확정한다. 비밀정보 검사 후 다시 비교하고, 등록 시 DB에 보관한다. 기존 준비 토큰에 Manifest가 없으면 분석을 차단한다.
- 직접 개발용 검사는 수집 후 스냅샷 생성 시 기준을 확정하고 분석 전에 DB 버전 행에 연결한다.
- 형식 1: `{"format":1,"files":[{"path":"main.tf","sha256":"…","size":38,"mode":420}]}`. 파일은 POSIX 상대 경로의 Python 문자열 순서로 정렬한다. 경로는 Unicode 정규화를 적용하지 않고 UTF-8로 인코딩한다. JSON은 `ensure_ascii=False`, 키 정렬, 공백 없는 구분자를 사용한다. `mode`는 0644(420) 또는 0755(493)이다.
- 파일 내용·크기·목록·이름·실행 권한 변경을 탐지한다. 바이트를 디코딩하지 않아 바이너리도 해시할 수 있다. 기존 업로드 허용 확장자 정책은 유지하므로 임의의 바이너리 파일을 새로 업로드할 수 있게 된 것은 아니다.
- 심볼릭 링크·특수 파일·역슬래시·콜론 경로를 거부한다. 디렉터리 FD 기준 `O_NOFOLLOW`로 읽어 링크 교체에 의한 트리 밖 읽기를 방지한다. 읽기 전후 파일 메타데이터 및 스냅샷 생성 전 두 번의 트리 비교로 수집 중 변경을 탐지한다.
- 준비 트리를 분석 작업 공간으로 옮기기 전, Checkov 직전, Checkov 후·정적 분석 전, 정적 분석 후, 최종 설명 정보 추출 후에 비교한다. 불일치나 읽기 오류는 FAILED 검사로 기록한다. 실패한 무결성 검사를 정상 게시 완료로 처리하지 않는다.
- `.git`, `.terraform`, 비밀정보 파일과 업로드 정책상 제외된 파일은 검증 대상이 아니다. 이 기능이 보장하는 원본은 **정책에 따라 수집·승인된 파일 집합**이며, 원격 Git 저장소의 모든 파일과 같다는 뜻은 아니다.

## DB 및 배포

`0010_integrity.py`를 추가했다. `0009`는 수정하지 않았다.

`module_versions` 추가 컬럼:

| 컬럼 | 의미 |
|---|---|
| manifest | 파일별 경로, SHA-256, 바이트 크기, 정규화 권한을 담은 JSON/JSONB |
| manifest_sha256 | 정규화 Manifest 바이트의 기준 SHA-256 |
| integrity_status | VERIFIED / MISMATCH / UNVERIFIED / ERROR |
| verified_at, collected_at | 마지막 성공 검증 및 수집 시각 |
| source_url, module_path | 확정한 소스 URL과 대상 경로 |
| mismatched_files | 확인된 변경 파일 목록 |

파일별 정보를 별도 테이블에 중복 저장하지 않고 버전별 Manifest JSON에 보관한다. 원본 Commit은 기존 `commit_hash`, 소스 버전은 기존 `source_ref`, 원본 압축 해시는 기존 `snapshot_sha256`을 사용한다.

`download_artifacts` 신규 테이블:

- `id`, `version_id`, `request_sha256`, `zip_sha256`, `artifact_size`, `content`, `integrity_status`, `created_at`
- `(version_id, request_sha256)` UNIQUE 제약으로 같은 입력의 동시 요청·재시도 중복을 방지한다.
- 버전 → 검사 → 모듈 관계를 통해 Scan ID와 Module ID를 추적하므로 이를 아티팩트 행에 중복 저장하지 않는다.
- `content`는 PostgreSQL BYTEA / SQLite BLOB이다. 별도 artifact_path 대신 DB 트랜잭션으로 불변 ZIP을 게시한다. 버전 삭제 시 FK cascade로 삭제한다.
- 정상 분석 완료 시 기본 ZIP(입력을 넣지 않은 사용 코드)을 만든다. 사용자 입력을 반영한 ZIP은 첫 요청에 별도 생성하고 이후에는 저장된 ZIP을 사용한다.

배포 시 백엔드 디렉터리에서 `alembic upgrade head`를 실행한 후 새 API 서버를 시작해야 한다. 실제 사용자 DB에는 이 작업 중 마이그레이션을 실행하지 않았다.

백필 정책: 기존 행의 기존 해시는 보존하지만 새 Manifest는 NULL, 상태는 UNVERIFIED로 둔다. 기존 tar.gz를 현재 시점에 읽어 임의로 기준 Manifest를 만들지 않는다. 신뢰 가능한 원본 Commit에서 새 버전을 수집·분석해야 다운로드가 가능하다. 기존 버전 기록은 그대로 남긴다. 0010 다운그레이드는 추가 무결성 정보와 ZIP 아티팩트 테이블을 제거하므로 운영 환경에서는 먼저 백업한다.

## 다운로드 검증

기존 `POST /api/repositories/{owner}/{name}/download`를 유지한다.

1. 기존 공개/비공개 접근 권한과 Origin 정책을 검사한다.
2. 해당 버전의 검사가 COMPLETED인지, 기준 Manifest가 있는지 확인한다.
3. 저장된 tar.gz 바이트를 한 번 읽어 압축 해시와 내부 파일 Manifest를 검증한다.
4. 검증한 메모리 바이트에서 ZIP을 생성한다. 경로를 다시 열지 않는다.
5. 일반 변수로 생성한 root `main.tf`/`variables.tf`와 원본 `modules/<name>/...`을 포함하는 최종 ZIP을 별도 기준 해시와 함께 저장한다.
6. 기존 ZIP이면 재생성하지 않는다. 저장된 ZIP 바이트의 SHA-256과 크기를 검증한다.
7. 검증한 동일 bytes 객체를 응답으로 전송한다. 파일 경로 교체 TOCTOU가 발생하지 않는다.
8. 브라우저는 준비 API에서 받은 ZIP 기준 해시를 유지한 채 `artifact_id`를 지정해 다운로드한다. Web Crypto로 실제 수신 Blob을 해시한 후 일치할 때만 저장 링크를 생성한다. Web Crypto 미지원/비보안 컨텍스트, 해시 형식 오류, 불일치 모두 저장을 차단한다.

일반 입력값은 이제 생성된 ZIP에 보관된다. UI에서 이를 알리며 민감 변수는 기존처럼 값을 받지 않고 `TF_VAR` 참조만 생성한다. 일반 변수에 비밀값을 잘못 넣지 않아야 한다. 요청 값과 ZIP 내용은 로그에 남기지 않는다. 사용자별 입력 ZIP을 다수 생성하면 DB 사용량이 늘어나므로 운영 환경에는 저장량·보관 기간 정책이 추가로 필요하다. 현재는 버전 삭제 시 정리되며 기존 요청 제한을 적용한다.

MISMATCH는 재검증 성공으로 자동 복구하지 않는다. 상태 갱신은 조건부 SQL로 처리하여 오래 걸린 성공 요청이 동시 요청에서 발견한 MISMATCH를 덮어쓰지 못한다. ERROR는 일시적 읽기 오류가 해소되면 동일 기준으로 다시 검증할 수 있다. ZIP MISMATCH는 소스 Manifest 상태와 별도로 관리한다.

## API 계약

기존 서비스의 owner/name 명명 규칙을 따른다. 모든 아래 경로는 `/api/repositories/{owner}/{name}` 접두사를 사용한다.

| 메서드·경로 | 응답 |
|---|---|
| GET `/integrity?version=v1` | 버전·검사 ID, 소스 Commit·URL·버전, 원본/Manifest/기본 ZIP 해시, 상태, 검증 시각, 변경 파일, 다운로드 허용 여부 |
| GET `/integrity/files?version=v1` | 기준 파일별 path / sha256 / size / mode 목록 |
| POST `/integrity/verify?version=v1` | 원본과 기본 ZIP을 재검증한 정보. 불일치는 HTTP 409 |
| POST `/download/prepare` | 입력에 대응하는 불변 artifact_id, version_id, zip_sha256, artifact_size, filename |
| POST `/download` | 검증된 ZIP. 기존 요청에 선택적 artifact_id만 추가 |

입력값이 다른 ZIP을 `artifact_id`로 선택할 수 없다. 원본 권한이 없는 경우 기존처럼 404를 반환한다. `version`을 생략한 요청 사이에 최신 버전이 바뀌면 아티팩트 연결 검사가 다운로드를 차단한다. UI는 화면에 선택된 버전을 지정한다.

검사 상세·요약 및 버전 요약에 기본값이 있는 `integrity` 필드를 추가했다. 최신 버전이 아닌 해당 검사의 `module_version_id`를 사용한다. OpenAPI와 생성 TypeScript 타입을 동기화했다. 다운로드 응답은 `X-Content-SHA256`, `X-Artifact-ID`를 추가하며 CORS 노출 목록에 포함한다.

무결성 응답 예시(해시·ID는 설명용):

```json
{
  "version_id": "11111111-1111-4111-8111-111111111111",
  "scan_id": "22222222-2222-4222-8222-222222222222",
  "source_url": "https://github.com/terraform-aws-modules/terraform-aws-kms",
  "source_commit_sha": "cccccccccccccccccccccccccccccccccccccccc",
  "source_version": "4.2.2",
  "module_path": ".",
  "manifest_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "snapshot_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "zip_sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
  "collected_at": "2026-10-29T00:00:00Z",
  "verified_at": "2026-10-29T00:01:00Z",
  "integrity_status": "VERIFIED",
  "artifact_integrity_status": "VERIFIED",
  "mismatched_files": [],
  "download_allowed": true
}
```

불일치 응답 예시:

```json
{
  "error": {
    "code": "IntegrityMismatch",
    "message": "모듈 무결성을 확인할 수 없어 분석 또는 다운로드를 차단했습니다.",
    "details": [{"path": "main.tf"}]
  }
}
```

압축 바이트 자체가 변조되었으면 내부 파일 목록을 신뢰할 수 없어 `details`가 생략될 수 있다. `IntegrityUnverified`, `IntegrityError`도 HTTP 409로 차단하며 소스 읽기 오류 원문은 노출하지 않는다. 기본 정보 GET은 마지막 검증 상태를 반환하며 현재 바이트 검증은 verify/download 요청에서 수행한다.

## 서명된 Manifest와 위협 모델

해시 일치는 동일성만 확인하며 코드의 안전성, 게시자의 신뢰성, 원격 저장소 자체의 안전성을 보증하지 않는다. DB의 기준값과 파일을 함께 바꿀 수 있는 공격자, 또는 브라우저 애플리케이션 자체를 바꿀 수 있는 서버 공격자는 일반 SHA-256 비교로 탐지할 수 없다.

다음 단계는 독립된 서명 서비스의 Ed25519 또는 KMS 서명이다. 버전 1 canonical Manifest, source URL/Commit/path, 스냅샷 해시, 배포 ZIP 해시, 발급 시각, key_id를 하나의 서명 봉투로 묶는다. 개인키는 API 서버·DB와 분리해 두고, 별도 배포 경로로 제공한 검증 클라이언트에 공개키를 고정한다. 키 회전·폐기·구버전 검증·롤백 방지 정책과 외부 투명성 로그도 필요하다. 같은 API 응답에서 공개키까지 받아 신뢰하는 방식은 이 위협을 해결하지 못한다. 실제 운영 키와 신뢰 배포 경로가 없으므로 이번 변경은 서명을 발급하거나 서명 검증 완료로 표시하지 않는다.

분석 전후 해시 비교는 검사 사이에 남아 있는 변경을 탐지한다. 호스트 권한을 가진 공격자가 검사 중 파일을 잠시 교체했다 원복하는 공격까지 방지하려면 분석 프로세스마다 읽기 전용 마운트 또는 격리된 불변 파일시스템을 추가해야 한다. 다운로드는 메모리의 검증된 동일 바이트를 사용하여 경로 재개방 경쟁을 제거했다.

## 검증

자동 테스트는 파일 해시, 바이너리, 정렬, 변경·추가·삭제·이름·권한 변경, 링크·경로 순회 거부, 수집 중 변경, 분석 전·중 변조, ZIP 변조 차단, 기준 누락, 읽기 오류, 동시 ZIP 생성, 동시 검증의 MISMATCH 유지, 권한 검사, 과거 검사 연결, 브라우저 저장 차단을 포함한다.

SQLite에서는 기존 0009 행 보존, 0010 적용·재적용·다운그레이드를 검사한다. PostgreSQL에서는 별도 임시 DB의 격리 스키마에서 실제 마이그레이션과 ORM 스키마 일치를 검사한다. 기존 사용자 데이터베이스는 사용하지 않는다.

브라우저 재현:

```sh
RUN_LIVE_SCAN=true npx playwright test e2e/integrity.spec.ts
```

전용 Docker PostgreSQL/API와 Next 프로덕션 빌드, Chromium, 실제 Checkov를 사용한다. ZIP 업로드, 실제 GitHub KMS 저장소, 실제 Registry `terraform-aws-modules/kms/aws` 4.2.2를 화면에서 가져와 분석하고 다운로드한다. 실제 저장 바이트를 Node SHA-256으로 다시 대조한다. 브라우저가 받은 ZIP 한 바이트를 변경한 경우 저장 이벤트가 발생하지 않는지도 검사한다. `terraform init/plan/apply`를 실행하지 않는다.
