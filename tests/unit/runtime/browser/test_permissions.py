"""Browser permissions / URL guards."""

from app.runtime.browser.permissions import (
    check_navigate_url,
    is_blocked_url,
    maybe_confirmation_required,
)


def test_blocked_chrome_urls():
    assert is_blocked_url("chrome://settings")
    assert is_blocked_url("chrome-extension://abc/page.html")
    assert is_blocked_url("file:///etc/passwd")
    assert not is_blocked_url("https://github.com")


def test_check_navigate_url():
    assert check_navigate_url("chrome://extensions") is not None
    assert check_navigate_url("https://example.com/path") is None
    assert check_navigate_url("ftp://x") is not None


def test_sensitive_click_confirmation():
    hit = maybe_confirmation_required(
        "browser_click",
        {"tab_id": "t", "ref": "e1"},
        target_name="Delete repository",
    )
    assert hit is not None
    assert hit["error"]["code"] == "CONFIRMATION_REQUIRED"

    ok = maybe_confirmation_required(
        "browser_click",
        {"tab_id": "t", "ref": "e1"},
        target_name="Issues",
    )
    assert ok is None
