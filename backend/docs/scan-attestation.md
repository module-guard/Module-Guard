# 검사 증명 발급·검증 (#27)

기준: #26 / PR #40 병합 main `d1696411a300bb0cedc64ebfee41dbe90d607593`.
구현은 AWS KMS 기반 DSSE 발급과 RFC 3161 시간 증거 검증이다. 운영 키·클라우드 자원은 생성하지 않는다.
서명은 누가 어떤 모듈·검사 결과에 서명했는지를 확인한다. 코드의 안전함·취약점 없음·검사 완전성 또는 게시자의 법적 책임을 보증하지 않는다.

## ADR

| 방식 | 장점 | 제약 |
| --- | --- | --- |
| Sigstore keyless bundle | OIDC 신원·인증서·로그/시간 증거를 표준 도구로 검증 | 정확한 issuer/subject 및 독립 신뢰 루트 필요. 공개 로그에는 비공개 digest·신원도 노출될 수 있음 |
| 사설 Sigstore | 사설 신원·로그 운영 가능 | CA/OIDC/로그·루트 갱신 운영 필요 |
| 관리형 KMS + DSSE + RFC 3161 TSA | 개인키를 API 서버 밖에 두고 공개 로그 없이 발급 가능 | 키 회전·폐기 정책, TSA 신뢰 루트, 정책 독립 배포 필요. KMS 키만으로 OIDC 신원은 증명되지 않음 |

선택: 관리형 AWS KMS, `securesystemslib`의 기존 `AWSSigner`와 `Envelope`, `rfc3161-client`를 사용한다.
자체 암호 서명·DSSE 인코딩·인증서 체인 검증을 구현하지 않는다. Cosign 별도 실행 파일 대신
동일한 표준 DSSE를 지원하는 라이브러리로 Python 서비스에 연결했다.
기본 알고리즘 허용 범위는 ECDSA P-256/SHA-256, RSA-PSS/SHA-256이다.
서명기에는 DSSE payload의 digest만 전달하고, TSA에는 그 서명의 hash를 전달한다.
공개 Rekor/OCI 로그로 payload·bundle·URL·digest·입력값을 자동 전송하지 않는다.
선택한 TSA도 서명 digest와 호출자 네트워크 정보를 볼 수 있으므로 비공개 서비스용으로 운영자가 승인해야 한다.

플랫폼 발급은 `platform` 전용이다. upstream 작성자 증명은 별도 `upstream` 키와 신뢰 정책으로
검증할 수 있으며 플랫폼 키가 작성자 서명으로 취급되지 않는다. 이번 KMS 방식은 Fulcio 인증서나
OIDC 신원을 검증했다고 표시하지 않는다. upstream keyless Sigstore bundle 자동 가져오기는 포함하지 않는다.

## 운영 설정과 의존성

추가 패키지는 로컬 검증 환경에 다음 버전으로 설치해 검사했다.

```sh
python -m pip install 'securesystemslib[crypto,awskms]==1.6.0' 'rfc3161-client==1.0.9'
```

이번 이슈에 대한 사용자의 명시적 예외 승인으로 `backend/requirements.txt`에 위 두 패키지를 고정했다.
기존 backend 의존성 설치 절차가 이 패키지도 설치한다. `backend/pyproject.toml`은 변경하지 않았다.
누락된 배포 환경에서는 검증이 `unknown / VERIFIER_UNAVAILABLE`, 발급이 503으로 실패한다.
암호 테스트의 라이브러리 누락은 실패이며, 실제 TSA fixture를 생성할 OpenSSL이 없을 때만 사유를 명시해 skip한다.
실제 KMS/TSA가 연결되지 않은 상태를 운영 검증 완료로 표시하지 않는다.

`MODULEGUARD_ATTESTATION_POLICY_FILE`에 운영자가 관리하는 JSON 파일의 절대 경로를 설정한다.
응답이나 업로드에서 정책·키·URL을 받아 신뢰하지 않는다. API 서버가 수정할 수 없는 별도 배포 경로로
정책과 신뢰 자료를 제공해야 한다. 파일은 최대 128 KiB이며 잘못된 정책은 사용하지 않는다.
다음 구조의 값은 모두 예시이며 실제 trust material로 사용할 수 없다.

