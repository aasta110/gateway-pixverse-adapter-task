"""A bounded, in-memory simulation of the gateway submission/polling loop.

This assessment harness has no database, persistence, scheduling, downloads,
billing, or retries. It does not sleep between polls. Production workers would
persist the task ID and schedule polling; that behavior is outside this task.
"""

import httpx

from gateway_task.contracts import RelayError


async def run_job(
    adapter,
    client: httpx.AsyncClient,
    model: str,
    body: dict,
    *,
    max_polls: int = 5,
) -> dict:
    """Submit once, then poll at most ``max_polls`` times.

    Only a submission ``RelayError`` is converted to a result. Programming
    errors and unimplemented adapter functions propagate to the caller.
    The adapter is responsible for validating its normalized result fields.
    """
    if type(max_polls) is not int or max_polls <= 0:
        raise ValueError("max_polls must be a positive integer")

    try:
        task_id = await adapter.submit(client, model, body)
    except RelayError as error:
        return {
            "status": "submit_error",
            "error": str(error),
            "upstream_status": error.upstream_status,
        }

    for _ in range(max_polls):
        status, result_url, error, usage = await adapter.poll(client, model, task_id)
        if status == "processing":
            continue
        if status == "done":
            return {
                "status": "succeeded",
                "task_id": task_id,
                "result_url": result_url,
                "usage": usage,
            }
        if status == "error":
            return {"status": "failed", "task_id": task_id, "error": error}
        raise ValueError("Unexpected poll status")

    return {"status": "timed_out", "task_id": task_id}
