import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def validate_single_episode(script: str) -> str:
    headings = re.findall(r"(?im)^\s*(?:#{1,6}\s*)?EPISODE\s+\d+\s*[—–:-]", script)
    if len(headings) > 1:
        raise ValueError("Import one episode at a time. Split the season document and use New Episode for each episode.")
    return script


class EpisodeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    script: str = Field(min_length=1)
    target_duration_seconds: int = Field(default=60, ge=0, le=1800)


    _single_episode = field_validator("script")(validate_single_episode)

class EpisodeUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    synopsis: str | None = None
    status: Literal["draft", "review", "shot_plan", "approved", "archived"] | None = None


class EpisodeSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    episode_number: int
    title: str
    synopsis: str | None = None
    status: str
    shot_count: int = 0
    duration_ms: int = 0
    created_at: datetime
    updated_at: datetime


class CompilationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    episode_ids: list[str] = Field(min_length=1)
    settings: dict[str, Any] = Field(default_factory=dict)


class CompilationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    episode_ids: list[str] | None = None
    settings: dict[str, Any] | None = None


class CompilationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    title: str
    description: str | None = None
    episode_ids: list[str] = []
    settings: dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime
    duration_ms: int = 0
    clip_count: int = 0


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    episode_label: str | None = Field(default=None, min_length=1, max_length=32)
    aspect_ratio: Literal["16:9", "9:16"] | None = None
    target_duration_seconds: int | None = Field(default=None, ge=0, le=1800)
    text_provider: Literal[
        "mock", "ollama", "gemini_web", "chatgpt_web", "compatible"
    ] | None = None
    flow_project_id: str | None = Field(default=None, max_length=120)


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    aspect_ratio: Literal["16:9", "9:16"] = "16:9"
    target_duration_seconds: int = Field(default=0, ge=0, le=1800)
    text_provider: Literal[
        "mock", "ollama", "gemini_web", "chatgpt_web", "compatible"
    ] = "mock"
    episode_label: str = "Episode"
    script: str = Field(min_length=1)


    _single_episode = field_validator("script")(validate_single_episode)

class ProjectContinuationCreate(BaseModel):
    script: str = Field(min_length=1)
    target_duration_seconds: int = Field(default=30, ge=5, le=1800)


    _single_episode = field_validator("script")(validate_single_episode)

class ProjectSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    status: str
    aspect_ratio: str
    target_duration_seconds: int
    text_provider: str
    episode_label: str = "Episode"
    flow_project_id: str | None = None
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
    color_boost_strength: float = 0.0
    can_undo_color: bool = False
    can_redo_color: bool = False


class ShotMediaVariantRead(ShotImageRead):
    media_type: Literal["image", "clip"]
    aspect_ratio: Literal["16:9", "9:16"]


class ShotClipRead(ShotImageRead):
    pass


class ShotContinuityFrameRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    aspect_ratio: Literal["16:9", "9:16"]
    url: str
    mime_type: str
    source_shot_id: str
    source_shot_title: str
    source_time_ms: int
    created_at: datetime
    color_boost_strength: float = 0.0
    can_undo_color: bool = False
    can_redo_color: bool = False


class ShotImageReferenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    aspect_ratio: Literal["16:9", "9:16"]
    filename: str
    url: str
    mime_type: str
    source_shot_id: str
    source_shot_title: str
    source_media_id: str
    source_media_kind: str
    source_media_type: Literal["image", "clip"]
    source_aspect_ratio: str
    source_time_ms: int | None
    mode: Literal["visual_continuity"]
    created_at: datetime
    color_boost_strength: float = 0.0
    can_undo_color: bool = False
    can_redo_color: bool = False


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
    conversation_url: str | None = None
    created_at: datetime


class CharacterContinuityStatus(BaseModel):
    character_id: str
    name: str
    has_reference: bool
    reference_ids: list[str] = []


class ShotContinuityStatus(BaseModel):
    cast_warnings: list[str] = []
    snapshot: dict[str, Any] | None = None
    characters: list[CharacterContinuityStatus] = []
    missing_character_references: list[str] = []
    missing_fields: list[str] = []
    ready: bool = True


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
    continuity_snapshot: dict[str, Any] | None = None
    continuity: ShotContinuityStatus = Field(default_factory=ShotContinuityStatus)
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
    continuity_frames: list[ShotContinuityFrameRead] = []
    image_references: list[ShotImageReferenceRead] = []
    conversation_url: str | None = None


