# Paste this into the scriptwriting chat

I will produce this script with Cutline, a local filmmaking app that imports scripts, preserves structured shot plans, manages cast/set/prop references, generates storyboard images and video clips through configured providers, and assembles an editable timeline. Write for a coherent finished film and for the production rules below.

This brief reflects my local Cutline source reviewed on 19 October 2026. Distinguish app requirements from creative recommendations. Do not assume any particular generation provider is connected or that every provider can produce speech, music, accurate text, or reliable lip sync.

## 1. Develop the story before expanding the production script

Use the creative context already in this conversation. Resolve any missing essentials: audience, platform, aspect ratio, genre/tone, language/accent, intended runtime, visual style, recurring cast, and standalone versus episodic structure. Do not invent my preferences or repeat questions I have already answered.

First establish the premise, protagonist's immediate goal, obstacle, escalation, turning point, and payoff. For informational or narrated work, use a clear question, progressing evidence, and satisfying answer instead. Each beat should cause, motivate, or clearly connect to the next. Avoid arbitrary twists, repeated exposition, and filler shots added just to reach a runtime.

Build an emotional and visual progression. Establish enough geography to follow the scene, show meaningful actions and reactions, and use inserts only when they reveal information. Vary coverage for a reason. Let dialogue respond to the previous line or action. Preserve pauses where they carry tension, comedy, discovery, or emotion.

Do not force every shot to have equal duration, every cut to be a cliffhanger, or every opening to be loud. Choose a hook appropriate to this story. Give the opening a deliberate sound design, even if it is restrained ambience. Make the ending resolve or intentionally transform the opening question.

## 2. Deliver one episode at a time in Cutline's structured format

Cutline detects headings of the exact form `SHOT 1 — Descriptive title`. Use consecutive numbering, a dash between number and title, and a title on every shot. Plain text is safest: no tables, bullets before field labels, bold field labels, or JSON.

Use this preamble:

```text
TITLE
Genre: ...
Duration: 60 seconds
Format: 10 shots
Aspect ratio: 16:9
Location: Default location
Set: Fixed architecture, layout, background details and spatial relationships.
Lighting: Source, direction, quality and color; stable through the scene.
Wardrobe: Exact clothing for each character in this sequence.
Props: Stable descriptions of recurring objects and their initial state.
Character consistency:
MAYA — 28 years old, physical identity, hair and identifying features. Exact baseline wardrobe. Voice: natural Nigerian-English cadence, warm middle register, measured conversational delivery.
TUNDE — 32 years old, physical identity, hair and identifying features. Exact baseline wardrobe. Voice: natural Nigerian-English cadence, low register, direct conversational delivery.

SHOT 1 — Descriptive title
Scene: 1 — Scene name
Location: Specific location
Time of day: Morning
Time: 00:00–00:06
Framing: Medium two-shot, eye level; MAYA screen-left, TUNDE screen-right.
Cast: MAYA, TUNDE
Action: Concrete visible action, including the meaningful change during this shot.
Dialogue:
MAYA:
"Exact spoken words."
Sound: Relevant ambience, effects and music direction.
Image prompt: A single opening frame, with the characters, starting pose, object positions, composition, environment, lighting and visual style.
Video prompt: From that opening frame, describe one manageable action, performance, camera behavior and end state. MAYA says, "Exact spoken words." TUNDE listens silently with lips closed. State relevant audio behavior.
```

Track who remains physically present across each continuous scene. New speakers and arrivals do not remove existing people. Medium, wide and group shots must retain established people in Cast, framing, and both prompts until an exit or justified coverage change. Widen a two-shot when another person joins. For deliberate solo close-ups, tag only the visible person and explicitly keep listeners off camera. Dialogue mentioning a person does not establish their presence.

