from pathlib import Path


def test_401_reauth_is_not_misreported_as_expired_session():
    app = Path(__file__).parents[1] / "server" / "static" / "app.js"
    source = app.read_text(encoding="utf-8")
    reauth = source.index('if (r.status === 401 && j && j.reauth_required)')
    expired = source.index('if (r.status === 401 && !path.includes("/api/login"))')
    assert reauth < expired
    assert "await requestReauthPassword()" in source

