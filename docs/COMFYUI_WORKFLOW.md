# ComfyUI image-to-video workflow

Install and run ComfyUI separately on `http://127.0.0.1:8188`, including the models and custom nodes required by your workflow. Verify the workflow works in ComfyUI before exporting it in API format (the node-ID object accepted by `/prompt`, not the editor graph).

Save the JSON to `backend/data/comfyui_i2v_workflow.json`, or set `MOVIE_COMFYUI_WORKFLOW_PATH` to an absolute path in `backend/.env`. Set `MOVIE_VIDEO_PROVIDER=comfyui` and restart the API and workers.

Replace the relevant input values in the exported JSON with these quoted placeholders:

| Placeholder | Value supplied by Cutline |
| --- | --- |
| `{{INPUT_IMAGE}}` | Image filename uploaded to ComfyUI's input directory |
| `{{PROMPT}}` | Shot video prompt |
| `{{WIDTH}}` | 832 for landscape, 480 for portrait |
| `{{HEIGHT}}` | 480 for landscape, 832 for portrait |
| `{{FRAME_COUNT}}` | Duration at configured FPS, rounded down to a count of 4n+1, minimum 5 |
| `{{FPS}}` | `MOVIE_VIDEO_FPS`, default 16 |
| `{{SEED}}` | Random seed for each request |

An entire placeholder value preserves its type: `"width": "{{WIDTH}}"` becomes a number. Embedded placeholders become text. Keep model filenames and other workflow-specific inputs as ordinary values.

The workflow must save an MP4, WebM, or MOV file and report it under `videos`, `gifs`, or `images` in ComfyUI history outputs. Cutline downloads that output as the shot clip. A workflow that only produces still images cannot supply a video clip.

Workflows and generated files live in ignored local data; no model-specific workflow is bundled.