```json
{
  "version": "scan-trust/1",
  "accepted_claim_versions": ["scan-trust/1"],
  "not_before": "2026-11-05T00:00:00Z",
  "expires_at": "2026-11-06T00:00:00Z",
  "revocations_valid_until": "2026-11-06T00:00:00Z",
  "signing_key_id": "<securesystemslib public key id>",
  "signers": {
    "<securesystemslib public key id>": {
      "public_key": {
        "keytype": "ecdsa",
        "scheme": "ecdsa-sha2-nistp256",
        "keyval": {"public": "<PEM public key>"}
      },
      "issuer": "moduleguard-platform",
      "subject": "scan-worker",
      "purpose": "platform",
      "not_before": "2026-11-05T00:00:00Z",
      "not_after": "2026-11-07T00:00:00Z",
      "revoked": false,
      "kms_key_arn": "arn:aws:kms:REGION:ACCOUNT:key/KEY-ID"
    }
  },
  "tsa_roots_pem": ["<trusted TSA CA certificate PEM>"],
  "tsa_url": "https://approved-private-tsa.example/timestamp",
  "ttl_seconds": 86400
}
```

키 alias 대신 고정 ARN과 독립 배포한 공개키를 사용한다. 개인키는 설정·DB·저장소에 넣지 않는다.
AWS 인증은 SDK의 배포 자격증명 체계를 사용하며 발급 주체에 한해 최소 `kms:Sign` 권한을 부여한다.
검증은 공개키와 저장된 TSA 증거로 로컬에서 수행하며 KMS나 사용자 지정 URL을 호출하지 않는다.
TSA는 설정된 HTTPS 한 곳만 호출하고 redirect, URL 인증정보/query, 환경 proxy를 사용하지 않는다.
KMS 연결 3초/응답 10초·제한 재시도, TSA 요청 10초·응답 128 KiB 상한을 둔다.
인증서·서명에 결부된 timestamp와 SHA-256 imprint, 발급 요청 nonce를 표준 라이브러리로 검증한다.

## 서명되는 claim과 기존 기능 재사용

payload는 in-toto Statement v1이며 payloadType은 `application/vnd.in-toto+json`이다.
predicateType은 `https://github.com/module-guard/Module-Guard/attestation/scan/v1`이다.
모든 subject digest와 아래 binding이 하나의 DSSE 서명으로 묶인다.

- #9의 버전 manifest SHA-256·snapshot SHA-256와 정확한 version ID.
- 해당 버전 기본 ZIP이 있으면 artifact ID와 ZIP SHA-256. 사용자 입력 ZIP은 이 API에서 발급하지 않는다.
- 원본 commit·모듈 경로, 원본 URL의 SHA-256, repository/module/version/scan ID.
- #26의 불변 `LineageRecord.id`. 부모 ID·digest·숨겨진 원본 정보는 claim에 넣지 않는다.
- 결과 export 규격 1의 SHA-256: 상태·partial 여부·finding 집계·AnalysisSummary·scope_summary·
  postprocessing과 finding/entity/edge 행. 행은 ID 순이며 UUID/시간은 문자열로, JSON은 UTF-8·
  정렬된 키·공백 없는 구분자를 사용한다. 비정상 숫자는 거절한다. 행당 테이블 50,000건, export 16 MiB 상한이다.
- #13 최초 provenance의 기존 도구/규칙/공개 옵션 필드만 뽑은 JSON의 SHA-256.
  현재 도구 버전으로 과거 null을 소급하지 않는다. 버전 메타데이터 조회는 기존 scan API를 사용한다.
- 기존 dependencies 응답(analysis 제외)의 SHA-256와 manifest에 있으면 `.terraform.lock.hcl`의 SHA-256.
- 의존성 실제 해석 버전·호스트 환경은 기록되지 않았음을 명시한다.
  `metadata_complete=false`, `resolved_versions_recorded=false`, `environment_recorded=false`를 유지한다.
- 발급자의 issuer/subject, `platform|upstream` 목적, 발급 정책 버전, 주장된 발급 시각·만료 시각.

원본 URL·임의 metadata·raw_data·표현식·진단 원문·사용자 변수값을 claim 응답에 복사하지 않는다.
결과 원문은 내부에서 해시할 뿐 응답이나 로그에 추가하지 않는다. source_path는 저장된 모듈 상대 경로다.
기존 snapshot 검증(`SnapshotStore.verified_bytes`)을 재사용하고 기본 ZIP 실제 바이트도 대조한다.
해시 일치와 서명 유효 상태는 독립이며 어느 하나로 다른 하나의 실패를 덮지 않는다.

reapply 전 증명 파일과 최초 provenance는 보존된다. 후처리로 현재 결과가 달라지면 과거 증명은
현재 결과에 대해 `invalid / TARGET_MISMATCH`가 되고, 새 발급은 별도 claim이다.
과거 revision 자체의 불변 결과 export를 추가 저장하거나 복원하지는 않는다.
같은 바이트를 복사한 fork도 별도 version/repository/lineage를 갖기 때문에 원본 증명을 재사용할 수 없다.
업로드 당시 계보 source_url이 null인 기존 기록은 현재 version URL과 동일했다고 추정하지 않는다.
manifest와 기록된 commit·경로 등 확인 가능한 계보 근거를 대조하고 누락은 그대로 유지한다.

## 저장·권한·API

