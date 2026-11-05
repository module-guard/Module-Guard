# 원본·파생 모듈의 불변 계보와 신뢰 근거 (#26)

모듈 버전마다 "어디서 왔는지"와 "누가 게시했는지"를 한 번만 기록하고 바꾸지 않는다. 비교는 #9의 SHA-256 manifest(`format: 1`)를 그대로 사용하며 무결성 검증을 다시 구현하지 않는다. 무결성 판정(`/integrity`, `/integrity/verify`)의 동작은 바뀌지 않는다.

## 기록 (`lineage_records`, migration 0011)

저장소 버전 하나에 행 하나. 버전이 만들어지는 같은 트랜잭션에서 서버가 기록하며, 부모는 요청자가 지정할 수 없다.

| 관계 | 언제 | 부모 |
| --- | --- | --- |
| `ORIGINAL` | 저장소의 첫 저장 버전 (업로드·GitHub·미러) | 없음 |
| `VERSION` | 같은 저장소의 다음 버전 | 같은 저장소의 직전 저장 버전 |
| `FORK` | "가져오기"로 다른 저장소 버전을 그대로 복사 | 원본 저장소의 해당 버전 |

- 행에는 부모·현재 manifest와 `manifest_sha256`, 출처 URL, commit, 모듈 경로, 관계, 게시자 계정 ID(`users.id`, 로그인 이름이 아님), 게시 시각, 게시 선언 버전이 들어간다. manifest는 버전에서 복사만 하고 다시 계산하지 않는다.
- 쓰기 규칙: `ORIGINAL`은 부모가 없고 나머지는 반드시 있다. `VERSION`의 부모는 같은 저장소, `FORK`의 부모는 다른 저장소여야 한다. 부모에서 위로 올라가며 자기 자신이나 기록하려는 버전을 다시 만나면 순환으로 보고 거부한다. 버전당 한 번만 기록한다. 위반은 409 `InvalidLineage`.
- 불변: ORM 수준에서 갱신·삭제를 거부한다(`LineageImmutableError`). 버전·저장소·사용자를 가리키는 외래 키를 두지 않아 그것들이 삭제돼도 계보 행은 남는다. 대신 보여 줄지 여부는 읽을 때 정한다(아래).
- 검사에 실패해 저장되지 못한 시도도 생성 시점에 기록되지만, 저장되지 않은 버전은 어디에도 보이지 않고 다음 버전의 부모로도 선택되지 않는다.

## 게시자 선언

게시자는 이 서비스에 해당 버전을 올린 계정이며 **코드의 작성자로 단정하지 않는다**. 응답의 `author_asserted`는 항상 `false`다. 법적 책임 주체를 판단하거나 주장하지 않는다.

`publisher-declaration/1` 문구:

> 게시자는 이 버전을 Module-Guard에 올린 계정입니다. 게시 사실과 시각, 올린 내용의 SHA-256 목록만 기록되며, 게시자가 코드의 작성자이거나 법적 책임을 진다는 뜻은 아닙니다. 가져온 모듈의 출처는 계보에 표시된 원본 버전을 확인하세요.

문구를 바꾸면 `app/models/lineage.py`의 `DECLARATION_VERSION`을 올린다. 기존 행은 게시 당시 버전을 그대로 유지한다.

## 노출 규칙

노드는 다음을 모두 만족할 때만 보인다: 버전이 아직 있고, 저장(snapshot)됐고, 기록 당시 저장소에 속해 있으며, 그 저장소가 요청자에게 보인다(`repository_visible_to`: 공개이거나 본인 소유).

- 비공개·삭제·접근 불가 부모는 **부모가 없는 것과 똑같이** 응답한다: `parent: null`, `comparison.status: NOT_COMPARABLE`, `reason: NO_VISIBLE_PARENT`. 존재 여부를 구별할 수 있는 값이 없다. (`FORK` 관계 자체는 저장소 페이지의 "가져온 모듈" 표시로 이미 공개된 정보다.)
- 조상 탐색은 처음 만나는 숨은 노드에서 멈춘다. 그 너머가 공개여도 보여 주지 않는다 — 보여 주면 중간에 숨은 버전이 있다는 사실이 드러난다.
- 후손 탐색은 보이는 자식만 따라가며, 숨은 자식과 그 아래는 건너뛴다. `total`은 보이는 노드만 센다.
- 비공개 저장소의 계보 요청은 존재하지 않는 저장소와 같은 404 본문을 받는다.
- digest나 계보 ID로 찾는 경로는 없다. 모든 엔드포인트는 `owner/name`과 버전 라벨로만 시작한다. 응답은 `Cache-Control: no-store`.

## 비교

부모와 현재의 기록된 manifest로 계산한다.

- `IDENTICAL`: 파일 목록과 각 파일의 `sha256`·`size`·`mode`가 모두 같다 (그대로 가져온 경우).
- `CHANGED`: `added` / `removed` / `modified` / `unchanged` 개수. 파일별 내용은 `/diff`.
- `NOT_COMPARABLE`: `reason`이 `NO_VISIBLE_PARENT`(부모 없음·숨김·삭제), `MISSING_MANIFEST`(0010 이전 버전이라 manifest 없음), `FORMAT_MISMATCH`(manifest 형식이 다름).

## API

모두 `GET`, 로그인 선택. `version`을 생략하면 최신 저장 버전.

