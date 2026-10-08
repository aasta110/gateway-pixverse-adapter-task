"""Offline tests for the PixVerse V6 adapter, including runner integration.

Every provider reply here is synthetic. Shapes follow the official docs,
checked 8 October 2026:
- submit: https://docs.platform.pixverse.ai/text-to-video-generation-13016634e0
- result: https://docs.platform.pixverse.ai/get-video-generation-status-13016632e0
- status guide example: https://docs.platform.pixverse.ai/get-video-generation-status-882972m0
- error codes: https://docs.platform.pixverse.ai/error-codes-796041m0
"""

import asyncio
import copy
import json
import logging
import uuid

import httpx
import pytest

from gateway_task.adapters import pixverse
from gateway_task.contracts import RelayError
from gateway_task.runner import run_job


MODEL = "pixverse/v6"
FAKE_KEY = "assessment-fake-key-not-a-credential"
BASE_URL = "https://pixverse.invalid"
SUBMIT_URL = BASE_URL + "/openapi/v2/video/text/generate"
RESULT_URL = BASE_URL + "/openapi/v2/video/result/"
VIDEO_ID = 368934578111
VIDEO_URL = "https://media.pixverse.invalid/openapi/output/video.mp4"
PROMPT = "A lighthouse beside a calm sea at sunrise"
PROVIDER_TEXT = "provider-detail-that-must-not-leak"
PROCESSING = ("processing", None, None, None)
# Valid JSON syntax that is too deep for Python's json parser.
DEEPLY_NESTED = b"[" * 100_000 + b"]" * 100_000


def submit_ok(video_id=VIDEO_ID):
    return {"ErrCode": 0, "ErrMsg": "success", "Resp": {"video_id": video_id}}


def result(status, url=""):
    """A result reply with the field set of the status guide's example."""
    return {
        "ErrCode": 0,
        "ErrMsg": "",
        "Resp": {
            "create_time": "2026-10-08T08:30:00Z",
            "id": VIDEO_ID,
            "modify_time": "2026-10-08T08:31:12Z",
            "negative_prompt": "",
            "outputHeight": 720,
            "outputWidth": 1280,
            "prompt": PROMPT,
            "resolution_ratio": 0,
            "seed": 42,
            "size": 8427520,
            "status": status,
            "style": "",
            "url": url,
        },
    }


def app_error(code):
    # The error page lists codes only; the failure body's Resp is undocumented.
    return {"ErrCode": code, "ErrMsg": PROVIDER_TEXT, "Resp": None}


