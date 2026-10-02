from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
PROJECTS_ROOT = REPO_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_prefix="MOVIE_",
        extra="ignore",
    )

    app_name: str = "Cutline Studio API"
    data_dir: Path = BACKEND_ROOT / "data"
    text_provider: str = "mock"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:8b"
    compatible_base_url: str = "http://127.0.0.1:6969/v1"
    compatible_model: str = "gemini-3-pro-plus"
    compatible_api_key: str = ""
    gemini_web_base_url: str = "http://127.0.0.1:4981/openai/v1"
    gemini_web_model: str = "gemini-advanced"
    image_providers: str = "chatgpt_web,gemini_web"
    image_provider: str | None = None  # Backward-compatible alias for MOVIE_IMAGE_PROVIDER
    image_provider_timeout_seconds: int = 240
    gemini_web_image_model: str = "gemini-3-pro-image-preview-11-2025"
    chatgpt_web_base_url: str = "http://127.0.0.1:8791/v1"
    chatgpt_web_model: str = "chatgpt-5.6-sol-high-web"
    chatgpt_web_api_key: str = ""
    video_provider: str = "runway"
    flowkit_base_url: str = "http://127.0.0.1:8100/api/flow"
    flowkit_project_id: str = ""
    flowkit_root: Path = PROJECTS_ROOT / "flowkit"
    flowkit_timeout_seconds: int = 900
    flowkit_model_family: str = "veo"
    comfyui_base_url: str = "http://127.0.0.1:8188"
    comfyui_workflow_path: Path = BACKEND_ROOT / "data" / "comfyui_i2v_workflow.json"
    comfyui_timeout_seconds: int = 1800
    video_fps: int = 16
    runway_api_key: str = ""
    # gen4_turbo is cheaper/faster for long-form drafts; set gen4.5 for highest quality.
    runway_model: str = "gen4_turbo"
    runway_ratio_landscape: str = "1280:720"
    runway_ratio_portrait: str = "720:1280"
    runway_timeout_seconds: int = 900
    # Cap Runway clip length at 5s (vs 10) to control cost on long films.
    runway_max_duration: int = 5
    vibevoice_root: Path = PROJECTS_ROOT / "VibeVoice"
    vibevoice_model: str = "microsoft/VibeVoice-1.5B"
    vibevoice_python: str = str(PROJECTS_ROOT / "VibeVoice" / ".venv" / "bin" / "python") if (PROJECTS_ROOT / "VibeVoice" / ".venv" / "bin" / "python").is_file() else "python"
    vibevoice_mode: str = "subprocess"
    vibevoice_timeout_seconds: int = 600
    elevenlabs_api_key: str = ""
    elevenlabs_model: str = "eleven_multilingual_v2"
    openai_api_key: str = ""
    export_width_landscape: int = 1920
    export_height_landscape: int = 1080
    export_width_shorts: int = 1080
    export_height_shorts: int = 1920
    publish_mode: str = "mock"
    youtube_access_token: str = ""
    youtube_channel_id: str = ""
    facebook_page_access_token: str = ""
    facebook_page_id: str = ""
    # OAuth app credentials for in-app connect flow
    google_client_id: str = ""
    google_client_secret: str = ""
    facebook_app_id: str = ""
    facebook_app_secret: str = ""
    # OAuth callback and opener origins
    public_base_url: str = "http://127.0.0.1:8000"
    frontend_origin: str = "http://127.0.0.1:5173"
    oauth_state_ttl_seconds: int = 600

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'movie_creator.db'}"

    @property
    def queue_path(self) -> Path:
        return self.data_dir / "worker_queue.db"

    @property
    def chatgpt_queue_path(self) -> Path:
        return self.data_dir / "chatgpt_worker_queue.db"

    @property
    def gemini_queue_path(self) -> Path:
        return self.data_dir / "gemini_worker_queue.db"

    @property
    def video_queue_path(self) -> Path:
        return self.data_dir / "video_worker_queue.db"

    @property
    def image_queue_path(self) -> Path:
        return self.data_dir / "image_worker_queue.db"

    @property
    def image_provider_order(self) -> list[str]:
        supported = {"chatgpt_web", "gemini_web"}
        raw = self.image_provider if (self.image_provider and self.image_provider.strip()) else self.image_providers
        providers = [item.strip() for item in raw.split(",") if item.strip()]
        return [item for item in providers if item in supported]

    @property
    def media_dir(self) -> Path:
        path = self.data_dir / "media"
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