Repeat the shot fields for every shot. Use `Dialogue: None` when there is no speech and explicitly say `No spoken dialogue` in its video prompt. Use `Cast: None` for a genuinely empty shot. Use stable uppercase character names in cast declarations and dialogue cues; avoid switching between full names, nicknames, pronouns and role names as speaker identifiers. Put each character on its own line in Character consistency, with no bullet. Keep the list contiguous. Put physical identity and wardrobe first: Cutline can use the first two sentences as a compact visual profile. End with an explicit `Voice:` description.

The title, genre and aspect-ratio lines are useful context, but the aspect ratio must also be selected in the Cutline project. The metadata does not configure the whole production automatically. Import only one episode per document; use Cutline's New Episode workflow for subsequent episodes.

## 3. Timing rules

- Every shot needs an explicit `Time: MM:SS–MM:SS` range. Start at 00:00, keep every boundary continuous, and finish at the declared duration. No gaps, overlaps, reversed ranges, or zero-length shots.
- Keep each shot between 2 and 10 seconds. This is the structured importer's expected generation range. If a beat needs longer, split it into motivated coverage with distinct starting and ending states.
- `Format: N shots` must match the actual count. `Duration: N seconds` must match the final end timestamp. Use whole-second timestamps for this format.
- Cutline estimates English dialogue at roughly 2.5 words per second plus 0.4 seconds of padding. This is a rough estimate, not a performance guarantee; its estimator caps at 10 seconds, so very long lines can escape a useful warning.
- As a writing guideline, use about 8–12 spoken words for a 6-second shot or 12–16 for an 8-second shot when performance also needs breathing room. These are recommendations, not app limits. Allow more room for hesitation, emotion, laughter, another speaker, or important action. Read lines aloud rather than filling the theoretical word budget.
- Aim for one speaker per generated clip when practical. Multiple speakers are supported in script data, but short turn-taking clips are easier to direct and correct. Split a busy exchange rather than rushing it.
- Use clip-relative instructions inside Video prompt, such as “during the first two seconds” and “at the end of the clip.” Keep episode timestamps in Time. Do not assume a provider delivers exactly the requested duration; actual clips may need trimming or retiming in the timeline.

## 4. Opening images and video motion must agree

An image prompt describes one still frame at the START of the shot. A video prompt describes what happens AFTER that frame. If a character opens an envelope, the starting image should show the envelope still closed. Do not place the completed reveal in the image and then ask the clip to perform it again.

Use one principal action and a small number of supporting movements per clip. Describe observable behavior: “She folds the receipt once and slides it under the cup,” rather than “She realizes she has been betrayed.” Show the realization through gaze, posture, hesitation, speech, or a specific action.

Keep each clip to one continuous setup unless a deliberate effect requires otherwise. Use modest, motivated camera movement. A new angle, location, time jump, or distinct action phase usually deserves another shot. Avoid cramming several cuts, a long exchange, complicated hand choreography and a camera orbit into one short clip.

Keep image and video prompts consistent with Framing, Action, Cast and dialogue. Repeat essential identity, set and style anchors concisely so each shot is intelligible independently. Avoid contradictory instructions and long lists of negatives.

Cutline adds a continuity instruction to use the supplied image as the exact opening frame and preserve composition, faces, wardrobe, set and lighting. It also adds “No on-screen text, subtitles, UI elements, or location tags.” Therefore, specify readable titles, captions, labels, phone messages and end cards in a separate editing plan for Cutline's text overlays. Do not rely on generated lettering or assume an `On-screen text:` script field will become an overlay automatically.

## 5. Continuity rules

Use the same `Scene:` and `Location:` values for consecutive shots in one production scene. Cutline groups consecutive shots by those two values. A repeated location name alone does not establish an intentional time transition.

Give each physical space a separate stable location name, such as `COMPOUND_COURTYARD`, `COMPOUND_PARLOUR`, and `COMPOUND_KITCHEN`. Reuse the same location across later scenes in that room. Cutline matches the location to a reusable Locations / Sets asset and attaches its reference automatically to new storyboard generation; each set needs a generated or uploaded reference first. The Reusable set selector can assign an existing asset to every shot in a scene. Keep permanent architecture and furniture in that asset's description, and time-of-day lighting and temporary staging in the scene notes. Do not describe a whole compound as a single interchangeable room.

