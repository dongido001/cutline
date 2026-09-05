# Local web bridges

These services turn existing browser subscriptions into local OpenAI-compatible endpoints. They are experimental, can break when upstream websites change, and must remain bound to localhost.

## Gemini Web in Docker

1. Copy `.env.example` to `.env`.
2. From `gemini.google.com`, copy `__Secure-1PSID` and `__Secure-1PSIDTS` into `.env`.
3. Start the service:

```sh
docker compose up -d gemini-web
docker compose ps
```

The endpoint is `http://127.0.0.1:4981/openai/v1`.

## ChatGPT Web on macOS

The ChatGPT bridge runs natively because its real backend needs a visible Chromium window for login and Cloudflare checks. Its source, virtual environment, browser binary, credentials, and profile are ignored by Git.

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