class ShotCreate(BaseModel):
    title: str = Field(default="", max_length=200)
    action: str = Field(default="", max_length=10000)
    dialogue: list[dict[str, Any]] = Field(default_factory=list)
    sound: str = Field(default="", max_length=1000)
    duration_ms: int = Field(default=5000, ge=1000, le=3_600_000)

    @field_validator("duration_ms", mode="before")
    @classmethod
    def _coerce_duration(cls, value: Any) -> Any:
        if isinstance(value, (int, float)):
            return round(value)
        return value
    framing: str = Field(default="Medium", min_length=1, max_length=1000)
    image_prompt: str = Field(default="", max_length=8000)
    video_prompt: str = Field(default="", max_length=8000)
    title_card_text: str = Field(default="", max_length=500)
    character_ids: list[str] = Field(default_factory=list)
    insert_after_shot_id: str | None = None


class ShotUpdate(BaseModel):
    framing: str = Field(min_length=1, max_length=1000)
    duration_ms: int = Field(ge=1000, le=3_600_000)

    @field_validator("duration_ms", mode="before")
    @classmethod
    def _coerce_duration(cls, value: Any) -> Any:
        if isinstance(value, (int, float)):
            return round(value)
        return value
    image_prompt: str = Field(min_length=1, max_length=8000)
    video_prompt: str = Field(default="", max_length=8000)
    title_card_text: str = Field(default="", max_length=500)
    character_ids: list[str] | None = None
    dialogue: list[dict[str, Any]] | None = None
    title: str | None = None
    action: str | None = None
    sound: str | None = None


class SceneUpdate(BaseModel):
    location: str | None = Field(default=None, min_length=1, max_length=160)
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
    variant: Literal["landscape", "shorts", "both"] = "landscape"
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


class ScriptVersionRestoreRequest(BaseModel):
    expected_version_id: str
    expected_fingerprint: str


class ScriptVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    episode_id: str | None = None
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
    view_type: str = "main"
    created_at: datetime


class VoicePresetRead(BaseModel):
    id: str
    provider: str
    name: str
    gender: Literal["female", "male", "neutral"] = "neutral"
    language: str = "en"
    description: str = ""
    preview_url: str | None = None


class VoiceProviderInfo(BaseModel):
    id: str
    label: str
    configured: bool
    available: bool
    detail: str
    voices: list[VoicePresetRead] = []


class VoiceAuditionRequest(BaseModel):
    provider: str = "vibevoice"
    voice_id: str
    text: str = Field(min_length=1, max_length=500)
    character_id: str | None = None
    settings: dict[str, Any] = {}


class VoiceAuditionResponse(BaseModel):
    url: str
    provider: str
    voice_id: str
    duration_ms: int


class CharacterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    kind: Literal["character", "scene", "prop"] = "character"
    notes: str
    voice_description: str = ""
    voice_provider: str = "vibevoice"
    voice_id: str | None = None
    voice_settings: dict[str, Any] = {}
    created_at: datetime
    references: list[CharacterReferenceRead] = []


class CharacterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["character", "scene", "prop"] = "character"
    notes: str = Field(default="", max_length=2000)
    voice_description: str = Field(default="", max_length=1000)
    voice_provider: str = "vibevoice"
    voice_id: str | None = None
    voice_settings: dict[str, Any] = {}


class CharacterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    kind: Literal["character", "scene", "prop"] | None = None
    notes: str | None = Field(default=None, max_length=2000)
    voice_description: str | None = Field(default=None, max_length=1000)
    voice_provider: str | None = Field(default=None, max_length=40)
    voice_id: str | None = Field(default=None, max_length=120)
    voice_settings: dict[str, Any] | None = None


class ShotImageGenerateRequest(BaseModel):
    character_ids: list[str] | None = None
    refresh_continuity: bool = False
    image_reference_id: str | None = Field(default=None, min_length=1)
    aspect_ratio: str | None = None
    use_default_image_reference: bool = False
    both_aspects: bool = False
    generate_video: bool = False
    include_cast_references: bool = False
    chatgpt_dual: bool = False


class ShotClipGenerateRequest(BaseModel):
    include_cast_references: bool = False
    both_aspects: bool = False
    video_mode: Literal["i2v", "r2v"] = "i2v"
    include_scene_reference: bool = True
    element_ids: list[str] | None = None



class PublishProviderStatus(BaseModel):
    id: Literal["youtube", "facebook"]
    label: str
    configured: bool
    available: bool
    detail: str
    privacy_options: list[str]


