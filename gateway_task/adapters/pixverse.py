"""Implement the PixVerse provider boundary using its official API docs.

Sources (checked 8 October 2026) are listed in docs/IMPLEMENTATION_NOTES.md.
"""

import os
import re
import uuid
from urllib.parse import urlsplit

import httpx

from gateway_task.contracts import PollResult, RelayError


MODEL = "pixverse/v6"

SUBMIT_PATH = "/openapi/v2/video/text/generate"
RESULT_PATH = "/openapi/v2/video/result/{}"

PROVIDER_MODEL = "v6"
# The V6 page and the submit reference say 5000; older error pages say 2048.
MAX_PROMPT_LENGTH = 5000
MAX_SEED = 2_147_483_647
# The only combination this exercise supports, keyed by Gateway field name.
FIXED_OUTPUT = {"duration": 5, "resolution": "720p", "aspect_ratio": "16:9"}
ALLOWED_FIELDS = {"prompt", "seed", *FIXED_OUTPUT}

# Resp.status values from the result reference schema.
STATUS_SUCCESS = 1
STATUS_GENERATING = 5
TERMINAL_STATUS_ERRORS = {
    6: "PixVerse deleted the video",
    7: "PixVerse content moderation rejected the video",
    8: "PixVerse failed to generate the video",
}

TASK_ID = re.compile(r"0|[1-9][0-9]*")
VISIBLE_ASCII = re.compile(r"[\x21-\x7e]+")
# Lone surrogates are valid in a Python str but cannot be encoded as UTF-8 JSON.
SURROGATE = re.compile(r"[\ud800-\udfff]")
HOSTNAME = re.compile(r"[a-z0-9_-]+(\.[a-z0-9_-]+)*\.?")

PROCESSING: PollResult = ("processing", None, None, None)


async def submit(client: httpx.AsyncClient, model: str, body: dict) -> str:
    """Validate a gateway request and submit one provider job.

    Read ``PIXVERSE_API_KEY`` from the environment for each call. The supplied
    client owns the provider base URL and timeout. Return the provider task ID.
    Never retried: after a timeout the job may exist, and the Gateway reconciles.
    """
    payload = _provider_payload(model, body)
    key = _api_key()
    if key is None:
        raise RelayError(500, "PixVerse API key is not configured")

    response = await _send(client, "POST", SUBMIT_PATH, key, json=payload)
    if isinstance(response, httpx.RequestError):
        if isinstance(response, (httpx.ConnectError, httpx.ConnectTimeout)):
            raise RelayError(502, "Could not connect to PixVerse; the job was not sent")
        raise RelayError(502, "PixVerse did not answer; the job may have been created")

    status = response.status_code
    if not response.is_success:
        raise RelayError(
            502, f"PixVerse returned HTTP {status}", upstream_status=status
        )
    envelope = _envelope(response)
    if envelope is None:
        raise RelayError(
            502, "PixVerse returned an invalid response", upstream_status=status
        )
    code, resp = envelope
    if code != 0:
        raise RelayError(502, f"PixVerse error {code}", upstream_status=status)

    video_id = resp.get("video_id") if isinstance(resp, dict) else None
    if type(video_id) is not int or video_id < 0:
        raise RelayError(
            502, "PixVerse returned no valid video ID", upstream_status=status
        )
    return str(video_id)


async def poll(
    client: httpx.AsyncClient,
    model: str,
    task_id: str,
) -> PollResult:
    """Read one provider job and normalize it to the gateway polling contract.

    Read ``PIXVERSE_API_KEY`` from the environment for each call. The supplied
    client owns the provider base URL and timeout.
    """
    if model != MODEL:
        return _error("Unsupported model")
    if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
        return _error("Invalid PixVerse task ID")
    key = _api_key()
    if key is None:
        return _error("PixVerse API key is not configured")

    response = await _send(client, "GET", RESULT_PATH.format(task_id), key)
    if isinstance(response, httpx.RequestError):
        return PROCESSING
    status = response.status_code
    if status == 429 or status >= 500:
        return PROCESSING
    if not response.is_success:
        return _error(f"PixVerse returned HTTP {status}")
    envelope = _envelope(response)
    if envelope is None:
        return _error("PixVerse returned an invalid response")
    code, resp = envelope
    if code != 0:
        return _error(f"PixVerse error {code}")

    if not isinstance(resp, dict) or type(resp.get("status")) is not int:
        return _error("PixVerse returned no valid status")
    state = resp["status"]
    if state == STATUS_GENERATING:
        return PROCESSING
    if state == STATUS_SUCCESS:
        url = resp.get("url")
        if not _usable_url(url):
            return _error("PixVerse reported success without a usable video URL")
        # The result schema has no usage field; credits are reported elsewhere.
        return ("done", url, None, None)
    return _error(
        TERMINAL_STATUS_ERRORS.get(state, f"PixVerse returned unknown status {state}")
    )