증명은 `<snapshot_root>/attestations/<version UUID>/`의 독립 파일에 저장한다. DB migration은 없다.
원본과 fork가 같은 Scan을 공유하더라도 증명 저장·발급 한도를 분리하며 scan.analysis를 쓰지 않는다.
버전당 최대 16건, proof 최대 256 KiB, envelope 최대 64 KiB이다.
권한 0700 디렉터리/0600 파일, 심볼릭 링크·비정규 파일 거부, 버전별 비차단 파일 잠금,
새 파일 쓰기·fsync·기존 파일을 덮지 않는 atomic link로 게시한다.
서명 전후 대상 binding을 다시 대조하고 도중 결과 변경은 409로 거절한다.
증명 파일 저장소는 기존 snapshot volume과 함께 백업·공유해야 한다. 다중 호스트는 같은 POSIX 잠금/
atomic-link 의미를 보장하는 공유 volume이 필요하다. 버전 삭제 후 파일 자동 GC는 추가하지 않았으며
운영자가 version 존재 여부를 확인하는 보존/정리 정책을 운영해야 한다. 삭제된 대상은 API에서 404다.

모든 경로는 `/api/scans/{scan_id}` 기준이고 `version_id=<UUID>` query가 필수다.
shared scan에 속한 다른 저장소·비공개 버전의 증명 조회를 version 연결과 기존 visibility 규칙으로 차단한다.

| 메서드·경로 | 동작 |
| --- | --- |
| GET `/attestations` | 저장 증명을 현재 정책·현재 대상과 자동 재검증. 무서명이면 unsigned |
| POST `/attestations` | 로그인한 해당 버전 저장소 소유자만 서버가 만든 claim 발급. 201 |
| POST `/attestations/verify` | 본문 `{ "envelope": { ... }, "timestamp": "<base64 RFC3161 response>" }` 검증. upstream/platform 구별 |

POST는 기존 Origin 검사를 사용하고 읽기·발급·검증에 기존 preview rate limiter를 재사용한다.
존재 없음/숨김/다른 버전 연결은 404, 발급 중·상한·대상 변경은 409, 미설정/서비스 장애는 503이다.
응답은 `Cache-Control: no-store`다. 오류에는 고정 코드/안전한 설명만 사용한다.

미서명 조회 예시:

```json
{
  "schema_version": 1,
  "scan_id": "22222222-2222-4222-8222-222222222222",
  "version_id": "11111111-1111-4111-8111-111111111111",
  "status": "unsigned",
  "reasons": ["NO_SIGNATURE"],
  "entries": []
}
```

증명이 있으면 `entries[]`는 id, proof, verification을 포함한다. 최상위 상태는 마지막 발급 증명의
현재 검증 상태이며 각 증명의 결과도 독립적으로 제공한다. verification은 목적·발급자·주체,
적용 정책 버전, `claimed_at`과 `trusted_at`, `security_guarantee=false`를 포함한다.
TSA 시각은 해당 서명의 존재를 뒷받침하며 검사 실행 시각을 독립적으로 증명하지 않는다.

| 상태 | 의미 |
| --- | --- |
| unsigned | 해당 버전 증명 없음. 서명 성공이 아님 |
| valid | 표준 서명, 전체 대상, 허용 신원·키·정책, 시간 증거 검사 통과 |
| invalid | 변조·대상 불일치·금지 신원/키·잘못된 증거·claim 만료 등 확인 |
| unknown | 정책/라이브러리/시간 증거/저장 자료 부재 또는 검증 불가 |
| revoked | 유효 서명이 확인된 키를 현재 신뢰 정책이 폐기 |
| policy-expired | 현재 검증 정책이 유효기간 밖이라 승인 불가 |

정책 만료/폐기 자료 만료는 성공으로 처리하지 않는다. 본문 임의 공개키·인증서·key ID로
신뢰 루트를 대체하지 않는다. 인증된 키의 issuer/subject/purpose도 claim과 일치해야 한다.
만료 24시간 이내, TSA 시각/주장 시각 오차 5분 이내, key 발급 기간과 미래 시각을 검사한다.
서명 필수 소비자는 valid만 승인해야 한다. unsigned를 허용하면 증명 삭제를 검출할 수 없다.

## 위협 모델·회전·장애 대응

보호 자산은 정확한 모듈/결과 바이트, 비공개 데이터, 서명 권한, 독립 신뢰 정책이다.
공격자가 파일·DB·bundle을 바꾸고 다른 버전/tenant의 증명을 제출할 수 있다고 가정한다.
독립 검증기의 trust policy와 KMS/TSA 권한까지 모두 침해되지 않았다는 경계가 필요하다.
API 서버 자체가 침해되고 Sign 권한까지 있으면 거짓 claim 발급을 막지 못한다. 강한 운영 경계가
필요한 배포는 signer를 분리하고 독립적으로 검사한 불변 대상에 대해서만 발급해야 한다.
침해된 scanner가 거짓 결과를 생성한 문제도 서명만으로 해결되지 않는다.

