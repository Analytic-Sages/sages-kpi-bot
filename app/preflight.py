"""Read-only production readiness preflight for Polaris.

Run with the same environment and service-account file as the deployment:
    python -m app.preflight

The command never writes to Google Sheets, sends Telegram messages, or enables integrations.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings, get_settings
from app.sheets.client import GoogleSheetsClient, GoogleSheetsError

CORE_SHEETS: dict[str, set[str]] = {
    "People Directory": {
        "Person ID",
        "Full Name",
        "Team",
        "Role",
        "Status",
    },
    "KPI Register": {
        "KPI ID",
        "Team",
        "Role",
        "KPI",
        "Owner Person ID",
        "Target",
        "Active?",
    },
    "Weekly KPI Log": {
        "Week Start",
        "KPI ID",
        "Owner Person ID",
        "Target",
        "Actual",
    },
    "Master Task Tracker": {
        "Task ID",
        "Task Name",
        "Assigned To ID",
        "Status",
    },
}

ACCOUNTABILITY_SAFETY_HEADERS: dict[str, set[str]] = {
    "People Directory": {
        "Notify?",
        "Reachable?",
        "Telegram Chat ID",
        "Manager Chat ID",
    },
    "Master Task Tracker": {"Remind Now?", "Escalate?"},
}


@dataclass(frozen=True, slots=True)
class Finding:
    severity: str
    component: str
    message: str


def missing_headers(actual: set[str], required: set[str]) -> list[str]:
    """Return missing header names in stable order for actionable reports."""
    return sorted(required - actual)


def configuration_findings(settings: Settings) -> list[Finding]:
    """Check configuration presence without printing or returning secret values."""
    findings: list[Finding] = []
    if not settings.google_sheet_id:
        findings.append(Finding("BLOCKER", "Google Sheets", "GOOGLE_SHEET_ID is not configured."))
    credentials = Path(settings.google_creds_path).expanduser()
    if not credentials.is_file():
        findings.append(
            Finding("BLOCKER", "Google Sheets", "Google service-account credentials file is missing.")
        )

    if settings.enable_team_workflows or settings.enable_scheduled_workflows:
        if not settings.telegram_bot_token:
            findings.append(
                Finding("BLOCKER", "Telegram", "Telegram workflows are enabled but the bot token is missing.")
            )
        if not settings.telegram_core_chat_id:
            findings.append(
                Finding("BLOCKER", "Telegram", "The private core chat ID is not configured.")
            )
    if settings.enable_team_workflows and not settings.job_descriptions_worksheet:
        findings.append(
            Finding(
                "BLOCKER",
                "Job descriptions",
                "Team workflows are enabled without an authoritative Job Descriptions worksheet.",
            )
        )
    if settings.enable_analytics:
        if not settings.ga4_property_id:
            findings.append(Finding("BLOCKER", "Analytics", "GA4_PROPERTY_ID is missing."))
        if not settings.youtube_channel_id:
            findings.append(Finding("BLOCKER", "Analytics", "YOUTUBE_CHANNEL_ID is missing."))
        if not settings.youtube_api_key:
            findings.append(
                Finding(
                    "WARNING",
                    "Analytics",
                    "YOUTUBE_API_KEY is not configured; verify the selected YouTube access method.",
                )
            )
    if settings.enable_webhooks and not settings.webhook_secret:
        findings.append(Finding("BLOCKER", "Webhooks", "Webhooks are enabled without WEBHOOK_SECRET."))

    for label, raw_path in (
        ("Reminder receipts", settings.reminder_receipts_db_path),
        ("Webhook ledger", settings.webhook_db_path),
    ):
        path = Path(raw_path).expanduser()
        parent = path.parent
        if parent.exists() and not os.access(parent, os.W_OK):
            findings.append(
                Finding("BLOCKER", label, f"Database directory is not writable: {parent}.")
            )
        elif not parent.exists():
            findings.append(
                Finding("WARNING", label, f"Database directory does not exist yet: {parent}.")
            )

    if settings.dry_run:
        findings.append(
            Finding("INFO", "Safety", "DRY_RUN is enabled; outbound Telegram sends should remain suppressed.")
        )
    else:
        findings.append(
            Finding(
                "WARNING",
                "Safety",
                "DRY_RUN is disabled. Confirm routing, recipients, live schema, and approval before operation.",
            )
        )
    if not settings.enable_team_workflows:
        findings.append(Finding("INFO", "Team workflows", "Team workflows are disabled."))
    if not settings.enable_scheduled_workflows:
        findings.append(Finding("INFO", "Scheduler", "Scheduled workflows are disabled."))
    if not settings.enable_analytics:
        findings.append(Finding("INFO", "Analytics", "Growth analytics are disabled."))
    if not settings.enable_webhooks:
        findings.append(Finding("INFO", "Webhooks", "Inbound webhooks are disabled."))
    findings.append(
        Finding(
            "WARNING",
            "Deployment",
            "This check cannot prove that SQLite storage is durable or that only one app instance is running.",
        )
    )
    return findings


async def sheet_findings(client: GoogleSheetsClient) -> list[Finding]:
    """Read worksheet headers only; never inspect or print individual people's records."""
    findings: list[Finding] = []
    for worksheet_name, required in CORE_SHEETS.items():
        try:
            rows = await client.read_rows(worksheet_name)
        except GoogleSheetsError:
            findings.append(
                Finding(
                    "BLOCKER",
                    worksheet_name,
                    "Worksheet is missing, inaccessible, or has invalid headers.",
                )
            )
            continue
        actual = set(rows[0].values) if rows else set()
        missing = missing_headers(actual, required)
        if missing:
            findings.append(
                Finding(
                    "BLOCKER",
                    worksheet_name,
                    "Missing required headers: " + ", ".join(missing) + ".",
                )
            )
        else:
            findings.append(Finding("PASS", worksheet_name, "Core headers are present."))

    for worksheet_name, required in ACCOUNTABILITY_SAFETY_HEADERS.items():
        try:
            rows = await client.read_rows(worksheet_name)
        except GoogleSheetsError:
            # The core-sheet check already reports this as a blocker.
            continue
        actual = set(rows[0].values) if rows else set()
        missing = missing_headers(actual, required)
        if missing:
            findings.append(
                Finding(
                    "BLOCKER",
                    f"{worksheet_name} / accountability",
                    "Reminder delivery is blocked until these safety fields exist: "
                    + ", ".join(missing)
                    + ".",
                )
            )
        else:
            findings.append(
                Finding("PASS", f"{worksheet_name} / accountability", "Safety headers are present.")
            )
    return findings


