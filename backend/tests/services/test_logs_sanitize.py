from app.services.logs import MAX_TEXT, sanitize


def test_long_strings_are_cut_with_marker():
    out = sanitize({"request": "字" * (MAX_TEXT + 15)})
    assert out["request"] == "字" * MAX_TEXT + "…(+15 ký tự)"


def test_secret_keys_masked_at_any_depth():
    out = sanitize({"headers": {"Authorization": "Bearer sk-1", "x": 1}, "list": [{"api_key": "sk-2"}]})
    assert out == {"headers": {"Authorization": "***", "x": 1}, "list": [{"api_key": "***"}]}


def test_configured_key_value_scrubbed_from_text(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-live-abcdef123456")
    get_settings.cache_clear()
    try:
        assert sanitize({"error": "401 for key sk-live-abcdef123456"}) == {"error": "401 for key ***"}
    finally:
        get_settings.cache_clear()


def test_non_text_values_kept():
    assert sanitize({"beam": 2, "ok": True, "x": None, "f": 1.5}) == {"beam": 2, "ok": True, "x": None, "f": 1.5}


def test_bearer_and_authorization_masked_in_free_text():
    out = sanitize({"error": "401: Authorization: Bearer sk-live-abcdef123456 rejected"})
    assert out == {"error": "401: Authorization: *** rejected"}
    out = sanitize({"error": "header bearer sk-live-abcdef123456 và AUTHORIZATION=Basic dXNlcjpwYXNz, tiếp"})
    assert "sk-live" not in out["error"] and "dXNlcjpwYXNz" not in out["error"]
    assert out["error"].endswith(", tiếp")
    assert sanitize({"m": '{"Authorization": "Bearer abcdefgh12345", "x": 1}'}) == {"m": '{"Authorization": "***", "x": 1}'}
    assert sanitize({"m": "the bearer of news"}) == {"m": "the bearer of news"}
