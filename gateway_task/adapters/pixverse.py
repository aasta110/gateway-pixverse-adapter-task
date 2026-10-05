"""Implement the PixVerse provider boundary using its official API docs."""

import httpx

from gateway_task.contracts import PollResult


MODEL = "pixverse/v6"


async def submit(client: httpx.AsyncClient, model: str, body: dict) -> str:
    """Validate a gateway request and submit one provider job.

    Read ``PIXVERSE_API_KEY`` from the environment for each call. The supplied
    client owns the provider base URL and timeout. Return the provider task ID.
    """
    raise NotImplementedError("Implement the PixVerse adapter")


async def poll(
    client: httpx.AsyncClient,
    model: str,
    task_id: str,
) -> PollResult:
    """Read one provider job and normalize it to the gateway polling contract.

    Read ``PIXVERSE_API_KEY`` from the environment for each call. The supplied
    client owns the provider base URL and timeout.
    """
    raise NotImplementedError("Implement the PixVerse adapter")
