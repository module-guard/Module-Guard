# 검사 이력 투명성 로그와 외부 앵커링 (#62)

#27 검사 증명(DSSE + RFC 3161)은 **발급한 개별 증명**의 변조를 탐지한다. 하지만 증명이 없는 검사, 삭제된 증명, 덮어쓴 결과, 과거 revision은 보호 범위 밖이다(`scan-attestation.md`의 한계).
이 기능은 완료된 **모든** 검사의 결과 커밋먼트를 append-only 머클 로그에 쌓고, 체크포인트 루트를 외부에 앵커링할 수 있게 한다. 목표는 서버나 공격자가 과거 검사 결과를 **수정·삭제·이력 재작성**했을 때 독립 검증기가 알아내는 것이다.

이 기능은 "결과가 안전하다"를 보증하지 않는다. 보증하는 것은 "기록된 이후 바뀌지 않았거나, 바뀐 사실이 드러난다"뿐이다(`security_guarantee=false`와 같은 취지).

## 기존 기능에 대한 영향

- DB migration, ORM 모델 변경 없음. 저장소는 파일이다(`<SNAPSHOT_ROOT>/transparency/`).
- 검사 파이프라인(`scan_service`, `publish_service`, `reapply`, `dispatcher`)은 수정하지 않았다. 로그는 별도 CLI가 DB에서 끌어와 쌓는다.
- 새 의존성 없음(표준 라이브러리, `httpx`, 기존 `rfc3161-client`).
- `AttestationService.binding()`의 출력과 검사 순서는 그대로다. 결과 export와 해시 계산만 `result_export_digest(session, scan)`으로 추출했고, `binding()`이 이를 호출한다. 기존 증명 테스트는 수정 없이 통과한다.
- 투명성 로그나 앵커가 실패해도 검사, 다운로드, 증명 API는 영향을 받지 않는다. 새 읽기 API만 `LOG_UNAVAILABLE` / `LOG_INCONSISTENT`를 보고한다.
- 앵커 기본값은 `none`이며, 이때 외부 호출은 0회다(테스트로 확인).

## ADR: 무엇에 앵커링하는가

| 방식 | 장점 | 단점 | 이번 이슈 |
|---|---|---|---|
| **OpenTimestamps → Bitcoin** | 서버·운영자와 독립. 확정되면 사후 변경 불가. 계정·키·비용 없음. 전송 값은 루트 32바이트뿐 | 비트코인 확정까지 수 시간. calendar는 가용성만 믿으면 됨. 서버는 확정을 검증하지 않고 독립 검증기가 `ots`로 확인해야 함 | 지원(선택) |
| **RFC 3161 TSA** | 즉시 시각 증명. #27과 같은 정책(`tsa_roots_pem`)·`rfc3161-client` 경로를 재사용 | TSA 운영자를 신뢰해야 함(키 폐지, 장기 검증) | 지원(선택) |
| 공개 투명성 로그(Rekor 등) | 기성 감시자 생태계 | 항목이 공개 서비스로 나감. 외부 서비스 의존. 지갑·키가 필요한 체인 포함 | 비목표 |
| 없음(`none`) | 외부 전송 0 | 서버 장악 시 로그 전체를 재작성해도 서버 안에서는 구별 불가. 검증기가 trusted checkpoint를 따로 보관해야 함 | **기본값** |

머클 트리를 쓰는 이유: 해시 체인은 포함·일관성 증명이 O(n)이지만 RFC 9162 머클 트리는 O(log n)이고, 공개 API가 형제 해시만으로 포함을 증명할 수 있다. 외부로 나가는 값이 루트 하나라서 앵커 비용과 노출이 일정하다.

## 형식

### 리프 커밋먼트 (format 1)

검사 1건의 현재 상태. 모든 값은 DB에서 다시 계산할 수 있다.

```json
{
  "scan_id": "<uuid>",
  "module_version_id": "<uuid|null>",
  "status": "COMPLETED|FAILED",
  "result_sha256": "<#27 결과 export format 1의 SHA-256>",
  "manifest_sha256": "<hex|null>",
  "snapshot_sha256": "<hex|null>"
}
```

