from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[2]


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
    image_provider_timeout_seconds: int = 180
    gemini_web_image_model: str = "gemini-3-pro-image-preview-11-2025"
    chatgpt_web_base_url: str = "http://127.0.0.1:8791/v1"
    chatgpt_web_model: str = "chatgpt-5.6-sol-high-web"
    chatgpt_web_api_key: str = ""
    video_provider: str = "runway"
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
    vibevoice_root: Path = Path("/Users/gideon/Projects/VibeVoice")
    vibevoice_model: str = "microsoft/VibeVoice-1.5B"
    vibevoice_python: str = "python"
    vibevoice_mode: str = "subprocess"
    vibevoice_timeout_seconds: int = 600
    export_width_landscape: int = 1920
    export_height_landscape: int = 1080
    export_width_shorts: int = 1080
    export_height_shorts: int = 1920

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
        providers = [item.strip() for item in self.image_providers.split(",") if item.strip()]
        return [item for item in providers if item in supported]

    @property
    def media_dir(self) -> Path:
        path = self.data_dir / "media"
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