def print_report(findings: list[Finding]) -> int:
    """Print a concise report and return a nonzero status if any blocker exists."""
    for finding in findings:
        print(f"[{finding.severity}] {finding.component}: {finding.message}")
    blockers = sum(finding.severity == "BLOCKER" for finding in findings)
    warnings = sum(finding.severity == "WARNING" for finding in findings)
    print(f"\nSummary: {blockers} blocker(s), {warnings} warning(s), {len(findings)} finding(s).")
    if blockers:
        print("RESULT: NOT READY — resolve blockers before enabling production workflows.")
        return 1
    print("RESULT: CONFIGURATION CHECK PASSED — complete the manual deployment/staging gates.")
    return 0


async def run_preflight(settings: Settings) -> int:
    findings = configuration_findings(settings)
    if settings.google_sheet_id and Path(settings.google_creds_path).expanduser().is_file():
        try:
            client = await asyncio.to_thread(GoogleSheetsClient.from_settings, settings)
            findings.extend(await sheet_findings(client))
        except GoogleSheetsError:
            findings.append(
                Finding(
                    "BLOCKER",
                    "Google Sheets",
                    "Could not authenticate or open the spreadsheet; check sharing and credentials.",
                )
            )
    return print_report(findings)


def main() -> int:
    return asyncio.run(run_preflight(get_settings()))


if __name__ == "__main__":
    raise SystemExit(main())