| 경로 | 설명 |
| --- | --- |
| `/api/repositories/{owner}/{name}/lineage?version=` | 한 버전의 노드 |
| `/api/repositories/{owner}/{name}/lineage/graph?version=&direction=ancestors\|descendants&limit=&offset=` | 조상(가까운 순) 또는 후손(너비 우선, 게시 시각 순) 페이지. `limit` 1–50(기본 20) |
| `/api/repositories/{owner}/{name}/lineage/diff?version=&limit=&offset=` | 부모 대비 파일별 변경, 경로순. `limit` 1–50(기본 50) |

`graph`의 `edges`는 이 페이지의 노드마다 하나씩, 그 노드를 탐색 경로상 바로 앞 노드(첫 노드는 `root`)와 잇는다. 탐색 상한(깊이 200, 노드 1000)에 걸리면 `truncated: true`.

노드 예시 (`GET /api/repositories/bob/net/lineage?version=v2`):

```json
{
  "ref": {"id": "32dd6cef-…", "owner": "bob", "name": "net", "version": "v2"},
  "relation": "VERSION",
  "parent": {"id": "54159774-…", "owner": "bob", "name": "net", "version": "v1"},
  "comparison": {"status": "CHANGED", "reason": null, "added": 1, "removed": 0, "modified": 1, "unchanged": 2},
  "manifest_sha256": "48f9a038…",
  "file_count": 4,
  "source": {"url": null, "commit_hash": null, "module_path": "."},
  "publisher": {"id": "be0ee395-…", "login": "bob", "system": false},
  "published_at": "2026-11-05T04:38:35Z",
  "declaration_version": "publisher-declaration/1",
  "backfilled": false,
  "author_asserted": false
}
```

부모가 숨겨졌거나 삭제됐거나 원래 없는 경우 (`GET /api/repositories/alice/net/lineage`):

```json
{
  "relation": "ORIGINAL",
  "parent": null,
  "comparison": {"status": "NOT_COMPARABLE", "reason": "NO_VISIBLE_PARENT", "added": 0, "removed": 0, "modified": 0, "unchanged": 0}
}
```

파일별 변경 (`GET /api/repositories/bob/net/lineage/diff?version=v2`):

```json
{
  "ref": {"owner": "bob", "name": "net", "version": "v2"},
  "parent": {"owner": "bob", "name": "net", "version": "v1"},
  "comparison": {"status": "CHANGED", "added": 1, "removed": 0, "modified": 1, "unchanged": 2},
  "files": [
    {"path": "main.tf", "change": "MODIFIED",
     "before": {"sha256": "f81e6198…", "size": 61, "mode": 420},
     "after": {"sha256": "5d03b2ec…", "size": 38, "mode": 420}},
    {"path": "outputs.tf", "change": "ADDED", "before": null,
     "after": {"sha256": "1c16abea…", "size": 30, "mode": 420}}
  ],
  "total": 2, "limit": 50, "offset": 0, "next_offset": null
}
```

파생의 파생 조상 (`GET /api/repositories/carol/net/lineage/graph?limit=2`, 노드는 일부만):

```json
{
  "root": {"owner": "carol", "name": "net", "version": "v1"},
  "direction": "ancestors",
  "nodes": [{"ref": {"owner": "bob", "version": "v2"}, "relation": "VERSION"},
            {"ref": {"owner": "bob", "version": "v1"}, "relation": "FORK"}],
  "edges": [
    {"parent": "<bob v2>", "child": "<carol v1>", "relation": "FORK", "comparison": "IDENTICAL"},
    {"parent": "<bob v1>", "child": "<bob v2>", "relation": "VERSION", "comparison": "CHANGED"}
  ],
  "total": 3, "limit": 2, "offset": 0, "next_offset": 2, "truncated": false
}
```

## 기존 데이터 (0011 backfill)

업그레이드 시 저장된 저장소 버전마다 `backfilled: true`인 행을 만든다. 이미 있는 값만 쓰고 새로 계산하지 않는다.

- 같은 저장소의 두 번째 이후 버전은 직전 저장 버전을 부모로 `VERSION`.
- 가져온 저장소의 첫 버전은 원본 저장소에서 같은 검사(`scan_id`)를 공유하는 버전을 부모로 `FORK`. 찾지 못하면(원본 삭제 등) `ORIGINAL`.
- 그 밖은 `ORIGINAL`. manifest는 그대로 복사하므로 0010 이전 버전은 `null`이고, 비교는 `NOT_COMPARABLE / MISSING_MANIFEST`.
- 게시자는 저장소 소유자, 게시 시각은 버전 생성 시각, `declaration_version`은 `null`(선언 이전 게시).
- 저장되지 않은 버전은 건너뛴다. 부모부터 넣으며, 풀리지 않는 부모가 있으면 추측하지 않고 마이그레이션을 실패시킨다.
- downgrade는 `lineage_records`만 지우며 다른 테이블은 건드리지 않는다.

## 검증

`tests/integration/test_lineage.py`: 그대로 복사(`IDENTICAL`), 한 파일 수정(`CHANGED` + 파일별), 파생의 파생(조상·후손 그래프와 페이지), 비교 불가 사유, 숨김·삭제 부모와 비공개 저장소의 응답이 존재하지 않는 것과 같음, digest·ID 추측 불가, 숨은 중간 노드에서 탐색 중단, 잘못된 부모·순환 거부, 불변성, 무결성(#9) 회귀, 0011 backfill(SQLite, PostgreSQL).
