# Cutline Studio

Local-first movie planning workspace. Import a screenplay, run a structured review, accept or reject proposed fixes, and produce scene-level image and video prompts.

## Stack

- Vue 3, TypeScript, Vite, Pinia
- FastAPI, SQLAlchemy, SQLite
- Huey with a persistent SQLite queue
- Pluggable text providers: built-in prototype parser, Ollama, or an OpenAI-compatible local bridge

Docker is intentionally not required for the prototype. The app database and task queue live in `backend/data/`.

## Run locally

Start and verify the complete local stack with one command:

```sh
./scripts/dev
```

This starts the ChatGPT browser bridge, Gemini Docker fallback when Docker is available, FastAPI, all persistent workers, and Vite. Logs are written under `.runtime/`; Ctrl+C cleanly stops processes started by the launcher.

For individual service development, the equivalent commands are:

```sh
cd frontend
npm install
npm run dev
```

```sh
cd backend
uv sync
uv run movie-api
```

```sh
cd backend
uv run movie-worker
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). The prototype parser is available immediately and does not use API credits.

## Shot plan and images

Approving a script opens the shot-plan workspace. Each shot supports prompt and timing edits, queued image generation, regeneration, manual JPG/PNG/WebP upload, and image deletion. Assets are stored locally in `backend/data/media/`.

Image generation uses `MOVIE_IMAGE_PROVIDERS=chatgpt_web,gemini_web`. Each ChatGPT attempt opens a fresh tab and conversation in the persistent signed-in browser context, records its conversation URL on the job, and closes the tab when complete. The bridge waits for generation progress to clear and a new image to remain stable before returning it; uncertain or timed-out results fail without replacing existing media. ChatGPT is tried once, followed by one Gemini attempt when its Docker bridge is online. Manual upload remains available.

The worker runs separate persistent `chatgpt`, `gemini`, `image`, `general`, and `video` lanes. Browser-backed image work is serialized while unrelated analysis and rendering continue independently. Provider settings distinguish a connected browser session from image generation verified by a successful real request.

## Shot clips and ComfyUI

The Clips workspace stores one local video per shot. MP4, WebM, and MOV files up to 250 MB can be uploaded, replaced, played, or deleted without installing a video model. The project Preview automatically uses a shot video when present and falls back to its storyboard image otherwise.

For shot-to-shot continuity, choose a frame from the immediately previous shot's video in the shot editor. Cutline saves it as a separate starting-frame asset for the selected aspect ratio, without replacing the storyboard image or cast references. Video generation uses that image as the provider's opening frame and asks it to preserve the prior shot's scene; remove or replace the selected frame at any time.

Local generation uses ComfyUI through the dedicated `video` queue. Start ComfyUI on `http://127.0.0.1:8188`, export an image-to-video workflow using **Save (API Format)**, and save it as `backend/data/comfyui_i2v_workflow.json`. See `backend/data/COMFYUI_WORKFLOW.md` for the required input placeholders. The Generate video button stays disabled until both the workflow and ComfyUI server are available.

## Final cut

The Final cut workspace assembles the current shot timeline locally with FFmpeg. Existing clips retain their audio; shots without clips use their storyboard image for the planned duration with silence. Every segment is normalized to H.264 video, stereo AAC audio, and the project aspect ratio before being joined into a downloadable MP4. Rendering runs on the persistent `video` queue and reports shot-level progress.

Changing a shot duration or replacing/deleting shot media invalidates the previous master. Rendering again safely replaces the old export while leaving source clips and images intact.

## Ollama

Install Ollama, then download and serve the selected model:

```sh
ollama pull qwen3:8b
ollama serve
```

The API checks `http://127.0.0.1:11434` and enables Ollama in the project form once it responds. Provider settings can be changed in `backend/.env`; copy the available keys from `backend/.env.example`.

## Compatible bridge

The `compatible` provider targets an OpenAI-compatible `/v1/chat/completions` server. This keeps browser-backed Gemini or ChatGPT experiments isolated behind one adapter. Configure its base URL, model, and optional key in `backend/.env`.

Browser-session bridges are experimental and may break when the upstream website changes. Ollama and the built-in parser remain the stable local paths.

## Structured screenplay imports

Screenplays containing explicit `SHOT`, `Time`, `Framing`, `Action`, `Sound`, `Image prompt`, and `Video prompt` sections are parsed deterministically before any model call. Consecutive shots are kept in one sequence by default; optional `Scene:` and shot-level `Location:` fields create compact production groupings. The selected text model reviews the resulting shot plan and returns suggestions only; it cannot remove, merge, or reorder parsed shots. Traditional scene-based screenplays continue through the provider's generative analyzer.

## Checks

```sh
cd frontend && npm run build && npm run test:unit -- --run && npm run lint
cd backend && uv run pytest
```