class PublishAssetRead(BaseModel):
    id: str
    scope: Literal["final", "scene", "shot"]
    label: str
    source_ref: str | None = None
    variant: Literal["landscape", "shorts"]
    aspect_ratio: str
    duration_ms: int
    ready: bool
    missing_count: int = 0
    url: str | None = None


class PublishPlanRead(BaseModel):
    variant: Literal["landscape", "shorts"]
    aspect_ratio: str
    providers: list[PublishProviderStatus]
    final: list[PublishAssetRead] = []
    scenes: list[PublishAssetRead] = []
    shots: list[PublishAssetRead] = []


class ReleaseKitTitleOption(BaseModel):
    id: str | None = None
    text: str = Field(max_length=180)
    angle: str = "custom"
    custom: bool = False


class ReleaseKitCoverIdea(BaseModel):
    id: str | None = None
    concept: str = Field(default="", max_length=400)
    headline: str = Field(default="", max_length=60)
    prompt: str = Field(default="", max_length=2000)


class ReleaseKitUpdate(BaseModel):
    brief: str | None = Field(default=None, max_length=4000)
    title_options: list[ReleaseKitTitleOption] | None = None
    selected_title_id: str | None = None
    description: str | None = Field(default=None, max_length=5000)
    tags: list[str] | None = None
    cover_ideas: list[ReleaseKitCoverIdea] | None = None
    selected_cover_id: str | None = None


class ReleaseKitFrameCreate(BaseModel):
    source: "TimelineSource"
    source_ms: int = Field(ge=0)
    timeline_ms: int = Field(default=0, ge=0)
    label: str = Field(default="", max_length=120)


class ReleaseKitSuggest(BaseModel):
    provider: Literal["chatgpt_web", "gemini_web", "ollama", "compatible", "mock"] | None = None


class ReleaseKitSuggestHooks(BaseModel):
    prompt: str | None = Field(default=None, max_length=2000)


class ReleaseKitCoverCreate(BaseModel):
    idea_id: str | None = None
    headline: str | None = Field(default=None, max_length=60)
    prompt: str | None = Field(default=None, max_length=2000)
    provider: Literal["chatgpt_web", "gemini_web"] | None = None


class ReleaseKitPromoteFrame(BaseModel):
    headline: str | None = Field(default=None, max_length=60)
    style: Literal["natural", "punch", "vivid", "drama", "warm"] | None = "punch"


class PublishCreate(BaseModel):
    platforms: list[Literal["youtube", "facebook"]]
    scope: Literal["final", "scenes", "shots"] = "final"
    variant: Literal["landscape", "shorts"] = "landscape"
    title: str = Field(min_length=1, max_length=180)
    description: str = Field(default="", max_length=5000)
    tags: list[str] = []
    privacy: dict[Literal["youtube", "facebook"], str] = {}
    selected_asset_ids: list[str] = []
    scheduled_at: str | None = None
    schedule_interval_hours: float | None = None
    made_for_kids: bool = False
    platform_options: dict[str, dict[str, Any]] = {}
    release_kit_id: str | None = None
    cover_id: str | None = None

    @field_validator("tags", mode="after")
    @classmethod
    def clean_tags(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            tag = str(item).strip().lstrip("#")
            if tag and tag not in cleaned:
                cleaned.append(tag[:60])
        return cleaned[:20]


class PublishRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    job_id: str | None = None
    scope: str
    variant: str
    platforms: list[str]
    title: str
    description: str
    tags: list[str]
    privacy: dict[str, Any]
    selected_asset_ids: list[str]
    scheduled_at: str | None = None
    schedule_interval_hours: float | None = None
    made_for_kids: bool
    options: dict[str, Any]
    status: str
    results: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectSummary):
    episodes: list[EpisodeSummary] = []
    current_episode_id: str | None = None
    current_version: ScriptVersionRead | None = None
    final_cut: ProjectFinalCutRead | None = None
    variant_exports: list[ProjectVariantExportRead] = []
    characters: list[CharacterRead] = []
    publishes: list[PublishRecordRead] = []
    compilations: list[CompilationRead] = []


class TimelineSource(BaseModel):
    kind: Literal["shot_clip", "shot_image", "shot_audio", "asset"]
    ref_id: str


