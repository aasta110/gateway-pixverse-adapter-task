# Implementation notes

## Documentation research

All pages are on the official PixVerse Platform docs, checked **8 October 2026**. The [page index](https://docs.platform.pixverse.ai/llms.txt) lists every page; appending `.md` to a page URL gives its raw text.

| Page | Used for |
| --- | --- |
| [Text-to-Video generation (API reference)](https://docs.platform.pixverse.ai/text-to-video-generation-13016634e0) | `POST /openapi/v2/video/text/generate`; required headers `API-KEY`, `Ai-trace-id` ("UUID, must be unique for each request"), `Content-Type: application/json`; body fields; `prompt` `maxLength: 5000`; seed range 0–2147483647; `generate_audio_switch` ("false: Audio off"); `generate_multi_clip_switch` (default single-clip); response `Resp.video_id` of type integer |
| [Get Video Generation Status (API reference)](https://docs.platform.pixverse.ai/get-video-generation-status-13016632e0) | `GET /openapi/v2/video/result/{id}`; same two headers; `Resp.status`: "Generation succesful = 1; Generating=5; Deleted = 6; Contents moderation failed = 7; Generation failed= 8"; `Resp.url` |
| [V6 model](https://docs.platform.pixverse.ai/v6-2056814m0) | `"model":"v6"`; prompt "≤5000 chars"; duration 1–15; quality 360p–1080p; 16:9 supported; audio and multi-clip switches supported for text-to-video |
| [Get video generation status (guide)](https://docs.platform.pixverse.ai/get-video-generation-status-882972m0) | Example result body (the test fixtures copy its field set); polling interval |
| [Text-to-video guide](https://docs.platform.pixverse.ai/text-to-video-882970m0) | Submit example; "400/500 status : Incorrect code" |
| [PixVerse API summary](https://docs.platform.pixverse.ai/pixverse-api-llm-txt-2109771m0) | Envelope `{ ErrCode, ErrMsg, Resp }`, "ErrCode=0 means success"; reusing a trace ID "returns the previous result instead of triggering a new generation" |
| [Quick Start](https://docs.platform.pixverse.ai/quick-start-796052m0), [FAQ](https://docs.platform.pixverse.ai/faq-796042m0) | "If you use the same ai-trace-id multiple times, You won't get a new video generated" |
| [Error codes](https://docs.platform.pixverse.ai/error-codes-796041m0) | Application codes, e.g. 400017 invalid parameter, 500044 concurrency limit, 500063 moderation, 500064 deleted, 500069 high load, 500090 insufficient balance |
| [Rate limit](https://docs.platform.pixverse.ai/rate-limit-796040m0) | Concurrency limit = "Maximum number of simultaneously generating tasks"; the error-code page lists 500044 for it, and no HTTP 429 is documented |
| [Webhook integration](https://docs.platform.pixverse.ai/webhook-integration-1905378m0), [Usage deduction query](https://docs.platform.pixverse.ai/usage-deduction-query-41209884e0) | The only places actual per-job credits are reported; neither is part of submit or poll. (The V6 page has a per-second price table, which is not usage.) |

### Conflicting or incomplete documentation

| Topic | Conflict | Followed |
| --- | --- | --- |
| Prompt limit | V6 page and submit reference: 5000. Error codes 400018/400019 and the common-errors table: 2048. | **5000.** The common-errors table describes `model: v3.5` and also restricts duration to 5/8 and 360p to "Turbo", which do not apply to V6. |
| Deleted state | Only the result schema and the API summary list status 6. The status guide, text-to-video guide, FAQ and the result endpoint's own description list 1, 5, 7, 8. | **Schema.** A shorter list is not evidence that 6 cannot occur, and TASK.md requires handling deleted output. |
| Success with empty URL | Text-to-video guide: "The url is accessible when status is 1". The status guide's own success example has `"status": 1, "url": ""`. | Treated as an error (tested with that exact example). |
| Example body fields | The "How does the API work?" example sends `motion_mode`, `negative_prompt` and `water_mark` with `model: v6`. The submit reference and V6 page do not list them. | Not sent. |
| Audio controls | The sound-effects guide presents `sound_effect_switch` for text-to-video (example uses v3.5). The submit reference says it is "Supported in v5 models and below". | `generate_audio_switch: false`, the V6 control. |
| Trace ID format | Endpoint references require a UUID. The template-list page says "Any string, recommended to be unique", and several examples use `Ai-Trace-Id: 123123`. | A fresh UUID4 for every request, submit and poll. |
| HTTP 200 label | The submit reference labels its 200 response "Token Expired". | Ignored; both HTTP status and `ErrCode` are checked. |
| Polling interval | "every 3–5s" vs "no less than 5 seconds (3~5 seconds)". | Not the adapter's concern; the worker schedules polls. |

Not documented: HTTP statuses for specific failures (only "400/500"), the body shape of a failed response (`Resp` may be absent or null), the unit behind "characters", and how long trace-ID deduplication lasts or whether it is guaranteed.

PixVerse's own MCP client ([PixVerseAI/PixVerse-MCP](https://github.com/PixVerseAI/PixVerse-MCP), an SDK-style wrapper, so not a source) maps statuses 2 (pending) and 3 (in progress), has no entry for 5, and retries submissions with a new trace ID. No API doc page mentions 2 or 3. The adapter follows the API reference: 2 and 3 are unknown states and become `error`. This is an open question to confirm with PixVerse.

## Mapping and decisions

**Request.** `pixverse/v6` → `model: "v6"`. Gateway `resolution` → provider `quality`. The body sent is:

```json
{"model": "v6", "prompt": "<as given>", "duration": 5, "quality": "720p", "aspect_ratio": "16:9",
 "generate_audio_switch": false, "generate_multi_clip_switch": false, "seed": 42}
```

`seed` is sent only when given. Multi-clip is pinned to its documented default, so "one video" stays a single shot if PixVerse ever changes the default. The prompt is sent unchanged; it is rejected if it is blank after `strip()` or longer than 5000 Python characters (code points).

**Input validation (400, `upstream_status=None`, before HTTP).** Unsupported model; non-object body; unknown keys; missing, non-string or blank prompt; a prompt with a lone surrogate (it cannot be encoded as UTF-8 JSON); prompt over 5000; any `duration`, `resolution` or `aspect_ratio` other than exactly `5`, `"720p"`, `"16:9"` (type checked, so `5.0`, `"5"` and `True` fail); explicit `null` for any field; a seed that is not a plain `int` (booleans fail) or is outside 0–2147483647. The caller's body is never modified.

**Configuration.** `PIXVERSE_API_KEY` is read on every call. Missing, empty, or containing anything other than visible ASCII (a pasted trailing newline, for example) counts as missing: `submit` raises `RelayError(500, ..., upstream_status=None)`, `poll` returns an error tuple, and no request is sent. Without this check a key with a newline would fail inside httpx and look like a network error.

**Submit response.** Non-2xx → 502 with the actual HTTP status. Then the JSON must be an object with an integer `ErrCode` (`false` is not `0`); nonzero → 502, message `PixVerse error <code>`, `upstream_status` stays the HTTP status (200). `Resp.video_id` must be an `int` ≥ 0, not a bool, float or string; it is returned with `str()`, so IDs above 2^53 and 2^64 keep every digit.

**Poll (one request, never a POST).** The task ID must match `0|[1-9][0-9]*` before it is put in the path, so signs, spaces, `../`, `%31` and non-ASCII digits (`"١٢".isdigit()` is true in Python) are rejected without HTTP.

| Provider result | Gateway |
| --- | --- |
| network error or timeout, HTTP 429, HTTP 5xx | `processing` |
| other non-2xx (including redirects) | `error` |
| invalid JSON or envelope, nonzero `ErrCode` | `error` |
| `status` 5 | `processing` |
| `status` 1 with a usable URL | `done` |
| `status` 1 without a usable URL | `error` |
| `status` 6 / 7 / 8 | `error` ("deleted" / "moderation rejected" / "failed to generate") |
| any other or missing `status`, or a non-int such as `"1"` or `true` | `error` |

A usable URL is visible ASCII with no backslash, scheme `https`, a DNS-style hostname (letters, digits, `-`, `_` and single dots; IP-literal hosts are rejected), no `@` in the authority and a valid port. Deeply nested JSON, which makes Python's parser raise `RecursionError`, is treated as malformed. The character check runs first because `urlsplit` silently removes embedded newlines and tabs. `usage` is always `None`: the result schema has no usage field, and fetching credits would need a second request.

**Errors.** Messages are fixed strings, plus PixVerse's numeric error code or the HTTP status. `ErrMsg`, response bodies, the prompt and the key never appear. Transport errors are converted outside the `except` block, so the httpx exception (which holds the request headers) is not chained to the `RelayError`.

**Redirects.** Every request passes `follow_redirects=False`. Checked locally: httpx forwards the custom `API-KEY` header to another host when a client follows a redirect. The per-request flag stops that without changing the caller's client.

**Submission timeout.** One request, never retried, `RelayError(502, ..., upstream_status=None)`. After a read or write timeout the job may exist and may be billed, so a second POST could create a second video. The message says "the job may have been created". If the connection itself failed (`ConnectError`, `ConnectTimeout`), nothing was sent and the message says so. The status is 502 either way, but reconciliation can tell the two apart. The docs say a reused trace ID returns the earlier result, which would make a safe retry possible, but the dedup window and guarantees are undocumented. So retries and reconciliation are left to the Gateway, as TASK.md requires.

**Where TASK.md wins over what I would do in production.** On poll, error 500069 ("high load") and HTTP 401 end the job as `error`, because TASK.md makes every nonzero application code and every non-429 4xx terminal.

## Verification

`make` is not installed on the Windows machine used, so the Makefile targets were run directly with uv 0.10.12 on Python 3.12.13 (httpx 0.28.1, pytest 8.3.5, as in CI):

- `uv run --frozen pytest -q tests/test_runner.py` (= `make starter-check`): 9 passed.
- `uv run --frozen pytest -q` (= `make verify`): 203 passed.
- `git diff --check`: clean. Files use LF endings to match the Linux CI.
- `uvx ruff check`: only I001 (import grouping), which the supplied `contracts.py` also triggers; the scaffold's style is kept. `uv run --with mypy mypy gateway_task/adapters/pixverse.py`: no issues.

The offline end-to-end tests run the supplied `run_job()` with the real adapter against `httpx.MockTransport`: submit → processing → success, transient poll failures (503, timeout, 429) → success with one POST, moderation failure, the poll limit, and a submit timeout that is not resubmitted. An autouse fixture makes any real `AsyncHTTPTransport` request fail the test.

**Breaking the code on purpose.** 37 rules in the adapter were broken one at a time, running the suite after each. Examples: accept a boolean seed, retry a submit, reuse the trace ID, treat status 6 as processing, accept `http://`, enable audio, follow redirects, accept `ErrCode: false`, route the ID through `float`, make 429 an error, check task IDs with `isdigit()`, mutate the caller's body, use the 2048 limit, chain the httpx exception, create a hidden client, change the client's timeout, hardcode the origin, print the key, report upstream 200 for a 201 reply. A test failed every time. One break first survived: ignoring a nonzero `ErrCode` on submit, because every error fixture happened to lack a `video_id`. A test where an error reply still carries one was added.

**Not verified against a live provider.** All provider replies are synthetic fixtures based on the docs. No PixVerse account, key or paid call was used. Unverified: real HTTP statuses for errors, the real failure body, whether statuses 2/3 occur, whether `video_id` ever exceeds 64 bits, and the real result URL host.

## AI-assisted work

Tools: Claude Code (research, implementation, tests, draft of these notes) and Codex (an independent, read-only research pass, then a hostile review of the finished code and notes).

Representative instruction to Codex: research the official PixVerse docs independently, without seeing the other agent's conclusions; quote the doc text and URL for every fact; write "not documented" instead of guessing; and list every place where two official pages disagree. The two reports were then compared line by line. The comparison changed two decisions: `generate_multi_clip_switch: false` is sent explicitly, and an invented 20-digit cap on task IDs was dropped because PixVerse documents no maximum.

What I checked myself: I approved the field and status mapping before any code was written, compared the two research reports, and required the code to be broken on purpose rule by rule so that every rule is shown to have a failing test. I also had Codex review the finished code and notes, and checked each of its findings against the code before acting. It found two crashes that escaped as raw exceptions (a prompt containing a lone surrogate failed inside httpx's UTF-8 JSON encoding, and deeply nested JSON raised `RecursionError`), a hostname check that accepted `https://%/`, four requirements no test protected (client configuration, use of the base URL, keys in logs, the exact 2xx upstream status), and an overstated sentence about credits in these notes. All were fixed, and each new test was confirmed to fail on the old code.

Correction: after the first research pass the plan looked ready to build, but I did not accept it and asked for a deeper second pass first. That pass found three things the first plan missed. PixVerse's own MCP client retries a submission up to three times with a new trace ID, which can create and bill duplicate videos; it also uses statuses 2 and 3, which no doc page lists. httpx forwards the custom `API-KEY` header to another host when a client follows a redirect. And `urlsplit` silently drops a newline inside a URL. The adapter now refuses redirects per request and checks URL characters before parsing, both with tests, and statuses 2/3 are recorded above as an open question.

## Time and remaining work

About 2 hours of wall-clock time, including research, tests, review and these notes.

Implementation gaps: none known within TASK.md. Open questions for PixVerse: statuses 2/3, failure body shape and HTTP statuses, trace-ID dedup guarantees.

Production onboarding outside this exercise: Gateway reconciliation of uncertain submits (for example, using the documented trace-ID reuse once PixVerse confirms its guarantees), poll scheduling of at least 5 s, treating 500044/500069 as back-off rather than terminal, reading credits from webhooks or the usage-deduction endpoint for billing, and key management and rotation.