| 위협 | 적용 방어 |
| --- | --- |
| 바이트·claim·결과 변조 | #9 바이트 확인 + 표준 DSSE + 전체 binding 대조 |
| 다른 버전/fork/검사/저장소에 재사용 | subject ID·digest 전체 및 불변 lineage ID 결부 |
| 임의 공개키·issuer/주체 위장 | 별도 정책의 키·정확한 issuer/subject/purpose 대조 |
| 숨김 부모·사용자 입력 유출 | 부모 정보·사용자 입력 ZIP 제외, version별 visibility, 원문 미반환 |
| 시각 위조·시간 증거 재사용 | 표준 TSA 인증서/서명/imprint/요청 nonce 검증, 서명과 시각 결부 |
| 검증기 SSRF·과대 입력 | 증명 URL 호출 금지, 고정 TSA, JSON/시간/행/출력 상한 |
| 병렬 발급·서명 중 변경 | 버전별 잠금·불변 파일 게시·대상 재대조 |
| 정책 롤백·폐기 캐시 | 현재 정책 파일 재로드, 만료·폐기 자료 유효기간 확인, valid 캐시 없음 |
| 서명 삭제·서버 응답 위조 | 독립적인 서명 필수 정책·검증 클라이언트가 필요. 앱 응답만으로 해결 안 됨 |

회전은 새 공개키/정책을 검증기에 먼저 배포하고 고정 새 KMS key로 발급을 전환한 뒤 구 키 발급 권한을
제거한다. 구 증명 검증에 필요한 키·TSA 자료와 승인된 claim policy 버전은 보존한다.
침해 시 signer의 Sign 권한을 중단하고 trusted signer를 `revoked=true`로 배포해 과거 증명도 재평가한다.
키별 효력 시점 예외를 추측하지 않고 보수적으로 해당 키의 모든 증명을 revoked로 처리한다.
TSA/정책/폐기 자료가 없으면 unknown이며 KMS/TSA 장애는 다른 키·공개 로그로 우회하지 않는다.
클라우드 SDK 오류가 서명 라이브러리 자체 로그로 흘러가기 전에 안전한 오류로 바꾼다.
정책/폐기 자료의 배포 rollback 방지는 운영자가 독립 배포 경로에서 보장해야 한다.

## 검증과 다른 담당자에게 요청할 변경

새 테스트는 실제 로컬 ECDSA DSSE 서명, OpenSSL RFC 3161 응답, 표준 라이브러리 인증서 검증을 사용한다.
클라우드 어댑터 테스트는 KMS/TSA 전송만 대체하며 서명·timestamp 검증을 mock하지 않는다.
변조/대상 바꾸기/신뢰되지 않은 키/issuer/주체/목적/만료/폐기/증거 누락/비공개 접근/원본과 fork/
reapply 전후·no-op/원문 비노출/저장 경쟁·권한·상한·심볼릭 링크 거부를 검사한다.
실제 운영 KMS/TSA 연결, 운영 IAM, 공유 volume 장애·다중 호스트 부하 검증은 수행하지 않았다.

배포 담당: 갱신된 requirements로 설치하고 운영 trust policy/KMS/TSA와 공유 증명 volume을 구성한다.
생성 API 계약(`openapi.json`, `src/api/generated/schema.d.ts`)도 사용자의 예외 승인 범위로 동기화했다.
기존 경로·schema는 유지하며, ORM·migration·main.py는 수정하지 않았다.

## 근거

- [#27](https://github.com/module-guard/Module-Guard/issues/27), [#26 병합 PR #40](https://github.com/module-guard/Module-Guard/pull/40)
- 기존 [무결성](module-integrity.md), [provenance](scan-provenance.md), [불변 계보](module-lineage.md)
- [securesystemslib DSSE](https://github.com/secure-systems-lab/securesystemslib/blob/main/securesystemslib/dsse.py)
- [securesystemslib AWS KMS signer](https://github.com/secure-systems-lab/securesystemslib/blob/main/securesystemslib/signer/_aws_signer.py)
- [RFC 3161 client](https://github.com/trailofbits/rfc3161-client), [in-toto Statement](https://github.com/in-toto/attestation/blob/main/spec/v1/statement.md)
- [Sigstore bundle](https://docs.sigstore.dev/about/bundle/), [TSA](https://docs.sigstore.dev/cosign/verifying/timestamps/)
- [KMS Sign](https://docs.aws.amazon.com/kms/latest/APIReference/API_Sign.html), [Verify](https://docs.aws.amazon.com/kms/latest/APIReference/API_Verify.html)
