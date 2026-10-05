# SOCIARO Gateway: PixVerse adapter task

Implement a small Python adapter for the **direct, official PixVerse API**. The adapter must fit the supplied Gateway transport interface. This is a standalone assessment based on the interface used by SOCIARO's media integration layer.

**Timebox: 3–4 hours, including research, tests and a short write-up.** Stop at four hours and explain what remains. A clear partial submission is useful. AI coding tools, including Codex and Claude Code, are welcome; you are responsible for checking the result.

## The task

Add text-to-video support for **PixVerse V6** under the Gateway model ID `pixverse/v6`:

- One video per request, five seconds, 720p, 16:9.
- A required text prompt and an optional seed.
- Audio generation disabled.
- Submit a generation and return its provider task ID.
- Check the provider task once per `poll()` call and translate it into the Gateway result contract.

You must **find the official provider documentation yourself**. Identify the direct API, current model documentation, authentication, required headers, valid request parameters, response format, task states and error behaviour. Document the URLs and the date checked in [your implementation notes](docs/IMPLEMENTATION_NOTES.md). Explain any disagreement between pages and which source you followed. Third-party provider wrappers are not the target integration.

Provider endpoint examples and completed response mappings are deliberately not supplied. The Gateway contract is specified in [TASK.md](TASK.md).

## Setup

You need Python 3.12+ and [uv](https://docs.astral.sh/uv/). Then run:

```sh
uv sync --locked
make starter-check
make verify
```

`starter-check` verifies the supplied runner. It should pass on the starter repository. **`verify` initially fails because the adapter is intentionally unimplemented.** It runs all public tests, including your tests after you add them. Passing the supplied tests alone does not demonstrate a working provider integration.

An alternative without uv is:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install httpx==0.28.1 pytest==8.3.5
python -m pytest -q
```

## Work locally without an account

No production access, real API key, provider account, payment, Redis, database or Docker service is needed. Research documentation using the web, but run the implementation tests **offline**, using `httpx.MockTransport` and a synthetic `PIXVERSE_API_KEY` set by the tests. Supply the client base URL and timeouts in your test harness.

Write your own documented, synthetic PixVerse response fixtures. Do not claim a mocked run is a live provider test. Do not make paid generation calls or put real credentials in this public repository.

## Where the adapter fits

```text
Gateway job -> submit(client, model, input) -> provider task ID
Gateway worker -> poll(client, model, task ID) -> status / URL / error / usage
Gateway services -> persist job, archive output, account for cost, deliver result
```

This repository supplies the interface and a small, bounded runner to exercise that flow. The runner has no database and polls without delays so offline tests are fast. A production worker owns polling intervals and deadlines. The adapter makes one HTTP request per operation; it does not run a polling loop or download the output.

This exercise covers the transport boundary. Production registration, durable jobs, billing, storage, authentication of customers and deployment belong to the Gateway and are outside this task.

## Submit your work

1. Fork this repository and create a working branch.
2. Implement `gateway_task/adapters/pixverse.py` and add tests.
3. Include an integration test using the supplied `run_job()` with your real adapter and mocked provider HTTP responses.
4. Complete `docs/IMPLEMENTATION_NOTES.md` and run `make verify`.
5. Open a pull request to this repository with the template completed. Do not include personal data in the public PR.

The main branch checks the supplied scaffold. Pull requests and manually triggered CI also run the full candidate test suite. A maintainer may need to approve the first CI run from a fork.

We may ask for one small follow-up change after review. Automated checks use additional response examples within the published contract; there are no undisclosed feature requirements.
