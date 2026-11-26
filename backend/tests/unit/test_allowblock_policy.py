from pathlib import Path

DOC = Path(__file__).parents[2] / "docs" / "allowblock-policy.md"
SECTIONS = (
    "## 1. 대상 단위",
    "## 2. 우선순위",
    "## 3. 만료와 해제",
    "## 4. 적용 지점",
    "## 5. 감사 이력",
    "## 6. 관리자 권한과 호출 제한",
    "## 7. 공개 범위",
)


def test_policy_document_covers_every_rule():
    text = DOC.read_text(encoding="utf-8")
    for heading in SECTIONS:
        assert heading in text
    assert "BLOCK이 ALLOW보다 우선" in text
    assert "소급" in text
