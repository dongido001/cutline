from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    aspect_ratio: Literal["16:9", "9:16"] = "16:9"
    target_duration_seconds: int = Field(default=0, ge=0, le=1800)
    text_provider: Literal[
        "mock", "ollama", "gemini_web", "chatgpt_web", "compatible"
    ] = "mock"
    script: str = Field(min_length=1)


class ProjectContinuationCreate(BaseModel):
    script: str = Field(min_length=1)
    target_duration_seconds: int = Field(default=30, ge=5, le=1800)


class ProjectSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    status: str
    aspect_ratio: str
    target_duration_seconds: int
    text_provider: str
    created_at: datetime
    updated_at: datetime


class ProjectFinalCutRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    url: str
    mime_type: str
    duration_ms: int
    width: int
    height: int
    clip_count: int
    fallback_count: int
    created_at: datetime


class ShotImageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    url: str
    mime_type: str
    source: str
    provider: str
    prompt: str
    created_at: datetime


class ShotMediaVariantRead(ShotImageRead):
    media_type: Literal["image", "clip"]
    aspect_ratio: Literal["16:9", "9:16"]


class ShotClipRead(ShotImageRead):
    pass


class ShotTakeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    media_type: Literal["image", "clip", "audio"]
    aspect_ratio: str
    url: str
    mime_type: str
    source: str
    provider: str
    prompt: str
    settings: dict[str, Any] = {}
    selected: bool
    created_at: datetime


class ShotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_index: int
    title: str
    start_ms: int | None
    end_ms: int | None
    action: str
    dialogue: list[dict[str, Any]]
    sound: str
    duration_ms: int
    framing: str
    image_prompt: str
    video_prompt: str
    title_card_text: str
    character_ids: list[str] = []
    status: str
    selected_image_take_id: str | None = None
    selected_clip_take_id: str | None = None
    selected_audio_take_id: str | None = None
    dialogue_audio_filename: str | None = None
    dialogue_audio_mime: str | None = None
    image: ShotImageRead | None = None
    clip: ShotClipRead | None = None
    media_variants: list[ShotMediaVariantRead] = []
    takes: list[ShotTakeRead] = []


class ShotUpdate(BaseModel):
    framing: str = Field(min_length=1, max_length=80)
    duration_ms: int = Field(ge=1000, le=30000)
    image_prompt: str = Field(min_length=1, max_length=8000)
    video_prompt: str = Field(default="", max_length=8000)
    title_card_text: str = Field(default="", max_length=500)
    character_ids: list[str] | None = None


class SceneUpdate(BaseModel):
    set_notes: str = Field(default="", max_length=4000)
    lighting_notes: str = Field(default="", max_length=4000)
    wardrobe_notes: str = Field(default="", max_length=4000)
    props_notes: str = Field(default="", max_length=4000)


class SceneRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_index: int
    title: str
    location: str
    time_of_day: str
    start_ms: int | None
    end_ms: int | None
    action: str
    dialogue: list[dict[str, Any]]
    set_notes: str = ""
    lighting_notes: str = ""
    wardrobe_notes: str = ""
    props_notes: str = ""
    shots: list[ShotRead] = []


class CreateDraftRequest(BaseModel):
    variant: Literal["landscape", "shorts"] = "landscape"
    retry_failed_only: bool = False


class TakeSelectRequest(BaseModel):
    take_id: str


class SuggestionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    category: str
    severity: str
    title: str
    reason: str
    target: dict[str, Any]
    before_value: Any = None
    after_value: Any = None
    status: str


class ScriptVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    version_number: int
    source_text: str
    status: str
    scenes: list[SceneRead] = []
    suggestions: list[SuggestionRead] = []


class ProjectVariantExportRead(ProjectFinalCutRead):
    variant: Literal["landscape", "shorts"]
    timeline_revision: int
    is_stale: bool = False


class CharacterReferenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    url: str
    mime_type: str
    created_at: datetime


class CharacterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    notes: str
    created_at: datetime
    references: list[CharacterReferenceRead] = []


class CharacterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    notes: str = Field(default="", max_length=2000)


class CharacterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)


class ShotImageGenerateRequest(BaseModel):
    character_ids: list[str] = []


class ProjectDetail(ProjectSummary):
    current_version: ScriptVersionRead | None = None
    final_cut: ProjectFinalCutRead | None = None
    variant_exports: list[ProjectVariantExportRead] = []
    characters: list[CharacterRead] = []


class TimelineSource(BaseModel):
    kind: Literal["shot_clip", "shot_image", "asset"]
    ref_id: str


class TimelineVariantGeometry(BaseModel):
    source_aspect: Literal["auto", "16:9", "9:16"] = "auto"
    fit: Literal["cover", "contain"] = "cover"
    focal_x: float = Field(default=0.5, ge=0, le=1)
    focal_y: float = Field(default=0.5, ge=0, le=1)
    zoom: float = Field(default=1, ge=1, le=3)
    position: Literal["top-left", "top", "top-right", "center", "bottom-left", "bottom", "bottom-right"] = "center"
    scale: float = Field(default=1, ge=0.05, le=2)


class TimelinePrimaryItem(BaseModel):
    id: str
    kind: Literal["video", "image", "title"]
    label: str = Field(default="Timeline item", max_length=200)
    source: TimelineSource | None = None
    duration_ms: int = Field(ge=100, le=300_000)
    source_in_ms: int = Field(default=0, ge=0)
    source_out_ms: int | None = Field(default=None, ge=1)
    text: str = Field(default="", max_length=1000)
    variant: dict[str, TimelineVariantGeometry] = {}


class TimelineImageOverlay(BaseModel):
    id: str
    source: TimelineSource
    start_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=100, le=300_000)
    opacity: float = Field(default=1, ge=0, le=1)
    variant: dict[str, TimelineVariantGeometry] = {}


class TimelineTextOverlay(BaseModel):
    id: str
    text: str = Field(min_length=1, max_length=1000)
    start_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=100, le=300_000)
    presentation: Literal["overlay", "full-frame"] = "overlay"
    size: Literal["small", "medium", "large"] = "medium"
    color: Literal["white", "black"] = "white"
    background: Literal["none", "shadow", "solid", "translucent"] = "shadow"
    variant: dict[str, TimelineVariantGeometry] = {}


class TimelineDocument(BaseModel):
    version: Literal[1] = 1
    primary: list[TimelinePrimaryItem]
    image_overlays: list[TimelineImageOverlay] = []
    text_overlays: list[TimelineTextOverlay] = []


class TimelineUpdate(BaseModel):
    revision: int = Field(ge=1)
    document: TimelineDocument
    edit: Literal["landscape", "shorts"] = "landscape"


class TimelineAssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    url: str
    mime_type: str
    label: str
    created_at: datetime


class ProjectTimelineRead(BaseModel):
    revision: int
    document: TimelineDocument
    edit: Literal["landscape", "shorts"] = "landscape"
    assets: list[TimelineAssetRead] = []


class MediaVariantRequest(BaseModel):
    aspect_ratio: Literal["16:9", "9:16"]
    prompt_override: str | None = Field(default=None, max_length=8000)


class FinalCutRenderRequest(BaseModel):
    variant: Literal["landscape", "shorts"] = "landscape"


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    kind: str
    status: str
    progress: float
    message: str
    error: str | None
    provider: str | None = None
    provider_phase: str | None = None
    conversation_url: str | None = None
    attempts: list[dict[str, Any]] = []
    queue_position: int | None = None
    created_at: datetime
    updated_at: datetime


class SuggestionDecision(BaseModel):
    decision: Literal["accepted", "rejected"]


class ProjectApproval(BaseModel):
    approved: bool = True


class ProviderStatus(BaseModel):
    id: str
    label: str
    configured: bool
    available: bool
    model: str
    detail: str


class ImageProviderStatus(ProviderStatus):
    priority: int
    image_generation: bool
    connected: bool = False
    verified: bool = False
