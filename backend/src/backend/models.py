import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def new_id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), default="draft")
    aspect_ratio: Mapped[str] = mapped_column(String(12), default="16:9")
    target_duration_seconds: Mapped[int] = mapped_column(Integer, default=60)
    text_provider: Mapped[str] = mapped_column(String(24), default="mock")
    image_conversation_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    versions: Mapped[list["ScriptVersion"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    jobs: Mapped[list["Job"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    final_cut: Mapped["ProjectFinalCut | None"] = relationship(
        back_populates="project", cascade="all, delete-orphan", uselist=False
    )
    timeline: Mapped["ProjectTimeline | None"] = relationship(
        back_populates="project", cascade="all, delete-orphan", uselist=False
    )
    timeline_assets: Mapped[list["TimelineAsset"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    variant_exports: Mapped[list["ProjectVariantExport"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    characters: Mapped[list["Character"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Character.name"
    )
    episode_label: Mapped[str] = mapped_column(String(32), default="Episode")

    episodes: Mapped[list["Episode"]] = relationship(
        "Episode",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="Episode.episode_number",
    )
    publish_records: Mapped[list["PublishRecord"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="PublishRecord.created_at.desc()"
    )


class Episode(Base):
    __tablename__ = "episodes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    episode_number: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(200))
    synopsis: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    project: Mapped["Project"] = relationship("Project", back_populates="episodes")
    script_versions: Mapped[list["ScriptVersion"]] = relationship(
        "ScriptVersion",
        foreign_keys="[ScriptVersion.episode_id]",
        cascade="all, delete-orphan",
        order_by="ScriptVersion.version_number",
    )
    timeline: Mapped["ProjectTimeline | None"] = relationship(
        "ProjectTimeline",
        foreign_keys="[ProjectTimeline.episode_id]",
        cascade="all, delete-orphan",
        uselist=False,
    )
    final_cut: Mapped["ProjectFinalCut | None"] = relationship(
        "ProjectFinalCut",
        foreign_keys="[ProjectFinalCut.episode_id]",
        cascade="all, delete-orphan",
        uselist=False,
    )
    variant_exports: Mapped[list["ProjectVariantExport"]] = relationship(
        "ProjectVariantExport",
        foreign_keys="[ProjectVariantExport.episode_id]",
        cascade="all, delete-orphan",
    )


class ScriptVersion(Base):
    __tablename__ = "script_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    episode_id: Mapped[str | None] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), index=True, nullable=True
    )
    version_number: Mapped[int] = mapped_column(Integer, default=1)
    source_text: Mapped[str] = mapped_column(Text)
    structured_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="imported")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    project: Mapped[Project] = relationship(back_populates="versions", foreign_keys="[ScriptVersion.project_id]")
    episode: Mapped["Episode | None"] = relationship(back_populates="script_versions", foreign_keys="[ScriptVersion.episode_id]")
    scenes: Mapped[list["Scene"]] = relationship(
        back_populates="script_version", cascade="all, delete-orphan", order_by="Scene.order_index"
    )
    suggestions: Mapped[list["Suggestion"]] = relationship(
        back_populates="script_version", cascade="all, delete-orphan"
    )


class Scene(Base):
    __tablename__ = "scenes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    script_version_id: Mapped[str] = mapped_column(
        ForeignKey("script_versions.id", ondelete="CASCADE"), index=True
    )
    order_index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    location: Mapped[str] = mapped_column(String(160), default="Unspecified")
    time_of_day: Mapped[str] = mapped_column(String(80), default="Unspecified")
    start_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(Text, default="")
    dialogue: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    set_notes: Mapped[str] = mapped_column(Text, default="")
    lighting_notes: Mapped[str] = mapped_column(Text, default="")
    wardrobe_notes: Mapped[str] = mapped_column(Text, default="")
    props_notes: Mapped[str] = mapped_column(Text, default="")

    script_version: Mapped[ScriptVersion] = relationship(back_populates="scenes")
    shots: Mapped[list["Shot"]] = relationship(
        back_populates="scene", cascade="all, delete-orphan", order_by="Shot.order_index"
    )


class Shot(Base):
    __tablename__ = "shots"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), index=True)
    order_index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200), default="")
    start_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(Text, default="")
    dialogue: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    sound: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=5000)
    framing: Mapped[str] = mapped_column(String(80), default="Medium")
    image_prompt: Mapped[str] = mapped_column(Text, default="")
    video_prompt: Mapped[str] = mapped_column(Text, default="")
    title_card_text: Mapped[str] = mapped_column(Text, default="")
    character_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    continuity_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="planned")
    selected_image_take_id: Mapped[str | None] = mapped_column(String, nullable=True)
    selected_clip_take_id: Mapped[str | None] = mapped_column(String, nullable=True)
    selected_audio_take_id: Mapped[str | None] = mapped_column(String, nullable=True)
    dialogue_audio_filename: Mapped[str | None] = mapped_column(String(240), nullable=True)
    dialogue_audio_mime: Mapped[str | None] = mapped_column(String(80), nullable=True)
    conversation_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    scene: Mapped[Scene] = relationship(back_populates="shots")
    image: Mapped["ShotImage | None"] = relationship(
        back_populates="shot", cascade="all, delete-orphan", uselist=False
    )
    clip: Mapped["ShotClip | None"] = relationship(
        back_populates="shot", cascade="all, delete-orphan", uselist=False
    )
    continuity_frames: Mapped[list["ShotContinuityFrame"]] = relationship(
        back_populates="shot", cascade="all, delete-orphan"
    )
    image_references: Mapped[list["ShotImageReference"]] = relationship(
        back_populates="shot", cascade="all, delete-orphan"
    )
    media_variants: Mapped[list["ShotMediaVariant"]] = relationship(
        back_populates="shot", cascade="all, delete-orphan"
    )
    takes: Mapped[list["ShotTake"]] = relationship(
        back_populates="shot", cascade="all, delete-orphan", order_by="ShotTake.created_at"
    )

    @property
    def continuity(self) -> dict[str, Any]:
        from .continuity import shot_continuity_status

        return shot_continuity_status(self)


class ShotImage(Base):
    __tablename__ = "shot_images"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    shot_id: Mapped[str] = mapped_column(
        ForeignKey("shots.id", ondelete="CASCADE"), unique=True, index=True
    )
    filename: Mapped[str] = mapped_column(String(240))
    mime_type: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(24))
    provider: Mapped[str] = mapped_column(String(80))
    prompt: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    shot: Mapped[Shot] = relationship(back_populates="image")

    @property
    def url(self) -> str:
        return f"/media/{self.filename}?v={int(self.created_at.timestamp())}"


