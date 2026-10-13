import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import backend.api as api_module
import backend.database as db_module
import backend.deep_review as deep_review_module
import backend.tasks as tasks_module
from backend.api import app, get_db
from backend.deep_review_context import (
    _validate_evidence,
    build_context,
    context_for_prompt,
    fingerprint,
    preflight,
    validate_patches,
    validate_report,
)


def test_cast_evidence_validates_exact_field_and_character():
    context = {"current": {"version_id": "v1", "episode_id": "e1"}, "cast": [
        {"id": "box", "name": "Counting Box", "voice_description": "Clear speaking voice"}
    ]}
    evidence = {"character_id": "box", "field": "voice_description", "quote": "Clear speaking voice"}
    result = _validate_evidence(evidence, context)
    assert result["character_id"] == "box"
    assert result["label"] == "Cast · Counting Box · voice description"
    assert _validate_evidence({**evidence, "quote": "Silent"}, context) is None
    assert _validate_evidence({**evidence, "character_id": "invented"}, context) is None
    assert _validate_evidence({**evidence, "field": "source_text"}, context) is None
    assert _validate_evidence({**evidence, "shot_id": "invented"}, context) is None
from backend.deep_review_provider import editorial_response
from backend.migrations import migrate_schema
from backend.models import (
    Base,
    Character,
    Episode,
    Job,
    Project,
    ProjectTimeline,
    Scene,
    ScriptVersion,
    Shot,
    ShotImage,
    ShotTake,
    Suggestion,
)
from backend.providers import (
    CompatibleScriptProvider,
    MockScriptProvider,
    OllamaScriptProvider,
)
from backend.settings import settings


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_deep_review.db"
    test_engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    Base.metadata.create_all(bind=test_engine)
    migrate_schema(test_engine)

    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(api_module, "engine", test_engine)
    monkeypatch.setattr(deep_review_module, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(tasks_module, "SessionLocal", TestingSessionLocal)

    def override_get_db():
        with TestingSessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    yield TestingSessionLocal
    app.dependency_overrides.pop(get_db, None)
    test_engine.dispose()


def _seed_synthetic_series(SessionLocal):
    """Create a synthetic multi-episode fixture covering continuity, prompts, audio, and time jumps."""
    with SessionLocal() as db:
        project = Project(
            title="Signal Line",
            status="review",
            aspect_ratio="16:9",
            target_duration_seconds=60,
            text_provider="ollama",
        )
        db.add(project)
        db.flush()

        db.add_all(
            [
                Character(
                    project_id=project.id,
                    name="Maya Lin",
                    notes="Lead investigator, dark wool coat, carries brass key.",
                ),
                Character(
                    project_id=project.id,
                    name="Cole Vance",
                    notes="Dispatcher at Harbor Control, calm baritone.",
                ),
            ]
        )

        # Episode 1: has an approved v1 and an unapproved draft v2 (approved v1 must be selected)
        ep1 = Episode(
            project_id=project.id,
            episode_number=1,
            title="Harbor Lock",
            status="shot_plan",
        )
        # Episode 2: only draft version exists (must be included and labeled draft=True)
        ep2 = Episode(
            project_id=project.id,
            episode_number=2,
            title="Night Shift",
            status="review",
        )
        # Episode 3: current episode under review
        ep3 = Episode(
            project_id=project.id,
            episode_number=3,
            title="Dead Drop",
            status="review",
        )
        # Episode 4: future episode (must be excluded when reviewing Episode 3)
        ep4 = Episode(
            project_id=project.id,
            episode_number=4,
            title="Aftermath",
            status="review",
        )
        db.add_all([ep1, ep2, ep3, ep4])
        db.flush()

        ep1_v1 = ScriptVersion(
            project_id=project.id,
            episode_id=ep1.id,
            version_number=1,
            source_text=(
                "SHOT 1: PIER GATE (0:00-0:05)\n"
                "Maya locks the iron gate and pockets the brass key.\n"
                'MAYA: "Nobody gets through tonight."'
            ),
            status="approved",
        )
        ep1_v2_draft = ScriptVersion(
            project_id=project.id,
            episode_id=ep1.id,
            version_number=2,
            source_text="SHOT 1: PIER GATE (0:00-0:05)\nDraft rewrite that was not approved.",
            status="review",
        )
        ep2_v1_draft = ScriptVersion(
            project_id=project.id,
            episode_id=ep2.id,
            version_number=1,
            source_text=(
                "SHOT 1: CONTROL ROOM (0:00-0:05)\n"
                "Cole remains at Harbor Control while Maya drives north.\n"
                'COLE: "Keep the channel open."'
            ),
            status="review",
        )
        ep3_v1 = ScriptVersion(
            project_id=project.id,
            episode_id=ep3.id,
            version_number=1,
            source_text=(
                "SHOT 1: DINER BOOTH (0:00-0:05)\n"
                "Maya sits in the corner booth across from Cole. Later that evening after driving back.\n"
                'MAYA: "Nobody gets through tonight."\n'
                'COLE (filtered phone voice): "I am still at Harbor Control."\n\n'
                "SHOT 2: DINER COUNTER (0:05-0:10)\n"
                "Without sitting down, Maya remains standing by the door and gasps in terror at the empty cup.\n"
                'MAYA: "We already said nobody gets through tonight."\n'
            ),
            status="review",
        )
        ep4_v1 = ScriptVersion(
            project_id=project.id,
            episode_id=ep4.id,
            version_number=1,
            source_text="SHOT 1: ROOFTOP (0:00-0:05)\nFuture episode spoiler.",
            status="approved",
        )
        db.add_all([ep1_v1, ep1_v2_draft, ep2_v1_draft, ep3_v1, ep4_v1])
        db.flush()

        for ver, title, action, dialogue in [
            (
                ep1_v1,
                "Pier Gate",
                "Maya locks the iron gate and pockets the brass key.",
                [{"character": "Maya", "text": "Nobody gets through tonight."}],
            ),
            (
                ep1_v2_draft,
                "Draft Pier",
                "Draft rewrite that was not approved.",
                [],
            ),
            (
                ep2_v1_draft,
                "Control Room",
                "Cole remains at Harbor Control while Maya drives north.",
                [{"character": "Cole", "text": "Keep the channel open."}],
            ),
            (
                ep4_v1,
                "Rooftop",
                "Future episode spoiler.",
                [],
            ),
        ]:
            sc = Scene(
                script_version_id=ver.id,
                order_index=0,
                title=title,
                location=title,
                time_of_day="Night",
                start_ms=0,
                end_ms=5000,
                action=action,
                dialogue=dialogue,
            )
            db.add(sc)
            db.flush()
            db.add(
                Shot(
                    scene_id=sc.id,
                    order_index=0,
                    title=title,
                    start_ms=0,
                    end_ms=5000,
                    duration_ms=5000,
                    framing="Medium",
                    action=action,
                    dialogue=dialogue,
                    image_prompt=action,
                    video_prompt=action,
                )
            )

        ep3_scene = Scene(
            script_version_id=ep3_v1.id,
            order_index=0,
            title="Diner Booth",
            location="Harbor Diner",
            time_of_day="Night",
            start_ms=0,
            end_ms=10000,
            action="Maya and Cole talk.",
            dialogue=[],
            set_notes="Neon rain on window",
        )
        db.add(ep3_scene)
        db.flush()

        # Shot 1: seated vs standing contradiction with Shot 2, plus private phone audio with 2 speakers
        shot1 = Shot(
            scene_id=ep3_scene.id,
            order_index=0,
            title="Diner Booth",
            start_ms=0,
            end_ms=5000,
            duration_ms=5000,
            framing="Medium",
            action="Maya sits in the corner booth. Later that evening after driving back.",
            dialogue=[
                {"character": "Maya", "text": "Nobody gets through tonight."},
                {"character": "Cole", "text": "I am still at Harbor Control."},
            ],
            sound="Cole heard as filtered phone voice while rain patters.",
            image_prompt="Maya standing in the parking lot in bright daylight.",
            video_prompt="Alternating close-ups of Maya and Cole.",
        )
        # Shot 2: standing contradiction + unmotivated reaction + repeated dialogue
        shot2 = Shot(
            scene_id=ep3_scene.id,
            order_index=1,
            title="Diner Counter",
            start_ms=5000,
            end_ms=10000,
            duration_ms=5000,
            framing="Close-Up",
            action="Without sitting down, Maya remains standing by the door and gasps in terror at the empty cup.",
            dialogue=[
                {
                    "character": "Maya",
                    "text": "We already said nobody gets through tonight.",
                }
            ],
            sound="Diner hum",
            image_prompt="Close-up of Maya standing by the diner door.",
            video_prompt="Slow push in on Maya.",
        )
        db.add_all([shot1, shot2])
        db.flush()

        db.add(
            ShotImage(
                shot_id=shot1.id,
                filename="shot1-existing.png",
                mime_type="image/png",
                source="generated",
                provider="mock",
                prompt=shot1.image_prompt,
            )
        )
        take = ShotTake(
            shot_id=shot1.id,
            media_type="image",
            aspect_ratio="16:9",
            filename="shot1-existing.png",
            mime_type="image/png",
            source="generated",
            provider="mock",
            prompt=shot1.image_prompt,
            selected=True,
        )
        db.add(take)
        db.flush()
        shot1.selected_image_take_id = take.id

        db.add(
            Suggestion(
                script_version_id=ep3_v1.id,
                category="pacing",
                severity="warning",
                title="Import suggestion stays untouched",
                reason="Existing import suggestion.",
                target={"scene_index": 0, "shot_index": 0, "field": "action"},
                before_value=shot1.action,
                after_value=shot1.action,
                status="pending",
            )
        )
        db.add(
            ProjectTimeline(
                project_id=project.id,
                episode_id=ep3.id,
                revision=1,
                document={
                    "version": 1,
                    "primary": [
                        {
                            "id": "item-1",
                            "kind": "image",
                            "label": "Diner Booth",
                            "source": {"kind": "shot_image", "ref_id": shot1.id},
                            "duration_ms": 5000,
                            "source_in_ms": 0,
                            "source_out_ms": None,
                            "text": "",
                            "variant": {},
                        }
                    ],
                    "image_overlays": [],
                    "text_overlays": [],
                },
            )
        )
        db.commit()
        return {
            "project_id": project.id,
            "ep1_v1_id": ep1_v1.id,
            "ep1_v2_id": ep1_v2_draft.id,
            "ep2_v1_id": ep2_v1_draft.id,
            "ep3_id": ep3.id,
            "ep3_v1_id": ep3_v1.id,
            "ep4_v1_id": ep4_v1.id,
            "shot1_id": shot1.id,
            "shot2_id": shot2.id,
        }


def test_synthetic_multi_episode_context_and_preflight(isolated_db):
    ids = _seed_synthetic_series(isolated_db)
    with isolated_db() as db:
        ep3_v1 = db.get(ScriptVersion, ids["ep3_v1_id"])
        ctx = build_context(db, ep3_v1)

        # Verify earlier episodes are ordered by episode_number, Ep 1 uses approved v1, Ep 2 uses draft fallback, Ep 4 is excluded
        prev = ctx["previous"]
        assert len(prev) == 2
        assert prev[0]["episode_number"] == 1
        assert prev[0]["version_id"] == ids["ep1_v1_id"]
        assert prev[0]["draft"] is False
        assert prev[1]["episode_number"] == 2
        assert prev[1]["version_id"] == ids["ep2_v1_id"]
        assert prev[1]["draft"] is True

        # Prompt context deduplicates matched text while preserving full source_text
        prompt_ctx = context_for_prompt(ctx)
        assert prompt_ctx["previous"][0]["source_text"] == prev[0]["source_text"]

        # Preflight catches phone speaker routing and multi-angle cuts without flagging valid time jump
        pf = preflight(ctx)
        titles = {item["title"] for item in pf}
        assert "Check phone or off-screen speaker routing" in titles
        assert "Multiple camera cuts inside one generated shot" in titles
        assert not any("time jump" in item["title"].lower() for item in pf)


def test_evidence_and_patch_validation(isolated_db):
    ids = _seed_synthetic_series(isolated_db)
    with isolated_db() as db:
        ep3_v1 = db.get(ScriptVersion, ids["ep3_v1_id"])
        ctx = build_context(db, ep3_v1)

        raw_report = {
            "assessment": "Episode 3 has a seated/standing contradiction and prompt mismatch.",
            "strengths": ["Clear noir atmosphere in the diner."],
            "priorities": ["Resolve Maya's physical posture between Shot 1 and Shot 2."],
            "findings": [
                # 1. Valid confirmed inconsistency with exact quotes across Shot 1 & Shot 2
                {
                    "kind": "confirmed_inconsistency",
                    "category": "continuity",
                    "title": "Seated vs standing posture contradiction",
                    "reason": "Shot 1 places Maya seated in the booth, but Shot 2 says she remains standing without sitting down.",
                    "recommendation": "Have Maya stand up from the booth in Shot 2.",
                    "evidence": [
                        {
                            "version_id": ids["ep3_v1_id"],
                            "shot_id": ids["shot1_id"],
                            "field": "action",
                            "quote": "Maya sits in the corner booth.",
                        },
                        {
                            "version_id": ids["ep3_v1_id"],
                            "shot_id": ids["shot2_id"],
                            "field": "action",
                            "quote": "Without sitting down, Maya remains standing by the door",
                        },
                    ],
                    "patches": [
                        {
                            "shot_id": ids["shot2_id"],
                            "field": "action",
                            "before": "Without sitting down, Maya remains standing by the door and gasps in terror at the empty cup.",
                            "after": "Maya rises from the booth, steps to the door, and stares uneasily at the empty cup.",
                        }
                    ],
                },
                # 2. Confirmed inconsistency with one fabricated quote -> downgraded to possible_gap
                {
                    "kind": "confirmed_inconsistency",
                    "category": "prompt",
                    "title": "Image prompt daylight mismatch",
                    "reason": "Shot 1 image prompt specifies bright daylight while the scene is at night.",
                    "recommendation": "Update the image prompt to match the night diner setting.",
                    "evidence": [
                        {
                            "version_id": ids["ep3_v1_id"],
                            "shot_id": ids["shot1_id"],
                            "field": "image_prompt",
                            "quote": "Maya standing in the parking lot in bright daylight.",
                        },
                        {
                            "version_id": ids["ep3_v1_id"],
                            "shot_id": ids["shot1_id"],
                            "field": "action",
                            "quote": "This quote does not exist anywhere in the script.",
                        },
                    ],
                    "patches": [],
                },
                # 3. Completely unsupported finding -> dropped entirely
                {
                    "kind": "confirmed_inconsistency",
                    "category": "story",
                    "title": "Hallucinated contradiction",
                    "reason": "Invented claim.",
                    "recommendation": "None.",
                    "evidence": [
                        {
                            "version_id": ids["ep3_v1_id"],
                            "quote": "Completely fabricated excerpt",
                        }
                    ],
                    "patches": [],
                },
            ],
        }

        validated = validate_report(raw_report, ctx)
        assert len(validated["findings"]) == 2
        f0, f1 = validated["findings"]
        assert f0["kind"] == "confirmed_inconsistency"
        assert f0["evidence_incomplete"] is False
        assert len(f0["patches"]) == 1

        assert f1["kind"] == "possible_gap"
        assert f1["evidence_incomplete"] is True

        # Reject invalid patches (wrong before value or unsupported field)
        with pytest.raises(ValueError):
            validate_patches(
                [
                    {
                        "shot_id": ids["shot1_id"],
                        "field": "action",
                        "before": "wrong before value",
                        "after": "new value",
                    }
                ],
                ctx,
            )


def test_provider_contract_and_limits(monkeypatch):
    # Unsupported provider rejected
    with pytest.raises(ValueError, match="Deep review requires an AI text provider"):
        editorial_response(MockScriptProvider(), {"current": {}})

    # Input character limit enforced without truncation
    monkeypatch.setattr(settings, "deep_review_ollama_max_input_chars", 100)
    with pytest.raises(ValueError, match="No material was truncated"):
        editorial_response(OllamaScriptProvider(), {"current": {"text": "x" * 200}})

    # Multi-episode series with heavy production prompts compacts below 130k chars while preserving story and current shot prompts
    from backend.deep_review_provider import _build_editorial_prompt

    long_img = "Vertical 9:16 cinematic starting frame. " * 15
    long_vid = "Smooth camera push in preserving exact identity. " * 15
    previous = []
    for ep_num in range(1, 5):
        shots_src = []
        shots_data = []
        for s_num in range(1, 21):
            action = f"Episode {ep_num} Shot {s_num} narrative action happens here."
            dialogue_line = f"Line spoken in episode {ep_num} shot {s_num}."
            shots_src.append(
                f"SHOT {s_num} - BEAT {s_num}\n"
                f"Action:\n{action}\n"
                f'MAYA:\n"{dialogue_line}"\n'
                f"Sound: Rain and piano.\n"
                f"Image prompt:\n{long_img}\n"
                f"Video prompt:\n{long_vid}\n"
            )
            shots_data.append(
                {
                    "id": f"ep{ep_num}-s{s_num}",
                    "order_index": s_num - 1,
                    "shot_number": s_num,
                    "title": f"BEAT {s_num}",
                    "start_ms": (s_num - 1) * 5000,
                    "end_ms": s_num * 5000,
                    "duration_ms": 5000,
                    "framing": "Medium",
                    "action": action,
                    "dialogue": [{"character": "MAYA", "text": dialogue_line}],
                    "sound": "Rain and piano.",
                    "image_prompt": long_img,
                    "video_prompt": long_vid,
                    "title_card_text": "",
                    "character_ids": ["char-1"],
                }
            )
        previous.append(
            {
                "version_id": f"ver-{ep_num}",
                "episode_id": f"ep-{ep_num}",
                "episode_number": ep_num,
                "title": f"Episode {ep_num}",
                "source_text": "\n\n".join(shots_src),
                "draft": False,
                "scenes": [
                    {
                        "id": f"sc-{ep_num}",
                        "order_index": 0,
                        "title": "Main Scene",
                        "location": "Detailed coastal cafe location description " * 10,
                        "time_of_day": "Night",
                        "start_ms": 0,
                        "end_ms": 100000,
                        "action": "\n\n".join(s["action"] for s in shots_data),
                        "dialogue": [d for s in shots_data for d in s["dialogue"]],
                        "shots": shots_data,
                    }
                ],
            }
        )
    raw_multi_ctx = {
        "project": {"id": "proj-1", "title": "Tide"},
        "current": {**previous[-1], "version_id": "ver-5", "episode_number": 5},
        "previous": previous,
        "cast": [{"id": "char-1", "name": "Maya"}],
    }
    prompt_ctx = context_for_prompt(raw_multi_ctx)
    full_prompt = _build_editorial_prompt(prompt_ctx, None, 2_000_000)
    assert len(full_prompt) < 2_000_000

    compacted_prompt = _build_editorial_prompt(prompt_ctx, None, 100_000)
    assert len(compacted_prompt) <= 100_000
    assert "Episode 1 Shot 1 narrative action happens here." in compacted_prompt
    assert "Line spoken in episode 4 shot 20." in compacted_prompt
    assert long_img.strip() in compacted_prompt



def test_deep_review_api_lifecycle_apply_and_undo(isolated_db, monkeypatch):
    ids = _seed_synthetic_series(isolated_db)
    client = TestClient(app)

    fake_llm_report = {
        "assessment": "Strong setup, but Shot 1 image prompt and Shot 2 posture conflict with Shot 1.",
        "strengths": ["Effective cross-episode callback to Episode 1's brass key."],
        "priorities": ["Fix Shot 2 standing/seated contradiction."],
        "findings": [
            {
                "kind": "confirmed_inconsistency",
                "category": "continuity",
                "title": "Maya seated then remains standing without sitting",
                "reason": "Shot 1 states Maya sits in the corner booth, while Shot 2 states she remains standing without sitting down.",
                "recommendation": "Update Shot 2 so Maya rises from the booth.",
                "evidence": [
                    {
                        "version_id": ids["ep1_v1_id"],
                        "quote": "Maya locks the iron gate",
                    },
                    {
                        "version_id": ids["ep3_v1_id"],
                        "shot_id": ids["shot1_id"],
                        "field": "action",
                        "quote": "Maya sits in the corner booth.",
                    },
                    {
                        "version_id": ids["ep3_v1_id"],
                        "shot_id": ids["shot2_id"],
                        "field": "action",
                        "quote": "Without sitting down, Maya remains standing by the door",
                    },
                ],
                "patches": [
                    {
                        "shot_id": ids["shot2_id"],
                        "field": "action",
                        "before": "Without sitting down, Maya remains standing by the door and gasps in terror at the empty cup.",
                        "after": "Maya rises from the corner booth and pauses by the door.",
                    }
                ],
            },
            {
                "kind": "editorial_suggestion",
                "category": "dialogue",
                "title": "Repeated line from Episode 1",
                "reason": "Maya repeats her Episode 1 line verbatim in Shot 1 and Shot 2.",
                "recommendation": "Trim the repeated line in Shot 2.",
                "evidence": [
                    {
                        "version_id": ids["ep3_v1_id"],
                        "shot_id": ids["shot2_id"],
                        "field": "dialogue",
                        "quote": "We already said nobody gets through tonight.",
                    }
                ],
                "patches": [],
            },
        ],
    }

    monkeypatch.setattr(
        deep_review_module,
        "editorial_response",
        lambda provider, context, finding=None: (
            {
                "patches": [
                    {
                        "shot_id": ids["shot2_id"],
                        "field": "dialogue",
                        "before": [
                            {
                                "character": "Maya",
                                "text": "We already said nobody gets through tonight.",
                            }
                        ],
                        "after": [
                            {
                                "character": "Maya",
                                "text": "The lock on Pier Gate is still holding.",
                            }
                        ],
                    }
                ]
            }
            if finding
            else fake_llm_report
        ),
    )
    monkeypatch.setattr(
        api_module,
        "enqueue_deep_review",
        lambda provider, run_id, job_id: deep_review_module.run_deep_review_job(
            run_id, job_id
        ),
    )
    monkeypatch.setattr(
        api_module,
        "enqueue_deep_review_draft",
        lambda provider, run_id, finding_id, job_id: deep_review_module.run_deep_review_draft_job(
            run_id, finding_id, job_id
        ),
    )

    # 1. Start deep review
    res = client.post(f"/api/script-versions/{ids['ep3_v1_id']}/deep-review")
    assert res.status_code == 202
    run = res.json()
    assert run["status"] == "completed"
    assert run["stale"] is False
    assert len(run["context"]) == 3
    assert [c["episode_number"] for c in run["context"]] == [1, 2, 3]
    assert run["context"][0]["draft"] is False
    assert run["context"][1]["draft"] is True

    # 2. Verify existing suggestions, scenes, and media were NOT modified by running deep review
    with isolated_db() as db:
        suggestions = db.scalars(
            select(Suggestion).where(
                Suggestion.script_version_id == ids["ep3_v1_id"]
            )
        ).all()
        assert len(suggestions) == 1
        assert suggestions[0].status == "pending"

    findings = run["report"]["findings"]
    posture_finding = next(
        f for f in findings if f["title"] == "Maya seated then remains standing without sitting"
    )
    dialogue_finding = next(
        f for f in findings if f["title"] == "Repeated line from Episode 1"
    )

    # 3. Draft a localized revision for the editorial finding that had no initial patches
    draft_res = client.post(
        f"/api/deep-reviews/{run['id']}/findings/{dialogue_finding['id']}/draft"
    )
    assert draft_res.status_code == 202
    assert draft_res.json()["status"] == "completed"

    get_res = client.get(f"/api/script-versions/{ids['ep3_v1_id']}/deep-review")
    assert get_res.status_code == 200
    updated_run = get_res.json()["latest"]
    updated_dialogue_finding = next(
        f
        for f in updated_run["report"]["findings"]
        if f["id"] == dialogue_finding["id"]
    )
    assert len(updated_dialogue_finding["patches"]) == 1

    # 4. Reject a finding -> leaves script version unchanged
    rej_res = client.patch(
        f"/api/deep-reviews/{run['id']}/findings/{dialogue_finding['id']}",
        json={"decision": "rejected"},
    )
    assert rej_res.status_code == 200
    assert rej_res.json()["version_id"] == ids["ep3_v1_id"]

    # 5. Accept posture finding with patches -> creates new ScriptVersion, preserves media & suggestions, updates timeline
    acc_res = client.patch(
        f"/api/deep-reviews/{run['id']}/findings/{posture_finding['id']}",
        json={
            "decision": "accepted",
            "expected_version_id": ids["ep3_v1_id"],
            "patches": posture_finding["patches"],
        },
    )
    assert acc_res.status_code == 200
    new_version_id = acc_res.json()["version_id"]
    assert new_version_id != ids["ep3_v1_id"]
    assert acc_res.json()["run"]["applied_version_id"] == new_version_id
    assert acc_res.json()["run"]["undo_version_id"] == ids["ep3_v1_id"]
    assert acc_res.json()["run"]["stale"] is True

    with isolated_db() as db:
        new_ver = db.get(ScriptVersion, new_version_id)
        new_shots = sorted(
            new_ver.scenes[0].shots, key=lambda item: item.order_index
        )
        assert (
            new_shots[1].action
            == "Maya rises from the corner booth and pauses by the door."
        )
        assert new_shots[0].image is not None
        assert new_shots[0].image.filename == "shot1-existing.png"
        tl = db.scalar(
            select(ProjectTimeline).where(
                ProjectTimeline.episode_id == ids["ep3_id"]
            )
        )
        assert tl.document["primary"][0]["source"]["ref_id"] == new_shots[0].id

    # 6. Undo creates a restoration version without deleting either historical version.
    undo_res = client.post(
        f"/api/deep-reviews/{run['id']}/undo",
        json={"expected_version_id": new_version_id},
    )
    assert undo_res.status_code == 200
    restored_id = undo_res.json()["version_id"]
    assert restored_id not in {ids["ep3_v1_id"], new_version_id}
    with isolated_db() as db:
        original = db.get(ScriptVersion, ids["ep3_v1_id"])
        assert db.get(ScriptVersion, new_version_id) is not None
        restored = db.get(ScriptVersion, restored_id)
        assert restored.source_text == original.source_text
        assert restored.scenes[0].shots[1].action == original.scenes[0].shots[1].action
        timeline = db.scalar(select(ProjectTimeline).where(ProjectTimeline.episode_id == ids["ep3_id"]))
        assert timeline.document["primary"][0]["source"]["ref_id"] == restored.scenes[0].shots[0].id

    after_undo = client.get(
        f"/api/script-versions/{restored_id}/deep-review"
    ).json()["latest"]
    assert after_undo["stale"] is True
    assert after_undo["applied_version_id"] is None


def test_duplicate_active_run_cancellation_and_previous_success_fallback(
    isolated_db, monkeypatch
):
    ids = _seed_synthetic_series(isolated_db)
    client = TestClient(app)

    # Enqueue without running immediately so run stays 'queued'
    monkeypatch.setattr(
        api_module, "enqueue_deep_review", lambda provider, run_id, job_id: None
    )
    res1 = client.post(f"/api/script-versions/{ids['ep3_v1_id']}/deep-review")
    assert res1.status_code == 202
    queued_run = res1.json()
    assert queued_run["status"] == "queued"

    # Duplicate active run for same inputs is rejected with 409
    res_dup = client.post(
        f"/api/script-versions/{ids['ep3_v1_id']}/deep-review"
    )
    assert res_dup.status_code == 409

    # Cancel the active job
    cancel_res = client.post(f"/api/jobs/{queued_run['job_id']}/cancel")
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "cancelled"

    status_after_cancel = client.get(
        f"/api/script-versions/{ids['ep3_v1_id']}/deep-review"
    ).json()
    assert status_after_cancel["latest"]["status"] == "cancelled"


def _correction_run(factory, *, field='action'):
    ids = _seed_synthetic_series(factory)
    with factory() as db:
        version = db.get(ScriptVersion, ids['ep3_v1_id'])
        version.status = 'approved'
        version.project.status = 'shot_plan'
        version.episode.status = 'shot_plan'
        shot = db.get(Shot, ids['shot2_id'])
        if field == 'dialogue':
            before = shot.dialogue
            after = [{'character': 'Maya', 'text': 'The gate is still locked.'}]
        else:
            before = shot.action
            after = 'Maya rises from the corner booth and pauses by the door.'
        patch = {'shot_id': shot.id, 'field': field, 'before': before, 'after': after}
        job = Job(project_id=version.project_id, episode_id=version.episode_id,
                  kind='deep_review', status='completed')
        db.add(job)
        db.flush()
        ctx = build_context(db, version)
        from backend.models import DeepReviewRun
        run = DeepReviewRun(script_version_id=version.id, job_id=job.id,
                            provider='ollama', input_context=ctx, input_fingerprint=fingerprint(ctx),
                            status='completed', report={'assessment': 'Review', 'strengths': [],
                            'priorities': [], 'findings': [{'id': 'correction', 'status': 'open',
                            'patches': [patch]}]})
        db.add(run)
        db.commit()
        return ids, run.id, patch


@pytest.mark.parametrize('modified', [False, True])
def test_accept_keeps_source_and_plan_in_sync_and_requires_approval(isolated_db, modified):
    ids, run_id, patch = _correction_run(isolated_db)
    client = TestClient(app)
    body = {'decision': 'accepted', 'expected_version_id': ids['ep3_v1_id']}
    if modified:
        patch['after'] = 'Maya rises slowly and waits beside the door.'
        body['patches'] = [patch]
    result = client.patch(f'/api/deep-reviews/{run_id}/findings/correction', json=body)
    assert result.status_code == 200
    with isolated_db() as db:
        original = db.get(ScriptVersion, ids['ep3_v1_id'])
        changed = db.get(ScriptVersion, result.json()['version_id'])
        assert patch['after'] in changed.source_text
        assert patch['before'] not in changed.source_text
        assert changed.source_text == original.source_text.replace(patch['before'], patch['after'], 1)
        assert changed.scenes[0].shots[1].action == patch['after']
        assert changed.status == changed.project.status == changed.episode.status == 'review'
        assert original.status == 'approved'
        assert changed.structured_data['review_production_changes']
        assert result.json()['run']['report']['findings'][0]['modified'] == modified


def test_dialogue_acceptance_updates_source_without_losing_other_lines(isolated_db):
    ids, run_id, patch = _correction_run(isolated_db, field='dialogue')
    client = TestClient(app)
    result = client.patch(f'/api/deep-reviews/{run_id}/findings/correction', json={
        'decision': 'accepted', 'expected_version_id': ids['ep3_v1_id'], 'patches': [patch]})
    assert result.status_code == 200, result.text
    with isolated_db() as db:
        changed = db.get(ScriptVersion, result.json()['version_id'])
        assert 'The gate is still locked.' in changed.source_text
        assert 'We already said nobody gets through tonight.' not in changed.source_text
        assert 'Cole (filtered phone voice)' in changed.source_text or 'COLE (filtered phone voice)' in changed.source_text
        assert changed.scenes[0].shots[1].dialogue[0]['text'] == patch['after'][0]['text']


@pytest.mark.parametrize('problem', ['empty_proposal', 'source_mismatch', 'changed_before'])
def test_unsafe_acceptance_is_atomic(isolated_db, problem):
    ids, run_id, patch = _correction_run(isolated_db)
    from backend.models import DeepReviewRun
    client = TestClient(app)
    if problem == 'source_mismatch':
        with isolated_db() as db:
            old = db.get(ScriptVersion, ids['ep3_v1_id'])
            old.source_text = old.source_text.replace(patch['before'], 'A different source action.')
            run = db.get(DeepReviewRun, run_id)
            run.input_fingerprint = fingerprint(build_context(db, old))
            db.commit()
    if problem == 'changed_before':
        patch['before'] = 'A stale original value.'
    payload = {'decision': 'accepted', 'expected_version_id': ids['ep3_v1_id'],
               'patches': [] if problem == 'empty_proposal' else [patch]}
    with isolated_db() as db:
        count_before = len(db.scalars(select(ScriptVersion)).all())
    result = client.patch(f'/api/deep-reviews/{run_id}/findings/correction', json=payload)
    assert result.status_code in {400, 422}, result.text
    with isolated_db() as db:
        assert len(db.scalars(select(ScriptVersion)).all()) == count_before
        assert db.get(DeepReviewRun, run_id).report['findings'][0]['status'] == 'open'
        assert db.get(Shot, ids['shot2_id']).action == 'Without sitting down, Maya remains standing by the door and gasps in terror at the empty cup.'


def test_interleaved_action_correction_preserves_dialogue_and_transitions(isolated_db):
    ids, run_id, patch = _correction_run(isolated_db)
    from backend.models import DeepReviewRun
    with isolated_db() as db:
        version = db.get(ScriptVersion, ids['ep3_v1_id'])
        shot = db.get(Shot, ids['shot2_id'])
        before = 'Maya turns.\nGabe stays seated with the coffee.\nCUT TO BLACK.'
        after = before.replace('stays seated', 'remains standing')
        shot.action = before
        marker = version.source_text.index('SHOT 2:')
        version.source_text = version.source_text[:marker] + '''SHOT 2 - DINER COUNTER
Time: 00:05-00:10
Action:
Maya turns.
MAYA:
"Coming."
Gabe stays seated with the coffee.
CUT TO BLACK.
Sound: Diner hum
Image prompt: Gabe stays seated with the coffee.
'''
        original_source = version.source_text
        run = db.get(DeepReviewRun, run_id)
        run.input_fingerprint = fingerprint(build_context(db, version))
        patch.update(before=before, after=after)
        db.commit()
    client = TestClient(app)
    result = client.patch(f'/api/deep-reviews/{run_id}/findings/correction', json={
        'decision': 'accepted', 'expected_version_id': ids['ep3_v1_id'], 'patches': [patch]})
    assert result.status_code == 200, result.text
    with isolated_db() as db:
        changed = db.get(ScriptVersion, result.json()['version_id'])
        assert changed.source_text == original_source.replace('Gabe stays seated with the coffee.', 'Gabe remains standing with the coffee.', 1)
        assert changed.scenes[0].shots[1].action == after


def test_script_history_preview_restore_and_timeline_recovery(isolated_db):
    ids = _seed_synthetic_series(isolated_db)
    with isolated_db() as db:
        original = deep_review_module._load_version(db, ids['ep3_v1_id'])
        newer, _, _ = deep_review_module._clone_version_with_patches(db, original, [])
        newer.source_text += '\nNewer revision.'
        newer_id = newer.id
        db.commit()
    client = TestClient(app)
    history = client.get(f'/api/script-versions/{newer_id}/history').json()
    assert [item['id'] for item in history['versions']] == [newer_id, ids['ep3_v1_id']]
    preview = client.get(f"/api/script-versions/{ids['ep3_v1_id']}").json()
    assert 'Newer revision.' not in preview['source_text']
    payload = {'expected_version_id': history['current_version_id'], 'expected_fingerprint': history['current_fingerprint']}
    response = client.post(f"/api/script-versions/{ids['ep3_v1_id']}/restore", json=payload)
    assert response.status_code == 200, response.text
    restored = response.json()
    assert restored['id'] not in {newer_id, ids['ep3_v1_id']}
    assert restored['status'] == 'review'
    assert restored['source_text'] == preview['source_text']
    assert restored['scenes'][0]['shots'][0]['image']['url'] == preview['scenes'][0]['shots'][0]['image']['url']
    assert client.post(f"/api/script-versions/{ids['ep3_v1_id']}/restore", json=payload).status_code == 409
    with isolated_db() as db:
        timeline = db.scalar(select(ProjectTimeline).where(ProjectTimeline.episode_id == ids['ep3_id']))
        assert timeline.document['primary'] == []
    latest = client.get(f"/api/script-versions/{restored['id']}/history").json()
    payload = {'expected_version_id': latest['current_version_id'], 'expected_fingerprint': latest['current_fingerprint']}
    recovered = client.post(f'/api/script-versions/{newer_id}/restore', json=payload)
    assert recovered.status_code == 200, recovered.text
    with isolated_db() as db:
        timeline = db.scalar(select(ProjectTimeline).where(ProjectTimeline.episode_id == ids['ep3_id']))
        assert timeline.document['primary'][0]['source']['ref_id'] == recovered.json()['scenes'][0]['shots'][0]['id']
    assert client.get(f"/api/script-versions/{ids['ep3_v1_id']}").json()['source_text'] == preview['source_text']


def test_history_restore_rejects_in_place_edits_and_other_episodes(isolated_db):
    ids = _seed_synthetic_series(isolated_db)
    client = TestClient(app)
    history = client.get(f"/api/script-versions/{ids['ep1_v1_id']}/history").json()
    payload = {'expected_version_id': history['current_version_id'], 'expected_fingerprint': history['current_fingerprint']}
    assert client.post(f"/api/script-versions/{ids['ep2_v1_id']}/restore", json=payload).status_code == 409
    with isolated_db() as db:
        version = db.get(ScriptVersion, history['current_version_id'])
        version.source_text += '\nEdited after preview.'
        db.commit()
    assert client.post(f"/api/script-versions/{ids['ep1_v1_id']}/restore", json=payload).status_code == 409
    assert client.get('/api/script-versions/missing/history').status_code == 404


def test_preflight_exact_cast_names_win_and_unresolved_cues_are_grouped():
    context = {'current': {'version_id': 'v1', 'scenes': [{'shots': [
        {'id': 's1', 'dialogue': [{'character': name, 'text': 'Hello.'} for name in ('MAYA', 'ROSA', 'UNKNOWN')]},
        {'id': 's2', 'dialogue': [{'character': name, 'text': 'Again.'} for name in ('MAYA', 'ROSA', 'UNKNOWN')]},
    ]}]}, 'cast': [{'name': name} for name in ('MAYA', 'MAYA SANTOS', 'ROSA', 'ROSA SANTOS')]}
    warnings = [finding for finding in preflight(context) if finding['category'] == 'cast']
    assert [finding['title'] for finding in warnings] == ['Check dialogue speaker UNKNOWN']
    context['cast'] = [{'name': name} for name in ('MAYA SANTOS', 'MAYA REYES', 'ROSA SANTOS')]
    warnings = [finding for finding in preflight(context) if finding['category'] == 'cast']
    assert [finding['title'] for finding in warnings] == ['Check dialogue speaker MAYA', 'Check dialogue speaker UNKNOWN']
