"""Read-only GA4 and YouTube analytics with safe, private reporting."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
GA4_ENDPOINT = "https://analyticsdata.googleapis.com/v1beta"
YOUTUBE_ENDPOINT = "https://www.googleapis.com/youtube/v3"


class AnalyticsError(RuntimeError):
    """Raised when a configured analytics provider cannot return usable data."""


class GrowthAnalyticsService:
    """Collect a compact last-seven-complete-days report from configured providers."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._http_client = http_client

    async def report(self) -> str:
        """Return provider metrics; unconfigured integrations are clearly marked."""
        sections = [
            "📈 Analytic Sages — Growth Intelligence",
            "Period: last 7 complete days for GA4; current lifetime channel totals for YouTube",
            "",
        ]
        configured = False

        if self.settings.ga4_property_id:
            configured = True
            try:
                metrics = await self._ga4_report()
                sections.append("🌐 Website (GA4)")
                sections.extend(f"• {label}: {value}" for label, value in metrics.items())
            except (AnalyticsError, httpx.HTTPError, OSError, ValueError) as exc:
                logger.warning("ga4_report_failed reason=%s", exc)
                sections.extend(
                    [
                        "🌐 Website (GA4)",
                        "• Data unavailable; check property access and credentials.",
                    ]
                )
            sections.append("")
        else:
            sections.extend(["🌐 Website (GA4)", "• Not configured (GA4_PROPERTY_ID).", ""])

        if self.settings.youtube_channel_id and self.settings.youtube_api_key:
            configured = True
            try:
                metrics = await self._youtube_report()
                sections.append("▶️ YouTube channel (lifetime totals)")
                sections.extend(f"• {label}: {value}" for label, value in metrics.items())
            except (AnalyticsError, httpx.HTTPError, ValueError) as exc:
                logger.warning("youtube_report_failed reason=%s", exc)
                sections.extend(
                    [
                        "▶️ YouTube channel",
                        "• Data unavailable; check channel ID and API key.",
                    ]
                )
            sections.append("")
        else:
            sections.extend(
                [
                    "▶️ YouTube channel",
                    "• Not configured (YOUTUBE_CHANNEL_ID and YOUTUBE_API_KEY).",
                    "",
                ]
            )

        if not configured:
            sections.append("Configure at least one analytics provider to enable this report.")
        sections.append("Data is read-only. No public/community chat is used for this report.")
        return "\n".join(sections).strip()

    async def _ga4_report(self) -> dict[str, str]:
        property_id = (self.settings.ga4_property_id or "").strip()
        if not property_id.isdigit():
            raise AnalyticsError("GA4_PROPERTY_ID must be a numeric property ID")
        credentials_path = Path(self.settings.google_creds_path)
        if not credentials_path.is_file():
            raise AnalyticsError("Google service-account credentials file is missing")
        token = await asyncio.to_thread(self._get_ga4_access_token, str(credentials_path))
        payload = {
            "dateRanges": [{"startDate": "7daysAgo", "endDate": "yesterday"}],
            "metrics": [
                {"name": "sessions"},
                {"name": "totalUsers"},
                {"name": "newUsers"},
                {"name": "engagedSessions"},
                {"name": "eventCount"},
            ],
        }
        data = await self._request_json(
            "POST",
            f"{GA4_ENDPOINT}/properties/{property_id}:runReport",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        metric_headers = [item.get("name", "") for item in data.get("metricHeaders", [])]
        values = data.get("rows", [{}])[0].get("metricValues", []) if data.get("rows") else []
        raw = {
            name: value.get("value", "0")
            for name, value in zip(metric_headers, values, strict=False)
        }
        labels = {
            "sessions": "Sessions",
            "totalUsers": "Total users",
            "newUsers": "New users",
            "engagedSessions": "Engaged sessions",
            "eventCount": "Events",
        }
        return {label: raw.get(key, "0") for key, label in labels.items()}

    @staticmethod
    def _get_ga4_access_token(credentials_path: str) -> str:
        """Refresh service-account credentials off the event loop."""
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.service_account import Credentials

            credentials = Credentials.from_service_account_file(
                credentials_path,
                scopes=[GA4_SCOPE],
            )
            credentials.refresh(Request())
            if not credentials.token:
                raise AnalyticsError("Google authentication returned no access token")
            return credentials.token
        except AnalyticsError:
            raise
        except Exception as exc:
            raise AnalyticsError("Google Analytics authentication failed") from exc

    async def _youtube_report(self) -> dict[str, str]:
        data = await self._request_json(
            "GET",
            f"{YOUTUBE_ENDPOINT}/channels",
            params={
                "part": "snippet,statistics",
                "id": self.settings.youtube_channel_id,
                "key": self.settings.youtube_api_key,
            },
        )
        items = data.get("items", [])
        if not items:
            raise AnalyticsError("YouTube API returned no channel for the configured ID")
        item = items[0]
        stats = item.get("statistics", {})
        return {
            "Channel": item.get("snippet", {}).get("title", "Unknown"),
            "Subscribers": stats.get("subscriberCount", "hidden or unavailable"),
            "Lifetime views": stats.get("viewCount", "0"),
            "Published videos": stats.get("videoCount", "0"),
        }

    async def _request_json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        owned_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(timeout=15.0)
        try:
            for attempt in range(3):
                try:
                    response = await client.request(method, url, **kwargs)
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt < 2:
                            await asyncio.sleep(0.25 * (2**attempt))
                            continue
                    response.raise_for_status()
                    data = response.json()
                    if not isinstance(data, dict):
                        raise AnalyticsError("Analytics provider returned a non-object response")
                    return data
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 429 or exc.response.status_code >= 500:
                        if attempt < 2:
                            await asyncio.sleep(0.25 * (2**attempt))
                            continue
                    raise AnalyticsError(
                        f"Analytics provider returned HTTP {exc.response.status_code}"
                    ) from exc
                except httpx.RequestError as exc:
                    if attempt < 2:
                        await asyncio.sleep(0.25 * (2**attempt))
                        continue
                    raise AnalyticsError("Analytics provider request failed") from exc
            raise AnalyticsError("Analytics provider retry limit reached")
        finally:
            if owned_client:
                await client.aclose()
