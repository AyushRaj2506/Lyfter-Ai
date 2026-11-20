# Lyftr Chat

React and FastAPI chatbot with per-session SQLite history, ordered LiteLLM key failover, and response caching. Each reply shows its source, model, latency, and token usage.

## 1. Live URLs

Public repository is [AyushRaj2506/Lyfter-Ai](https://github.com/AyushRaj2506/Lyfter-Ai). Frontend, backend, and demo video links are pending.

| Local service | URL |
| --- | --- |
| Frontend (Docker) | [localhost:3000](http://localhost:3000) |
| Backend health | [localhost:8000/health](http://localhost:8000/health) |
| Interactive API docs | [localhost:8000/docs](http://localhost:8000/docs) |

### Publish and connect the two services

1. Push the source to a public GitHub repository. Keep `.env` private and commit only `.env.example`.
2. Create a Render Docker web service from the repository. Use `backend` as its root directory, `./Dockerfile` in that directory, and `/health` as its health-check path. Set two real keys in `LLM_API_KEYS`, set `LLM_MODEL`, set `ENABLE_FAILURE_SIMULATION=true`, and set `DATABASE_URL=sqlite:////data/app.db`. Render supplies `PORT`. To keep history across redeploys, attach a persistent disk at `/data` that UID 10001 can write to. Free Render instances can cold-start and have ephemeral storage. [Render Docker](https://render.com/docs/docker) · [Persistent disks](https://render.com/docs/disks) · [Free-instance limits](https://render.com/docs/free)
3. Import the repository into Vercel. Set the project root to `frontend`, choose Vite, and set `VITE_API_BASE_URL` to the public HTTPS backend URL before building. The build command is `npm run build` and the output directory is `dist`. [Vite on Vercel](https://vercel.com/docs/frameworks/frontend/vite)
4. Set `ALLOWED_ORIGINS` on the backend to the public frontend origin and redeploy. Open both URLs in a signed-out browser, run the HTTP verification script with the deployed URLs, and record the demo.

## 2. Run locally (with Docker and without)

Copy `.env.example` to `.env` in the repository root and replace both `LLM_API_KEYS` placeholders. For Gemini, use keys from separate projects so their rate limits are independent. The default model is `gemini/gemini-3.1-flash-lite`. Confirm access with your keys using the live probe below. [Gemini model](https://aistudio.google.com/docs/models/gemini-3.1-flash-lite) · [Gemini rate limits](https://aistudio.google.com/docs/rate-limits)

### With Docker

Install Docker with the Compose plugin and Make. On Windows, use a shell with `make` and `cp` available, such as WSL with Docker integration.

```sh
cp .env.example .env
# Edit .env and set your two provider keys.
make up
make logs
```

`make up` creates `.env` from the example if it is missing. The frontend waits for a healthy backend, so configure real keys before bringing up both services. A health response confirms that the key pool is configured and SQLite is reachable. The live probe checks whether Gemini accepts the keys.

`make down` stops both services and preserves the `app_data` volume mounted at `/data`. `make clean` also removes the volume and its history.

### Without Docker

Use Python 3.12 and Node.js 24. In the root `.env`, set `DATABASE_URL=sqlite:///data/app.db` for a local relative path. For disk caching, also set `CACHE_DIR=data/litellm_cache`.

```sh
python -m venv .venv
source .venv/bin/activate
# PowerShell activation command is .\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements-dev.txt
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

In another terminal from the repository root, run:

```sh
cd frontend
npm ci
npm run dev
```

Open [localhost:5173](http://localhost:5173). To set a different backend URL, export `VITE_API_BASE_URL` before starting Vite or put it in `frontend/.env.local`. Vite embeds that URL at build time.

## 3. Design decisions (key fallback, caching, SQLite session storage)

- **Key fallback.** The gateway calls `litellm.completion` with zero SDK retries. A manual ordered loop starts at `key_1` every time and advances on provider errors, timeouts, or empty replies. This makes key IDs and fallback counts deterministic. Provider failures log the exception class, status code, and key ID. All keys failing returns JSON 503.
- **Caching.** LiteLLM's exact-match local cache is enabled by default. The key uses the configured model and the exact message list, including the system prompt and history. A hit returns the cached text and usage, sets a null key ID and zero fallbacks, and measures fresh server latency. Simulated requests bypass reads so a cache hit cannot hide a key failure. Use `CACHE_TYPE=disk` to retain cache entries across restarts on a persistent volume. [LiteLLM caching](https://docs.litellm.ai/docs/proxy/caching)
- **Session storage.** The browser generates a fresh session ID on page load and sends only new messages. The API reconstructs the model context with the stored system prompt and up to `MAX_HISTORY_MESSAGES` recent messages. SQLite keeps the full successful history. The system prompt and both messages are committed together after a successful reply. An unwritable configured SQLite location falls back to temporary storage and logs an error. A single Uvicorn worker keeps the in-memory cache and per-session locks shared across requests.
- **Usage and latency.** The API reports token counts for the current prompt and reply using the provider's usage, with LiteLLM's token counter as a fallback. Latency includes server work through the model or cache result. The app displays the measurements next to each reply.

## 4. Verification (how to see failover, cache hits and per-message token usage)

From the repository root with the backend dependencies installed:

```sh
python -m pytest -q backend
python backend/litellm_spike.py
python backend/litellm_spike.py --live
python scripts/verify_contract.py http://localhost:8000 --origin http://localhost:3000
```

The first probe uses LiteLLM's test response. The live probe sends requests to Gemini with the configured keys. The HTTP verification script checks health, CORS, exact cache hits, per-request usage, failover, 422 and 503 responses, and multi-turn persistence. Run it again with the public backend URL and public frontend origin after deploying.

### Demo sequence for a 2–3 minute recording

1. Open the deployed frontend and show `GET /health` in the API docs. Send a fresh first message and point out `LIVE_INFERENCE`, latency and input/output token usage.
2. Start a new chat and send the exact same first message. Show `LLM_CACHE`, identical text and token counts, and the new latency. Continue the conversation, click the session badge to copy its ID, and use it in `GET /sessions/{session_id}/messages`.
3. Select **Key 1 · Rate limit (429)** under **Simulate failure** and send a message. Point out `key_2`, one fallback, and a normal reply. Use `POST /chat` in the API docs to show the `X-Simulate-Failure` header, then send `*=429` to demonstrate the 503. Get the history again to confirm the failed turn is absent.
4. Add the verified public links and the narrated recording to the assignment form.

## 5. Setup Used (editor and AI tools)

OpenAI Codex desktop was used to implement the project and run checks on Windows with PowerShell. The frontend uses React, Vite, and TypeScript. The backend uses FastAPI, LiteLLM, Python's `sqlite3`, and pytest.

## 6. Bonus features (if any)

- **Session controls.** Start a new chat, override the session's system prompt, or simulate a provider failure for the next message from the UI.
- **Reply diagnostics.** Key ID and fallback attempts are shown alongside the required source, model, latency, and token counts.
- **Disk cache option.** Retain exact-match responses across process restarts when the cache directory is mounted on persistent storage.