class TimelineVariantGeometry(BaseModel):
    source_aspect: Literal["auto", "16:9", "9:16"] = "auto"
    fit: Literal["cover", "contain"] = "cover"
    focal_x: float = Field(default=0.5, ge=0, le=1)
    focal_y: float = Field(default=0.5, ge=0, le=1)
    zoom: float = Field(default=1, ge=1, le=3)
    position: Literal["top-left", "top", "top-right", "center", "bottom-left", "bottom", "bottom-right", "full-frame"] = "center"
    scale: float = Field(default=1, ge=0.05, le=2)
    crop_top: float = Field(default=0, ge=0, le=0.85)
    crop_bottom: float = Field(default=0, ge=0, le=0.85)
    crop_left: float = Field(default=0, ge=0, le=0.85)
    crop_right: float = Field(default=0, ge=0, le=0.85)
    offset_x: float = Field(default=0, ge=-1.0, le=1.0)
    offset_y: float = Field(default=0, ge=-1.0, le=1.0)
    lut: str = "none"
    lut_strength: float = Field(default=1.0, ge=0.0, le=1.0)
    # Frame radius/width are fractions of the output canvas width so preview (cqw) and export match.
    frame_style: Literal["none", "solid", "double", "dashed", "glow", "neon"] = "none"
    frame_radius: float = Field(default=0.0, ge=0.0, le=0.25)
    frame_width: float = Field(default=0.008, ge=0.0, le=0.04)
    frame_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")


TransitionKind = Literal[
    "cut", "fade", "dissolve", "fadeblack", "fadewhite", "wipeleft", "wiperight", "slideleft", "slideright",
]


class TimelineTransition(BaseModel):
    kind: TransitionKind = "cut"
    duration_ms: int = Field(default=500, ge=100, le=2000)

    @field_validator("duration_ms", mode="before")
    @classmethod
    def _coerce_transition_ms(cls, value: Any) -> Any:
        return round(value) if isinstance(value, (int, float)) else value