- `result_sha256`은 `result_export_digest()`(#27과 같은 export)다. 다시 구현하지 않았다. 정규화는 `signing.canonical()`, 해시는 `storage.integrity.digest()`.
- export가 행 50,000건 또는 16 MiB를 넘으면 기록하지 않고 `not_logged / TOO_LARGE`로 보고한다. 결과가 검증되지 않으면 `RESULT_INVALID`.
- 커밋먼트는 **검사 단위**다. 원본과 fork가 한 검사를 공유하므로 `scan.module_version_id`(원본 쪽 버전)를 쓴다.
- 계보·snapshot 검증 없이 export만 계산하므로, 계보가 없는 과거 검사도 기록된다.

### 로그 항목과 리프 해시

```json
{"format":1,"seq":0,"kind":"SCAN","reason":null,"recorded_at":"2026-11-26T05:00:00+00:00","commitment":{}}
```

- `kind`: `SCAN`(최초 기록) | `REVISION`(운영자가 선언한 정당한 변경, `reason` 필수, 200자 이하).
- 항목은 정규화 JSON 한 줄이다. 저장된 줄이 정규화 결과와 한 바이트라도 다르면 `LOG_INCONSISTENT`다(리프 해시가 정확히 이 바이트를 덮는다).
- `leaf_hash = SHA256(0x00 || canonical(entry))`.
- 한 scan의 **기대값은 그 scan_id의 마지막 항목**의 commitment다. 이전 항목은 영구히 남는다. 한 scan의 첫 항목은 `SCAN`, 이후 항목은 `REVISION`이어야 한다(어기면 `LOG_INCONSISTENT`).

### 머클 트리 (`merkle.py`)

RFC 9162 §2.1: 리프 `SHA256(0x00||d)`, 내부 노드 `SHA256(0x01||l||r)`, n보다 작은 가장 큰 2의 거듭제곱에서 분할, 빈 트리 루트 `SHA256("")`. 포함 증명은 §2.1.3.2, 일관성 증명은 §2.1.4.2를 따른다. RFC가 호출자에게 남긴 경우(`m=0`, `m=n`)는 명시적으로 처리한다. 증명 길이 상한 64.
테스트는 별도의 단순 재귀 구현으로 n=1..64의 모든 포함·일관성 증명을 비교하고, 증명이나 루트를 1비트 바꾸면 실패함을 확인한다.

### 저장소

| 경로 | 내용 |
|---|---|
| `leaves.jsonl` | 항목 한 줄씩. `O_APPEND`, 줄 하나를 `write` 한 번으로 기록, fsync. 기존 줄은 수정하지 않음 |
| `checkpoints/<tree_size:020d>.json` | `{"format":1,"tree_size":n,"root_hash":"..","created_at":"..","previous":{"tree_size":m,"root_hash":".."}\|null}`. 한 번 게시하면 교체하지 않음 |
| `anchors/<tree_size:020d>-<type>.json` | 아래 앵커 기록 |
| `.lock` | `flock` 비차단 배타 잠금. 동시 쓰기는 즉시 `LOG_BUSY`로 실패 |

디렉터리 0700, 파일 0600, `O_NOFOLLOW`, 정규 파일만 허용, 임시 파일 → fsync → `os.link`(덮어쓰기 없음), `strict_json` 파싱. `attestations/store.py`의 패턴을 따르되 그 파일은 수정하지 않았다.

읽을 때 검사한다: `seq`가 0부터 연속, 줄당 4 KiB 이하, 줄이 정규 형태, 체크포인트 `previous` 사슬이 맞음, 체크포인트가 항목 수를 넘지 않음, **마지막 체크포인트 루트가 항목 앞 `tree_size`개로 다시 계산한 값과 같음**. 어긋나면 `LOG_INCONSISTENT`다. `verify`는 모든 인접 체크포인트 쌍의 일관성 증명을 추가로 검사한다.

마지막 줄이 개행으로 끝나지 않는(쓰다 중단된) 로그는 20 ms 간격으로 최대 3번(`TORN_LINE_ATTEMPTS`)만 다시 읽어 보고(동시에 기록 중일 수 있으므로), 그래도 끝나지 않으면 그 줄을 무시하거나 추측하지 않고 `LOG_INCONSISTENT`로 처리한다. 운영자는 원인을 조사한 뒤 마지막 체크포인트 이후의 잘린 꼬리만 직접 제거할 수 있다. 체크포인트에 포함된 줄은 절대 지우지 않는다.

### 앵커 기록

```json
{"type":"opentimestamps","status":"submitted","tree_size":128,"root_hash":"..","receipts_b64":["..."],"receipt_sha256":"..","created_at":".."}
```

- `receipts_b64`는 calendar(또는 TSA)가 준 **원본 응답**이다. 이슈 초안의 단수 `receipt_b64` 대신 목록으로 두었다(calendar가 여럿일 수 있음). `receipt_sha256 = SHA256(canonical(receipts_b64))`.
- 실패 기록은 `status:"failed"`와 고정 코드 `code`(`ANCHOR_UNAVAILABLE`, `ANCHOR_RESPONSE_TOO_LARGE`, `ANCHOR_RESPONSE_INVALID`)만 가진다. 외부 오류 원문은 저장하지 않는다.
- 각 기록은 `root_hash`를 가진다. `verify`는 이것이 같은 `tree_size`의 체크포인트 루트와 같은지 확인한다(`ANCHOR_MISMATCH`).
- 이슈 초안은 "게시는 `os.link`, 덮어쓰기 없음"과 "실패 후 다음 실행에서 재시도"를 함께 요구한다. 둘을 이렇게 조정했다: **`submitted` 기록은 절대 교체하지 않고, `failed` 기록만 원자적 `rename`으로 교체**한다. 다른 파일(체크포인트, 항목)은 교체 경로가 없다.

## 앵커 어댑터

환경변수로만 설정한다(`config.py`는 수정하지 않았다).

| 변수 | 값 |
|---|---|
| `MODULEGUARD_TRANSPARENCY_ANCHOR` | `none`(기본) \| `opentimestamps` \| `rfc3161`. 그 외 값은 종료 코드 2 |
| `MODULEGUARD_OTS_CALENDARS` | 쉼표로 구분한 https calendar(운영자 승인 목록, 최대 8개) |
| `MODULEGUARD_ATTESTATION_POLICY_FILE` | `rfc3161`에서 #27과 같은 정책 파일(`tsa_url`, `tsa_roots_pem`) |

공통: `httpx`, `follow_redirects=False`, `trust_env=False`, 타임아웃 10초, 응답 64 KiB 상한, 인증정보·query·fragment가 있는 URL 거부(https만). **외부로 나가는 값은 머클 루트 32바이트뿐**이다. scan ID, digest, URL, 쿠키, 인증 헤더는 보내지 않는다. 앵커가 실패해도 체크포인트는 이미 저장되어 있고, `failed`로 남긴 뒤 다음 `checkpoint` 실행에서 재시도한다. 다른 calendar나 TSA로 자동 우회하지 않는다. 설정 오류(잘못된 URL, 정책 없음)는 전송 없이 종료 코드 2이며 아무것도 기록하지 않는다.

- **opentimestamps**: 각 calendar에 `POST <calendar>/digest`로 루트 32바이트를 보내고(`Accept: application/vnd.opentimestamps.v1`) 응답 바이트를 그대로 저장한다. 하나라도 응답하면 `submitted`이고, 응답하지 않은 calendar는 다시 시도하지 않는다. 저장되는 것은 **대기 중인 타임스탬프**이지 완전한 `.ots` 파일이 아니다. 비트코인 확정(upgrade)과 완전 검증은 서버가 하지 않고 독립 검증기가 공식 `ots` 도구로 수행한다. **`.ots` export는 구현하지 않았다(미구현·미검증)**. 공식 구현(python-opentimestamps)을 이 환경에서 확인할 수 없어, 컨테이너 직렬화 규격을 추측해 하드코딩하지 않았다.
- **rfc3161**: 루트 바이트에 대한 SHA-256 imprint로 타임스탬프를 받고, 정책의 TSA 루트로 저장 전에 검증한다(`signing._timestamp` 재사용). `verify`/`verify-bundle`은 정책 파일이 있으면 암호학적으로 다시 검증하고, 없으면 `ANCHOR_UNVERIFIED` 경고만 낸다.

## CLI

```
python -m app.transparency.cli append
python -m app.transparency.cli checkpoint
python -m app.transparency.cli record-revisions --reason "<text>" [--scan-id ID ...] [--apply]
python -m app.transparency.cli verify [--json] [--trusted-checkpoint FILE]
python -m app.transparency.cli export DIR
python -m app.transparency.cli verify-bundle DIR [--trusted-checkpoint FILE] [--json]
```

`--database-url`(기본 `DATABASE_URL`/`.env`)과 `--log-dir`(기본 `<SNAPSHOT_ROOT>/transparency`)는 DB나 로그를 쓰는 명령에서 선택한다.

| 명령 | 동작 |
|---|---|
| `append` | 아직 기록되지 않은 COMPLETED/FAILED 검사(`finished_at` 있음)를 `(finished_at, id)` 순으로 `SCAN` 항목으로 추가. 커밋먼트를 만들 수 없는 검사는 고정 코드로 건너뜀 |
| `checkpoint` | `append` → 항목이 늘었으면 체크포인트 → 앵커. 멱등. 앵커 실패 시 종료 코드 2 |
| `record-revisions` | 현재 commitment가 마지막 항목과 다른 검사를 `REVISION`으로 추가. **기본 dry-run**(`reapply.py`와 같은 UX). 로그 디렉터리조차 만들지 않음 |
| `verify` | ① 로그 내부 일관성 ② 인접 체크포인트 일관성 ③ 앵커가 해당 루트를 가리키는지 ④ DB 현재 commitment와 각 scan의 마지막 항목 대조 ⑤ `--trusted-checkpoint`와의 일관성(롤백·포크 탐지) |
| `export` | 검증한 로그·체크포인트·앵커를 새(빈) 디렉터리로 내보냄. `.ots`는 만들지 않음 |
| `verify-bundle` | DB 없이 묶음만으로 ①②③⑤ 검증 |

종료 코드: `0` 정상, `1` 조작 의심(`MISMATCH`, `LOG_INCONSISTENT`, `CONSISTENCY_FAILED`, `ANCHOR_MISMATCH`), `2` 환경 문제(DB, 저장소, 잠금, 앵커, 인자 오류). 출력에는 결과 원문, 외부 오류 원문, 경로가 들어가지 않는다.

`verify`의 scan별 상태:

| 상태 | 의미 | 종료 코드 영향 |
|---|---|---|
| `ok` | 일치하고 체크포인트에 포함 | 없음 |
| `pending` | 기록됐지만 아직 체크포인트에 없음 | 없음 |
| `mismatch` | DB 값이 마지막 항목과 다름 → **조작 또는 미선언 변경** | 1 |
| `missing` | 로그에는 있으나 DB 행이 없음. 정상적인 저장소 삭제일 수 있음. 원래 항목은 계속 증명 가능 | 경고만 |
| `not_logged` | 끝난 검사인데 아직 기록되지 않음 | 경고만 |

### 실제 실행 예시 (로컬 SQLite, `anchor=none`)

```
$ cli append
appended=2 not_logged=0
$ cli checkpoint
appended=0 not_logged=0
checkpoint created: tree_size=2 root_hash=5bacce36...c337c
$ cli verify
status: ok
scans: ok=2 pending=0 mismatch=0 missing=0 not_logged=0
[exit 0]
  (DB에서 finding 1건의 guideline을 직접 수정)
$ cli verify
status: suspect
scans: ok=1 pending=0 mismatch=1 missing=0 not_logged=0
  mismatch: 304e869f-38a8-4d5e-a330-da11ea4d75e6
finding: MISMATCH
[exit 1]
$ cli record-revisions --reason "manual run: declared change"
would record revision 304e869f-38a8-4d5e-a330-da11ea4d75e6
Would record 1 revision(s). Dry run: nothing written. Re-run with --apply.
$ cli record-revisions --reason "manual run: declared change" --apply
revision 304e869f-38a8-4d5e-a330-da11ea4d75e6
Recorded 1 revision(s). Run `checkpoint` to include them.
$ cli checkpoint
checkpoint created: tree_size=3 root_hash=512f9d03...eb876
$ cli verify
status: ok
scans: ok=2 pending=0 mismatch=0 missing=0 not_logged=0
[exit 0]
```

`leaves.jsonl`에는 `SCAN`, `SCAN`, `REVISION`이 남는다. 첫 항목은 지워지지 않았다.

## API (읽기 전용, 추가만)

모든 응답에 `Cache-Control: no-store`를 붙이고 `usage_service.preview_limiter`를 사용한다. 사용자 예외 승인으로 생성 API 계약 동기화: 이 PR에 한해 `npm run api:sync`로 `openapi.json`과 `src/api/generated/**`를 갱신해 별도 커밋(`chore: sync API contract for #62`)으로 넣었다(추가만, 삭제 없음). 이후 변경은 원칙대로 프런트 담당이 `npm run api:sync`로 갱신한다.

### GET `/api/transparency/checkpoint`

가장 최근 체크포인트(공개 정보만). 로그가 없으면 `enabled=false, checkpoint=null, anchors=[]`. 영수증 원문은 내려주지 않고 `receipt_sha256`만 보여준다.

```json
{"schema_version":1,"enabled":true,
 "checkpoint":{"tree_size":3,"root_hash":"b8858d1e…89d7","created_at":"2026-11-26T06:32:46.870565Z"},
 "anchors":[{"type":"opentimestamps","status":"submitted","receipt_sha256":"1e8192a4…de65","created_at":"2026-11-26T05:00:03Z"}]}
```

### GET `/api/transparency/consistency?first=<m>&second=<n>`

두 tree_size는 실제 체크포인트여야 하고(`first ≤ second`), 아니면 404다.

```json
{"schema_version":1,"first":1,"second":3,"first_root":"23e331cf…dc4e","second_root":"b8858d1e…89d7",
 "proof":["f73864b4…18b5","2e819c7d…b37c"]}
```

### GET `/api/scans/{scan_id}/transparency?version_id=<UUID>`

가시성은 #27과 같다. `AttestationService.target()`을 재사용하고 라우터 수준 `require_visible_scan`도 적용한다. 숨김, 없음, 버전 불일치는 모두 404다.

```json
{"schema_version":1,"scan_id":"8b2098a2-…","version_id":"4fe5d7e6-…","status":"included","reasons":[],
 "leaf_index":0,
 "entry":{"format":1,"seq":0,"kind":"SCAN","reason":null,"recorded_at":"2026-11-26T06:32:46.856508+00:00",
          "commitment":{"scan_id":"8b2098a2-…","module_version_id":"4fe5d7e6-…","status":"COMPLETED",
                        "result_sha256":"b1709fb9…88e7","manifest_sha256":"9089969f…2d52","snapshot_sha256":"a0b2a829…8958"}},
 "leaf_hash":"23e331cf…dc4e","current_commitment_sha256":"be27d284…007d","logged_commitment_sha256":"be27d284…007d",
 "revisions":0,"checkpoint":{"tree_size":3,"root_hash":"b8858d1e…89d7"},
 "inclusion_proof":["f73864b4…18b5","2e819c7d…b37c"]}
```

- `status`: `included` | `pending` | `mismatch` | `not_logged`. `mismatch`는 `included`·`pending`보다 우선한다.
- `reasons`(고정 코드): `COMMITMENT_MISMATCH`, `NOT_IN_CHECKPOINT`, `TOO_LARGE`, `RESULT_INVALID`, `SCAN_NOT_FINISHED`, `LOG_UNAVAILABLE`, `LOG_INCONSISTENT`.
- 불일치여도 원래 기록한 항목과 증명은 그대로 내려간다(원래 값은 계속 증명 가능).
- `entry`는 응답 바이트가 곧 리프 입력이다. `recorded_at` 등은 저장된 문자열 그대로다. 독립 검증기는 `SHA256(0x00 || canonical(entry))`가 `leaf_hash`와 같은지, 포함 증명이 `checkpoint.root_hash`로 이어지는지 확인한다.
- 결과 원문, finding, 경로, URL은 응답에 넣지 않는다. 형제 해시는 UUID가 들어간 항목 해시의 해시라서 다른 비공개 검사 정보를 드러내지 않는다(UUID는 추측할 수 없고 SHA-256은 되돌릴 수 없다).
- **fork 노출 규칙**: fork는 원본과 한 검사를 공유하므로 항목의 `module_version_id`가 원본의 버전을 가리킬 수 있다. 이 ID가 요청한 버전이 아니고 요청자가 그 저장소를 볼 수 없으면(예: 원본이 비공개로 바뀜) `entry`를 `null`로 돌려준다. 그 경우에도 `leaf_hash`와 증명은 내려가므로 포함 여부는 확인할 수 있고, 숨겨진 원본의 존재는 드러나지 않는다.
- 로그가 읽히지 않으면 200과 함께 `status: not_logged, reasons:["LOG_UNAVAILABLE"]`, 일관되지 않으면 `status: mismatch, reasons:[…, "LOG_INCONSISTENT"]`이다. `checkpoint`/`consistency` 엔드포인트는 같은 코드로 503을 돌려준다(본문은 고정 문구).

## 운영 절차

1. **주기 실행**(5~15분마다). 검사 완료와 체크포인트 사이 간격이 곧 "첫 기록 전 위조" 위험 구간이다.

   ```
   */10 * * * * cd /app/backend && python -m app.transparency.cli checkpoint >> /var/log/moduleguard-transparency.log 2>&1
   ```

   종료 코드 2(앵커 실패 포함)는 알림 대상이다.
2. **`reapply --apply` 뒤**: 결과를 정당하게 바꿨다면 `record-revisions --reason "reapply 2026-11-26"`(dry-run)로 대상 확인 → `--apply` → `checkpoint`. 이 절차 없이 바뀐 결과는 `verify`에서 `mismatch`다.
3. **서버 밖 검증**: 서버 API만으로는 서버의 거짓 응답을 막을 수 없다. 주기적으로 `export`한 묶음을 서버 밖으로 옮겨 `verify-bundle DIR --trusted-checkpoint last.json`을 실행하고, **통과한 가장 최신 체크포인트 파일을 검증기 쪽에 따로 보관**한다(롤백·포크 탐지의 기준점). 앵커를 쓰면 같은 환경에서 `ots verify`와 TSA 검증도 수행한다.
4. **로그 보존**: `<SNAPSHOT_ROOT>/transparency/`를 백업하되 `leaves.jsonl`은 append만 한다. 스냅샷 정리(`sweep_unreferenced`)는 `*.tar.gz`만 지우므로 이 디렉터리에는 영향이 없다. 다른 정리 작업의 대상에 넣지 않는다.

## 위협 모델

| 공격 | 탐지 |
|---|---|
| 기록 후 DB의 결과·집계·finding 수정 | 탐지(`mismatch`) |
| 로그 항목 수정·삭제·순서 변경 | 탐지(`LOG_INCONSISTENT`, `CONSISTENCY_FAILED`) |
| 로그를 통째로 다시 만들기(이력 재작성) | 앵커된 루트나 검증기의 trusted checkpoint와 일관성 실패로 탐지 |
| 서버 장악 후 `record-revisions`로 변경 "세탁" | 막을 수 없음. 다만 **`REVISION` 항목이 영구히 남고** 원래 값도 계속 증명 가능 → 숨길 수는 없음 |
| 첫 기록 전 위조된 결과 | 탐지 불가 → cron 간격을 짧게 운영 |
| 스캐너 자체가 거짓 결과 생성 | 범위 밖(#27과 동일) |
| 서버가 검증 API 응답을 위조 | 서버 API만으로는 해결 안 됨 → `export` + `verify-bundle`을 서버 밖에서 실행 |
| 가장 최신 체크포인트와 그 이후 항목 삭제(롤백) | 서버 안에서는 탐지 불가. 검증기가 보관한 trusted checkpoint나 앵커가 있어야 탐지 |
| 앵커 없이 로그 전체와 체크포인트를 함께 교체 | 서버 안에서는 탐지 불가(앵커 또는 trusted checkpoint 필요) |

알려진 오탐·한계:

- 원본 버전이 삭제되고 fork가 검사를 계속 가지면 `scan.module_version_id`가 NULL이 되어 `verify`가 `mismatch`를 낼 수 있다. 조작이 아니라 커밋먼트 입력이 바뀐 경우이며, 확인 후 `record-revisions`로 선언한다.
- 저장소 삭제 tombstone 기록은 이번 범위가 아니다(`missing`은 경고).
- 리프에 시각(`recorded_at`)이 있지만 이는 서버가 적은 값이다. 신뢰할 수 있는 시각은 앵커(TSA/비트코인)에서만 나온다.
- API는 요청마다 로그를 읽는다(항목 수에 비례, 상한 100만 줄/256 MiB). 요청 제한이 있지만 로그가 커지면 캐시가 필요하다. `verify`는 O(체크포인트 × 항목)이다.
- `fcntl`을 쓰므로 **Linux 전용**이다(기존 증명 저장소와 같다). Windows에는 `fcntl`이 없어 `app.main` 자체를 가져올 수 없다. 이 작업의 테스트와 CLI 실행은 모두 **WSL(Ubuntu, Python 3.12)에서만** 했고 Windows 네이티브에서는 실행하지 않았다.

## 외부 전송 승인 체크리스트

앵커를 `none` 밖으로 바꾸기 전에 모두 확인한다.

- [ ] 외부로 나가는 값이 머클 루트 32바이트(hex 64자)뿐임을 확인했다(테스트: 요청 본문이 루트와 같고 쿠키·인증 헤더 없음).
- [ ] calendar 또는 TSA 목록과 운영 주체를 소유자가 승인했다. 모두 https이고 인증정보·query가 없다.
- [ ] 서버의 외부 송신(egress) 허용 목록에 해당 호스트만 추가했다.
- [ ] 실패 시 동작(`failed` 기록, 다음 실행에서 재시도, 자동 우회 없음)을 운영팀이 알고 있다. 종료 코드 2 알림이 연결되어 있다.
- [ ] `rfc3161`이면 `MODULEGUARD_ATTESTATION_POLICY_FILE`의 `tsa_url`과 `tsa_roots_pem`이 승인된 값이다.
- [ ] trusted checkpoint 보관 위치와 서버 밖 `verify-bundle` 실행 환경, 담당자가 정해졌다.
- [ ] 첫 앵커링은 승인 직후 한 번만 `checkpoint`로 실행하고 결과(`submitted`/`failed`)를 확인한다.

## 이번 이슈에서 실행·검증하지 않은 것

- **실제 OpenTimestamps calendar나 TSA에 연결한 적이 없다.** 모든 테스트는 `httpx` 전송만 대체했다. `POST /digest` 요청 형식(본문은 루트 원본 32바이트, `Accept` 헤더)은 공개된 프로토콜 설명에 맞춰 작성했지만 실제 calendar 응답이나 공식 구현과 대조하지 않았다.
- **`.ots` export는 미구현이다.** 저장하는 것은 calendar 원본 응답뿐이며 공식 `ots` 도구로 열 수 있는지 확인하지 않았다. 비트코인 확정과 `ots verify`는 서버가 하지 않는다(`OTS_NOT_VERIFIED` 경고).
- RFC 3161은 로컬 OpenSSL TSA fixture로만 검증했다(OpenSSL이 없으면 테스트가 사유를 명시하고 skip).
- Windows 네이티브 실행은 지원하지 않으며 시도하지 않았다(위 `fcntl` 한계). 모든 검증은 WSL에서 했다.
- PostgreSQL 대상 실행은 하지 않았다(SQLite와 `-m 'not postgres'`).
- 대용량(수만 건 이상) 로그의 성능은 측정하지 않았다.
- 후속 작업: 저장소 삭제 tombstone(`publish_service` hook), `reapply --apply` 직후 REVISION 자동 기록, `main.py` lifespan 주기 실행, 체크포인트 DSSE 서명.
