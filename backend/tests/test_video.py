from backend.video import find_video_output, frame_count, render_workflow


def test_flowkit_omni_duration_maps_to_supported_bucket(monkeypatch, tmp_path):
    from backend import video

    captured = {}

    class FakeResponse:
        def __init__(self, payload=None, content=b"video-bytes"):
            self._payload = payload
            self.content = content
            self.headers = {"content-type": "video/mp4"}

        def json(self):
            return self._payload

        def raise_for_status(self):
            return None

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, **kwargs):
            if url.endswith("/upload-image"):
                return FakeResponse({"media_id": "image-media", "project_id": "project-1"})
            if url.endswith("/generate-video"):
                captured.update(kwargs["json"])
                return FakeResponse({"operations": [{"operation": {"name": "op-1"}}], "project_id": "project-1"})
            if url.endswith("/check-status"):
                return FakeResponse({
                    "operations": [{
                        "operation": {
                            "name": "op-1",
                            "metadata": {"video": {"mediaId": "video-media", "fifeUrl": "https://example.test/video.mp4"}},
                        },
                        "status": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
                    }]
                })
            raise AssertionError(url)

        def get(self, url):
            assert url == "https://example.test/video.mp4"
            return FakeResponse()

    monkeypatch.setattr(video.httpx, "Client", lambda **kwargs: FakeClient())
    monkeypatch.setattr(video.settings, "data_dir", tmp_path)
    monkeypatch.setattr(video.settings, "flowkit_base_url", "http://flowkit.test/api/flow")
    monkeypatch.setattr(video.settings, "flowkit_project_id", "")
    monkeypatch.setattr(video.settings, "flowkit_model_family", "omni_flash")

    image_path = tmp_path / "frame.png"
    image_path.write_bytes(b"png")

    video.generate_flowkit_clip(
        image_path=image_path,
        prompt="slow pan",
        duration_ms=5000,
        aspect_ratio="16:9",
        scene_id="scene-1",
    )

    assert captured["duration_s"] == 4



def test_frame_count_matches_video_model_constraint() -> None:
    count = frame_count(6000, 16)

    assert count <= 96
    assert count % 4 == 1


def test_render_workflow_replaces_nested_values_and_keeps_numeric_types() -> None:
    workflow = {
        "1": {
            "inputs": {
                "image": "{{INPUT_IMAGE}}",
                "prompt": "Move: {{PROMPT}}",
                "width": "{{WIDTH}}",
                "frames": "{{FRAME_COUNT}}",
            }
        }
    }

    rendered = render_workflow(
        workflow,
        {
            "{{INPUT_IMAGE}}": "input/frame.png",
            "{{PROMPT}}": "slow push in",
            "{{WIDTH}}": 832,
            "{{FRAME_COUNT}}": 93,
        },
    )

    assert rendered["1"]["inputs"] == {
        "image": "input/frame.png",
        "prompt": "Move: slow push in",
        "width": 832,
        "frames": 93,
    }


def test_find_video_output_ignores_preview_images() -> None:
    history = {
        "outputs": {
            "preview": {"images": [{"filename": "preview.png"}]},
            "save": {
                "gifs": [
                    {
                        "filename": "shot-01.mp4",
                        "subfolder": "cutline",
                        "type": "output",
                    }
                ]
            },
        }
    }

    assert find_video_output(history) == {
        "filename": "shot-01.mp4",
        "subfolder": "cutline",
        "type": "output",
    }


def test_flowkit_provider_status(monkeypatch):
    from backend import video

    monkeypatch.setattr(video.settings, "video_provider", "flowkit")
    monkeypatch.setattr(video, "flowkit_status", lambda: (True, True, "ready"))
    provider_id, label, configured, available, model, detail = video.video_provider_status()
    assert provider_id == "flowkit"
    assert label == "Google Flow (FlowKit)"
    assert configured is True
    assert available is True
    assert detail == "ready"