`Set`, `Lighting`, `Wardrobe` and `Props` become scene-level notes. Different shot-level values can overwrite or accumulate across a grouped scene. Keep them stable within a group. For a genuine set, lighting or wardrobe change that needs separate generation context, give the new group a distinct Scene name and explain the transition. For moment-to-moment prop movement, use Action and the prompts rather than rewriting the shared prop bible every shot.

Track who is present, screen positions, eyelines, entrances/exits, which hand holds an object, object ownership, whether objects are open/closed or full/empty, and what information each character knows. The next opening image must follow the previous ending logically, allowing a motivated change of camera angle. Keep geography stable across reverse coverage.

Cast should explicitly list intended visible characters. For off-screen dialogue, retain the speaker in the character bible and mark the dialogue cue, for example `TUNDE (O.S.)` or `TUNDE (ON PHONE)`. State that the caller is unseen and that the visible listener remains silent with mouth closed. Verify shot cast assignments after import: off-screen speakers can produce cast advisories, and reference/voice routing needs checking. Favor separate caller/listener shots when routing becomes complicated.

Cutline supports reference images for characters and other production assets, and a selected frame from the preceding clip can be used as the next opening frame. These require production setup; script descriptions alone do not guarantee identity consistency. Reserve frame reuse for compatible composition and action, rather than using it for every reverse angle.

Optional advanced prop convention: define `@LETTER is a cream envelope with a blue wax seal` in Props and mention `@LETTER` in relevant shot text. Cutline filters tagged prop definitions to shots mentioning those tags. If used, keep tags exact and define every tagged prop. Plain-language props are also supported and simpler for most scripts.

## 6. Dialogue and sound need a production plan

Dialogue is the authoritative spoken text. Keep acting instructions in Action or Video prompt, outside the quoted speech. If repeating dialogue in Video prompt for a speaking clip, copy it exactly; never introduce alternate wording, extra narration or additional lines there. Specify who speaks and who stays silent, especially in two-shots.

Sound is audio direction. Cutline appends it to the generation prompt, but that does not create guaranteed, separately editable ambience, effects or music tracks. Speech generation, voice assignment and lip sync depend on the configured provider. Writing a voice description does not automatically select a matching TTS voice. Some audio/video paths combine audio with visuals without producing true facial lip sync.

Plan whether speech will come from the video provider or separate voice production. Avoid producing duplicate dialogue with both. When the production route is unknown, keep the script usable for either route and flag the choice in separate production notes.

Use sound to connect shots: stable room tone, a continuing environmental sound, or a motivated music change. Describe deliberate silence explicitly. Cross-shot music beds, sound bridges and exact audio timing belong in a separate editing plan; do not assume the script importer creates them. Cutline's timeline supports audio assets, text/image/video overlays, transitions and separate landscape/shorts edits, but those still need assembly.

## 7. Required delivery and self-check

Deliver separately:

1. A concise story/beat outline and intended emotional progression.
2. The complete plain-text Cutline import script, with no analysis, tables, alternatives or editorial notes mixed into shot blocks. Keep the import script in its own code block or file.
3. A production checklist: reference assets, difficult shots, chosen/undecided speech route, overlays, continuous music/ambience and transitions to add in editing. State any effect that needs manual assembly.

Before delivery, check count and duration arithmetic; 2–10-second shot lengths; continuous timestamps; valid headings; all shot fields; consistent speaker names; plausible spoken timing; and clear scene grouping. Check every opening frame against its action, every ending against the next opening, every prop's state, and every dialogue line against what the character knows. Remove repeated beats and prompts that contradict shared scene notes. Make the progression understandable when reading only Action and Dialogue.

Do not claim that a clean import guarantees good acting, exact timing, consistent faces, readable text or finished audio. Cutline preserves a well-structured plan and offers review tools; production still needs reference setup, take selection, timing checks and a final watch-through.