class Provider:
    """MockTransport handler that replays scripted replies and records requests."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if not self.replies:
            raise AssertionError("Unexpected extra request to PixVerse")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply)

    @property
    def methods(self):
        return [request.method for request in self.requests]


def call(provider, operation, *args, **client_options):
    async def scenario():
        async with httpx.AsyncClient(
            base_url=BASE_URL,
            transport=httpx.MockTransport(provider),
            timeout=10,
            **client_options,
        ) as client:
            return await operation(client, *args)

    return asyncio.run(scenario())


def submit_error(provider, body=None, **client_options):
    with pytest.raises(RelayError) as caught:
        call(
            provider,
            pixverse.submit,
            MODEL,
            body or {"prompt": PROMPT},
            **client_options,
        )
    return caught.value


def poll(provider, task_id=str(VIDEO_ID), **client_options):
    return call(provider, pixverse.poll, MODEL, task_id, **client_options)


def trace_id(request):
    value = request.headers["Ai-trace-id"]
    uuid.UUID(value)
    return value


@pytest.fixture(autouse=True)
def fake_key_and_no_network(monkeypatch):
    monkeypatch.setenv("PIXVERSE_API_KEY", FAKE_KEY)

    async def no_network(self, request):
        raise AssertionError(f"Real network request to {request.url}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)


# Submission request


def test_submit_sends_documented_v6_request():
    provider = Provider(submit_ok())
    body = {
        "prompt": PROMPT,
        "duration": 5,
        "resolution": "720p",
        "aspect_ratio": "16:9",
        "seed": 42,
    }

    assert call(provider, pixverse.submit, MODEL, body) == str(VIDEO_ID)

    [request] = provider.requests
    assert request.method == "POST"
    assert request.url == SUBMIT_URL
    assert request.headers["API-KEY"] == FAKE_KEY
    trace_id(request)
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == {
        "model": "v6",
        "prompt": PROMPT,
        "duration": 5,
        "quality": "720p",
        "aspect_ratio": "16:9",
        "seed": 42,
        "generate_audio_switch": False,
        "generate_multi_clip_switch": False,
    }


@pytest.mark.parametrize(
    "body",
    [
        {"prompt": "  A lighthouse at dawn \n"},
        {"prompt": PROMPT, "seed": 7, "duration": 5},
    ],
)
def test_submit_applies_defaults_and_leaves_caller_body_unchanged(body):
    original = copy.deepcopy(body)
    provider = Provider(submit_ok())

    call(provider, pixverse.submit, MODEL, body)

    assert body == original
    sent = json.loads(provider.requests[0].content)
    assert sent["prompt"] == body["prompt"]
    assert (sent["duration"], sent["quality"], sent["aspect_ratio"]) == (
        5,
        "720p",
        "16:9",
    )
    assert ("seed" in sent, sent.get("seed")) == ("seed" in body, body.get("seed"))


@pytest.mark.parametrize(
    "extra", [{"seed": 0}, {"seed": 2_147_483_647}, {"prompt": "x" * 5000}]
)
def test_submit_accepts_documented_boundaries(extra):
    provider = Provider(submit_ok())

    call(provider, pixverse.submit, MODEL, {"prompt": PROMPT, **extra})

    assert json.loads(provider.requests[0].content).items() >= extra.items()


@pytest.mark.parametrize("video_id", [0, 2**53 + 1, 2**64 + 1])
def test_submit_returns_integer_id_without_precision_loss(video_id):
    provider = Provider(submit_ok(video_id))

    assert call(provider, pixverse.submit, MODEL, {"prompt": PROMPT}) == str(video_id)


def test_every_request_gets_a_fresh_uuid_trace_id():
    provider = Provider(submit_ok(), submit_ok(), result(5), result(5))

    async def operations(client):
        for _ in range(2):
            task_id = await pixverse.submit(client, MODEL, {"prompt": PROMPT})
        for _ in range(2):
            await pixverse.poll(client, MODEL, task_id)

    call(provider, operations)

    traces = [trace_id(request) for request in provider.requests]
    assert len(set(traces)) == 4


# Input validation


VALID = {"prompt": PROMPT}


@pytest.mark.parametrize(
    "model, body",
    [
        ("pixverse/v5", VALID),
        ("PixVerse/V6", VALID),
        ("v6", VALID),
        (None, VALID),
        (MODEL, None),
        (MODEL, ["prompt"]),
        (MODEL, {}),
        (MODEL, {"prompt": ""}),
        (MODEL, {"prompt": " \n\t "}),
        (MODEL, {"prompt": None}),
        (MODEL, {"prompt": 42}),
        (MODEL, {"prompt": "x" * 5001}),
        (MODEL, {"prompt": "A lighthouse \ud800"}),
        *[(MODEL, {**VALID, "duration": v}) for v in (8, 10, 5.0, "5", True, None)],
        *[(MODEL, {**VALID, "resolution": v}) for v in ("1080p", "720P", 720, None)],
        *[(MODEL, {**VALID, "aspect_ratio": v}) for v in ("9:16", "16/9", None)],
        *[
            (MODEL, {**VALID, "seed": v})
            for v in (True, False, -1, 2**31, 1.0, "42", None)
        ],
        (MODEL, {**VALID, "negative_prompt": "blurry"}),
        (MODEL, {**VALID, "generate_audio_switch": True}),
        (MODEL, {**VALID, "quality": "720p"}),
        (MODEL, {**VALID, "model": "v6"}),
    ],
)
def test_invalid_input_is_rejected_before_http(model, body):
    provider = Provider()

    with pytest.raises(RelayError) as caught:
        call(provider, pixverse.submit, model, body)

    assert (caught.value.status, caught.value.upstream_status) == (400, None)
    assert provider.requests == []


@pytest.mark.parametrize("key", [None, "", "   ", FAKE_KEY + "\n"])
def test_missing_or_unusable_key_fails_before_http(monkeypatch, key):
    if key is None:
        monkeypatch.delenv("PIXVERSE_API_KEY")
    else:
        monkeypatch.setenv("PIXVERSE_API_KEY", key)
    provider = Provider()

    error = submit_error(provider)
    assert (error.status, error.upstream_status) == (500, None)
    status, url, message, usage = poll(provider)
    assert (status, url, usage) == ("error", None, None) and message
    assert provider.requests == []


# Submission responses


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 502, 503])
def test_submit_http_error_reports_actual_status(status):
    provider = Provider(httpx.Response(status, json=app_error(400017)))

    error = submit_error(provider)

    assert (error.status, error.upstream_status) == (502, status)
    assert len(provider.requests) == 1


@pytest.mark.parametrize("code", [400017, 500044, 500063, 500090, 99999])
def test_submit_application_error_inside_http_200(code):
    provider = Provider(app_error(code))

    error = submit_error(provider)

    assert (error.status, error.upstream_status) == (502, 200)
    assert PROVIDER_TEXT not in error.message


def test_submit_application_error_wins_over_a_returned_video_id():
    provider = Provider({**submit_ok(), "ErrCode": 500044, "ErrMsg": PROVIDER_TEXT})

    error = submit_error(provider)

    assert (error.status, error.upstream_status) == (502, 200)


@pytest.mark.parametrize(
    "content",
    [
        b"<html>Service unavailable</html>",
        pytest.param(DEEPLY_NESTED, id="deeply-nested"),
        b"[]",
        b"null",
        b'{"ErrMsg": "success", "Resp": {"video_id": 1}}',
        b'{"ErrCode": "0", "Resp": {"video_id": 1}}',
        b'{"ErrCode": false, "Resp": {"video_id": 1}}',
        b'{"ErrCode": 0.0, "Resp": {"video_id": 1}}',
        b'{"ErrCode": 0}',
        b'{"ErrCode": 0, "Resp": null}',
        b'{"ErrCode": 0, "Resp": [1]}',
        b'{"ErrCode": 0, "Resp": {}}',
        *[
            b'{"ErrCode": 0, "Resp": {"video_id": %s}}' % value
            for value in (
                b'"123"',
                b"123.0",
                b"1e3",
                b"true",
                b"false",
                b"null",
                b"-1",
                b"[1]",
                b'{"id": 1}',
            )
        ],
    ],
)
def test_submit_malformed_response_is_rejected(content):
    error = submit_error(Provider(httpx.Response(200, content=content)))

    assert (error.status, error.upstream_status) == (502, 200)


@pytest.mark.parametrize(
    "reply",
    [httpx.Response(201, json=app_error(500044)), httpx.Response(202, content=b"[]")],
)
def test_submit_reports_the_received_2xx_status(reply):
    error = submit_error(Provider(reply))

    assert (error.status, error.upstream_status) == (502, reply.status_code)


def test_caller_client_is_used_as_configured_and_left_open():
    provider = Provider(submit_ok(), result(5))

    async def scenario():
        async with httpx.AsyncClient(
            base_url="https://gateway.invalid/pixverse/",
            transport=httpx.MockTransport(provider),
            timeout=7,
            follow_redirects=True,
        ) as client:
            before = (client.base_url, client.timeout, client.follow_redirects)
            task_id = await pixverse.submit(client, MODEL, {"prompt": PROMPT})
            await pixverse.poll(client, MODEL, task_id)
            assert (client.base_url, client.timeout, client.follow_redirects) == before
            assert not client.is_closed

    asyncio.run(scenario())

    assert [str(request.url) for request in provider.requests] == [
        "https://gateway.invalid/pixverse/openapi/v2/video/text/generate",
        f"https://gateway.invalid/pixverse/openapi/v2/video/result/{VIDEO_ID}",
    ]
    for request in provider.requests:
        assert request.extensions["timeout"] == httpx.Timeout(7).as_dict()


@pytest.mark.parametrize(
    "failure, hint",
    [
        (httpx.ReadTimeout("read timed out"), "may have been created"),
        (httpx.WriteTimeout("write timed out"), "may have been created"),
        (httpx.RemoteProtocolError("connection closed"), "may have been created"),
        (httpx.ConnectTimeout("connect timed out"), "not sent"),
        (httpx.ConnectError("connection refused"), "not sent"),
    ],
)
def test_submit_transport_failure_is_one_attempt_without_upstream_status(failure, hint):
    # A second, successful reply is queued: a retry would consume it.
    provider = Provider(failure, submit_ok())

    error = submit_error(provider)

    assert (error.status, error.upstream_status) == (502, None)
    assert hint in error.message
    assert error.__cause__ is None and error.__context__ is None
    assert len(provider.requests) == 1


def test_redirects_are_not_followed_even_if_the_client_follows_them():
    def redirect():
        return httpx.Response(302, headers={"Location": "https://elsewhere.invalid/"})

    provider = Provider(redirect(), redirect())

    error = submit_error(provider, follow_redirects=True)
    assert (error.status, error.upstream_status) == (502, 302)
    status, _, message, _ = poll(provider, follow_redirects=True)
    assert status == "error" and message
    assert [request.url.host for request in provider.requests] == [
        "pixverse.invalid"
    ] * 2


# Polling


@pytest.mark.parametrize("task_id", ["0", str(VIDEO_ID), str(2**64 + 1)])
def test_poll_sends_documented_request(task_id):
    provider = Provider(result(5))

    assert poll(provider, task_id) == PROCESSING

    [request] = provider.requests
    assert request.method == "GET"
    assert request.url == RESULT_URL + task_id
    assert request.headers["API-KEY"] == FAKE_KEY
    trace_id(request)
    assert request.content == b""


@pytest.mark.parametrize(
    "url",
    [
        VIDEO_URL,
        "https://media.pixverse.invalid/v.mp4?Expires=1760000000&Signature=abc%2B",
        "https://media.pixverse.invalid:443/v.mp4",
    ],
)
def test_poll_success_returns_https_url_and_no_usage(url):
    assert poll(Provider(result(1, url))) == ("done", url, None, None)


@pytest.mark.parametrize("state", [6, 7, 8])
def test_poll_documented_terminal_failures(state):
    # A URL is present but must not be returned for a failed job.
    status, url, error, usage = poll(Provider(result(state, VIDEO_URL)))

    assert (status, url, usage) == ("error", None, None)
    assert isinstance(error, str) and error


def test_poll_terminal_failures_are_distinguishable():
    messages = {poll(Provider(result(state)))[2] for state in (6, 7, 8)}

    assert len(messages) == 3


@pytest.mark.parametrize("state", [0, 2, 3, 4, 9, -1, "1", "5", 1.0, True, None])
def test_poll_unknown_or_invalid_state_is_error(state):
    status, url, error, usage = poll(Provider(result(state, VIDEO_URL)))

    assert (status, url, usage) == ("error", None, None) and error


def test_poll_status_guide_success_example_with_empty_url_is_error():
    # Copied from the status guide: status 1, but the url is empty.
    example = {
        "ErrCode": 0,
        "ErrMsg": "",
        "Resp": {
            "create_time": "2025-03-25T08:30:00Z",
            "id": 12345,
            "modify_time": "2025-03-25T08:35:12Z",
            "negative_prompt": "blurry, low quality",
            "outputHeight": 720,
            "outputWidth": 1280,
            "prompt": "City sunset timelapse",
            "resolution_ratio": 0,
            "seed": 42,
            "size": 8427520,
            "status": 1,
            "style": "anime",
            "url": "",
        },
    }

    assert poll(Provider(example), "12345")[0] == "error"


@pytest.mark.parametrize(
    "url",
    [
        None,
        123,
        "http://media.pixverse.invalid/v.mp4",
        "ftp://media.pixverse.invalid/v.mp4",
        "//media.pixverse.invalid/v.mp4",
        "media.pixverse.invalid/v.mp4",
        "https://",
        "https:///v.mp4",
        "https://user:pass@media.pixverse.invalid/v.mp4",
        "https://user@media.pixverse.invalid/v.mp4",
        "https://media.pixverse.invalid:99999/v.mp4",
        "https://media.pixverse.invalid:abc/v.mp4",
        "https://media.pix\nverse.invalid/v.mp4",
        " https://media.pixverse.invalid/v.mp4",
        "https://media.pixverse.invalid/my video.mp4",
        "https://media.pixverse.invalid\\@elsewhere.invalid/v.mp4",
        "https://[::1/v.mp4",
        "https://%/v.mp4",
        "https://media..pixverse.invalid/v.mp4",
    ],
)
def test_poll_success_without_usable_https_url_is_error(url):
    status, result_url, error, _ = poll(Provider(result(1, url)))

    assert (status, result_url) == ("error", None) and error


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(429),
        httpx.Response(500),
        httpx.Response(502),
        httpx.Response(503, json=app_error(500069)),
        httpx.Response(504),
        httpx.ReadTimeout("read timed out"),
        httpx.ConnectError("connection refused"),
        httpx.RemoteProtocolError("connection closed"),
    ],
)
def test_poll_retryable_failure_is_processing(reply):
    provider = Provider(reply)

    assert poll(provider) == PROCESSING
    assert provider.methods == ["GET"]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409])
def test_poll_other_http_error_is_error(status):
    result_status, _, error, _ = poll(
        Provider(httpx.Response(status, json=app_error(500008)))
    )

    assert result_status == "error" and error
    assert PROVIDER_TEXT not in error


@pytest.mark.parametrize("code", [400017, 500008, 500064, 500069, 99999])
def test_poll_application_error_inside_http_200_is_error(code):
    status, url, error, usage = poll(Provider(app_error(code)))

    assert (status, url, usage) == ("error", None, None)
    assert error and PROVIDER_TEXT not in error


def test_poll_application_error_wins_over_a_success_shaped_resp():
    reply = {**result(1, VIDEO_URL), "ErrCode": 500064, "ErrMsg": PROVIDER_TEXT}

    assert poll(Provider(reply))[0] == "error"


@pytest.mark.parametrize(
    "content",
    [
        b"<html>OK</html>",
        pytest.param(DEEPLY_NESTED, id="deeply-nested"),
        b"[]",
        b"null",
        b'{"ErrMsg": "", "Resp": {"status": 5}}',
        b'{"ErrCode": "0", "Resp": {"status": 5}}',
        b'{"ErrCode": false, "Resp": {"status": 1, "url": "https://media.pixverse.invalid/v.mp4"}}',
        b'{"ErrCode": 0}',
        b'{"ErrCode": 0, "Resp": null}',
        b'{"ErrCode": 0, "Resp": {}}',
        b'{"ErrCode": 0, "Resp": {"status": 1}}',
    ],
)
def test_poll_malformed_response_is_error(content):
    status, url, error, usage = poll(Provider(httpx.Response(200, content=content)))

    assert (status, url, usage) == ("error", None, None) and error


@pytest.mark.parametrize(
    "task_id",
    [
        "",
        "abc",
        "-1",
        "+1",
        "1.0",
        "1e3",
        " 1",
        "1 ",
        "1\n",
        "01",
        "00",
        "١٢",
        "１２",
        "1/2",
        "../1",
        "1?x=1",
        "1#x",
        "%31",
        12,
        None,
    ],
)
def test_poll_invalid_task_id_is_rejected_before_http(task_id):
    provider = Provider()

    status, url, error, usage = poll(provider, task_id)

    assert (status, url, usage) == ("error", None, None) and error
    assert provider.requests == []


def test_poll_unsupported_model_is_rejected_before_http():
    provider = Provider()

    status, _, error, _ = call(provider, pixverse.poll, "pixverse/v5", str(VIDEO_ID))

    assert status == "error" and error
    assert provider.requests == []


# Safe error output


def test_errors_and_logs_do_not_echo_key_prompt_or_provider_text(capsys, caplog):
    caplog.set_level(logging.DEBUG)
    prompt = "a-private-prompt-for-this-test"
    echo = {"ErrCode": 500090, "ErrMsg": f"{PROVIDER_TEXT} {FAKE_KEY} {prompt}"}
    replies = [
        httpx.Response(200, json=echo),
        httpx.Response(500, json=echo),
        httpx.Response(200, content=f"<p>{PROVIDER_TEXT} {FAKE_KEY}</p>".encode()),
        httpx.ReadTimeout(f"timed out sending {FAKE_KEY}"),
    ]
    texts = []
    for reply in replies:
        error = submit_error(Provider(reply), {"prompt": prompt})
        texts += [str(error), repr(error), error.message]
    for reply in [httpx.Response(200, json=echo), httpx.Response(404, json=echo)]:
        texts.append(poll(Provider(reply))[2])
    captured = capsys.readouterr()
    texts += [captured.out, captured.err, caplog.text]

    for text in texts:
        assert FAKE_KEY not in text
        assert prompt not in text
        assert PROVIDER_TEXT not in text


# Runner integration with the real adapter


def run(provider, *, max_polls=5):
    async def job(client):
        body = {"prompt": PROMPT, "seed": 42}
        return await run_job(pixverse, client, MODEL, body, max_polls=max_polls)

    return call(provider, job)


def test_runner_submit_processing_success():
    provider = Provider(submit_ok(), result(5), result(1, VIDEO_URL))

    assert run(provider) == {
        "status": "succeeded",
        "task_id": str(VIDEO_ID),
        "result_url": VIDEO_URL,
        "usage": None,
    }
    assert provider.methods == ["POST", "GET", "GET"]
    assert all(
        request.url == RESULT_URL + str(VIDEO_ID) for request in provider.requests[1:]
    )


def test_runner_poll_failures_never_create_a_second_job():
    provider = Provider(
        submit_ok(),
        httpx.Response(503),
        httpx.ReadTimeout("read timed out"),
        httpx.Response(429),
        result(1, VIDEO_URL),
    )

    assert run(provider)["status"] == "succeeded"
    assert provider.methods == ["POST", "GET", "GET", "GET", "GET"]


def test_runner_terminal_failure():
    provider = Provider(submit_ok(), result(5), result(7))

    outcome = run(provider)

    assert outcome["status"] == "failed" and outcome["task_id"] == str(VIDEO_ID)
    assert outcome["error"]
    assert provider.methods == ["POST", "GET", "GET"]


def test_runner_poll_limit():
    provider = Provider(submit_ok(), result(5), result(5), result(5))

    assert run(provider, max_polls=3) == {
        "status": "timed_out",
        "task_id": str(VIDEO_ID),
    }
    assert provider.methods == ["POST", "GET", "GET", "GET"]


def test_runner_ambiguous_submit_timeout_is_not_resubmitted():
    provider = Provider(
        httpx.ReadTimeout("read timed out"), submit_ok(), result(1, VIDEO_URL)
    )

    outcome = run(provider)

    assert outcome["status"] == "submit_error"
    assert outcome["upstream_status"] is None
    assert provider.methods == ["POST"]
