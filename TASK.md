# Gateway contract

Implement the two existing asynchronous functions in `gateway_task/adapters/pixverse.py`. Keep their signatures and the shared types unchanged.

```python
async def submit(client: httpx.AsyncClient, model: str, body: dict) -> str: ...
async def poll(client: httpx.AsyncClient, model: str, task_id: str) -> PollResult: ...
```

The functions receive a caller-owned HTTPX client. Use that client; do not create a hidden client, change its configuration, close it or enable redirects. The caller provides the base URL and a finite timeout. Construct requests relative to that base URL so tests can intercept every request. Read the provider credential from `PIXVERSE_API_KEY`; tests set a fake value. Missing configuration must produce a controlled, credential-free error before HTTP.

## Accepted Gateway input

The only supported Gateway model is `pixverse/v6`. The body has this shape:

```json
{
  "prompt": "A lighthouse beside a calm sea at sunrise",
  "duration": 5,
  "resolution": "720p",
  "aspect_ratio": "16:9",
  "seed": 42
}
```

- `prompt` is a nonempty string after whitespace validation, within the model's documented length limit.
- `duration`, `resolution` and `aspect_ratio` may be omitted; defaults are 5, `720p` and `16:9`. This exercise supports only that combination.
- `seed` is optional. Validate its type and documented range; a boolean is not an integer seed.
- Reject unknown keys, unsupported models and unsupported parameter values before sending HTTP. Do not silently clamp or ignore them.
- Do not mutate the caller's body.
- Translate the Gateway fields into the provider's documented request. Ensure generated audio is disabled.

No images, uploads, webhooks, SDK wrappers, multi-model catalog or pricing implementation is required.

## Submission

Perform one provider submission and return its numeric task identifier as a string without loss of precision. Check HTTP status, the provider's application-level success indicator, response structure and ID type. The provider JSON must contain a nonnegative integer ID; a string, boolean, list or object is not a valid response ID.

Use the provider's documented authentication and a fresh request trace identifier for each HTTP request. Keep credentials out of errors, logs and returned values.

Raise the supplied `RelayError`:

| Situation | `status` | `upstream_status` |
| --- | --- | --- |
| Invalid Gateway input | 400 | `None` |
| Provider HTTP or application error | 502 | Actual HTTP status received |
| Malformed response or missing task ID | 502 | Actual HTTP status received |
| Timeout or network error | 502 | `None` |

Use short, safe messages. Do not copy a raw provider error, full response, prompt or credential into the error. Provider application error numbers are not HTTP status codes.

**Do not automatically retry a submission.** After an inconclusive timeout, the provider may already have accepted a billable job. The Gateway owns reconciliation; the adapter must preserve the uncertainty. Do not turn this into a second generation request.

## Polling

Each `poll()` call makes at most one HTTP request and returns:

```python
(status, result_url, error, usage)
```

| Gateway status | URL | Error | Meaning |
| --- | --- | --- | --- |
| `processing` | `None` | `None` | Provider is still working, or the status request had a retryable transport failure |
| `done` | A usable HTTPS URL | `None` | Provider reports success and supplies a result |
| `error` | `None` | A short, safe, nonempty string | Terminal provider failure or an invalid response |

Discover the provider's numeric states yourself. Handle all documented terminal states, including deleted output and moderation refusal. Unknown/missing states, malformed JSON, an invalid response shape or reported success without a usable HTTPS URL must not become `done`; return `error`. A usable URL must have a hostname and no embedded credentials. Do not fetch it.

For this assessment, poll network errors, HTTP 429 and HTTP 5xx return `processing`. Other HTTP errors and nonzero provider application error codes return `error`. The supplied runner bounds retries; no sleeping or retry loop belongs inside the adapter. Invalid task IDs must be rejected without sending HTTP or interpolating an arbitrary path; a controlled error tuple or `RelayError` is acceptable for local validation/configuration errors.

Return `usage=None` unless the provider actually reports documented usage. Do not infer billed cost or usage from the requested duration, dimensions, output size or a public price list.

## Tests you should add

Use synthetic fixtures grounded in the documentation, with HTTPX `MockTransport`. Cover:

- Request method/path, authentication, unique tracing, payload and ID extraction.
- Input rejection before HTTP, optional defaults, caller input unchanged.
- Processing, success, terminal provider failure and malformed/unknown responses.
- HTTP errors, application errors inside HTTP 200, and timeouts.
- One submit on an ambiguous outcome; a poll failure never creates a second job.
- Safe error output and no accidental real network calls.
- Runner integration: submit -> processing -> success; also a terminal failure and the runner's poll limit.

These are behaviour requirements, not a target test count. Add the tests that demonstrate them; don't modify the provided tests to make failures disappear. Keep the solution small enough to review.