class ShotClip(Base):
    __tablename__ = "shot_clips"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    shot_id: Mapped[str] = mapped_column(
        ForeignKey("shots.id", ondelete="CASCADE"), unique=True, index=True
    )
    filename: Mapped[str] = mapped_column(String(240))
    mime_type: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(24))
    provider: Mapped[str] = mapped_column(String(80))
    prompt: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    shot: Mapped[Shot] = relationship(back_populates="clip")

    @property
    def url(self) -> str:
        return f"/media/{self.filename}"


class ShotContinuityFrame(Base):
    __tablename__ = "shot_continuity_frames"
    __table_args__ = (UniqueConstraint("shot_id", "aspect_ratio"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    shot_id: Mapped[str] = mapped_column(
        ForeignKey("shots.id", ondelete="CASCADE"), index=True
    )
    aspect_ratio: Mapped[str] = mapped_column(String(12))
    filename: Mapped[str] = mapped_column(String(240))
    mime_type: Mapped[str] = mapped_column(String(80))
    source_shot_id: Mapped[str] = mapped_column(String)
    source_shot_title: Mapped[str] = mapped_column(String(200), default="")
    source_time_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    shot: Mapped[Shot] = relationship(back_populates="continuity_frames")

    @property
    def url(self) -> str:
        return f"/media/{self.filename}?v={int(self.created_at.timestamp())}"


class ShotImageReference(Base):
    """Active image guidance; immutable asset copies also belong to jobs and takes."""

    __tablename__ = "shot_image_references"
    __table_args__ = (UniqueConstraint("shot_id", "aspect_ratio"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    shot_id: Mapped[str] = mapped_column(ForeignKey("shots.id", ondelete="CASCADE"), index=True)
    aspect_ratio: Mapped[str] = mapped_column(String(12))
    filename: Mapped[str] = mapped_column(String(240))
    mime_type: Mapped[str] = mapped_column(String(80))
    source_shot_id: Mapped[str] = mapped_column(String)
    source_shot_title: Mapped[str] = mapped_column(String(200), default="")
    source_media_id: Mapped[str] = mapped_column(String)
    source_media_kind: Mapped[str] = mapped_column(String(24))
    source_media_type: Mapped[str] = mapped_column(String(12))
    source_aspect_ratio: Mapped[str] = mapped_column(String(12))
    source_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mode: Mapped[str] = mapped_column(String(40), default="visual_continuity")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    shot: Mapped[Shot] = relationship(back_populates="image_references")

    @property
    def url(self) -> str:
        return f"/media/{self.filename}"


class ShotMediaVariant(Base):
    __tablename__ = "shot_media_variants"
    __table_args__ = (UniqueConstraint("shot_id", "media_type", "aspect_ratio"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    shot_id: Mapped[str] = mapped_column(ForeignKey("shots.id", ondelete="CASCADE"), index=True)
    media_type: Mapped[str] = mapped_column(String(12))
    aspect_ratio: Mapped[str] = mapped_column(String(12))
    filename: Mapped[str] = mapped_column(String(240))
    mime_type: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(24))
    provider: Mapped[str] = mapped_column(String(80))
    prompt: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    shot: Mapped[Shot] = relationship(back_populates="media_variants")

    @property
    def url(self) -> str:
        return f"/media/{self.filename}?v={int(self.created_at.timestamp())}"


class ShotTake(Base):
    """Selectable generation result for a shot (image, clip, or dialogue audio)."""

    __tablename__ = "shot_takes"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    shot_id: Mapped[str] = mapped_column(ForeignKey("shots.id", ondelete="CASCADE"), index=True)
    media_type: Mapped[str] = mapped_column(String(12))
    aspect_ratio: Mapped[str] = mapped_column(String(12), default="16:9")
    filename: Mapped[str] = mapped_column(String(240))
    mime_type: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(24))
    provider: Mapped[str] = mapped_column(String(80))
    prompt: Mapped[str] = mapped_column(Text, default="")
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    shot: Mapped[Shot] = relationship(back_populates="takes")

    @property
    def url(self) -> str:
        return f"/media/{self.filename}?v={int(self.created_at.timestamp())}"


class ProjectFinalCut(Base):
    __tablename__ = "project_final_cuts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    episode_id: Mapped[str | None] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), index=True, nullable=True
    )
    filename: Mapped[str] = mapped_column(String(240))
    mime_type: Mapped[str] = mapped_column(String(80), default="video/mp4")
    duration_ms: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    clip_count: Mapped[int] = mapped_column(Integer)
    fallback_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    project: Mapped[Project] = relationship(back_populates="final_cut", foreign_keys="[ProjectFinalCut.project_id]")
    episode: Mapped["Episode | None"] = relationship(back_populates="final_cut", foreign_keys="[ProjectFinalCut.episode_id]")

    @property
    def url(self) -> str:
        return f"/media/{self.filename}"


class ProjectTimeline(Base):
    __tablename__ = "project_timelines"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    episode_id: Mapped[str | None] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), index=True, nullable=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    document: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    shorts_document: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    project: Mapped[Project] = relationship(back_populates="timeline", foreign_keys="[ProjectTimeline.project_id]")
    episode: Mapped["Episode | None"] = relationship(back_populates="timeline", foreign_keys="[ProjectTimeline.episode_id]")


class TimelineAsset(Base):
    __tablename__ = "timeline_assets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(240))
    mime_type: Mapped[str] = mapped_column(String(80))
    label: Mapped[str] = mapped_column(String(160), default="Uploaded image")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    project: Mapped[Project] = relationship(back_populates="timeline_assets")

    @property
    def url(self) -> str:
        return f"/media/{self.filename}"


class ProjectVariantExport(Base):
    __tablename__ = "project_variant_exports"
    __table_args__ = (
        UniqueConstraint("project_id", "episode_id", "variant", name="uq_project_variant_exports"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    episode_id: Mapped[str | None] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), index=True, nullable=True
    )
    variant: Mapped[str] = mapped_column(String(16))
    filename: Mapped[str] = mapped_column(String(240))
    mime_type: Mapped[str] = mapped_column(String(80), default="video/mp4")
    duration_ms: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    clip_count: Mapped[int] = mapped_column(Integer)
    fallback_count: Mapped[int] = mapped_column(Integer)
    timeline_revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    project: Mapped[Project] = relationship(back_populates="variant_exports", foreign_keys="[ProjectVariantExport.project_id]")
    episode: Mapped["Episode | None"] = relationship(back_populates="variant_exports", foreign_keys="[ProjectVariantExport.episode_id]")

    @property
    def url(self) -> str:
        return f"/media/{self.filename}"


class PublishRecord(Base):
    __tablename__ = "publish_records"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String(16))
    variant: Mapped[str] = mapped_column(String(16), default="landscape")
    platforms: Mapped[list[str]] = mapped_column(JSON, default=list)
    title: Mapped[str] = mapped_column(String(180))
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    privacy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    selected_asset_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    scheduled_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    made_for_kids: Mapped[bool] = mapped_column(Boolean, default=False)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    project: Mapped[Project] = relationship(back_populates="publish_records")
    job: Mapped["Job | None"] = relationship()


class Character(Base):
    __tablename__ = "characters"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    notes: Mapped[str] = mapped_column(Text, default="")
    voice_provider: Mapped[str] = mapped_column(String(40), default="vibevoice")
    voice_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    voice_settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    project: Mapped[Project] = relationship(back_populates="characters")
    references: Mapped[list["CharacterReference"]] = relationship(
        back_populates="character", cascade="all, delete-orphan", order_by="CharacterReference.created_at"
    )


class CharacterReference(Base):
    __tablename__ = "character_references"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    character_id: Mapped[str] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(240))
    mime_type: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    character: Mapped[Character] = relationship(back_populates="references")

    @property
    def url(self) -> str:
        return f"/media/{self.filename}?v={int(self.created_at.timestamp())}"


class Suggestion(Base):
    __tablename__ = "suggestions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    script_version_id: Mapped[str] = mapped_column(
        ForeignKey("script_versions.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(20), default="warning")
    title: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text)
    target: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    before_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    after_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")

    script_version: Mapped[ScriptVersion] = relationship(back_populates="suggestions")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    episode_id: Mapped[str | None] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), index=True, nullable=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0)
    message: Mapped[str] = mapped_column(String(240), default="Queued")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    provider_phase: Mapped[str | None] = mapped_column(String(24), nullable=True)
    conversation_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    project: Mapped[Project] = relationship(back_populates="jobs")


class OAuthState(Base):
    """Persistent one-time OAuth state used across restarts and worker processes."""

    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String(120), primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SocialToken(Base):
    """Stores OAuth tokens for social media publishing platforms (YouTube, Facebook)."""

    __tablename__ = "social_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    platform: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # 'youtube' | 'facebook'
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_type: Mapped[str] = mapped_column(String(32), default="Bearer")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Channel / page metadata shown in the UI
    account_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    account_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    account_picture: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Extra raw JSON (e.g. Facebook page_id, scope granted)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        # SQLite returns naive datetimes; normalize before comparison
        exp = self.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= exp
