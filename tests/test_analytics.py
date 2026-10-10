from unittest.mock import AsyncMock

import httpx
import pytest

from app.analytics.service import GrowthAnalyticsService
from app.config import Settings


@pytest.mark.asyncio
async def test_report_marks_unconfigured_providers_and_does_not_make_requests():
    transport = httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(AssertionError("unexpected HTTP request"))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        service = GrowthAnalyticsService(Settings(_env_file=None), http_client=client)
        report = await service.report()

    assert "GA4_PROPERTY_ID" in report
    assert "YOUTUBE_CHANNEL_ID and YOUTUBE_API_KEY" in report
    assert "read-only" in report


@pytest.mark.asyncio
async def test_youtube_report_formats_public_channel_statistics():
    service = GrowthAnalyticsService(
        Settings(
            _env_file=None,
            youtube_channel_id="UCexample",
            youtube_api_key="test-key",
        )
    )
    service._request_json = AsyncMock(
        return_value={
            "items": [
                {
                    "snippet": {"title": "Analytic Sages"},
                    "statistics": {
                        "subscriberCount": "1234",
                        "viewCount": "56789",
                        "videoCount": "42",
                    },
                }
            ]
        }
    )

    metrics = await service._youtube_report()

    assert metrics == {
        "Channel": "Analytic Sages",
        "Subscribers": "1234",
        "Lifetime views": "56789",
        "Published videos": "42",
    }


@pytest.mark.asyncio
async def test_ga4_requires_numeric_property_id():
    service = GrowthAnalyticsService(
        Settings(_env_file=None, ga4_property_id="properties/123")
    )

    with pytest.raises(Exception, match="numeric property ID"):
        await service._ga4_report()


@pytest.mark.asyncio
async def test_youtube_missing_channel_is_reported_as_provider_error():
    service = GrowthAnalyticsService(
        Settings(
            _env_file=None,
            youtube_channel_id="UCexample",
            youtube_api_key="test-key",
        )
    )
    service._request_json = AsyncMock(return_value={"items": []})

    with pytest.raises(Exception, match="no channel"):
        await service._youtube_report()
