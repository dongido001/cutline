"""OAuth helpers for YouTube (Google) and Facebook platform connections."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from .models import OAuthState, SocialToken
from .settings import settings

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

FACEBOOK_AUTH_URL = "https://www.facebook.com/v20.0/dialog/oauth"
FACEBOOK_TOKEN_URL = "https://graph.facebook.com/v20.0/oauth/access_token"
FACEBOOK_ME_URL = "https://graph.facebook.com/v20.0/me"
FACEBOOK_PAGES_URL = "https://graph.facebook.com/v20.0/me/accounts"

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

FACEBOOK_SCOPES = [
    "pages_manage_posts",
    "pages_read_engagement",
    "pages_show_list",
    "publish_video",
]

# ─────────────────────────────────────────────────────────────────────────────
# Persistent OAuth state helpers
# ─────────────────────────────────────────────────────────────────────────────


def _new_state(platform: str, db: Session) -> str:
    state = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    db.query(OAuthState).filter(OAuthState.expires_at <= now).delete(synchronize_session=False)
    db.add(OAuthState(
        state=state,
        platform=platform,
        expires_at=now + timedelta(seconds=settings.oauth_state_ttl_seconds),
    ))
    db.commit()
    return state


def _consume_state(state: str, platform: str, db: Session) -> None:
    record = db.get(OAuthState, state)
    if record is None:
        raise ValueError("Invalid or expired OAuth state")
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    valid = record.platform == platform and expires_at > datetime.now(timezone.utc)
    db.delete(record)
    db.commit()
    if not valid:
        raise ValueError("Invalid or expired OAuth state")


# ─────────────────────────────────────────────────────────────────────────────
# Redirect URI helpers
# ─────────────────────────────────────────────────────────────────────────────

def _redirect_uri(platform: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/auth/{platform}/callback"


# ─────────────────────────────────────────────────────────────────────────────
# Start OAuth flows (build authorization URLs)
# ─────────────────────────────────────────────────────────────────────────────

def youtube_auth_url(db: Session) -> str:
    if not settings.google_client_id:
        raise RuntimeError(
            "Google OAuth is not configured. Set MOVIE_GOOGLE_CLIENT_ID and "
            "MOVIE_GOOGLE_CLIENT_SECRET in your .env file."
        )
    state = _new_state("youtube", db)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": _redirect_uri("youtube"),
        "response_type": "code",
        "scope": " ".join(YOUTUBE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def facebook_auth_url(db: Session) -> str:
    if not settings.facebook_app_id:
        raise RuntimeError(
            "Facebook OAuth is not configured. Set MOVIE_FACEBOOK_APP_ID and "
            "MOVIE_FACEBOOK_APP_SECRET in your .env file."
        )
    state = _new_state("facebook", db)
    params = {
        "client_id": settings.facebook_app_id,
        "redirect_uri": _redirect_uri("facebook"),
        "response_type": "code",
        "scope": ",".join(FACEBOOK_SCOPES),
        "state": state,
    }
    return f"{FACEBOOK_AUTH_URL}?{urlencode(params)}"


# ─────────────────────────────────────────────────────────────────────────────
# Callback handlers
# ─────────────────────────────────────────────────────────────────────────────

def _upsert_token(db: Session, platform: str, data: dict[str, Any]) -> SocialToken:
    token = db.query(SocialToken).filter_by(platform=platform).first()
    if token is None:
        token = SocialToken(platform=platform)
        db.add(token)
    for key, value in data.items():
        setattr(token, key, value)
    db.commit()
    db.refresh(token)
    return token


def handle_youtube_callback(code: str, state: str, db: Session) -> SocialToken:
    _consume_state(state, "youtube", db)

    with httpx.Client(timeout=30) as client:
        token_resp = client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": _redirect_uri("youtube"),
                "grant_type": "authorization_code",
            },
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()

        access_token: str = token_data["access_token"]
        refresh_token: str | None = token_data.get("refresh_token")
        expires_in: int = token_data.get("expires_in", 3600)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

        ch_resp = client.get(
            GOOGLE_CHANNELS_URL,
            params={"part": "snippet", "mine": "true"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        ch_resp.raise_for_status()
        channels = ch_resp.json().get("items", [])

    account_id = account_name = account_picture = None
    if channels:
        ch = channels[0]
        account_id = ch.get("id")
        account_name = ch.get("snippet", {}).get("title")
        thumbs = ch.get("snippet", {}).get("thumbnails", {})
        account_picture = (thumbs.get("default") or thumbs.get("medium") or {}).get("url")

    existing = get_social_token("youtube", db)
    preserved_refresh_token = refresh_token or (existing.refresh_token if existing else None)
    return _upsert_token(db, "youtube", {
        "access_token": access_token,
        "refresh_token": preserved_refresh_token,
        "expires_at": expires_at,
        "account_id": account_id,
        "account_name": account_name,
        "account_picture": account_picture,
        "extra": {"scope": token_data.get("scope", "")},
    })


def handle_facebook_callback(code: str, state: str, db: Session) -> SocialToken:
    _consume_state(state, "facebook", db)

    with httpx.Client(timeout=30) as client:
        token_resp = client.get(
            FACEBOOK_TOKEN_URL,
            params={
                "client_id": settings.facebook_app_id,
                "client_secret": settings.facebook_app_secret,
                "redirect_uri": _redirect_uri("facebook"),
                "code": code,
            },
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()
        user_token: str = token_data["access_token"]

        long_resp = client.get(
            FACEBOOK_TOKEN_URL,
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.facebook_app_id,
                "client_secret": settings.facebook_app_secret,
                "fb_exchange_token": user_token,
            },
        )
        if long_resp.is_success:
            long_data = long_resp.json()
            user_token = long_data.get("access_token", user_token)
            token_data = {**token_data, **long_data}

        me_resp = client.get(
            FACEBOOK_ME_URL,
            params={"fields": "id,name,picture", "access_token": user_token},
        )
        me_resp.raise_for_status()
        me = me_resp.json()

        pages_resp = client.get(
            FACEBOOK_PAGES_URL,
            params={"fields": "id,name,access_token,picture", "access_token": user_token},
        )
        pages_resp.raise_for_status()
        pages_data = pages_resp.json()

    pages = pages_data.get("data", [])
    expires_in = token_data.get("expires_in")
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        if expires_in else None
    )

    publishable_pages = [p for p in pages if p.get("id") and p.get("access_token")]
    if not publishable_pages:
        raise RuntimeError("No Facebook Page with publishing permission was found for this account.")
    if len(publishable_pages) > 1:
        names = ", ".join(str(p.get("name") or p.get("id")) for p in publishable_pages)
        raise RuntimeError(
            f"Multiple Facebook Pages are available ({names}). Page selection is required before connecting."
        )

    page = publishable_pages[0]
    page_token: str = page["access_token"]
    account_id: str | None = page.get("id")
    account_name: str | None = page.get("name")
    account_picture: str | None = page.get("picture", {}).get("data", {}).get("url")
    extra: dict[str, Any] = {
        "page_id": page.get("id"),
        "user_id": me.get("id"),
        "all_pages": [{"id": p.get("id"), "name": p.get("name")} for p in publishable_pages],
    }

    return _upsert_token(db, "facebook", {
        "access_token": page_token,
        "refresh_token": None,
        "expires_at": expires_at,
        "account_id": account_id,
        "account_name": account_name,
        "account_picture": account_picture,
        "extra": extra,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Token retrieval & status
# ─────────────────────────────────────────────────────────────────────────────

def get_social_token(platform: str, db: Session) -> SocialToken | None:
    return db.query(SocialToken).filter_by(platform=platform).first()


def disconnect_platform(platform: str, db: Session) -> None:
    db.query(SocialToken).filter_by(platform=platform).delete()
    db.commit()


def token_status(platform: str, db: Session) -> dict[str, Any]:
    tok = get_social_token(platform, db)
    if tok:
        return {
            "platform": platform,
            "connected": True,
            "account_id": tok.account_id,
            "account_name": tok.account_name,
            "account_picture": tok.account_picture,
            "expires_at": tok.expires_at.isoformat() if tok.expires_at else None,
            "is_expired": tok.is_expired,
        }
    return {"platform": platform, "connected": False}


def all_token_statuses(db: Session) -> list[dict[str, Any]]:
    return [token_status(p, db) for p in ("youtube", "facebook")]
