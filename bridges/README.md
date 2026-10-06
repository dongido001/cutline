# Local web bridges

These services turn existing browser subscriptions into local OpenAI-compatible endpoints. They are experimental, can break when upstream websites change, and must remain bound to localhost.

## Availability

Bridge implementations are not bundled with Cutline. The original upstream projects are [chatgpt-web-provider](https://github.com/guberm/chatgpt-web-provider) and [gemini-web-to-api](https://github.com/ntthanh2603/gemini-web-to-api). Cutline's local integrations may require modifications absent upstream, particularly ChatGPT's `/v1/provider/capabilities` endpoint advertising `status_endpoint`. An upstream checkout alone is not a verified image-generation setup.

Use a separately installed compatible service, or supply compatible source in the ignored directories described below. Follow that implementation's dependency and browser installation instructions. Core Cutline works without either bridge.

`./scripts/dev --bridges` attempts configured bridges; failures do not prevent core startup. Set the matching URL and API key in `backend/.env`. Never expose these services outside localhost.

## Gemini Web in Docker

1. Supply a compatible source checkout with a Dockerfile in `bridges/gemini-web-to-api/`, then copy `bridges/.env.example` to `bridges/.env`.
2. From `gemini.google.com`, copy `__Secure-1PSID` and `__Secure-1PSIDTS` into `.env`.
3. Start the service:

```sh
cd bridges
docker compose up -d gemini-web
docker compose ps
```

The endpoint is `http://127.0.0.1:4981/openai/v1`.

## ChatGPT Web on macOS

The ChatGPT bridge runs natively because its real backend needs a visible Chromium window for login and Cloudflare checks. Its source, virtual environment, browser binary, credentials, and profile are ignored by Git.

Supply a compatible source checkout in `bridges/chatgpt-web-provider/` and install its dependencies into its `.venv`. Install Chromium for its Playwright version into `bridges/.playwright/`. Copy `bridges/chatgpt.env.example` to `bridges/chatgpt.env`, replace the token, and set the absolute profile path. Use the same token as `MOVIE_CHATGPT_WEB_API_KEY` in `backend/.env`.

Initialize the browser profile once:

```sh
cd bridges/chatgpt-web-provider
set -a
source ../chatgpt.env
set +a
PLAYWRIGHT_BROWSERS_PATH=../.playwright .venv/bin/chatgpt-web-provider-browser-setup
```

Log in to `chatgpt.com` in the opened Chromium window, then press Enter in the terminal to save the profile. Start the bridge with:

```sh
cd bridges/chatgpt-web-provider
set -a
source ../chatgpt.env
set +a
PLAYWRIGHT_BROWSERS_PATH=../.playwright .venv/bin/chatgpt-web-provider
```

The endpoint is `http://127.0.0.1:8791/v1`.

Never commit, paste into chat, or expose the Gemini cookies, ChatGPT profile, or bridge API token.