class TimelinePrimaryItem(BaseModel):
    id: str
    kind: Literal["video", "image", "title"]
    label: str = Field(default="Timeline item", max_length=200)
    source: TimelineSource | None = None
    duration_ms: int = Field(ge=100, le=3_600_000)
    source_in_ms: int = Field(default=0, ge=0)
    source_out_ms: int | None = Field(default=None, ge=1)
    text: str = Field(default="", max_length=1000)
    mute_audio: bool = False
    loop: bool = False
    # Transition into this clip from the previous one; None uses the document default.
    transition: TimelineTransition | None = None
    variant: dict[str, TimelineVariantGeometry] = {}

    @field_validator("duration_ms", "source_in_ms", "source_out_ms", mode="before")
    @classmethod
    def _coerce_primary_ms(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return round(value)
        return value


class TimelineVideoOverlay(BaseModel):
    id: str
    label: str = Field(default="Video layer", max_length=200)
    source: TimelineSource
    start_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=100, le=3_600_000)
    # Editor row on its track; display only, so the clip keeps its row until dragged up or down.
    lane: int | None = Field(default=None, ge=0, le=31)
    source_in_ms: int = Field(default=0, ge=0)
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    volume: float = Field(default=1.0, ge=0.0, le=2.0)
    mute_audio: bool = False
    variant: dict[str, TimelineVariantGeometry] = {}

    @field_validator("start_ms", "duration_ms", "source_in_ms", mode="before")
    @classmethod
    def _coerce_video_overlay_ms(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return round(value)
        return value


class TimelineImageOverlay(BaseModel):
    id: str
    source: TimelineSource
    start_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=100, le=3_600_000)
    # Editor row on its track; display only, so the clip keeps its row until dragged up or down.
    lane: int | None = Field(default=None, ge=0, le=31)
    opacity: float = Field(default=1, ge=0, le=1)
    variant: dict[str, TimelineVariantGeometry] = {}

    @field_validator("start_ms", "duration_ms", mode="before")
    @classmethod
    def _coerce_overlay_ms(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return round(value)
        return value


class TimelineTextOverlay(BaseModel):
    id: str
    text: str = Field(min_length=1, max_length=1000)
    start_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=100, le=3_600_000)

    @field_validator("start_ms", "duration_ms", mode="before")
    @classmethod
    def _coerce_text_ms(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return round(value)
        return value
    presentation: Literal["overlay", "full-frame"] = "overlay"
    size: Literal["small", "medium", "large"] = "medium"
    color: Literal["white", "black"] = "white"
    background: Literal["none", "shadow", "solid", "translucent"] = "shadow"
    variant: dict[str, TimelineVariantGeometry] = {}


class TimelineAudioOverlay(BaseModel):
    id: str
    label: str = Field(default="Audio clip", max_length=200)
    source: TimelineSource
    start_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=100, le=3_600_000)
    # Editor row on its track; display only, so the clip keeps its row until dragged up or down.
    lane: int | None = Field(default=None, ge=0, le=31)
    source_in_ms: int = Field(default=0, ge=0)
    volume: float = Field(default=1.0, ge=0.0, le=2.0)
    mute_audio: bool = False
    # Repeat the audio from source_in_ms when the clip is longer than what's left of the file.
    loop: bool = True

    @field_validator("start_ms", "duration_ms", "source_in_ms", mode="before")
    @classmethod
    def _coerce_audio_ms(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return round(value)
        return value


class TimelineCanvasLayout(BaseModel):
    """Per-document canvas template for the active edit (shorts / landscape)."""

    template: str = "full_bleed"
    matte: Literal["solid", "blur", "gradient", "duotone"] = "solid"
    matte_color: str = Field(default="#0B0D12", max_length=20)
    matte_color_b: str = Field(default="#1A2233", max_length=20)
    top_text: str = Field(default="", max_length=280)
    bottom_text: str = Field(default="", max_length=280)
    brand_text: str = Field(default="", max_length=80)
    accent_bar: bool = False
    accent_color: str = Field(default="#39D0FF", max_length=20)


class TimelineDocument(BaseModel):
    version: Literal[1] = 1
    primary: list[TimelinePrimaryItem]
    video_overlays: list[TimelineVideoOverlay] = []
    image_overlays: list[TimelineImageOverlay] = []
    text_overlays: list[TimelineTextOverlay] = []
    audio_overlays: list[TimelineAudioOverlay] = []
    layout: TimelineCanvasLayout = Field(default_factory=TimelineCanvasLayout)
    default_transition: TimelineTransition | None = None


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
    episode_id: str | None = None
    compilation_id: str | None = None
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

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


class JobQueueItemRead(JobRead):
    project_title: str | None = None
    elapsed_seconds: float | None = None


class SuggestionDecision(BaseModel):
    decision: Literal["accepted", "rejected"]


class DeepReviewFindingDecision(BaseModel):
    decision: Literal["accepted", "rejected", "addressed"]
    expected_version_id: str | None = None
    patches: list[dict[str, Any]] | None = None


class DeepReviewUndoRequest(BaseModel):
    expected_version_id: str


class ProjectApproval(BaseModel):
    approved: bool = True


class ProviderStatus(BaseModel):
    id: str
    label: str
    configured: bool
    available: bool
    model: str
    detail: str
    active_preset: str | None = None
    presets: list[dict[str, str]] | None = None


class FlowModelUpdateRequest(BaseModel):
    preset: str


class ImageProviderOrderUpdate(BaseModel):
    order: list[str] = Field(min_length=1)


class ImageProviderStatus(ProviderStatus):
    supports_image_references: bool = False
    priority: int
    image_generation: bool
    connected: bool = False
    verified: bool = False


class StemSeparatorStatus(BaseModel):
    id: str
    label: str
    configured: bool
    available: bool
    selected: bool
    detail: str


class StemSeparatorUpdate(BaseModel):
    provider: Literal["lalal", "demucs", "uvr"]


class VideoProviderSelect(BaseModel):
    provider: Literal["flowkit", "gemini_web", "comfyui", "runway"]


class StemSeparationCreate(BaseModel):
    source: TimelineSource
    source_in_ms: int = Field(default=0, ge=0)
    duration_ms: int = Field(ge=500, le=30 * 60 * 1000)
    provider: Literal["lalal", "demucs", "uvr"] | None = None
    label: str = Field(default="Clip", max_length=120)
    episode_id: str | None = None


class LibraryAudioCreate(BaseModel):
    source: TimelineSource
    source_in_ms: int = Field(default=0, ge=0)
    duration_ms: int = Field(ge=100, le=60 * 60 * 1000)
    label: str = Field(min_length=1, max_length=160)


class LibraryAudioRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    label: str
    url: str
    mime_type: str
    duration_ms: int
    created_at: datetime


class StemSeparationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    job_id: str | None
    provider: str
    source_kind: str
    source_ref_id: str
    label: str
    source_in_ms: int
    duration_ms: int
    status: str
    music_asset_id: str | None
    voice_asset_id: str | None
    music_asset: TimelineAssetRead | None = None
    voice_asset: TimelineAssetRead | None = None
    error: str | None
    created_at: datetime


class StemSeparationStarted(BaseModel):
    separation: StemSeparationRead
    job: JobRead


ReleaseKitFrameCreate.model_rebuild()
