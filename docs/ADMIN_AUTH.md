# Polaris Google Admin Authentication

## Scope

Polaris uses Google OpenID Connect (OIDC) for admin identity and a signed, time-limited session cookie. Access is restricted to exact email addresses in the server-side `ADMIN_EMAILS` allowlist. Authentication does not grant spreadsheet mutation permissions: this first increment exposes only `GET /admin/session` as a protected identity check.

No Clerk/Auth0 subscription or paid identity provider is required. Google OAuth itself is not billed as a per-admin authentication service for this design; hosting, custom domains, and any Google Workspace licensing remain separate from the sign-in flow.

## Google Cloud setup

1. Select/create the Google Cloud project used for Polaris's web sign-in.
2. Configure the OAuth consent screen for the intended audience. If the organization supports an Internal audience, use it; otherwise configure the External audience and comply with the requirements Google presents.
3. Create an OAuth client ID of type **Web application**.
4. Add the exact authorized redirect URI, for example `https://YOUR-RENDER-SERVICE.onrender.com/auth/callback`. It must exactly match `ADMIN_GOOGLE_REDIRECT_URI`.
5. Keep the client secret in Render's environment/secret UI; never commit it or put it in browser code.

## Render environment

Set these values in the Render service environment:

- `ADMIN_GOOGLE_CLIENT_ID`: Web OAuth client ID.
- `ADMIN_GOOGLE_CLIENT_SECRET`: Web OAuth client secret.
- `ADMIN_GOOGLE_REDIRECT_URI`: exact HTTPS callback URI registered with Google.
- `ADMIN_EMAILS`: comma-separated list of exact approved admin email addresses.
- `ADMIN_SESSION_SECRET`: random secret of at least 32 characters. Generate locally with `python -c "import secrets; print(secrets.token_urlsafe(48))"`; enter it directly in Render.
- `ADMIN_COOKIE_SECURE=true`: keep true for deployed HTTPS.

All required values must be present before sign-in is enabled. When configuration is incomplete, `GET /auth/login` returns 503 and does not redirect.

## Routes

- `GET /auth/login`: starts Google OIDC authorization with state, nonce, and PKCE; sets a short-lived, signed, HttpOnly flow cookie.
- `GET /auth/callback`: exchanges the code, verifies Google's ID token and nonce, requires a verified email, and checks the exact allowlist.
- `GET /admin/session`: protected session check; returns the signed-in admin email and a CSRF token for the browser client. Its access label is explicitly `read_only`.
- `POST /auth/logout`: clears the session; authenticated requests must provide the session CSRF token in `X-CSRF-Token`.

The session cookie is Secure (configurable only for local HTTP development), HttpOnly, SameSite=Lax, and expires after eight hours. The OIDC flow cookie expires after ten minutes. Cookies are signed but not stored in a server-side session database, so clearing the browser cookie logs out that browser but does not remotely revoke a copied session token before expiry. Rotating `ADMIN_SESSION_SECRET` invalidates existing sessions.

## Acceptance and limitations

- Test authorized and non-allowlisted accounts with the deployed HTTPS callback.
- Verify missing configuration fails closed and unauthenticated access to `/admin/session` returns 401.
- Keep every write, workflow, analytics, webhook, and Telegram reply flag disabled during this authentication rollout.
- This is not yet a dashboard, a task/KPI mutation API, or approval to enable writes.
- A persistent audit database and verified Render storage/instance topology are still deployment gates. Do not expose the Sheets mutation service until Stage 0 and write-acceptance checks are complete.
