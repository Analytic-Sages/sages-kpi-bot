"""Read-only PostgreSQL deployment preflight."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

from app.config import Settings, get_settings
from app.operations_db import database_engine


@dataclass(frozen=True)
class Finding:
    severity: str
    component: str
    message: str


def configuration_findings(settings: Settings) -> list[Finding]:
    findings = []
    if not settings.database_url:
        findings.append(Finding("BLOCKER", "PostgreSQL", "DATABASE_URL is not configured."))
    if not settings.admin_google_client_id or not settings.admin_google_client_secret or not settings.admin_google_redirect_uri:
        findings.append(Finding("BLOCKER", "Authentication", "Google OIDC client, secret, and callback URI are required."))
    if not settings.admin_emails.strip():
        findings.append(Finding("BLOCKER", "Authentication", "ADMIN_EMAILS must contain at least one administrator."))
    if not settings.admin_session_secret or len(settings.admin_session_secret) < 32:
        findings.append(Finding("BLOCKER", "Authentication", "ADMIN_SESSION_SECRET must contain at least 32 characters."))
    if settings.app_env == "production" and not settings.admin_cookie_secure:
        findings.append(Finding("BLOCKER", "Authentication", "Secure session cookies are required in production."))
    if not findings:
        findings.append(Finding("PASS", "Configuration", "Required database and authentication settings are present."))
    return findings


def database_findings() -> list[Finding]:
    engine = database_engine()
    if engine is None:
        return [Finding("BLOCKER", "PostgreSQL", "Database engine is not configured.")]
    try:
        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            connection.execute(text("SELECT 1 FROM settings LIMIT 0"))
        return [Finding("PASS", "PostgreSQL", f"Database connected at migration {revision}.")]
    except Exception as exc:
        return [Finding("BLOCKER", "PostgreSQL", f"Database connection or schema check failed ({type(exc).__name__}).")]


def print_report(findings: list[Finding]) -> int:
    for finding in findings:
        print(f"[{finding.severity}] {finding.component}: {finding.message}")
    blocked = any(item.severity == "BLOCKER" for item in findings)
    print("NOT READY" if blocked else "PREFLIGHT PASSED")
    return 1 if blocked else 0


def main() -> int:
    settings = get_settings()
    return print_report(configuration_findings(settings) + database_findings())


if __name__ == "__main__":
    raise SystemExit(main())
