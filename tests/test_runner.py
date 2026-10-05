"""Provider-independent examples of the assessment runner's contract."""

import asyncio

import httpx
import pytest

from gateway_task.contracts import RelayError
from gateway_task.runner import run_job


class FakeAdapter:
    """Test double for the gateway interface, with no provider API knowledge."""

    def __init__(self, results=(), submit_error=None):
        self.results = iter(results)
        self.submit_error = submit_error
        self.submit_calls = []
        self.poll_calls = []

    async def submit(self, client, model, body):
        self.submit_calls.append((client, model, body))
        if self.submit_error is not None:
            raise self.submit_error
        return "example-task-id"

    async def poll(self, client, model, task_id):
        self.poll_calls.append((client, model, task_id))
        return next(self.results)


def run(adapter, *, max_polls=5):
    async def scenario():
        async with httpx.AsyncClient(base_url="https://provider.invalid") as client:
            return await run_job(
                adapter,
                client,
                "example/video-model",
                {"prompt": "A paper boat on a pond"},
                max_polls=max_polls,
            )

    return asyncio.run(scenario())


def test_success_after_processing_submits_only_once():
    adapter = FakeAdapter(
        [
            ("processing", None, None, None),
            ("done", "https://assets.invalid/video.mp4", None, {"units": 1}),
        ]
    )

    assert run(adapter) == {
        "status": "succeeded",
        "task_id": "example-task-id",
        "result_url": "https://assets.invalid/video.mp4",
        "usage": {"units": 1},
    }
    assert len(adapter.submit_calls) == 1
    assert len(adapter.poll_calls) == 2
    client, model, body = adapter.submit_calls[0]
    assert model == "example/video-model"
    assert body == {"prompt": "A paper boat on a pond"}
    assert adapter.poll_calls == [(client, model, "example-task-id")] * 2


def test_terminal_error_stops_polling():
    adapter = FakeAdapter(
        [
            ("error", None, "The provider could not generate this video", None),
            ("done", "https://assets.invalid/unexpected.mp4", None, None),
        ]
    )

    assert run(adapter) == {
        "status": "failed",
        "task_id": "example-task-id",
        "error": "The provider could not generate this video",
    }
    assert len(adapter.submit_calls) == 1
    assert len(adapter.poll_calls) == 1


def test_poll_limit_stops_without_resubmission():
    adapter = FakeAdapter([("processing", None, None, None)] * 3)

    assert run(adapter, max_polls=3) == {
        "status": "timed_out",
        "task_id": "example-task-id",
    }
    assert len(adapter.submit_calls) == 1
    assert len(adapter.poll_calls) == 3


def test_submit_error_does_not_poll_or_resubmit():
    adapter = FakeAdapter(
        submit_error=RelayError(503, "Provider temporarily unavailable", upstream_status=503)
    )

    assert run(adapter) == {
        "status": "submit_error",
        "error": "Provider temporarily unavailable",
        "upstream_status": 503,
    }
    assert len(adapter.submit_calls) == 1
    assert adapter.poll_calls == []


@pytest.mark.parametrize("max_polls", [0, -1])
def test_invalid_poll_limit_is_rejected_before_submission(max_polls):
    adapter = FakeAdapter()

    with pytest.raises(ValueError, match="max_polls must be a positive integer"):
        run(adapter, max_polls=max_polls)

    assert adapter.submit_calls == []
    assert adapter.poll_calls == []


def test_unimplemented_adapter_is_not_hidden_as_a_provider_error():
    adapter = FakeAdapter(submit_error=NotImplementedError("adapter not implemented"))

    with pytest.raises(NotImplementedError, match="adapter not implemented"):
        run(adapter)


def test_unrecognized_poll_status_is_rejected():
    adapter = FakeAdapter([("unexpected", None, None, None)])

    with pytest.raises(ValueError, match="Unexpected poll status"):
        run(adapter)


def test_relay_error_string_contains_only_the_safe_message():
    error = RelayError(502, "Provider returned an invalid response", upstream_status=200)

    assert error.status == 502
    assert error.message == "Provider returned an invalid response"
    assert error.upstream_status == 200
    assert str(error) == "Provider returned an invalid response"
