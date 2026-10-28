import pytest
from pydantic import ValidationError

from ctxfs.models import GrepPage


def test_limit_page_exposes_actionable_continuation_guidance() -> None:
    page = GrepPage(
        items=[], complete=False, stopped_by="limit", next_cursor="page-2"
    )

    assert page.continuation_required is True
    assert page.continuation_hint is not None
    assert "same pattern and prefix" in page.continuation_hint
    assert "next_cursor as cursor" in page.continuation_hint


def test_timeout_page_exposes_recovery_guidance() -> None:
    page = GrepPage(
        items=[], complete=False, stopped_by="timeout", next_cursor="page-2"
    )

    assert page.continuation_required is True
    assert page.continuation_hint is not None
    assert "increase timeout_seconds" in page.continuation_hint
    assert "narrow prefix" in page.continuation_hint


@pytest.mark.parametrize("stopped_by", [None, "catalog_mismatch", "max_bytes"])
def test_page_without_cursor_does_not_request_continuation(
    stopped_by: str | None,
) -> None:
    page = GrepPage(items=[], complete=stopped_by is None, stopped_by=stopped_by)

    assert page.continuation_required is False
    assert page.continuation_hint is None


def test_complete_page_rejects_cursor() -> None:
    with pytest.raises(ValidationError, match="complete grep page"):
        GrepPage(items=[], complete=True, next_cursor="page-2")
