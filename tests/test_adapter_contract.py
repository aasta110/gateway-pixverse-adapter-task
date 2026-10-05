"""A minimal Gateway-boundary check. Add provider tests after researching its API."""

import asyncio

import httpx
import pytest

from gateway_task.adapters import pixverse
from gateway_task.contracts import RelayError


def test_unsupported_model_is_rejected_before_any_http(monkeypatch):
    monkeypatch.setenv("PIXVERSE_API_KEY", "assessment-fake-key-not-a-credential")

    def no_request(request):
        raise AssertionError("Unsupported model reached the provider")

    async def check():
        async with httpx.AsyncClient(
            base_url="https://pixverse.invalid",
            transport=httpx.MockTransport(no_request),
            timeout=10,
        ) as client:
            with pytest.raises(RelayError) as caught:
                await pixverse.submit(client, "unsupported/model", {"prompt": "A lighthouse"})
            assert caught.value.status == 400
            assert caught.value.upstream_status is None

    asyncio.run(check())
