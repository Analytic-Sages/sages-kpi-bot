"""Asynchronous, header-driven Google Sheets repository."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import google.auth
import gspread
from google.oauth2.credentials import Credentials as OAuthCredentials

from app.config import Settings
from app.sheets.schema import SheetRow, normalize_headers, rows_from_values, validate_record_fields

logger = logging.getLogger(__name__)
SHEETS_SCOPE = ["https://www.googleapis.com/auth/spreadsheets"]
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
MAX_API_ATTEMPTS = 3
T = TypeVar("T")


class GoogleSheetsError(RuntimeError):
    """Raised for Google Sheets access or mutation failures."""


class GoogleSheetsConflictError(GoogleSheetsError):
    """Raised when a record no longer matches the expected mutation snapshot."""


def google_auth_mode(settings: Settings) -> str:
    """Return the configured Google auth mode or raise a safe configuration error.

    OAuth refresh-token authentication takes precedence when all three OAuth settings are
    supplied. Credential files may be service-account JSON or external-account/WIF configs.
    Secret values and file contents are never included in error messages.
    """
    oauth_values = (
        settings.google_oauth_client_id,
        settings.google_oauth_client_secret,
        settings.google_oauth_refresh_token,
    )
    configured_oauth_values = sum(bool(value and value.strip()) for value in oauth_values)
    if configured_oauth_values:
        if configured_oauth_values != len(oauth_values):
            raise GoogleSheetsError(
                "Google OAuth authentication requires "
                "GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, and "
                "GOOGLE_OAUTH_REFRESH_TOKEN to be configured together"
            )
        return "oauth"

    credentials_path = Path(settings.google_creds_path).expanduser()
    if credentials_path.is_file():
        return "file"
    raise GoogleSheetsError(
        "Google authentication is not configured: provide all three Google OAuth settings "
        "or a supported credentials file via GOOGLE_CREDS_PATH"
    )


class GoogleSheetsClient:
    """Read and update worksheets by header names, never fixed column positions."""

    def __init__(self, spreadsheet: Any) -> None:
        self._spreadsheet = spreadsheet

    @classmethod
    def from_settings(cls, settings: Settings) -> GoogleSheetsClient:
        """Create an authenticated client after validating required configuration."""
        if not settings.google_sheet_id:
            raise GoogleSheetsError("GOOGLE_SHEET_ID is not configured")
        try:
            auth_mode = google_auth_mode(settings)
            if auth_mode == "oauth":
                credentials = OAuthCredentials(
                    token=None,
                    refresh_token=settings.google_oauth_refresh_token,
                    token_uri=GOOGLE_TOKEN_URI,
                    client_id=settings.google_oauth_client_id,
                    client_secret=settings.google_oauth_client_secret,
                    scopes=SHEETS_SCOPE,
                )
            else:
                # google-auth handles service-account and external-account (WIF) configs.
                credentials, _ = google.auth.load_credentials_from_file(
                    str(Path(settings.google_creds_path).expanduser()),
                    scopes=SHEETS_SCOPE,
                )
            client = gspread.authorize(credentials)
            spreadsheet = client.open_by_key(settings.google_sheet_id)
        except GoogleSheetsError:
            raise
        except Exception as exc:
            raise GoogleSheetsError(
                "Unable to authenticate or open the configured spreadsheet"
            ) from exc
        return cls(spreadsheet)

    async def _api_call(
        self,
        operation: Callable[[], T],
        *,
        context: str,
        max_attempts: int = MAX_API_ATTEMPTS,
    ) -> T:
        """Run blocking Google API calls off-loop, retrying only when the caller allows it."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        for attempt in range(1, max_attempts + 1):
            try:
                return await asyncio.to_thread(operation)
            except Exception as exc:
                if attempt == max_attempts:
                    raise GoogleSheetsError(context) from exc
                delay = 0.25 * (2 ** (attempt - 1))
                logger.warning(
                    "google_sheets_retry attempt=%s/%s delay_seconds=%s context=%s error_type=%s",
                    attempt,
                    max_attempts,
                    delay,
                    context,
                    type(exc).__name__,
                )
                await asyncio.sleep(delay)
        raise GoogleSheetsError(context)

    def _worksheet(self, worksheet_name: str) -> Any:
        try:
            return self._spreadsheet.worksheet(worksheet_name)
        except Exception as exc:
            raise GoogleSheetsError(f"Unable to open worksheet: {worksheet_name}") from exc

    async def read_headers(self, worksheet_name: str) -> list[str]:
        """Read and validate row 1 directly, even when a worksheet has no data rows."""
        worksheet = self._worksheet(worksheet_name)
        try:
            raw_headers = await self._api_call(
                lambda: worksheet.row_values(1),
                context=f"Unable to read headers: {worksheet_name}",
            )
            return normalize_headers(raw_headers)
        except (GoogleSheetsError, ValueError) as exc:
            if isinstance(exc, GoogleSheetsError):
                raise
            raise GoogleSheetsError(f"Invalid worksheet headers: {worksheet_name}") from exc

    async def read_rows(self, worksheet_name: str) -> list[SheetRow]:
        """Read rows off the event loop and map values using the first row's headers."""
        worksheet = self._worksheet(worksheet_name)
        try:
            values = await self._api_call(
                worksheet.get_all_values, context=f"Unable to read worksheet: {worksheet_name}"
            )
            return rows_from_values(values)
        except (GoogleSheetsError, ValueError) as exc:
            if isinstance(exc, GoogleSheetsError):
                raise
            raise GoogleSheetsError(f"Invalid worksheet schema: {worksheet_name}") from exc

    async def read_formula_rows(self, worksheet_name: str) -> list[dict[str, str]]:
        """Read formula-visible cells for a read-only integrity audit.

        Formula strings are consumed by the caller and must never be included in public
        responses or logs. This method does not perform writes.
        """
        worksheet = self._worksheet(worksheet_name)
        try:
            values = await self._api_call(
                lambda: worksheet.get_all_values(value_render_option="FORMULA"),
                context=f"Unable to read formula-visible worksheet: {worksheet_name}",
            )
            return [row.values for row in rows_from_values(values)]
        except (GoogleSheetsError, ValueError) as exc:
            if isinstance(exc, GoogleSheetsError):
                raise
            raise GoogleSheetsError(
                f"Invalid formula-visible worksheet schema: {worksheet_name}"
            ) from exc

    async def read_records(self, worksheet_name: str) -> list[dict[str, str]]:
        """Return worksheet records as dictionaries keyed by their header names."""
        return [row.values for row in await self.read_rows(worksheet_name)]

    async def update_record(
        self,
        worksheet_name: str,
        *,
        key_header: str,
        key_value: str,
        values: dict[str, Any],
        expected_values: dict[str, Any] | None = None,
        value_input_option: str = "USER_ENTERED",
    ) -> int:
        """Update one unique record and return its row number; reject ambiguous/stale keys."""
        worksheet = self._worksheet(worksheet_name)
        grid = await self._api_call(
            worksheet.get_all_values, context=f"Unable to read worksheet: {worksheet_name}"
        )
        if not grid:
            raise GoogleSheetsError(f"Worksheet has no headers: {worksheet_name}")
        headers = normalize_headers(grid[0])
        if key_header not in headers:
            raise GoogleSheetsError(f"Key header {key_header!r} missing from {worksheet_name}")
        try:
            validate_record_fields(values, headers)
        except ValueError as exc:
            raise GoogleSheetsError(str(exc)) from exc
        key_index = headers.index(key_header)
        matches = [
            index + 1
            for index, row in enumerate(grid[1:], start=1)
            if key_value.strip()
            and key_index < len(row)
            and str(row[key_index]).strip() == key_value.strip()
        ]
        if len(matches) != 1:
            raise GoogleSheetsError(
                f"Expected one {key_header}={key_value!r} record in {worksheet_name}; "
                f"found {len(matches)}"
            )
        row_number = matches[0]
        if expected_values:
            row = grid[row_number - 1]
            for header, expected in expected_values.items():
                if header not in headers:
                    raise GoogleSheetsError(
                        f"Expected-value header {header!r} missing from {worksheet_name}"
                    )
                column_index = headers.index(header)
                actual = str(row[column_index]).strip() if column_index < len(row) else ""
                expected_text = "" if expected is None else str(expected).strip()
                if actual != expected_text:
                    raise GoogleSheetsConflictError(
                        f"Record changed since it was loaded: {header!r} no longer matches"
                    )
        updates = [
            {
                "range": gspread.utils.rowcol_to_a1(row_number, headers.index(header) + 1),
                "values": [["" if value is None else str(value)]],
            }
            for header, value in values.items()
        ]
        if updates:
            await self._api_call(
                lambda: worksheet.batch_update(
                    updates, value_input_option=value_input_option
                ),
                context=f"Unable to update worksheet: {worksheet_name}",
                max_attempts=1,
            )
        return row_number

    async def append_record(self, worksheet_name: str, values: dict[str, Any]) -> int:
        """Append a record aligned to worksheet headers and return the resulting row number."""
        worksheet = self._worksheet(worksheet_name)
        grid = await self._api_call(
            worksheet.get_all_values, context=f"Unable to read worksheet: {worksheet_name}"
        )
        if not grid:
            raise GoogleSheetsError(f"Worksheet has no headers: {worksheet_name}")
        headers = normalize_headers(grid[0])
        try:
            validate_record_fields(values, headers)
        except ValueError as exc:
            raise GoogleSheetsError(str(exc)) from exc
        row = [
            "" if values.get(header) is None else str(values.get(header, ""))
            for header in headers
        ]
        await self._api_call(
            lambda: worksheet.append_row(row, value_input_option="USER_ENTERED"),
            context=f"Unable to append to worksheet: {worksheet_name}",
            max_attempts=1,
        )
        return len(grid) + 1
