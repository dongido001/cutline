from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from sqlalchemy import create_engine
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.database import Base, get_db
from backend.models import OAuthState, SocialToken
from backend import api, oauth
from backend.settings import settings


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.is_success:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    def __init__(self, *, post_responses=None, get_responses=None, **_kwargs):
        self.post_responses = list(post_responses or [])
        self.get_responses = list(get_responses or [])

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, *_args, **_kwargs):
        return self.post_responses.pop(0)

    def get(self, *_args, **_kwargs):
        return self.get_responses.pop(0)


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def test_oauth_state_is_persisted_and_one_time(monkeypatch):
    engine, db = make_session()
    try:
        monkeypatch.setattr(settings, "google_client_id", "client")
        url = oauth.youtube_auth_url(db)
        state = parse_qs(urlparse(url).query)["state"][0]
        assert db.get(OAuthState, state) is not None
        oauth._consume_state(state, "youtube", db)
        assert db.get(OAuthState, state) is None
    finally:
        db.close()
        engine.dispose()


def test_expired_oauth_state_is_rejected():
    engine, db = make_session()
    try:
        db.add(OAuthState(state="expired", platform="youtube", expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        db.commit()
        try:
            oauth._consume_state("expired", "youtube", db)
            assert False, "expired state should fail"
        except ValueError:
            pass
        assert db.get(OAuthState, "expired") is None
    finally:
        db.close()
        engine.dispose()


def test_youtube_reconnect_preserves_refresh_token(monkeypatch):
    engine, db = make_session()
    try:
        monkeypatch.setattr(settings, "google_client_id", "client")
        monkeypatch.setattr(settings, "google_client_secret", "secret")
        db.add(SocialToken(platform="youtube", access_token="old-access", refresh_token="keep-me"))
        db.add(OAuthState(state="state", platform="youtube", expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)))
        db.commit()

        fake = FakeClient(
            post_responses=[FakeResponse({"access_token": "new-access", "expires_in": 3600})],
            get_responses=[FakeResponse({"items": [{"id": "channel-1", "snippet": {"title": "Channel", "thumbnails": {}}}]})],
        )
        monkeypatch.setattr(oauth.httpx, "Client", lambda **kwargs: fake)

        token = oauth.handle_youtube_callback("code", "state", db)
        assert token.access_token == "new-access"
        assert token.refresh_token == "keep-me"
    finally:
        db.close()
        engine.dispose()


def test_youtube_callback_escapes_html_and_uses_configured_target_origin(monkeypatch):
    engine, db = make_session()
    try:
        malicious_name = "Channel </script><script>alert('x')</script>"
        monkeypatch.setattr(settings, "frontend_origin", "http://127.0.0.1:5173")
        monkeypatch.setattr(
            api,
            "handle_youtube_callback",
            lambda *_args, **_kwargs: SocialToken(
                platform="youtube",
                access_token="token",
                account_name=malicious_name,
            ),
        )
        api.app.dependency_overrides[get_db] = lambda: db
        response = TestClient(api.app).get("/auth/youtube/callback?code=code&state=state")
        assert response.status_code == 200
        assert "Channel &lt;/script&gt;&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in response.text
        assert "postMessage" in response.text
        assert '"http://127.0.0.1:5173"' in response.text
        assert "}, '*')" not in response.text
        assert "</script><script>alert('x')</script>" not in response.text
    finally:
        api.app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_youtube_callback_escapes_error_and_uses_configured_target_origin(monkeypatch):
    engine, db = make_session()
    try:
        malicious_error = "bad </script><script>alert('x')</script>"
        monkeypatch.setattr(settings, "frontend_origin", "http://127.0.0.1:5173")

        def fail(*_args, **_kwargs):
            raise ValueError(malicious_error)

        monkeypatch.setattr(api, "handle_youtube_callback", fail)
        api.app.dependency_overrides[get_db] = lambda: db
        response = TestClient(api.app).get("/auth/youtube/callback?code=code&state=state")
        assert response.status_code == 400
        assert "bad &lt;/script&gt;&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in response.text
        assert '"http://127.0.0.1:5173"' in response.text
        assert "}, '*')" not in response.text
        assert "</script><script>alert('x')</script>" not in response.text
    finally:
        api.app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_facebook_requires_publishable_page(monkeypatch):
    engine, db = make_session()
    try:
        monkeypatch.setattr(settings, "facebook_app_id", "app")
        monkeypatch.setattr(settings, "facebook_app_secret", "secret")
        db.add(OAuthState(state="state", platform="facebook", expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)))
        db.commit()

        fake = FakeClient(get_responses=[
            FakeResponse({"access_token": "short", "expires_in": 3600}),
            FakeResponse({}, status_code=400),
            FakeResponse({"id": "user-1", "name": "User"}),
            FakeResponse({"data": []}),
        ])
        monkeypatch.setattr(oauth.httpx, "Client", lambda **kwargs: fake)

        try:
            oauth.handle_facebook_callback("code", "state", db)
            assert False, "Facebook connection without a publishable Page should fail"
        except RuntimeError as exc:
            assert "No Facebook Page" in str(exc)
        assert db.query(SocialToken).filter_by(platform="facebook").first() is None
    finally:
        db.close()
        engine.dispose()
