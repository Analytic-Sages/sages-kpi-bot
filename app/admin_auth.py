"""Google OIDC sign-in and allowlisted admin sessions for Polaris.

This module provides authentication only. It intentionally does not enable or expose
Google Sheets mutations. Session cookies are signed, HttpOnly, and time-limited.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import Settings

logger = logging.getLogger("polaris.auth")
GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
SESSION_COOKIE = "polaris_admin_session"
FLOW_COOKIE = "polaris_oidc_flow"
SESSION_MAX_AGE_SECONDS = 8 * 60 * 60
FLOW_MAX_AGE_SECONDS = 10 * 60


def _configured(settings: Settings) -> bool:
    return bool(
        settings.admin_google_client_id
        and settings.admin_google_client_secret
        and settings.admin_google_redirect_uri
        and settings.admin_session_secret
        and len(settings.admin_session_secret) >= 32
        and settings.admin_emails
    )


def _serializer(settings: Settings, salt: str) -> URLSafeTimedSerializer:
    secret = settings.admin_session_secret
    if not secret or len(secret) < 32:
        raise ValueError("Admin session secret is not configured securely")
    return URLSafeTimedSerializer(secret_key=secret, salt=salt)


def _admin_emails(settings: Settings) -> set[str]:
    return {value.strip().casefold() for value in settings.admin_emails.split(",") if value.strip()}


def _safe_cookie(response: RedirectResponse | JSONResponse, settings: Settings, **kwargs: Any) -> None:
    response.set_cookie(
        **kwargs,
        secure=settings.admin_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _decode_session(request: Request, settings: Settings) -> dict[str, Any] | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token or not _configured(settings):
        return None
    try:
        payload = _serializer(settings, "polaris-admin-session-v1").loads(
            token, max_age=SESSION_MAX_AGE_SECONDS
        )
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict):
        return None
    email = payload.get("email")
    if not isinstance(email, str) or email.casefold() not in _admin_emails(settings):
        return None
    if not isinstance(payload.get("csrf"), str) or not payload["csrf"]:
        return None
    return payload


def require_admin(request: Request) -> dict[str, Any] | None:
    """Return the authenticated allowlisted session or None."""
    return _decode_session(request, request.app.state.settings)


def create_admin_auth_router(settings: Settings) -> APIRouter:
    router = APIRouter(tags=["admin-auth"])

    @router.get("/auth/login")
    async def login() -> RedirectResponse | JSONResponse:
        if not _configured(settings):
            return JSONResponse(
                status_code=503,
                content={"detail": "Admin sign-in is not configured"},
            )
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        flow = _serializer(settings, "polaris-admin-oidc-flow-v1").dumps(
            {"state": state, "nonce": nonce, "verifier": verifier, "created": int(time.time())}
        )
        params = {
            "client_id": settings.admin_google_client_id,
            "redirect_uri": settings.admin_google_redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        response = RedirectResponse(f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}", status_code=302)
        _safe_cookie(
            response,
            settings,
            key=FLOW_COOKIE,
            value=flow,
            max_age=FLOW_MAX_AGE_SECONDS,
            path="/auth",
        )
        return response

    @router.get("/auth/callback")
    async def callback(request: Request, code: str | None = None, state: str | None = None):
        if not _configured(settings):
            return JSONResponse(status_code=503, content={"detail": "Admin sign-in is not configured"})
        flow_cookie = request.cookies.get(FLOW_COOKIE)
        if not code or not state or not flow_cookie:
            return JSONResponse(status_code=400, content={"detail": "Invalid sign-in response"})
        try:
            flow = _serializer(settings, "polaris-admin-oidc-flow-v1").loads(
                flow_cookie, max_age=FLOW_MAX_AGE_SECONDS
            )
        except (BadSignature, SignatureExpired):
            return JSONResponse(status_code=400, content={"detail": "Sign-in expired; please retry"})
        if not isinstance(flow, dict) or not secrets.compare_digest(str(flow.get("state", "")), state):
            return JSONResponse(status_code=400, content={"detail": "Invalid sign-in response"})
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                token_response = await client.post(
                    GOOGLE_TOKEN_ENDPOINT,
                    data={
                        "code": code,
                        "client_id": settings.admin_google_client_id,
                        "client_secret": settings.admin_google_client_secret,
                        "redirect_uri": settings.admin_google_redirect_uri,
                        "grant_type": "authorization_code",
                        "code_verifier": flow["verifier"],
                    },
                )
                token_response.raise_for_status()
                tokens = token_response.json()
            raw_id_token = tokens.get("id_token")
            if not isinstance(raw_id_token, str):
                raise ValueError("ID token missing")
            claims = await asyncio.to_thread(
                id_token.verify_oauth2_token,
                raw_id_token,
                GoogleRequest(),
                settings.admin_google_client_id,
            )
            if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
                raise ValueError("Unexpected token issuer")
            if not secrets.compare_digest(str(claims.get("nonce", "")), str(flow["nonce"])):
                raise ValueError("OIDC nonce mismatch")
            email = claims.get("email")
            if claims.get("email_verified") is not True or not isinstance(email, str):
                raise ValueError("Verified email required")
            email = email.strip().casefold()
            if email not in _admin_emails(settings):
                logger.warning("admin_signin_rejected reason=not_allowlisted")
                return JSONResponse(status_code=403, content={"detail": "This account is not authorized"})
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            logger.warning("admin_signin_failed reason=provider_validation")
            return JSONResponse(status_code=401, content={"detail": "Sign-in could not be verified"})
        except Exception as exc:
            # Do not log provider responses, tokens, or exception strings.
            logger.warning("admin_signin_failed reason=verification error_type=%s", type(exc).__name__)
            return JSONResponse(status_code=401, content={"detail": "Sign-in could not be verified"})

        session = _serializer(settings, "polaris-admin-session-v1").dumps(
            {"email": email, "csrf": secrets.token_urlsafe(32), "issued_at": int(time.time())}
        )
        response = RedirectResponse("/admin/session", status_code=303)
        _safe_cookie(
            response,
            settings,
            key=SESSION_COOKIE,
            value=session,
            max_age=SESSION_MAX_AGE_SECONDS,
            path="/",
        )
        response.delete_cookie(
            FLOW_COOKIE,
            path="/auth",
            secure=settings.admin_cookie_secure,
            httponly=True,
            samesite="lax",
        )
        return response

    @router.get("/admin/session")
    async def admin_session(request: Request) -> JSONResponse:
        session = require_admin(request)
        if session is None:
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})
        return JSONResponse(
            content={
                "authenticated": True,
                "email": session["email"],
                "csrf_token": session["csrf"],
                "access": "read_only",
            }
        )

    @router.post("/auth/logout")
    async def logout(request: Request) -> JSONResponse:
        session = require_admin(request)
        csrf = request.headers.get("X-CSRF-Token", "")
        if session is None:
            response = Response(status_code=204)
        elif not csrf or not secrets.compare_digest(csrf, session["csrf"]):
            return JSONResponse(status_code=403, content={"detail": "CSRF validation failed"})
        else:
            response = JSONResponse(status_code=204, content=None)
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            secure=settings.admin_cookie_secure,
            httponly=True,
            samesite="lax",
        )
        response.delete_cookie(FLOW_COOKIE, path="/auth", secure=settings.admin_cookie_secure, httponly=True, samesite="lax")
        return response

    return router