def _provider_payload(model: str, body: dict) -> dict:
    """Validate the Gateway input and build the provider request body."""
    if model != MODEL:
        raise RelayError(400, "Unsupported model")
    if not isinstance(body, dict):
        raise RelayError(400, "Request body must be an object")
    if body.keys() - ALLOWED_FIELDS:
        raise RelayError(400, "Request contains an unsupported field")

    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise RelayError(400, "prompt must be a nonempty string")
    if SURROGATE.search(prompt):
        raise RelayError(400, "prompt must be valid Unicode text")
    if len(prompt) > MAX_PROMPT_LENGTH:
        raise RelayError(400, f"prompt must be at most {MAX_PROMPT_LENGTH} characters")

    for field, supported in FIXED_OUTPUT.items():
        value = body.get(field, supported)
        if type(value) is not type(supported) or value != supported:
            raise RelayError(400, f"Only {field} {supported} is supported")

    payload = {
        "model": PROVIDER_MODEL,
        "prompt": prompt,
        "duration": FIXED_OUTPUT["duration"],
        "quality": FIXED_OUTPUT["resolution"],
        "aspect_ratio": FIXED_OUTPUT["aspect_ratio"],
        "generate_audio_switch": False,
        "generate_multi_clip_switch": False,
    }
    if "seed" in body:
        seed = body["seed"]
        if type(seed) is not int or not 0 <= seed <= MAX_SEED:
            raise RelayError(400, f"seed must be an integer from 0 to {MAX_SEED}")
        payload["seed"] = seed
    return payload


def _api_key() -> str | None:
    """Return the configured key, or None if it is missing or not header-safe."""
    key = os.environ.get("PIXVERSE_API_KEY", "")
    return key if VISIBLE_ASCII.fullmatch(key) else None


async def _send(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    key: str,
    json: dict | None = None,
) -> httpx.Response | httpx.RequestError:
    """Make one request and return the response or the transport error.

    Callers raise their own error outside this ``except`` block, so the httpx
    exception, which holds the request headers, is never chained to it.
    Redirects are refused so the API key cannot be forwarded to another host.
    """
    headers = {"API-KEY": key, "Ai-trace-id": str(uuid.uuid4())}
    try:
        return await client.request(
            method, path, json=json, headers=headers, follow_redirects=False
        )
    except httpx.RequestError as error:
        return error


def _envelope(response: httpx.Response) -> tuple[int, object] | None:
    """Return ``(ErrCode, Resp)`` from PixVerse's response envelope, or None."""
    try:
        data = response.json()
    except (ValueError, RecursionError):  # RecursionError: deeply nested JSON
        return None
    if not isinstance(data, dict) or type(data.get("ErrCode")) is not int:
        return None
    return data["ErrCode"], data.get("Resp")


def _usable_url(url: object) -> bool:
    """Accept an absolute HTTPS URL with a DNS-style hostname and no credentials."""
    # urlsplit silently drops some whitespace and treats "\" loosely, so
    # reject those characters before parsing.
    if not isinstance(url, str) or not VISIBLE_ASCII.fullmatch(url) or "\\" in url:
        return False
    try:
        parts = urlsplit(url)
        return (
            parts.scheme == "https"
            and HOSTNAME.fullmatch(parts.hostname or "") is not None
            and "@" not in parts.netloc
            and parts.port != 0  # .port raises ValueError when malformed
        )
    except ValueError:
        return False


def _error(message: str) -> PollResult:
    return ("error", None, message, None)