def test_flowkit_operation_result_exposes_video_url(monkeypatch, tmp_path):
    from backend import video

    class FakeResponse:
        def __init__(self, payload=None, content=b"video-bytes", headers=None):
            self._payload = payload
            self.content = content
            self.headers = headers or {"content-type": "video/mp4"}

        def json(self):
            return self._payload

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self):
            self.posts = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, **kwargs):
            self.posts += 1
            if url.endswith("/upload-image"):
                return FakeResponse({"media_id": "image-media", "project_id": "project-1"})
            if url.endswith("/generate-video"):
                return FakeResponse({"operations": [{"operation": {"name": "op-1"}}], "project_id": "project-1"})
            if url.endswith("/check-status"):
                return FakeResponse({
                    "operations": [{
                        "operation": {
                            "name": "op-1",
                            "metadata": {"video": {"mediaId": "video-media", "fifeUrl": "https://example.test/video.mp4"}},
                        },
                        "status": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
                    }]
                })
            raise AssertionError(url)

        def get(self, url):
            assert url == "https://example.test/video.mp4"
            return FakeResponse()

    monkeypatch.setattr(video.httpx, "Client", lambda **kwargs: FakeClient())
    monkeypatch.setattr(video.settings, "data_dir", tmp_path)
    monkeypatch.setattr(video.settings, "flowkit_base_url", "http://flowkit.test/api/flow")
    monkeypatch.setattr(video.settings, "flowkit_project_id", "")

    image_path = tmp_path / "frame.png"
    image_path.write_bytes(b"png")

    filename, mime, provider = video.generate_flowkit_clip(
        image_path=image_path,
        prompt="slow pan",
        duration_ms=6000,
        aspect_ratio="16:9",
        scene_id="scene-1",
    )

    assert provider == "flowkit"
    assert mime == "video/mp4"
    assert (tmp_path / "media" / filename).read_bytes() == b"video-bytes"


def test_generate_shot_clip_dispatches_to_flowkit(monkeypatch, tmp_path):
    from backend import video

    monkeypatch.setattr(video.settings, "video_provider", "flowkit")
    called = {}

    def fake_generate(image_path, prompt, duration_ms, aspect_ratio, scene_id=None):
        called.update(
            image_path=image_path,
            prompt=prompt,
            duration_ms=duration_ms,
            aspect_ratio=aspect_ratio,
            scene_id=scene_id,
        )
        return "clip-test.mp4", "video/mp4", "flowkit"

    monkeypatch.setattr(video, "generate_flowkit_clip", fake_generate)
    image_path = tmp_path / "frame.png"
    result = video.generate_shot_clip(
        image_path=image_path,
        prompt="A Lagos street at dusk",
        duration_ms=8000,
        aspect_ratio="16:9",
        scene_id="scene-123",
    )
    assert result == ("clip-test.mp4", "video/mp4", "flowkit")
    assert called == {
        "image_path": image_path,
        "prompt": "A Lagos street at dusk",
        "duration_ms": 8000,
        "aspect_ratio": "16:9",
        "scene_id": "scene-123",
    }


def test_flowkit_polling_retries_transient_503(monkeypatch, tmp_path):
    from backend import video

    class FakeResponse:
        def __init__(self, payload=None, content=b"video-bytes", status_code=200):
            self._payload = payload
            self.content = content
            self.headers = {"content-type": "video/mp4"}
            self.status_code = status_code

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise video.httpx.HTTPStatusError("error", request=None, response=self)

    class FakeClient:
        def __init__(self):
            self.check_polls = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, **kwargs):
            if url.endswith("/upload-image"):
                return FakeResponse({"media_id": "image-media", "project_id": "project-1"})
            if url.endswith("/generate-video"):
                return FakeResponse({"operations": [{"operation": {"name": "op-1"}}], "project_id": "project-1"})
            if url.endswith("/check-status"):
                self.check_polls += 1
                if self.check_polls == 1:
                    return FakeResponse(status_code=503)
                return FakeResponse({
                    "operations": [{
                        "operation": {
                            "name": "op-1",
                            "metadata": {"video": {"mediaId": "video-media", "fifeUrl": "https://example.test/video.mp4"}},
                        },
                        "status": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
                    }]
                })
            raise AssertionError(url)

        def get(self, url):
            return FakeResponse()

    monkeypatch.setattr(video.time, "sleep", lambda _: None)
    monkeypatch.setattr(video.httpx, "Client", lambda **kwargs: FakeClient())
    monkeypatch.setattr(video.settings, "data_dir", tmp_path)
    monkeypatch.setattr(video.settings, "flowkit_base_url", "http://flowkit.test/api/flow")
    monkeypatch.setattr(video.settings, "flowkit_project_id", "")

    image_path = tmp_path / "frame.png"
    image_path.write_bytes(b"png")

    filename, mime, provider = video.generate_flowkit_clip(
        image_path=image_path,
        prompt="slow pan",
        duration_ms=6000,
        aspect_ratio="16:9",
        scene_id="scene-1",
    )

    assert provider == "flowkit"
    assert mime == "video/mp4"
