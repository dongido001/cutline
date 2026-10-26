"""Build and resolve the persisted visual continuity package for a shot."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .cast import load_cast_references, matching_set_element, shot_element_ids
from .models import Character, Project, Scene, Shot
from .screenplay_parser import (
    canonical_character_name,
    expand_character_cue_names,
    infer_voice_description,
)
from .settings import settings
import re

PHONE_CALL_PATTERN = re.compile(
    r"\b(?:phone|cellphone|smartphone|handset|receiver|caller|on the line)\b",
    re.IGNORECASE,
)
OFFSCREEN_SPEAKER_PATTERN = re.compile(
    r"\((?:O\.?S\.?|V\.?O\.?|ON PHONE|OVER PHONE|THROUGH PHONE|FILTERED)\)",
    re.IGNORECASE,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _people(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in snapshot.get("characters") or []
            if isinstance(item, dict) and (item.get("kind") or "character") == "character"]


_PROP_TAG_RE = re.compile(r"@([A-Za-z0-9_]+)")
# Prop definition chunks: "@PROP is …" or "@PROP — …" up to the next @tag.
_PROP_DEFINITION_RE = re.compile(
    r"(@[A-Za-z0-9_]+\s*(?:is\b|[—–-])[^@]*)",
    re.IGNORECASE,
)


def _shot_text_for_prop_filter(
    snapshot: dict[str, Any] | None,
    shot: Shot | None,
    video_prompt: str = "",
) -> str:
    """Collect shot-local text used to decide which scene props apply."""
    snapshot = snapshot or {}
    parts = [
        _text(shot.action) if shot is not None else _text(snapshot.get("action")),
        _text(shot.image_prompt) if shot is not None else _text(snapshot.get("image_prompt_base")),
        _text(video_prompt)
        or (_text(shot.video_prompt) if shot is not None else _text(snapshot.get("video_prompt"))),
        _text(getattr(shot, "sound", "")) if shot is not None else _text(snapshot.get("sound")),
        _text(shot.framing) if shot is not None else _text(snapshot.get("framing")),
    ]
    dialogue = (shot.dialogue if shot is not None else None) or snapshot.get("dialogue") or []
    for line in dialogue:
        if isinstance(line, dict):
            parts.append(_text(line.get("text") or line.get("line")))
            parts.append(_text(line.get("character") or line.get("speaker")))
    return "\n".join(part for part in parts if part)


def _filter_wardrobe_notes_for_shot(wardrobe_notes: str, characters: list[dict[str, Any]]) -> str:
    """Keep only wardrobe descriptions for characters present in this shot.

    If no characters are declared or if notes are general/unsegmented, returns notes unchanged.
    """
    notes = _text(wardrobe_notes)
    if not notes or not characters:
        return notes
    char_names = [
        _text(c.get("name")).upper().strip()
        for c in characters
        if (c.get("kind") or "character") == "character" and _text(c.get("name")).strip()
    ]
    if not char_names:
        return notes

    protected = re.sub(r"\b(MR|MRS|MS|DR|PROF|JR|SR)\.", r"\1__DOT__", notes, flags=re.I)
    sentences = [
        s.replace("__DOT__", ".").strip()
        for s in re.split(r"\n+|(?<=[.])\s+(?=[A-Z@])", protected)
        if s.strip()
    ]
    if len(sentences) <= 1:
        return notes

    kept: list[str] = []
    has_any_match = False
    for sent in sentences:
        mentioned_this_shot = any(
            re.search(r"\b" + re.escape(name) + r"\b", sent.upper())
            for name in char_names
        )
        if mentioned_this_shot:
            kept.append(sent.rstrip(" ."))
            has_any_match = True
        else:
            is_other_char = bool(
                re.match(r"^@?[A-Za-z]+(?:\s+[A-Za-z]+)?\s*(?:wears\b|is\s+wearing\b|in\b|:)", sent)
                or re.search(r"\b[A-Z]{3,}(?:\s+[A-Z]{3,})?\b\s*(?:wears\b|:)", sent)
            )
            if not is_other_char:
                kept.append(sent.rstrip(" ."))

    if not has_any_match:
        return notes
    return ". ".join(kept) + "."


def _filter_props_notes_for_shot(props_notes: str, shot_text: str) -> str:
    """Keep only prop definitions whose @TAG or key object is mentioned in the shot.

    Tagged episode-wide prop bibles filter by @TAG.
    Plain-text prop bibles filter by object keywords matching the shot text.
    """
    notes = _text(props_notes)
    if not notes or not shot_text:
        return notes
    definitions = [seg.strip() for seg in _PROP_DEFINITION_RE.findall(notes) if seg.strip()]
    if definitions:
        shot_tags = {tag.upper() for tag in _PROP_TAG_RE.findall(shot_text or "")}
        if not shot_tags:
            return ""
        kept: list[str] = []
        for segment in definitions:
            match = _PROP_TAG_RE.match(segment)
            if match and match.group(1).upper() in shot_tags:
                kept.append(segment.rstrip(" ."))
        return " ".join(kept)

    segments = [seg.strip(" .,-–—") for seg in re.split(r"[;\n]+", notes) if seg.strip(" .,-–—")]
    if not segments:
        return ""
    if len(segments) == 1 and "—" not in segments[0] and "–" not in segments[0]:
        return notes

    shot_lower = shot_text.lower()
    shot_words = set(re.findall(r"\b[a-z]{3,}\b", shot_lower))
    modifiers = {
        "small", "large", "brown", "white", "black", "blue", "cream", "red", "green", "yellow",
        "metal", "plastic", "plain", "three", "two", "one", "courtyard", "side", "unlettered",
        "closed", "enclosed", "exterior", "the", "and", "with", "from", "for", "are", "etc",
        "near", "beside", "behind", "into", "onto", "over", "under", "all", "each", "some",
        "tobi", "amaka", "bamidele", "zara", "mira", "jun", "ben", "maya", "gabe", "rosa",
    }

    kept_plain: list[str] = []
    for seg in segments:
        base = re.split(
            r"\s+(?:centered\s+on|beneath|initially\s+in|clipped\s+to|beside|inside|with|attached\s+to|held\s+by|worn\s+by|hanging\s+from|placed\s+on|is\s+an?)\b",
            seg,
            flags=re.IGNORECASE,
        )[0].strip()
        words = [w for w in re.findall(r"\b[a-z]{3,}\b", base.lower()) if w not in modifiers]
        if any(w in shot_words for w in words):
            kept_plain.append(seg)
        elif not words and any(w in shot_words for w in re.findall(r"\b[a-z]{3,}\b", seg.lower()) if w not in modifiers):
            kept_plain.append(seg)

    if not kept_plain:
        return ""
    return "; ".join(kept_plain) + "."


def _resolve_character_voice(character: Character) -> str:
    explicit = _text(getattr(character, "voice_description", ""))
    if explicit:
        return explicit
    notes = _text(getattr(character, "notes", ""))
    if notes:
        return infer_voice_description(character.name, notes)
    return ""


def _scene_package(scene: Scene) -> dict[str, str]:
    package = {
        "title": _text(scene.title),
        "location": _text(scene.location),
        "time_of_day": _text(scene.time_of_day),
        "set_notes": _text(scene.set_notes),
        "lighting_notes": _text(scene.lighting_notes),
        "wardrobe_notes": _text(scene.wardrobe_notes),
        "props_notes": _text(scene.props_notes),
    }
    reusable_set = matching_set_element(scene.script_version.project.characters, scene.location)
    if reusable_set and reusable_set.notes:
        package["set_notes"] = f"Canonical set layout: {reusable_set.notes.strip()}"
        staging = _text(scene.set_notes)
        if staging and staging != reusable_set.notes.strip():
            package["set_notes"] += f"\nScene staging (preserve the canonical layout): {staging}"
    return package


def naturalize_screenplay_tags(text: str) -> str:
    """Convert raw screenplay tokens like @NKIRU, @NKIRU_SHOP, @RED_CHAIR into natural English."""
    if not text or "@" not in text:
        return text

    # Specific compound tokens first
    text = re.sub(r"@([A-Za-z0-9]+)_(?:SHOP|STORE)S?\b", lambda m: f"{m.group(1).capitalize()}'s shop", text)
    text = re.sub(r"@([A-Za-z0-9]+)_COUNTER\b", lambda m: f"{m.group(1).capitalize()}'s counter", text)
    text = re.sub(r"@LAGOS_SHOPFRONT\b", "Lagos street provision shopfront", text, flags=re.I)
    text = re.sub(r"@RED_CHAIR\b", "red plastic chair", text, flags=re.I)
    text = re.sub(r"@BLUE_CHAIR\b", "blue plastic chair", text, flags=re.I)
    text = re.sub(r"@BISCUIT_PACK\b", "biscuit packet", text, flags=re.I)
    text = re.sub(r"@EXPIRED_BISCUITS\b", "expired biscuit packet", text, flags=re.I)
    text = re.sub(r"@SHOPPING_BAG\b", "transparent shopping bag", text, flags=re.I)
    text = re.sub(r"@NAIRA_NOTES\b", "Nigerian naira banknotes", text, flags=re.I)
    text = re.sub(r"@BREAD_LOAF\b", "packaged loaf of bread", text, flags=re.I)
    text = re.sub(r"@MILK_TIN\b", "tin of powdered milk", text, flags=re.I)
    text = re.sub(r"@PRODUCT_BASKET\b", "plastic provisions basket", text, flags=re.I)
    text = re.sub(r"@BLACK_HANDBAG\b", "black handbag", text, flags=re.I)
    text = re.sub(r"@CUSTOMER_(\d+)\b", r"Customer \1", text, flags=re.I)

    # General fallback for any remaining @TAG:
    def _clean_tag(m: re.Match) -> str:
        tag = m.group(1)
        if "_" in tag:
            words = tag.split("_")
            return " ".join(words).lower()
        return tag.capitalize()

    return re.sub(r"@([A-Za-z0-9_]+)\b", _clean_tag, text)


def _clean_character_bio(notes: str) -> str:
    desc = _text(notes)
    if not desc:
        return ""
    desc = re.sub(
        r"^@?[A-Z][A-Z0-9_.\s]*\s*[:—–]\s*",
        "",
        desc,
        count=1,
        flags=re.IGNORECASE,
    ).strip()
    desc = re.sub(
        r"(?:speaking\s+)?voice(?:\s+profile)?\s*:\s*[^\n]+",
        "",
        desc,
        flags=re.IGNORECASE,
    ).strip(" .,-–—")
    return desc


def _char_tag(name: str) -> str:
    return "@" + re.sub(r"[^A-Za-z0-9]+", "_", _text(name)).strip("_").upper()


def _char_patterns(name: str) -> list[str]:
    clean = _text(name)
    if not clean:
        return []
    tag = _char_tag(clean)
    pats = [re.escape(tag), re.escape(clean)]
    short = re.sub(r"^(?:MR|MRS|MS|DR|PROF)\.?\s+", "", clean, flags=re.I).strip()
    if short and short != clean:
        pats.append(re.escape(short))
    return pats


def _mentions_char(clause: str, name: str) -> bool:
    return any(
        re.search(rf"(?i)(?:^|(?<=[^A-Za-z0-9_])){p}(?=$|[^A-Za-z0-9_])", clause)
        for p in _char_patterns(name)
    )


def _strip_offcamera_clauses(text: str) -> str:
    return re.sub(
        r"(?i)[^.;\n]*\b(?:off[- ]camera|off[- ]screen|outside (?:the )?(?:frame|camera(?:\s+view)?)|unseen)\b[^.;\n]*",
        "",
        text,
    )


def _focus_text_on_character(
    text: str,
    focal_character: str,
    all_character_names: list[str],
    *,
    strip_environment: bool = True,
    character_bios: dict[str, str] | None = None,
    is_framing: bool = False,
) -> str:
    """Strip non-focal character descriptions and two-shot staging from base prompt/framing when focusing on a single character."""
    if not text or not focal_character:
        return text
    focal_clean = focal_character.strip()
    focal_tag = _char_tag(focal_clean)

    other_names = [
        _text(name)
        for name in all_character_names
        if _text(name) and _text(name).upper() != focal_clean.upper()
    ]
    other_patterns: list[str] = []
    for clean in other_names:
        for pat in _char_patterns(clean):
            if pat not in other_patterns:
                other_patterns.append(pat)

    # When not doing an explicit reference-frame zoom-in (strip_environment=False),
    # if the text already only mentions the selected character and no unselected characters,
    # leave it untouched.
    on_cam_text = _strip_offcamera_clauses(text)
    has_other_on_cam = any(_mentions_char(on_cam_text, n) for n in other_names)
    has_focal_on_cam = _mentions_char(on_cam_text, focal_clean)
    if not strip_environment and not has_other_on_cam and has_focal_on_cam:
        return text

    # 1. Rewrite "Visible character references: ..." to list only the focal character
    text = re.sub(
        r"(?i)(Visible character references:\s*)([^\n.]+)(\.?)",
        f"\\1{focal_tag}\\3",
        text,
    )
    # 2. Rewrite "declared cast (...)" lists to only the focal character
    text = re.sub(
        r"(?i)(declared cast\s*\()[^)]+(\))",
        f"\\1{focal_tag}\\2",
        text,
    )
    if not other_patterns:
        return text

    # 3. Strip "@OTHER_CHAR: <appearance description>." sentences, or replace with focal bio if missing
    focal_bio = (character_bios or {}).get(focal_clean.upper(), "").strip(" .")
    focal_has_bio = bool(
        re.search(rf"(?i)(?:^|(?<=\.\s)|(?<=\n))\s*(?:{re.escape(focal_clean)}|{re.escape(focal_tag)})\s*:", text)
    )
    injected_bio = False
    for pat in other_patterns:
        if not is_framing and focal_bio and not focal_has_bio and not injected_bio:
            text, n_sub = re.subn(
                rf"(?i)(?:^|(?<=\.\s)|(?<=\n))\s*{pat}\s*:\s*[^.\n]+\.",
                f" {focal_tag}: {focal_bio}.",
                text,
                count=1,
            )
            if n_sub:
                injected_bio = True
                focal_has_bio = True
        text = re.sub(
            rf"(?i)(?:^|(?<=\.\s)|(?<=\n))\s*{pat}\s*:\s*[^.\n]+\.",
            "",
            text,
        )

    def _mentions_other(clause: str) -> bool:
        return any(
            re.search(rf"(?i)(?:^|(?<=[^A-Za-z0-9_])){p}(?=$|[^A-Za-z0-9_])", clause)
            for p in other_patterns
        )

    # Strip trailing interaction phrases toward unselected characters ("looking directly at @AMAKA", etc.)
    for pat in other_patterns:
        text = re.sub(
            rf"(?i),?\s*(?:looking|glancing|turning|facing|gesturing|nodding|speaking)(?:\s+directly)?\s+(?:at|toward|towards|to)\s+{pat}\b",
            "",
            text,
        )

    # 4. Strip wide-room Environment: sentences and background-fixture placement phrases
    # when strip_environment=True (opt-in reference-frame zoom-in).
    if strip_environment:
        text = re.sub(
            r"(?i)\s*Set reference\s*:\s*[^.\n]+\.",
            "",
            text,
        )
        text = re.sub(
            r"(?i)(?:^|(?<=\.\s)|(?<=\n))\s*Environment\s*:\s*[^.\n]+\.",
            "",
            text,
        )
        text = re.sub(
            r"(?i);\s*[^;.\n]*\b(?:remain|remains|stay|stays)\s+off[- ]camera[^.\n]*",
            "",
            text,
        )
        text = re.sub(
            rf"(?i)(?:^|(?<=\.\s)|(?<=\n))\s*(?:{re.escape(focal_clean)}|{re.escape(focal_tag)})\s+stands\s+beside\s+[^.\n]+\.",
            "",
            text,
        )
        text = re.sub(
            r"(?i)\s+through\s+the\s+curtained\s+[^,.\n]+",
            "",
            text,
        )
        text = re.sub(
            r"(?i),?\s*(?:and\s+)?\b(?:black|white|flat|dark|brown|leather|canvas)?\s*(?:sandals|sneakers|shoes|footwear)\b",
            "",
            text,
        )

    # 5. In "Medium shot, eye level; CHAR1 screen-left, CHAR2 screen-right" sentences,
    # upgrade shot size to a single medium close-up on focal_character and drop wide-room placement clauses.
    _framing_head_re = re.compile(
        r"(?i)\b(?:medium[- ]wide(?:\s+ensemble)?|medium(?:\s+two[- ]shot|\s+close[- ]up|\s+group)?|wide(?:\s+ensemble)?|two[- ]shot|ensemble)\s*(?:shot|close[- ]up)?\b"
    )

    def _clean_sentence(sent: str) -> str:
        # Don't strip semicolons inside "@ZARA: 27-year-old ...; forest-green blouse..."
        if re.match(rf"(?i)^\s*(?:{re.escape(focal_clean)}|{re.escape(focal_tag)})\s*:", sent):
            return sent
        if ";" in sent:
            head, rest = sent.split(";", 1)
            if (strip_environment and _framing_head_re.search(head)) or _mentions_other(rest):
                trailing_dot = "." if rest.rstrip().endswith(".") else ""
                head = _framing_head_re.sub(f"Single medium close-up on {focal_clean}", head)
                return f"{head}{trailing_dot}"
        return sent

    protected = re.sub(r"\b(MR|MRS|MS|DR|PROF|JR|SR)\.", r"\1__DOT__", text, flags=re.I)
    out_lines: list[str] = []
    for line in protected.split("\n"):
        sents = [
            s.replace("__DOT__", ".").strip()
            for s in re.split(r"(?<=[.])\s+", line)
            if s.strip()
        ]
        cleaned_sents = [_clean_sentence(s) for s in sents if s.strip()]
        out_lines.append(" ".join(cleaned_sents))
    return "\n".join(out_lines)


def _sync_multi_cast_text(
    text: str,
    selected_people: list[str],
    all_character_names: list[str],
    *,
    character_bios: dict[str, str] | None = None,
    is_framing: bool = False,
) -> str:
    """Synchronize base prompt or framing text with 2+ selected cast members when unselected characters appear in the text or newly selected characters are missing."""
    if not text or len(selected_people) < 2:
        return text
    selected_clean = [_text(p) for p in selected_people if _text(p)]
    selected_upper = {p.upper() for p in selected_clean}
    unselected_names = [
        _text(name)
        for name in all_character_names
        if _text(name) and _text(name).upper() not in selected_upper
    ]

    on_cam_text = _strip_offcamera_clauses(text)
    unselected_in_text = [n for n in unselected_names if _mentions_char(on_cam_text, n)]
    newly_selected = [n for n in selected_clean if not _mentions_char(on_cam_text, n)]

    if not unselected_in_text and not newly_selected:
        return text

    selected_tags = [_char_tag(p) for p in selected_clean]
    tags_str = ", ".join(selected_tags)

    # 1. Rewrite "Visible character references: ..." and "declared cast (...)"
    text = re.sub(
        r"(?i)(Visible character references:\s*)([^\n.]+)(\.?)",
        f"\\1{tags_str}\\3",
        text,
    )
    text = re.sub(
        r"(?i)(declared cast\s*\()[^)]+(\))",
        f"\\1{tags_str}\\2",
        text,
    )

    # 2. Remove any off-camera exclusion clause for a character that is now selected
    for sel in selected_clean:
        for pat in _char_patterns(sel):
            text = re.sub(
                rf"(?i);\s*{pat}\s+remains\s+off[- ]camera[^.\n]*",
                "",
                text,
            )

    # 3. 1-to-1 character replacement (e.g. AMAKA unchecked and TOBI checked in a 2-person shot)
    if len(unselected_in_text) == 1 and len(newly_selected) == 1:
        old_char = unselected_in_text[0]
        new_char = newly_selected[0]
        old_tag = _char_tag(old_char)
        new_tag = _char_tag(new_char)
        new_bio = (character_bios or {}).get(new_char.upper(), "").strip(" .")

        if not is_framing:
            for pat in _char_patterns(old_char):
                replacement = f" {new_tag}: {new_bio}." if new_bio else ""
                text, n_sub = re.subn(
                    rf"(?i)(?:^|(?<=\.\s)|(?<=\n))\s*{pat}\s*:\s*[^.\n]+\.",
                    replacement,
                    text,
                    count=1,
                )
                if n_sub:
                    break

        use_tags = not is_framing and "@" in text
        target_label = new_tag if use_tags else new_char
        text = re.sub(
            rf"(?i)(?:^|(?<=[^A-Za-z0-9_])){re.escape(old_tag)}(?=$|[^A-Za-z0-9_])",
            target_label,
            text,
        )
        for pat in _char_patterns(old_char):
            text = re.sub(
                rf"(?i)(?:^|(?<=[^A-Za-z0-9_])){pat}(?=$|[^A-Za-z0-9_])",
                target_label,
                text,
            )
        return re.sub(r"  +", " ", text).strip()

    # 4. General removal of remaining unselected characters in text
    if unselected_in_text:
        for u_char in unselected_in_text:
            for pat in _char_patterns(u_char):
                if not is_framing:
                    text = re.sub(
                        rf"(?i)(?:^|(?<=\.\s)|(?<=\n))\s*{pat}\s*:\s*[^.\n]+\.",
                        "",
                        text,
                    )
                text = re.sub(
                    rf"(?i),?\s*(?:looking|glancing|turning|facing|gesturing|nodding|speaking)(?:\s+directly)?\s+(?:at|toward|towards|to)\s+{pat}\b",
                    "",
                    text,
                )

        def _mentions_any_unselected(clause: str) -> bool:
            return any(_mentions_char(clause, u) for u in unselected_in_text)

        def _mentions_any_selected(clause: str) -> bool:
            return any(_mentions_char(clause, s) for s in selected_clean)

        protected = re.sub(r"\b(MR|MRS|MS|DR|PROF|JR|SR)\.", r"\1__DOT__", text, flags=re.I)
        out_lines: list[str] = []
        for line in protected.split("\n"):
            sents = [
                s.replace("__DOT__", ".").strip()
                for s in re.split(r"(?<=[.])\s+", line)
                if s.strip()
            ]
            cleaned_sents: list[str] = []
            for sent in sents:
                if ";" in sent and not re.match(r"^\s*@[A-Za-z0-9_]+\s*:", sent):
                    head, rest = sent.split(";", 1)
                    if _mentions_any_unselected(rest):
                        trailing_dot = "." if rest.rstrip().endswith(".") else ""
                        clauses = [c.strip() for c in rest.rstrip(".").split(",") if c.strip()]
                        kept_clauses: list[str] = []
                        for c in clauses:
                            if _mentions_any_unselected(c) and not _mentions_any_selected(c):
                                continue
                            for u_char in unselected_in_text:
                                for pat in _char_patterns(u_char):
                                    c = re.sub(rf"(?i)\b{pat}\s+and\s+", "", c)
                                    c = re.sub(rf"(?i)\s+and\s+{pat}\b", "", c)
                            kept_clauses.append(c.strip())
                        if kept_clauses:
                            sent = f"{head}; {', '.join(kept_clauses)}{trailing_dot}"
                        else:
                            sent = f"{head}{trailing_dot}"
                cleaned_sents.append(sent)
            out_lines.append(" ".join(cleaned_sents))
        text = "\n".join(out_lines)

    # 5. General addition of newly selected characters missing from text
    if newly_selected:
        if not is_framing:
            bio_Insertions: list[str] = []
            for n_char in newly_selected:
                n_tag = _char_tag(n_char)
                if not re.search(rf"(?i){re.escape(n_tag)}\s*:", text):
                    n_bio = (character_bios or {}).get(n_char.upper(), "").strip(" .")
                    if n_bio:
                        bio_Insertions.append(f"{n_tag}: {n_bio}.")
            if bio_Insertions:
                joined_bios = " ".join(bio_Insertions)
                if re.search(r"(?i)\bEnvironment\s*:", text):
                    text = re.sub(
                        r"(?i)(\bEnvironment\s*:)",
                        f"{joined_bios} \\1",
                        text,
                        count=1,
                    )
                else:
                    text = f"{text.rstrip()} {joined_bios}"

        use_tags = not is_framing and "@" in text
        added_labels = [(_char_tag(n) if use_tags else n) for n in newly_selected]
        added_phrase = f", with {', '.join(added_labels)} visible in frame"

        _two_shot_re = re.compile(r"(?i)\b(?:medium\s+close[- ]up|medium\s+two[- ]shot|two[- ]shot)\b")

        def _augment_framing_sent(sent: str) -> str:
            if ";" in sent and not re.match(r"^\s*@[A-Za-z0-9_]+\s*:", sent):
                head, rest = sent.split(";", 1)
                if any(_mentions_char(rest, s) for s in selected_clean):
                    trailing_dot = "." if rest.rstrip().endswith(".") else ""
                    body = rest.rstrip(".")
                    if len(selected_clean) >= 3:
                        head = _two_shot_re.sub("Medium group shot", head)
                    return f"{head};{body}{added_phrase}{trailing_dot}"
            return sent

        protected = re.sub(r"\b(MR|MRS|MS|DR|PROF|JR|SR)\.", r"\1__DOT__", text, flags=re.I)
        out_lines = []
        for line in protected.split("\n"):
            sents = [
                s.replace("__DOT__", ".").strip()
                for s in re.split(r"(?<=[.])\s+", line)
                if s.strip()
            ]
            out_lines.append(" ".join(_augment_framing_sent(s) for s in sents))
        text = "\n".join(out_lines)

    return re.sub(r"  +", " ", text).strip()


def _strip_wide_scene_for_locked_blocking(
    text: str,
    target_people: list[str],
    *,
    is_framing: bool = False,
) -> str:
    """When lock_blocking=True, strip wide-scene Environment/Set/table/chair placement and footwear so the camera zooms in waist-up on target_people."""
    if not text:
        return text
    text = re.sub(r"(?i)\s*Set reference\s*:\s*[^.\n]+\.", "", text)
    text = re.sub(r"(?i)(?:^|(?<=\.\s)|(?<=\n))\s*Environment\s*:\s*[^.\n]+\.", "", text)
    text = re.sub(r"(?i);\s*[^;.\n]*\b(?:remain|remains|stay|stays)\s+off[- ]camera[^.\n]*", "", text)
    text = re.sub(
        r"(?i),?\s*(?:and\s+)?\b(?:black|white|flat|dark|brown|leather|canvas)?\s*(?:sandals|sneakers|shoes|footwear)\b",
        "",
        text,
    )
    _framing_head_re = re.compile(
        r"(?i)\b(?:medium[- ]wide(?:\s+ensemble)?|medium(?:\s+two[- ]shot|\s+close[- ]up|\s+group)?|wide(?:\s+ensemble)?|two[- ]shot|ensemble)\s*(?:shot|close[- ]up)?\b"
    )
    if is_framing and ";" not in text and _framing_head_re.search(text):
        text = _framing_head_re.sub("Tight waist-up medium close-up", text)
        return re.sub(r"  +", " ", text).strip()

    def _clean_blocking_sent(sent: str) -> str:
        if re.match(r"^\s*@[A-Za-z0-9_]+\s*:", sent):
            return sent
        if ";" in sent:
            head, rest = sent.split(";", 1)
            if _framing_head_re.search(head):
                head = _framing_head_re.sub("Tight waist-up medium close-up", head)
            trailing_dot = "." if rest.rstrip().endswith(".") else ""
            clauses = [c.strip() for c in rest.rstrip(".").split(",") if c.strip()]
            cleaned_clauses: list[str] = []
            for clause in clauses:
                for p in target_people:
                    for pat in _char_patterns(p):
                        clause = re.sub(
                            rf"(?i)^({pat})\s+.*?\b(screen-left|screen-right|(?:at\s+)?center)\b.*$",
                            r"\1 \2",
                            clause,
                        )
                cleaned_clauses.append(clause.strip())
            if cleaned_clauses:
                return f"{head}; {', '.join(cleaned_clauses)}{trailing_dot}"
        return sent

    protected = re.sub(r"\b(MR|MRS|MS|DR|PROF|JR|SR)\.", r"\1__DOT__", text, flags=re.I)
    out_lines: list[str] = []
    for line in protected.split("\n"):
        sents = [
            s.replace("__DOT__", ".").strip()
            for s in re.split(r"(?<=[.])\s+", line)
            if s.strip()
        ]
        cleaned_sents = [_clean_blocking_sent(s) for s in sents if s.strip()]
        if cleaned_sents:
            out_lines.append(" ".join(cleaned_sents))
    return re.sub(r"  +", " ", "\n".join(out_lines)).strip()


def _image_prompt(
    base_prompt: str, characters: list[dict[str, Any]], scene: dict[str, str],
    framing: str, action: str,
    lock_blocking: bool = False,
    focal_character: str | None = None,
    all_project_people: list[str] | None = None,
    character_bios: dict[str, str] | None = None,
) -> str:
    prompt = _text(base_prompt)
    framing = _text(framing)
    selected_people = [_text(c["name"]) for c in (characters or []) if (c.get("kind") or "character") == "character" and _text(c.get("name"))]
    people_all = list(dict.fromkeys(selected_people + list(all_project_people or [])))
    bios: dict[str, str] = dict(character_bios or {})
    for c in characters or []:
        if (c.get("kind") or "character") == "character" and _text(c.get("name")) and _text(c.get("notes")):
            key = _text(c["name"]).upper()
            if key not in bios:
                bios[key] = _clean_character_bio(_text(c["notes"]))

    if lock_blocking and focal_character:
        prompt = _focus_text_on_character(
            prompt, focal_character, people_all, strip_environment=True, character_bios=bios, is_framing=False
        )
        framing = _focus_text_on_character(
            framing, focal_character, people_all, strip_environment=True, character_bios=bios, is_framing=True
        )
    elif selected_people and people_all:
        if len(selected_people) == 1:
            prompt = _focus_text_on_character(
                prompt, selected_people[0], people_all, strip_environment=bool(lock_blocking), character_bios=bios, is_framing=False
            )
            framing = _focus_text_on_character(
                framing, selected_people[0], people_all, strip_environment=bool(lock_blocking), character_bios=bios, is_framing=True
            )
        else:
            prompt = _sync_multi_cast_text(
                prompt, selected_people, people_all, character_bios=bios, is_framing=False
            )
            framing = _sync_multi_cast_text(
                framing, selected_people, people_all, character_bios=bios, is_framing=True
            )
            if lock_blocking:
                prompt = _strip_wide_scene_for_locked_blocking(prompt, selected_people, is_framing=False)
                framing = _strip_wide_scene_for_locked_blocking(framing, selected_people, is_framing=True)
    constraints: list[str] = []
    if framing:
        constraints.append(f"Shot framing: {framing.rstrip(' .')}.")
    if action:
        constraints.append(f"Visible shot action: {action.rstrip(' .')}.")
    if characters:
        people = [c["name"] for c in characters if (c.get("kind") or "character") == "character"]
        props = [c["name"] for c in characters if (c.get("kind") or "") == "prop"]
        scenes = [c["name"] for c in characters if (c.get("kind") or "") == "scene"]
        # Back-compat: snapshots without kind treat everyone as characters.
        if not people and not props and not scenes:
            people = [c["name"] for c in characters]
        if people:
            identity_people = (
                [focal_character] if (lock_blocking and focal_character) else people
            )
            constraints.append(
                f"Character identity references: {', '.join(identity_people)}. Preserve each canonical face and identity."
            )
            if lock_blocking:
                if focal_character:
                    constraints.append(
                        f"Spatial blocking lock: This shot is a single close-up punch-in on {focal_character} only, "
                        "taken from the exact continuous scene. From the attached reference image, turn the camera toward "
                        f"{focal_character} and zoom in closer (tight waist-up close-up) to capture {focal_character} without changing anything in the image sent. "
                        f"Simulate a professional film camera zooming in on {focal_character} from the original camera position on the same movie set: all actors stay in their "
                        f"fixed positions in the room, and only the camera lens turns and zooms in to capture {focal_character}. "
                        f"Keep the established horizontal stage blocking, eyeline direction, and lighting. {focal_character} stays in their established screen position and side of frame. "
                        "DO NOT swap, mirror, or rearrange characters."
                    )
                    constraints.append(
                        f"Camera coverage framing: Frame ONLY {focal_character} as the sole subject from the waist up. "
                        "Remove all other people from the frame — they remain in their established positions outside the camera's field of view."
                    )
                    constraints.append(
                        "CRITICAL BACKGROUND CONTINUITY: Preserve the original environment directly behind "
                        f"{focal_character} in the attached reference image exactly as it would appear from this tighter camera framing. "
                        f"The surface/wall/door directly behind {focal_character}'s body in the reference image must occupy most of the background, "
                        "with only the narrow adjacent edge that would naturally remain visible at this tighter zoom level. "
                        "Do NOT leave a large empty area of a neighbouring window, chair, or desk in the frame if another person stood between "
                        f"{focal_character} and that window/chair/desk in the reference image — zooming in on {focal_character} alone means "
                        "the area where the neighbour stood (and the chair/window/furniture behind or in front of that neighbour) is completely outside the camera's field of view. "
                        "The result must look like a closer camera shot filmed moments later on the same movie set, not a separate portrait or a wide room view."
                    )
                else:
                    target_label = " and ".join(people) if len(people) <= 2 else ", ".join(people)
                    constraints.append(
                        f"Spatial blocking lock: From the attached reference image, turn the camera toward {target_label} and zoom in closer "
                        f"(tight waist-up framing) to capture ONLY {target_label} without changing anything in the image sent. "
                        "Maintain the exact horizontal stage blocking, character positions, and eyelines established in the continuous scene — "
                        f"just turn the camera toward {target_label} and zoom in so {target_label} fill the frame from the waist up. "
                        "DO NOT swap, mirror, or rearrange characters."
                    )
                    constraints.append(
                        f"Camera coverage framing: Camera coverage frames ONLY {', '.join(people)} in their continuous established stage positions from the waist up; "
                        "all other people from the reference image remain outside the zoomed-in camera frame."
                    )
                    constraints.append(
                        f"CRITICAL BACKGROUND CONTINUITY: Preserve ONLY the portion of the original environment directly behind {target_label} "
                        f"in the attached reference image as it naturally appears when the camera turns and zooms in on {target_label}. "
                        "Do NOT use a wide full-body shot, do NOT show feet or floor paving, and do NOT include the outer left or right areas of the reference image "
                        "where other people were standing (such as empty white plastic chairs, side tables, gates, staircases, windows, or desks that were in front of or behind the removed characters). "
                        "Those outer chairs and side areas must be completely cropped out of the camera's tighter field of view."
                    )
            else:
                constraints.append(
                    f"Camera coverage framing: Camera is tightly cropped and angled to frame ONLY {', '.join(people)}; all other scene participants and unoccupied location areas remain off-camera outside the frame."
                )
        if props:
            constraints.append(
                f"Prop object references: {', '.join(props)}. Preserve each prop's shape, materials, and colors."
            )
        if scenes and not lock_blocking:
            constraints.append(
                f"Scene/set references: {', '.join(scenes)}. Preserve architecture, layout and materials; use this scene's lighting instructions."
            )
    shot_text = "\n".join(part for part in (prompt, framing, action) if part)
    wardrobe_characters = (
        [c for c in characters if (c.get("kind") or "character") != "character" or _text(c.get("name")).upper() == focal_character.strip().upper()]
        if (lock_blocking and focal_character)
        else characters
    )
    for key, label in (
        ("location", "Location"),
        ("time_of_day", "Time of day"),
        ("set_notes", "Set"),
        ("lighting_notes", "Lighting"),
        ("wardrobe_notes", "Wardrobe"),
        ("props_notes", "Props"),
    ):
        if lock_blocking and key == "set_notes":
            # In a locked-blocking camera zoom-in, the attached reference image defines the
            # immediate background behind the target character(s); full-room/courtyard set notes
            # list wide-shot fixtures (desks, windows, white plastic chairs across the courtyard)
            # that cause the model to pull wide-scene furniture into the zoomed-in frame.
            continue
        value = scene[key]
        if key == "wardrobe_notes":
            value = _filter_wardrobe_notes_for_shot(value, wardrobe_characters)
            if lock_blocking and value:
                value = re.sub(
                    r"(?i),?\s*(?:and\s+)?\b(?:black|white|flat|dark|brown|leather|canvas)?\s*(?:sandals|sneakers|shoes|footwear)\b",
                    "",
                    value,
                )
                value = re.sub(r",\s*,", ",", value)
        elif key == "props_notes":
            value = _filter_props_notes_for_shot(value, shot_text)
        elif key == "lighting_notes" and lock_blocking and focal_character:
            value = re.sub(r"(?i)\s+through\s+the\s+curtained\s+[^,.\n]+", "", value)
        if value and value.lower() != "unspecified":
            constraints.append(f"{label}: {value.rstrip(' .')}.")
    compiled = "\n".join(part for part in (prompt, "Continuity constraints:\n" + "\n".join(constraints) if constraints else "") if part)
    return naturalize_screenplay_tags(compiled)


def _short_character_cue(name: str, bio_notes: str = "", wardrobe_notes: str = "") -> str:
    """Return a concise visual cue like 'MR. BAMIDELE (the man in the crisp pale-blue short-sleeved button-down shirt)'
    so the model knows which person in the reference image to turn/zoom the camera toward without triggering
    a full-body or wide-scene regeneration."""
    clean_name = _text(name)
    if not clean_name:
        return ""
    bio = _clean_character_bio(bio_notes)
    gender = "person"
    m_gender = re.search(r"\b(woman|man|girl|boy|lady|gentleman)\b", bio, flags=re.I)
    if m_gender:
        gender = m_gender.group(1).lower()

    top_garment = ""
    if wardrobe_notes:
        filtered = _filter_wardrobe_notes_for_shot(
            wardrobe_notes,
            [{"name": clean_name, "kind": "character"}],
        )
        m_wears = re.search(r"(?i)\bwears\s+(?:an?\s+)?([^,.;\n]+)", filtered)
        if m_wears:
            candidate = m_wears.group(1).strip()
            if not re.search(r"(?i)\b(?:trousers|jeans|pants|sandals|sneakers|shoes|footwear)\b", candidate):
                top_garment = candidate
    if not top_garment and ";" in bio:
        after_semi = bio.split(";", 1)[1].strip()
        first_piece = after_semi.split(",", 1)[0].strip(" .")
        if first_piece and not re.search(r"(?i)\b(?:trousers|jeans|pants|sandals|sneakers|shoes|footwear)\b", first_piece):
            top_garment = first_piece

    if top_garment:
        return f"{clean_name} (the {gender} in the {top_garment})"
    if m_gender:
        return f"{clean_name} (the {gender})"
    return clean_name


def build_locked_blocking_camera_zoom_prompt(
    snapshot: dict[str, Any],
    shot: Shot | None = None,
) -> str:
    """Build a direct, self-contained camera-turn-and-zoom prompt for Lock Scene Blocking.

    Unlike normal shot generation, this does NOT pass through the screenplay continuity
    pipeline (Environment, Set, Props, Wardrobe, Cast Turnaround Sheets). It operates
    purely on the attached reference image, turning and zooming the camera toward the
    target character(s) without changing anything in the image sent.
    """
    focal_name = _text(snapshot.get("focal_character_name"))
    raw_chars = [
        item for item in (snapshot.get("characters") or [])
        if isinstance(item, dict) and (item.get("kind") or "character") == "character" and _text(item.get("name"))
    ]
    if focal_name:
        target_chars = [c for c in raw_chars if _text(c.get("name")).upper() == focal_name.upper()]
        if not target_chars:
            target_chars = [{"name": focal_name, "kind": "character"}]
    else:
        target_chars = raw_chars

    voices_by_name = {
        _text(v.get("name")).upper(): _text(v.get("notes"))
        for v in (snapshot.get("character_voices") or [])
        if isinstance(v, dict) and _text(v.get("name"))
    }
    if shot is not None and getattr(shot, "scene", None) and getattr(shot.scene, "script_version", None):
        project = shot.scene.script_version.project
        if project is not None:
            for c in project.characters or []:
                if (getattr(c, "kind", None) or "character") == "character" and _text(c.name) and _text(c.notes):
                    voices_by_name.setdefault(_text(c.name).upper(), _text(c.notes))

    wardrobe_notes = _text((snapshot.get("scene") or {}).get("wardrobe_notes"))
    cues = [
        _short_character_cue(
            _text(c.get("name")),
            voices_by_name.get(_text(c.get("name")).upper(), ""),
            wardrobe_notes,
        )
        for c in target_chars
        if _text(c.get("name"))
    ]

    if len(cues) == 1:
        single_cue = re.sub(r"^([^(]+?)\s*\(([^)]+)\)$", r"\1, \2", cues[0].strip())
        who_phrase = f"this character ({single_cue})"
        single_name = _text(target_chars[0].get("name"))
        single_bio = _clean_character_bio(voices_by_name.get(single_name.upper(), ""))
        cue_and_bio = f"{cues[0]} {single_bio}"
        if re.search(r"\b(woman|girl|lady|female|she|her)\b", cue_and_bio, flags=re.I):
            pronoun = "her"
        elif re.search(r"\b(man|boy|gentleman|male|he|his|him|mr\.)\b", f"{single_name} {cue_and_bio}", flags=re.I):
            pronoun = "him"
        else:
            pronoun = "this character"
    elif len(cues) == 2:
        who_phrase = f"these two characters ({cues[0]} and {cues[1]})"
        pronoun = "them"
    elif len(cues) > 2:
        who_phrase = f"these characters ({', '.join(cues[:-1])}, and {cues[-1]})"
        pronoun = "them"
    else:
        who_phrase = "the selected character"
        pronoun = "this character"

    return (
        f"From this image, turn your camera to capture {who_phrase} without changing anything in the image sent. "
        f"Just turn your camera towards {pronoun}, zoom if possible."
    )


def create_shot_snapshot(
    db: Session,
    project: Project,
    shot: Shot,
    aspect_ratio: str,
    character_ids: list[str] | None = None,
    prompt_override: str | None = None,
    *,
    require_cast_references: bool = False,
    lock_blocking: bool = False,
    focal_character_id: str | None = None,
) -> dict[str, Any]:
    if not lock_blocking:
        focal_character_id = None
    elements = list(db.scalars(select(Character).where(Character.project_id == project.id)))
    selected_ids = shot_element_ids(elements, shot, shot.character_ids if character_ids is None else character_ids)
    refs = load_cast_references(db, project.id, selected_ids, require_all=require_cast_references)
    by_id = {
        character.id: character
        for character in db.scalars(
            select(Character).where(Character.project_id == project.id, Character.id.in_(selected_ids))
        ).all()
    } if selected_ids else {}
    reference_ids_by_character: dict[str, list[str]] = {character_id: [] for character_id in selected_ids}
    names = {character.id: character.name for character in by_id.values()}
    for ref in refs:
        if ref.character_id in reference_ids_by_character:
            reference_ids_by_character[ref.character_id].append(ref.reference_id)
    cast = [
        {
            "id": character_id,
            "name": names[character_id],
            "kind": (getattr(by_id[character_id], "kind", None) or "character") if character_id in by_id else "character",
            "reference_ids": reference_ids_by_character[character_id],
        }
        for character_id in selected_ids
    ]
    character_voices = [
        {
            "id": character_id,
            "name": names[character_id],
            "notes": _text(by_id[character_id].notes) if character_id in by_id else "",
            "voice_description": _resolve_character_voice(by_id[character_id]) if character_id in by_id else "",
        }
        for character_id in selected_ids
        if character_id in by_id and (by_id[character_id].kind or "character") == "character"
    ]
    focal_name = None
    if lock_blocking and focal_character_id and focal_character_id in names:
        focal_name = names[focal_character_id]
    elif lock_blocking and focal_character_id:
        char_obj = db.get(Character, focal_character_id)
        if char_obj:
            focal_name = char_obj.name

    all_project_people = [
        c.name for c in elements if (getattr(c, "kind", None) or "character") == "character" and _text(c.name)
    ]
    character_bios = {
        _text(c.name).upper(): _clean_character_bio(_text(c.notes))
        for c in elements
        if (getattr(c, "kind", None) or "character") == "character" and _text(c.name) and _text(c.notes)
    }
    scene = _scene_package(shot.scene)
    framing = _text(shot.framing)
    action = _text(shot.action)
    base_prompt = _text(prompt_override if prompt_override is not None else shot.image_prompt)
    return {
        "version": 1,
        "character_ids": selected_ids,
        "characters": cast,
        "character_voices": character_voices,
        "dialogue": list(shot.dialogue or []),
        "scene": scene,
        "framing": framing,
        "action": action,
        "video_prompt": _text(shot.video_prompt),
        "aspect_ratio": aspect_ratio,
        "image_prompt_base": base_prompt,
        "lock_blocking": lock_blocking,
        "focal_character_id": focal_character_id,
        "focal_character_name": focal_name,
        "effective_image_prompt": _image_prompt(
            base_prompt, cast, scene, framing, action,
            lock_blocking=lock_blocking, focal_character=focal_name,
            all_project_people=all_project_people,
            character_bios=character_bios,
        ),
    }


def get_or_create_shot_snapshot(
    db: Session,
    project: Project,
    shot: Shot,
    aspect_ratio: str,
    *,
    refresh: bool = False,
    character_ids: list[str] | None = None,
    prompt_override: str | None = None,
    require_cast_references: bool = False,
    lock_blocking: bool = False,
    focal_character_id: str | None = None,
) -> dict[str, Any]:
    if not lock_blocking:
        focal_character_id = None
    saved = shot.continuity_snapshot
    elements = list(db.scalars(select(Character).where(Character.project_id == project.id)))
    expected_ids = shot_element_ids(elements, shot, shot.character_ids if character_ids is None else character_ids)
    explicit_changes = (
        character_ids is not None
        or prompt_override is not None
        or lock_blocking
        or focal_character_id is not None
        or (saved is not None and saved.get("character_ids") != expected_ids)
        or (saved is not None and saved.get("image_prompt_base") != _text(shot.image_prompt))
        or (saved is not None and saved.get("framing") != _text(shot.framing))
        or (saved is not None and bool(saved.get("lock_blocking")) != bool(lock_blocking))
    )
    if (
        saved and not refresh and not explicit_changes
        and saved.get("aspect_ratio") == aspect_ratio
        and not require_cast_references
    ):
        return saved
    snapshot = create_shot_snapshot(
        db, project, shot, aspect_ratio, character_ids=character_ids,
        prompt_override=prompt_override,
        require_cast_references=require_cast_references,
        lock_blocking=lock_blocking,
        focal_character_id=focal_character_id,
    )
    shot.character_ids = snapshot["character_ids"]
    shot.continuity_snapshot = snapshot
    return snapshot


def current_shot_snapshot(shot: Shot, aspect_ratio: str) -> dict[str, Any]:
    project = shot.scene.script_version.project
    selected_ids = shot_element_ids(project.characters, shot, shot.character_ids or [])
    by_id = {character.id: character for character in project.characters}
    characters = [
        {
            "id": character_id,
            "name": by_id[character_id].name,
            "kind": getattr(by_id[character_id], "kind", None) or "character",
            "reference_ids": [reference.id for reference in by_id[character_id].references],
        }
        for character_id in selected_ids if character_id in by_id
    ]
    character_voices = [
        {
            "id": character_id,
            "name": by_id[character_id].name,
            "notes": _text(by_id[character_id].notes),
            "voice_description": _resolve_character_voice(by_id[character_id]),
        }
        for character_id in selected_ids if character_id in by_id and (by_id[character_id].kind or "character") == "character"
    ]
    scene = _scene_package(shot.scene)
    all_project_people = [
        c.name for c in (project.characters or [])
        if (getattr(c, "kind", None) or "character") == "character" and _text(c.name)
    ]
    character_bios = {
        _text(c.name).upper(): _clean_character_bio(_text(c.notes))
        for c in (project.characters or [])
        if (getattr(c, "kind", None) or "character") == "character" and _text(c.name) and _text(c.notes)
    }
    return {
        "version": 1, "character_ids": selected_ids, "characters": characters,
        "character_voices": character_voices,
        "dialogue": list(shot.dialogue or []),
        "scene": scene, "framing": _text(shot.framing), "action": _text(shot.action),
        "video_prompt": _text(shot.video_prompt), "aspect_ratio": aspect_ratio,
        "image_prompt_base": _text(shot.image_prompt),
        "effective_image_prompt": _image_prompt(
            _text(shot.image_prompt), characters, scene, _text(shot.framing), _text(shot.action),
            all_project_people=all_project_people,
            character_bios=character_bios,
        ),
    }


_MOUTH_STILL_PHONE_QUOTE_RE = re.compile(
    r"Keep\s+(?P<listener>[A-Z][a-zA-Z]+)'s\s+(?:mouth|lips)\s+(?:still|closed|shut|motionless)\s+"
    r"while\s+(?P<caller>[^:\n]+?)\s*:\s*(?P<quote>[\"\u201c][^\"\u201d]+[\"\u201d])",
    re.IGNORECASE,
)
_OFFSCREEN_PHONE_CUE_RE = re.compile(
    r"\b(?:filtered\s+(?:phone\s+|handset\s+)?(?:voice|line|audio)|"
    r"handset\s+audio|off-screen\s+phone\s+voice|"
    r"(?:assistant|caller)\s+says\s*,)",
    re.IGNORECASE,
)
# Require an explicit phone/off-screen cue. Optional prefixes used to match plain
# `@GABE says, "..."` on-camera dialogue and rewrite it into a phone call.
_OFFSCREEN_CALLER_QUOTE_RE = re.compile(
    r"(?:"
    r"Over the (?:filtered\s+)?telephone\s+earpiece,\s+(?:unseen\s+caller\s+)?"
    r"|unseen\s+caller\s+"
    r")"
    r"(?P<caller>[A-Z][a-zA-Z0-9_\s\x27-]+?)(?:\x27s\s+[^,\n]+?)?\s+"
    r"(?:says|delivers[^,\n]*),\s*[\"\u201c](?P<quote>[^\"\u201d]+)[\"\u201d]",
    re.IGNORECASE,
)


_EPISODE_TIMECODE_RE = re.compile(r"\b(?P<prep>at|by|until)\s+(?P<min>\d{1,2}):(?P<sec>\d{2})(?![\d:])", re.IGNORECASE)


def _clip_relative_timecodes(video_prompt: str, shot: Shot | None) -> str:
    """Rewrite episode timecodes ("snap to black at 03:00") as clip-relative time; video models read MM:SS as clip time."""
    if shot is None or shot.start_ms is None or not shot.duration_ms:
        return video_prompt
    start_ms = shot.start_ms
    duration_ms = shot.duration_ms

    def replace(match: re.Match[str]) -> str:
        absolute_ms = (int(match.group("min")) * 60 + int(match.group("sec"))) * 1000
        relative_ms = absolute_ms - start_ms
        if relative_ms < 0 or relative_ms > duration_ms:
            return match.group(0)
        prep = match.group("prep")
        if relative_ms >= duration_ms - 500:
            return f"{prep} the very end of the clip"
        return f"{prep} {relative_ms / 1000:g} seconds into the clip"

    return _EPISODE_TIMECODE_RE.sub(replace, video_prompt)


def _sanitize_phone_video_prompt(video_prompt: str) -> str:
    """Rewrite clauses that place '<Listener>'s mouth' directly before an off-screen caller's quote,
    and sanitize off-screen caller quoted speech so video models do not lip-sync the visible actor.

    Only rewrites speech that is already marked as telephone / unseen-caller audio.
    Plain on-camera cues like ``@GABE says, "I'll help."`` must stay untouched.
    """
    text = _text(video_prompt)
    if not text:
        return text

    text = _MOUTH_STILL_PHONE_QUOTE_RE.sub(
        lambda m: (
            f"{m.group('listener').strip()} listens silently with lips firmly closed and jaw motionless. "
            f"Over the filtered telephone earpiece, unseen caller ({m.group('caller').strip()}): {m.group('quote').strip()}"
        ),
        text,
    )

    def _replace_offscreen_quote(match: re.Match[str]) -> str:
        caller = match.group("caller").strip()
        return (
            f"Over the filtered telephone earpiece, unseen caller {caller} speaks while the listener "
            f"remains completely silent with mouth closed and jaw motionless."
        )

    text = _OFFSCREEN_CALLER_QUOTE_RE.sub(_replace_offscreen_quote, text)
    return text



def _on_camera_canon_set(snapshot: dict[str, Any], shot: Shot | None = None) -> set[str]:
    canons: set[str] = set()
    for item in _people(snapshot):
        if isinstance(item, dict) and _text(item.get("name")):
            canon = canonical_character_name(_text(item.get("name")))
            if canon:
                canons.add(canon)
                canons.add(canon.split()[0])
    if not canons and shot is not None and getattr(shot, "scene", None) and getattr(shot.scene, "script_version", None):
        project = shot.scene.script_version.project
        if project is not None:
            selected = set(shot.character_ids or [])
            for char in project.characters or []:
                if char.id in selected and char.name:
                    canon = canonical_character_name(char.name)
                    if canon:
                        canons.add(canon)
                        canons.add(canon.split()[0])
    return canons


_STOP_FIRST_NAMES = {
    "THE", "A", "AN", "MR", "MRS", "MS", "DR", "IN", "ON", "AT", "TO", "FOR",
    "OF", "WITH", "BY", "AND", "OR", "NO", "ALL", "SOME", "EVERY", "THIS", "THAT",
}


def _detect_offscreen_phone_characters(
    snapshot: dict[str, Any],
    video_prompt: str,
    shot: Shot | None = None,
) -> list[tuple[str, Character | None, str]]:
    """Return (display_name, Character | None, voice_description) for off-screen phone speakers in this shot."""
    action = _text(shot.action if shot is not None else snapshot.get("action"))
    framing = _text(shot.framing if shot is not None else snapshot.get("framing"))
    sound = _text(getattr(shot, "sound", "") if shot is not None else snapshot.get("sound"))
    combined = f"{action}\n{video_prompt}\n{framing}\n{sound}"
    if not PHONE_CALL_PATTERN.search(combined):
        return []

    on_camera_canons = _on_camera_canon_set(snapshot, shot)
    project_chars: list[Character] = []
    if shot is not None and getattr(shot, "scene", None) and getattr(shot.scene, "script_version", None):
        project = shot.scene.script_version.project
        if project is not None:
            project_chars = [
                c for c in (project.characters or [])
                if (getattr(c, "kind", None) or "character") == "character"
            ]
    by_name: dict[str, Character] = {}
    for char in project_chars:
        key = canonical_character_name(char.name)
        if key:
            by_name[key] = char
            first = key.split()[0]
            if first and first.upper() not in _STOP_FIRST_NAMES and first not in by_name:
                by_name[first] = char

    found: list[tuple[str, Character | None, str]] = []
    seen: set[str] = set()

    dialogue = (shot.dialogue if shot is not None else None) or snapshot.get("dialogue") or []
    for line in dialogue:
        if not isinstance(line, dict):
            continue
        raw_speaker = _text(line.get("character") or line.get("speaker"))
        if not raw_speaker:
            continue
        canon = canonical_character_name(raw_speaker)
        if not canon or canon in seen:
            continue
        is_offscreen_cue = bool(OFFSCREEN_SPEAKER_PATTERN.search(raw_speaker))
        is_unselected_speaker = bool(
            on_camera_canons
            and canon not in on_camera_canons
            and (not canon.split()[0] or canon.split()[0].upper() in _STOP_FIRST_NAMES or canon.split()[0] not in on_camera_canons)
        )
        if is_offscreen_cue or is_unselected_speaker:
            first_candidate = canon.split()[0] if canon.split() else ""
            valid_first = first_candidate if (first_candidate and first_candidate.upper() not in _STOP_FIRST_NAMES) else None
            live_char = by_name.get(canon) or (by_name.get(valid_first) if valid_first else None)
            voice_desc = _resolve_character_voice(live_char) if live_char is not None else ""
            seen.add(canon)
            found.append((canon, live_char, voice_desc))

    search_text = f"{action}\n{video_prompt}\n{sound}"
    # If the shot explicitly notes the caller's voice is inaudible / no off-screen speech, do not infer caller from action text.
    if re.search(
        r"\b(?:caller(?:['\x27]s)?\s+voice\s+is\s+not\s+audible|caller\s+(?:is\s+)?not\s+audible|no\s+off-screen\s+speech)\b",
        search_text,
        re.IGNORECASE,
    ):
        dialogue_speakers = {
            canonical_character_name(_text(l.get("character") or l.get("speaker")))
            for l in dialogue
            if isinstance(l, dict)
        }
        return [item for item in found if item[0] in dialogue_speakers]

    # Strip quoted dialogue strings so names mentioned within dialogue (e.g. "Isabel, send a car...") are not treated as offscreen speakers
    clean_search = re.sub(r'["\u201c\u201d][^"\u201c\u201d]*["\u201c\u201d]|\'[^\']*\'', ' ', search_text)
    for char in project_chars:
        canon = canonical_character_name(char.name)
        if not canon or canon in seen:
            continue
        first = canon.split()[0]
        has_valid_first = bool(first and first.upper() not in _STOP_FIRST_NAMES and len(first) >= 3)
        if canon in on_camera_canons or (has_valid_first and first in on_camera_canons):
            continue
        parts = [re.escape(canon)]
        if has_valid_first:
            parts.append(re.escape(first))
        c_pattern = rf"\b(?:{'|'.join(parts)})\b"
        cue_pattern = re.compile(
            rf"(?:"
            rf"{c_pattern}(?:'s)?\s+(?:filtered|phone|handset|earpiece|voice|line|report|speech|audio|call|speaks|says|replies|talking)\b|"
            rf"(?:listens to|hears|answering|on the (?:phone|line|call) with)\s+{c_pattern}\b|"
            rf"(?:filtered|phone|handset|earpiece)\s+(?:voice|line|speech|audio)\s+(?:of|from)\s+{c_pattern}\b|"
            rf"{c_pattern}\s+on the (?:phone|line|other end|call)\b"
            rf")",
            re.IGNORECASE,
        )
        if cue_pattern.search(clean_search):
            seen.add(canon)
            found.append((canon, char, _resolve_character_voice(char)))

    return found


def _collect_voice_entries(
    snapshot: dict[str, Any],
    shot: Shot | None = None,
    video_prompt: str = "",
) -> list[str]:
    characters = _people(snapshot)
    snapshot_voices = {
        str(item.get("id") or item.get("name") or ""): item
        for item in (snapshot.get("character_voices") or [])
        if isinstance(item, dict)
    }
    project_chars: list[Character] = []
    if shot is not None and getattr(shot, "scene", None) and getattr(shot.scene, "script_version", None):
        project = shot.scene.script_version.project
        if project is not None:
            project_chars = [
                c for c in (project.characters or [])
                if (getattr(c, "kind", None) or "character") == "character"
            ]
    by_id = {char.id: char for char in project_chars}
    by_name: dict[str, Character] = {}
    for char in project_chars:
        key = canonical_character_name(char.name)
        if key:
            by_name[key] = char
            first = key.split()[0]
            if first and first.upper() not in _STOP_FIRST_NAMES and first not in by_name:
                by_name[first] = char

    offscreen_phone_chars = _detect_offscreen_phone_characters(
        snapshot,
        video_prompt or _text(getattr(shot, "video_prompt", "") if shot is not None else snapshot.get("video_prompt")),
        shot=shot,
    )
    offscreen_canons = {canon for canon, _, _ in offscreen_phone_chars}

    seen_names: set[str] = set()
    entries: list[str] = []

    def add_voice(
        raw_name: str,
        char_id: str | None = None,
        fallback_voice: str = "",
        fallback_notes: str = "",
        is_offscreen_phone: bool = False,
    ) -> None:
        clean_name = _text(raw_name)
        canon = canonical_character_name(clean_name)
        if not canon or canon in seen_names:
            return
        live_char = (by_id.get(char_id) if char_id else None) or by_name.get(canon) or (
            by_name.get(canon.split()[0]) if canon.split() else None
        )
        voice_desc = ""
        if live_char is not None:
            voice_desc = _resolve_character_voice(live_char)
        if not voice_desc:
            voice_desc = _text(fallback_voice)
        if not voice_desc and fallback_notes:
            voice_desc = infer_voice_description(canon, fallback_notes)
        if voice_desc:
            seen_names.add(canon)
            if is_offscreen_phone or canon in offscreen_canons:
                entries.append(
                    f"{canon} (off-screen phone caller, unseen filtered telephone earpiece voice-over only — never lip-sync on-camera face to this voice): {voice_desc.rstrip('.')}"
                )
            else:
                entries.append(f"{clean_name}: {voice_desc.rstrip('.')}")

    dialogue = (shot.dialogue if shot is not None else None) or snapshot.get("dialogue") or []
    char_by_canon = {
        canonical_character_name(_text(item.get("name"))): item
        for item in characters
        if isinstance(item, dict) and _text(item.get("name"))
    }
    if dialogue:
        for line in dialogue:
            if not isinstance(line, dict):
                continue
            raw_speaker = _text(line.get("character") or line.get("speaker"))
            if not raw_speaker:
                continue
            speaker_parts = expand_character_cue_names(raw_speaker)
            for speaker in (speaker_parts if len(speaker_parts) > 1 else [raw_speaker]):
                canon = canonical_character_name(speaker)
                item = char_by_canon.get(canon) or {}
                char_id = _text(item.get("id"))
                sv = snapshot_voices.get(char_id) or snapshot_voices.get(canon) or snapshot_voices.get(speaker) or {}
                add_voice(
                    speaker,
                    char_id=char_id or None,
                    fallback_voice=_text(item.get("voice_description") or sv.get("voice_description")),
                    fallback_notes=_text(item.get("notes") or sv.get("notes")),
                    is_offscreen_phone=bool(OFFSCREEN_SPEAKER_PATTERN.search(raw_speaker)),
                )
    elif not re.search(r'\b(?:no\s+(?:spoken\s+words|speech|dialogue|voice|audible)|complete\s+silence|without\s+saying\s+any\s+words)\b', video_prompt, re.IGNORECASE):
        for item in characters:
            if not isinstance(item, dict):
                continue
            char_id = _text(item.get("id"))
            name = _text(item.get("name"))
            sv = snapshot_voices.get(char_id) or snapshot_voices.get(name) or {}
            add_voice(
                name,
                char_id=char_id or None,
                fallback_voice=_text(item.get("voice_description") or sv.get("voice_description")),
                fallback_notes=_text(item.get("notes") or sv.get("notes")),
            )

    for canon, live_char, voice_desc in offscreen_phone_chars:
        if canon not in seen_names:
            add_voice(
                canon,
                char_id=live_char.id if live_char is not None else None,
                fallback_voice=voice_desc,
                fallback_notes=_text(live_char.notes) if live_char is not None else "",
                is_offscreen_phone=True,
            )

    return entries


def _phone_routing_instruction(
    snapshot: dict[str, Any],
    video_prompt: str,
    shot: Shot | None = None,
) -> str:
    action = _text(shot.action if shot is not None else snapshot.get("action"))
    framing = _text(shot.framing if shot is not None else snapshot.get("framing"))
    sound = _text(getattr(shot, "sound", "") if shot is not None else snapshot.get("sound"))
    combined = f"{action}\n{video_prompt}\n{framing}\n{sound}"
    if not PHONE_CALL_PATTERN.search(combined):
        return ""

    on_camera_canons = _on_camera_canon_set(snapshot, shot)
    offscreen_phone_chars = _detect_offscreen_phone_characters(snapshot, video_prompt, shot=shot)
    offscreen_canons = {canon for canon, _, _ in offscreen_phone_chars}

    dialogue = (shot.dialogue if shot is not None else None) or snapshot.get("dialogue") or []
    on_camera_speakers: list[str] = []
    on_camera_lines: list[str] = []
    for line in dialogue:
        if not isinstance(line, dict):
            continue
        raw_speaker = _text(line.get("character") or line.get("speaker"))
        text = _text(line.get("text") or line.get("line"))
        if not raw_speaker or not text:
            continue
        if OFFSCREEN_SPEAKER_PATTERN.search(raw_speaker):
            continue
        canon = canonical_character_name(raw_speaker)
        if canon in offscreen_canons or (
            on_camera_canons
            and canon not in on_camera_canons
            and canon.split()[0] not in on_camera_canons
        ):
            continue
        if canon and canon not in on_camera_speakers:
            on_camera_speakers.append(canon)
        if text not in on_camera_lines:
            on_camera_lines.append(text)

    if not on_camera_speakers and re.search(r'\bspeaks\s*:|["\u201c][^"\u201d]+["\u201d]', video_prompt, re.IGNORECASE):
        for item in _people(snapshot):
            if isinstance(item, dict) and _text(item.get("name")):
                on_camera_speakers.append(_text(item.get("name")))

    if not on_camera_speakers:
        return ""

    speakers_label = ", ".join(on_camera_speakers)
    base_routing = (
        f"Voice routing: {speakers_label} speaks on camera with their natural, direct, unfiltered chest voice "
        "in the local space; do not apply telephone, radio, or speakerphone filtering to on-camera speech."
    )

    raw_on_camera_names: list[str] = []
    for item in _people(snapshot):
        if isinstance(item, dict) and _text(item.get("name")):
            name = canonical_character_name(_text(item.get("name")))
            if name and name not in raw_on_camera_names:
                raw_on_camera_names.append(name)
    if not raw_on_camera_names and shot is not None and getattr(shot, "scene", None) and getattr(shot.scene, "script_version", None):
        project = shot.scene.script_version.project
        if project is not None:
            selected = set(shot.character_ids or [])
            for char in project.characters or []:
                if char.id in selected and char.name:
                    name = canonical_character_name(char.name)
                    if name and name not in raw_on_camera_names:
                        raw_on_camera_names.append(name)

    silent_on_camera = [
        name for name in raw_on_camera_names
        if name not in on_camera_speakers and name.split()[0] not in on_camera_speakers
    ]
    silent_clause = (
        f" Silent character lock: {', '.join(silent_on_camera)}'s lips and jaw MUST remain completely closed and motionless at all times — do NOT lip-sync {', '.join(silent_on_camera)} to any speech or audio."
        if silent_on_camera
        else ""
    )

    has_offscreen_phone_speech = bool(
        offscreen_phone_chars or _OFFSCREEN_PHONE_CUE_RE.search(f"{video_prompt}\n{sound}")
    )
    if not has_offscreen_phone_speech:
        return f"{base_routing}{silent_clause}"

    offscreen_label = (
        ", ".join(canon for canon, _, _ in offscreen_phone_chars)
        if offscreen_phone_chars
        else "the off-screen phone caller"
    )
    quoted_lines = ", ".join(f'"{line}"' for line in on_camera_lines)
    lines_clause = f" ({quoted_lines})" if quoted_lines else ""
    return (
        f"{base_routing} "
        f"Lip-sync lock: {offscreen_label} is an unseen off-screen telephone caller heard ONLY as filtered, "
        f"band-limited earpiece voice-over — {speakers_label}'s lips and jaw MUST remain completely closed and "
        f"motionless in silent listening while {offscreen_label}'s phone line plays, and {speakers_label}'s mouth "
        f"must move ONLY when speaking {speakers_label}'s own on-camera dialogue{lines_clause}.{silent_clause}"
    )


_SCREEN_POSITION_RE = re.compile(r"\bscreen[-\s]?(left|right|center|centre)\b", re.IGNORECASE)


def _speaker_position_lock(snapshot: dict[str, Any], shot: Shot | None = None) -> str:
    """Pin each on-camera dialogue line to the screen position its speaker holds in the frame."""
    names = [
        canonical_character_name(_text(item.get("name")))
        for item in _people(snapshot)
        if isinstance(item, dict) and _text(item.get("name"))
    ]
    names = [name for name in dict.fromkeys(names) if name]
    if len(names) < 2:
        return ""
    name_patterns = {
        name: re.compile(r"@?\b" + r"[\s_]".join(map(re.escape, name.split())) + r"\b", re.IGNORECASE)
        for name in names
    }
    sources = [
        _text(shot.framing if shot is not None else snapshot.get("framing")),
        _text(shot.action if shot is not None else snapshot.get("action")),
        _text(getattr(shot, "image_prompt", "") if shot is not None else snapshot.get("image_prompt")),
    ]
    positions: dict[str, str] = {}
    for source in sources:
        for match in _SCREEN_POSITION_RE.finditer(source):
            window_start = max(0, match.start() - 90)
            window = source[window_start:match.start()]
            protected = re.sub(r"\b(MR|MRS|MS|DR|PROF|JR|SR)\.", r"\1 ", window, flags=re.I)
            clause = re.split(r"[.;!?]", protected)[-1]
            nearest: tuple[int, str] | None = None
            for name, pattern in name_patterns.items():
                plain_name = re.sub(r"\b(MR|MRS|MS|DR|PROF|JR|SR)\.", r"\1", name, flags=re.I)
                plain_pattern = re.compile(
                    r"@?\b" + r"[\s_]".join(map(re.escape, plain_name.split())) + r"\b",
                    re.IGNORECASE,
                )
                for name_match in (*pattern.finditer(window), *plain_pattern.finditer(clause)):
                    if nearest is None or name_match.start() > nearest[0]:
                        nearest = (name_match.start(), name)
            if nearest is None:
                continue
            side = match.group(1).lower().replace("centre", "center")
            positions.setdefault(nearest[1], f"screen-{side}")
    if len(set(positions.values())) < 2:
        return ""

    dialogue = (shot.dialogue if shot is not None else None) or snapshot.get("dialogue") or []
    entries: list[str] = []
    for line in dialogue:
        if not isinstance(line, dict):
            continue
        speaker = canonical_character_name(_text(line.get("character") or line.get("speaker")))
        text = _text(line.get("text") or line.get("line"))
        position = positions.get(speaker)
        if not position or not text:
            continue
        entries.append(f'{speaker} on {position} speaks "{text}"')
    if not entries:
        return ""
    return (
        f"Speaker lock: {'; '.join(entries)}. Each line is lip-synced only by the character at that screen position; "
        "whoever is not speaking keeps their lips closed while listening."
    )


def _collect_visual_profiles(
    snapshot: dict[str, Any],
    shot: Shot | None = None,
) -> list[str]:
    characters = _people(snapshot)
    snapshot_voices = {
        str(item.get("id") or item.get("name") or ""): item
        for item in (snapshot.get("character_voices") or [])
        if isinstance(item, dict)
    }
    project_chars: list[Character] = []
    if shot is not None and getattr(shot, "scene", None) and getattr(shot.scene, "script_version", None):
        project = shot.scene.script_version.project
        if project is not None:
            project_chars = list(project.characters or [])
    by_id = {char.id: char for char in project_chars}
    by_name = {canonical_character_name(char.name): char for char in project_chars if char.name}

    profiles: list[str] = []
    seen: set[str] = set()
    for item in characters:
        if not isinstance(item, dict):
            continue
        char_id = _text(item.get("id"))
        name = _text(item.get("name"))
        canon = canonical_character_name(name)
        if not canon or canon in seen:
            continue
        live_char = (by_id.get(char_id) if char_id else None) or by_name.get(canon)
        sv = snapshot_voices.get(char_id) or snapshot_voices.get(name) or {}
        notes = _text((live_char.notes if live_char is not None else "") or item.get("notes") or sv.get("notes"))
        if not notes:
            continue
        # Keep the first two sentences (physical identity + wardrobe) so plot notes don't clutter the video prompt.
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", notes) if part.strip()]
        visual_summary = " ".join(sentences[:2]).rstrip(".")
        if visual_summary:
            seen.add(canon)
            profiles.append(f"{name}: {visual_summary}")
    return profiles


def video_continuity_prompt(
    snapshot: dict[str, Any] | None,
    video_prompt: str,
    aspect_ratio: str,
    *,
    shot: Shot | None = None,
) -> str:
    sanitized_video_prompt = _clip_relative_timecodes(_sanitize_phone_video_prompt(video_prompt), shot)
    anchor = (
        "Approved storyboard frame: use the supplied image as the exact opening frame and visual anchor; "
        "preserve its composition, faces, wardrobe, set, and lighting throughout the clip, "
        "moving or removing props only as explicitly performed in the shot action. "
        "No on-screen text, subtitles, UI elements, or location tags."
    )
    if not snapshot:
        return "\n\n".join(part for part in (sanitized_video_prompt.strip(), anchor) if part)

    # Sync snapshot characters with the live shot.character_ids selection and any
    # focal_character lock so unchecked characters never leak into the video prompt.
    effective_snapshot = dict(snapshot)
    all_project_people: list[str] = []
    if shot is not None and getattr(shot, "scene", None) and getattr(shot.scene, "script_version", None):
        project = shot.scene.script_version.project
        if project is not None:
            all_project_people = [
                c.name for c in (project.characters or [])
                if (getattr(c, "kind", None) or "character") == "character" and _text(c.name)
            ]
    is_locked_focal = bool(effective_snapshot.get("lock_blocking") and (_text(effective_snapshot.get("focal_character_name")) or _text(effective_snapshot.get("focal_character_id"))))
    focal_name = _text(effective_snapshot.get("focal_character_name")) if is_locked_focal else ""
    focal_id = _text(effective_snapshot.get("focal_character_id")) if is_locked_focal else ""
    live_selected_ids = set(shot.character_ids or []) if shot is not None and shot.character_ids is not None else None

    raw_chars = list(effective_snapshot.get("characters") or [])
    filtered_chars: list[dict[str, Any]] = []
    for item in raw_chars:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind") or "character"
        cid = _text(item.get("id"))
        cname = _text(item.get("name"))
        if kind == "character":
            if focal_id and cid and cid != focal_id:
                continue
            if focal_name and cname and cname.upper() != focal_name.upper():
                continue
            if live_selected_ids is not None and cid and cid not in live_selected_ids:
                continue
        filtered_chars.append(item)
    effective_snapshot["characters"] = filtered_chars

    scene = effective_snapshot.get("scene") or {}
    characters = _people(effective_snapshot)
    if not all_project_people:
        all_project_people = [
            _text(item.get("name"))
            for item in raw_chars
            if isinstance(item, dict) and (item.get("kind") or "character") == "character" and _text(item.get("name"))
        ]

    live_framing = _text(shot.framing) if shot is not None else _text(effective_snapshot.get("framing"))
    live_action = _text(shot.action) if shot is not None else _text(effective_snapshot.get("action"))

    single_focal_name = focal_name if is_locked_focal else ""
    selected_people = [_text(item.get("name")) for item in characters if _text(item.get("name"))]
    if single_focal_name:
        sanitized_video_prompt = _focus_text_on_character(
            sanitized_video_prompt, single_focal_name, all_project_people, strip_environment=True
        )
        sanitized_video_prompt = re.sub(
            r"(?i)\s*Other\s+visible\s+(?:residents|characters|people|participants)\s+[^.\n]+\.",
            f" Single-subject frame: ONLY {single_focal_name} is visible on camera; no other people appear or enter the frame.",
            sanitized_video_prompt,
        )
        live_framing = _focus_text_on_character(
            live_framing, single_focal_name, all_project_people, strip_environment=True, is_framing=True
        )
    elif selected_people and all_project_people:
        unselected_names = [
            _text(n) for n in all_project_people
            if _text(n) and _text(n).upper() not in {p.upper() for p in selected_people}
        ]
        if any(_mentions_char(sanitized_video_prompt, u) for u in unselected_names):
            tags_str = ", ".join(_char_tag(p) for p in selected_people)
            sanitized_video_prompt = re.sub(
                r"(?i)(Visible character references:\s*)([^\n.]+)(\.?)",
                f"\\1{tags_str}\\3",
                sanitized_video_prompt,
            )
        if len(selected_people) == 1:
            live_framing = _focus_text_on_character(
                live_framing, selected_people[0], all_project_people, strip_environment=bool(effective_snapshot.get("lock_blocking")), is_framing=True
            )
        else:
            live_framing = _sync_multi_cast_text(
                live_framing, selected_people, all_project_people, is_framing=True
            )
            if effective_snapshot.get("lock_blocking"):
                live_framing = _strip_wide_scene_for_locked_blocking(
                    live_framing, selected_people, is_framing=True
                )

    cast_names = ", ".join(item.get("name", "") for item in characters if item.get("name"))
    package = [
        anchor,
        f"Shot framing: {live_framing or 'preserve the supplied frame'}. Aspect ratio: {aspect_ratio}.",
        f"Shot action: {live_action or 'continue the action naturally from the supplied frame'}.",
    ]
    if cast_names:
        package.append(
            f"Character identity continuity: {cast_names}; preserve canonical identities without face, age, hair, body, or voice changes."
        )
    visual_profiles = _collect_visual_profiles(effective_snapshot, shot=shot)
    if visual_profiles:
        package.append(f"Character visual profiles: {'; '.join(visual_profiles)}.")
    voice_entries = _collect_voice_entries(effective_snapshot, shot=shot, video_prompt=sanitized_video_prompt)
    if voice_entries:
        package.append(f"Character voices: {'; '.join(voice_entries)}.")
    speaker_lock = _speaker_position_lock(effective_snapshot, shot=shot)
    if speaker_lock:
        package.append(speaker_lock)
    phone_routing = _phone_routing_instruction(effective_snapshot, sanitized_video_prompt, shot=shot)
    if phone_routing:
        package.append(phone_routing)
    is_locked_any = bool(effective_snapshot.get("lock_blocking"))
    shot_text = (
        "\n".join(part for part in (sanitized_video_prompt, live_framing, live_action) if part)
        if is_locked_any
        else _shot_text_for_prop_filter(effective_snapshot, shot, sanitized_video_prompt)
    )
    for key, label in (
        ("location", "Location"), ("time_of_day", "Time of day"),
        ("set_notes", "Set"), ("lighting_notes", "Lighting"),
        ("wardrobe_notes", "Wardrobe"), ("props_notes", "Props"),
    ):
        if is_locked_any and key == "set_notes":
            # Keep background locked to the opening storyboard frame rather than listing
            # wide-room furniture that is outside the zoomed-in camera crop.
            continue
        value = _text(scene.get(key))
        if key == "wardrobe_notes":
            value = _filter_wardrobe_notes_for_shot(value, effective_snapshot.get("characters", []))
        elif key == "props_notes":
            value = _filter_props_notes_for_shot(value, shot_text)
        elif key == "lighting_notes" and single_focal_name:
            value = re.sub(r"(?i)\s+through\s+the\s+curtained\s+[^,.\n]+", "", value)
        if value and value.lower() != "unspecified":
            clean_value = re.sub(
                r"@([A-Z0-9_]+)",
                lambda m: m.group(1).replace("_", " ").title(),
                value,
            )
            package.append(f"{label} continuity: {clean_value}.")
    return "\n\n".join([sanitized_video_prompt.strip(), *package]).strip()


def cast_continuity_warnings(shot: Shot) -> list[str]:
    """Flag unexplained omissions; never silently add people to a composition."""
    people = {c.id: c.name for c in shot.scene.script_version.project.characters if c.kind == "character"}
    established: set[str] = set()
    for previous in sorted(shot.scene.shots, key=lambda item: item.order_index):
        if previous.order_index >= shot.order_index:
            break
        established.update(set(previous.character_ids or []) & people.keys())
        for character_id in list(established):
            name = re.escape(people[character_id])
            if re.search(rf"\b{name}\s+(?:exits|leaves|walks out|steps out)\b", previous.action or "", re.I):
                established.remove(character_id)
    text = "\n".join([shot.framing or "", shot.action or "", shot.image_prompt or "", shot.video_prompt or ""])
    protected_text = re.sub(r"\b(MR|MRS|MS|DR|PROF|JR|SR)\.", r"\1", text, flags=re.I)
    unexplained = []
    for character_id in sorted(established - set(shot.character_ids or []), key=lambda item: people[item]):
        name = people[character_id]
        plain_name = re.sub(r"\b(MR|MRS|MS|DR|PROF|JR|SR)\.", r"\1", name, flags=re.I)
        if re.search(
            rf"\b(?:{re.escape(name)}|{re.escape(plain_name)})\b[^.!?\n]{{0,100}}\b(?:off[- ]camera|off[- ]screen|outside (?:the )?frame|exits|leaves)\b",
            protected_text,
            re.I,
        ):
            continue
        unexplained.append(name)
    if not unexplained:
        return []
    return [f"Earlier in this scene: {', '.join(unexplained)}. They are not tagged here. Include them in the cast and composition, or explicitly describe their exit or off-camera position."]


def shot_continuity_status(shot: Shot) -> dict[str, Any]:
    snapshot = shot.continuity_snapshot
    scene = snapshot.get("scene") if snapshot else _scene_package(shot.scene)
    cast = snapshot.get("characters", []) if snapshot else []
    if not snapshot:
        project = shot.scene.script_version.project
        selected_ids = list(shot.character_ids or [])
        selected = set(selected_ids)
        cast = [
            {"id": character.id, "name": character.name, "reference_ids": [ref.id for ref in character.references]}
            for character in project.characters if character.id in selected
        ]
        known_ids = {item["id"] for item in cast}
        cast.extend({"id": item, "name": "Unknown character", "reference_ids": []}
                    for item in selected_ids if item not in known_ids)
    missing_names: list[str] = []
    characters_status: list[dict[str, Any]] = []
    for character in cast:
        character_id = character.get("id")
        reference_ids = list(character.get("reference_ids") or [])
        available_ids: list[str] = []
        if character_id:
            refs = shot.scene.script_version.project.characters
            owner = next((item for item in refs if item.id == character_id), None)
            if owner:
                for ref in owner.references:
                    if (not reference_ids or ref.id in reference_ids) and (settings.media_dir / ref.filename).is_file():
                        available_ids.append(ref.id)
        has_reference = bool(available_ids)
        if not has_reference:
            missing_names.append(str(character.get("name") or "Unknown character"))
        characters_status.append({
            "character_id": str(character_id or ""),
            "name": str(character.get("name") or "Unknown character"),
            "has_reference": has_reference,
            "reference_ids": available_ids,
        })
    missing_fields: list[str] = []
    for key in ("location", "time_of_day", "set_notes", "lighting_notes", "wardrobe_notes", "props_notes"):
        value = _text((scene or {}).get(key))
        if not value or (key == "location" and value.lower() == "unspecified"):
            missing_fields.append(key)
    return {
        "snapshot": snapshot,
        "characters": characters_status,
        "missing_character_references": missing_names,
        "missing_fields": missing_fields,
        "ready": not missing_names,
        "cast_warnings": cast_continuity_warnings(shot),
    }
