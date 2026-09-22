#!/usr/bin/env python3
"""Build and seed the Greenhouse short (Burning remodel) into Cutline."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_SRC = ROOT / "backend" / "src"
sys.path.insert(0, str(BACKEND_SRC))

API = "http://127.0.0.1:8000"
SCRIPT_PATH = Path(__file__).with_name("greenhouse_screenplay.txt")

# Shared continuity locks for every image/video prompt.
JUN = (
    "JUN: Korean man mid-20s, lean build, short black hair slightly messy, "
    "quiet watchful eyes, light stubble, faded olive utility jacket over a grey crewneck, "
    "dark jeans, scuffed white sneakers"
)
MIRA = (
    "MIRA: Korean woman mid-20s, shoulder-length black hair with soft waves, warm open face, "
    "mustard knit cardigan over a white tee, high-waisted blue jeans, thin silver necklace, "
    "bare feet indoors"
)
BEN = (
    "BEN: Korean man early 30s, polished calm face, neat side-part black hair, "
    "expensive charcoal crewneck sweater, black slim trousers, clean black loafers, "
    "thin gold watch, faintly amused expression"
)
APT = (
    "Mira's small Seoul apartment living room at blue-hour dusk: worn wood floor, "
    "one large window with thin grey curtains half-drawn, a low table with an empty glass, "
    "a single warm floor lamp, sparse plants, soft urban glow outside, no other locations"
)
STYLE = (
    "cinematic still, naturalistic Korean indie drama, soft practical lighting, "
    "subtle film grain, 16:9, no text, no subtitles, no watermark"
)


def ms_to_tc(ms: int) -> str:
    total = ms // 1000
    m, s = divmod(total, 60)
    return f"{m:02d}:{s:02d}"


def shot_block(
    number: int,
    title: str,
    scene: str,
    start_ms: int,
    duration_ms: int,
    framing: str,
    action: str,
    dialogue: str,
    sound: str,
    image: str,
    video: str,
    *,
    location: str = "Mira's small Seoul apartment living room",
    time_of_day: str = "Evening",
) -> str:
    end_ms = start_ms + duration_ms
    return f"""SHOT {number} - {title}

Scene: {scene}
Location: {location}
Time of day: {time_of_day}
Time: {ms_to_tc(start_ms)}-{ms_to_tc(end_ms)}
Framing: {framing}

Action:
{action}

Dialogue:
{dialogue}

Sound: {sound}

Image prompt:
{image}

Video prompt:
{video}
"""


def build_screenplay() -> str:
    """50 shots, exactly 300000 ms, continuous timeline."""
    # (title, scene, duration_ms, framing, action, dialogue, sound, image_extra, video)
    # image_extra describes the specific composition beyond locked identity/set.
    specs: list[tuple] = [
        # --- RETURN ---
        (
            "STAIRWELL ARRIVAL",
            "RETURN",
            6000,
            "Wide establishing shot",
            "Jun climbs the last stairs to Mira's floor, pauses with a plastic bag of fruit, listening.",
            "Dialogue: None.",
            "Distant traffic, soft stairwell echo, keys faintly jingling.",
            f"Exterior-adjacent stair landing leading to apartment door, dusk through a small window. {JUN}. He holds a small grocery bag. Door number faintly visible. {STYLE}",
            "Slow push toward Jun as he reaches the landing; subtle breathing; identity preserved.",
        ),
        (
            "DOOR KNOCK",
            "RETURN",
            5000,
            "Medium shot",
            "Jun knocks twice on Mira's door, then waits, bag hanging from his fingers.",
            "Dialogue: None.",
            "Two soft knocks, hallway hush.",
            f"Apartment hallway, Mira's door closed. {JUN} knocking. Warm spill under the door. {APT} continuity through the door crack. {STYLE}",
            "Hand knocks twice; slight body sway; camera locked; face consistent.",
        ),
        (
            "DOOR OPENS",
            "RETURN",
            5000,
            "Over-the-shoulder",
            "Mira opens the door mid-laugh, surprised and delighted to see Jun.",
            """MIRA:
"Jun? You actually came." """,
            "Door latch, warm room tone, Mira's bright voice.",
            f"Doorway threshold. {MIRA} opening the door toward camera. {JUN} on the landing in soft focus foreground shoulder. {APT} visible behind her. {STYLE}",
            "Door swings open; Mira's smile widens; light lipsync on dialogue; keep both faces locked.",
        ),
        (
            "THRESHOLD HUG",
            "RETURN",
            6000,
            "Medium two-shot",
            "Mira pulls Jun into a quick half-hug; he stiffens then softens, still holding the bag.",
            """MIRA:
"Come in before the neighbours invent a story." """,
            "Fabric rustle, room tone, distant city.",
            f"Inside the doorway. {MIRA} and {JUN} embracing lightly. Same mustard cardigan and olive jacket. {APT}. {STYLE}",
            "Short embrace; bag swings; camera slow lateral drift; identity preserved.",
        ),
        (
            "FRUIT ON THE TABLE",
            "RETURN",
            7000,
            "High angle",
            "Jun sets oranges and a small carton of juice on the low table. Mira pads barefoot to the lamp and brightens it.",
            """JUN:
"I brought fruit. You said you forget to eat." """,
            "Bag on wood, lamp click, soft room hum.",
            f"Low table with oranges and juice. {JUN} placing them. {MIRA} at the floor lamp. {APT}. Blue hour at the window. {STYLE}",
            "Hands set fruit down; lamp brightens; gentle camera settle; continuity of wardrobe and set.",
        ),
        (
            "WINDOW BLUE HOUR",
            "RETURN",
            7000,
            "Wide shot",
            "They stand near the window. Mira points at the skyline as if showing a secret; Jun watches her more than the view.",
            """MIRA:
"Look — the greenhouses on the ridge look like tiny planets when the lights come on." """,
            "City murmur under glass, Mira's voice soft.",
            f"Wide of living room. {MIRA} and {JUN} at the window. Distant greenhouse lights as tiny warm dots outside. {APT}. {STYLE}",
            "Subtle city light flicker; Mira gestures; Jun's gaze shifts to her; slow push-in.",
        ),
        # --- THE THIRD ---
        (
            "A VOICE FROM THE HALL",
            "THE THIRD",
            6000,
            "Medium close-up",
            "Jun freezes as a calm male voice calls from deeper in the apartment, already at home.",
            """BEN:
"Mira, is that your writer friend?" """,
            "Ben's voice off-screen, room tone.",
            f"Close on {JUN}, startled, looking toward an off-screen hallway. {APT} bokeh. {STYLE}",
            "Micro expression of surprise; slight head turn; hold identity; no cutaway yet.",
        ),
        (
            "BEN APPEARS",
            "THE THIRD",
            6000,
            "Medium shot",
            "Ben steps in from the short hall with easy ownership, glass of water in hand, smiling as if he expected Jun.",
            """BEN:
"I'm Ben. Don't look so hunted — I'm harmless." """,
            "Soft footsteps, glass clink, amused voice.",
            f"{BEN} entering the living room with a glass of water. {JUN} and {MIRA} present. {APT}. {STYLE}",
            "Ben walks in smoothly; offers a polite smile; lipsync; keep all three identities locked.",
        ),
        (
            "INTRODUCTIONS",
            "THE THIRD",
            7000,
            "Three-shot",
            "Awkward triangle: Mira bright, Ben relaxed, Jun polite and cornered.",
            """MIRA:
"Ben gave me a ride back from Gangnam. He was already upstairs when you texted." """,
            "Room tone, overlapping soft city noise.",
            f"Three people standing in the small living room. {MIRA}, {BEN}, {JUN}. Same clothes. {APT}. {STYLE}",
            "Small shifts in weight; Mira glances between them; subtle tension in Jun's shoulders.",
        ),
        (
            "HANDSHAKE",
            "THE THIRD",
            7000,
            "Close-up insert",
            "Ben extends a clean hand. Jun hesitates half a beat, then shakes.",
            """BEN:
"I've read nothing you've written. That makes me honest." """,
            "Quiet handshake, Ben's dry humor.",
            f"Close insert of handshake between {BEN} and {JUN}. Gold watch visible. Olive jacket sleeve vs charcoal sweater. {STYLE}",
            "Handshake completes; slight camera push; faces partially in frame stay consistent.",
        ),
        (
            "WHO SITS WHERE",
            "THE THIRD",
            6000,
            "Wide shot",
            "Mira sits on the floor by the table. Ben takes the only chair. Jun remains standing a moment too long, then sits opposite.",
            "Dialogue: None.",
            "Floor creak, fabric, room tone.",
            f"Blocking diagram as image: {MIRA} on floor, {BEN} in chair, {JUN} sitting opposite. Oranges on table. {APT}. {STYLE}",
            "They settle into positions; camera slow arc; maintain identities and set.",
        ),
        (
            "SMALL TALK CUTS",
            "THE THIRD",
            6000,
            "Medium close-up",
            "Ben studies Jun with mild curiosity while peeling an orange slowly, as if he has all night.",
            """BEN:
"Mira says you notice things. Dangerous hobby." """,
            "Orange peel soft tears, Ben's low voice.",
            f"Close on {BEN} peeling an orange, eyes on off-screen Jun. {APT} warm lamp. {STYLE}",
            "Fingers peel orange; tiny smile; lipsync; identity locked.",
        ),
        (
            "JUN DEFLECTS",
            "THE THIRD",
            6000,
            "Medium close-up",
            "Jun tries to smile it off, fingers worrying the edge of the table.",
            """JUN:
"I mostly notice when people leave." """,
            "Room tone, Jun's quieter voice.",
            f"Close on {JUN}, uneasy smile, olive jacket, grey crewneck. {APT}. {STYLE}",
            "Fingers fidget; eyes flick toward Ben; lipsync; preserve face.",
        ),
        # --- PANTOMIME ---
        (
            "MIRA'S IDEA",
            "PANTOMIME",
            5000,
            "Medium shot",
            "Mira kneels up suddenly, excited, palms open like a stage magician.",
            """MIRA:
"Wait — I learned something in Africa. A dance for hunger." """,
            "Her bright energy cutting the tension.",
            f"{MIRA} rising onto her knees, animated. {JUN} and {BEN} watching. {APT}. {STYLE}",
            "She sits up with energy; hair moves; keep mustard cardigan and necklace consistent.",
        ),
        (
            "INVISIBLE TANGERINE",
            "PANTOMIME",
            6000,
            "Medium close-up",
            "Mira mimes plucking a huge invisible tangerine from the air, cradling it with reverence.",
            "Dialogue: None.",
            "Soft breath, fabric, distant traffic.",
            f"{MIRA} hands cupped around empty air as if holding fruit. Soft focus lamp. {APT}. {STYLE}",
            "Hands shape imaginary fruit; sincere face; slow camera drift.",
        ),
        (
            "SHE EATS AIR",
            "PANTOMIME",
            7000,
            "Close-up",
            "She bites the invisible fruit, eyes closing, juice that isn't there running down an imagined chin — fully committed.",
            "Dialogue: None.",
            "Quiet room, almost ceremonial silence.",
            f"Intimate close-up of {MIRA} biting invisible fruit, eyes closed, emotional. Mustard cardigan, silver necklace. {STYLE}",
            "Bite motion, swallow, emotional micro-expressions; no real fruit appears.",
        ),
        (
            "BEN APPLAUDS",
            "PANTOMIME",
            6000,
            "Medium shot",
            "Ben claps once, delighted like a collector who found a rare piece.",
            """BEN:
"Beautiful. Most people mime appetite. You mime need." """,
            "Single clap, Ben's pleased voice.",
            f"{BEN} clapping lightly, watching Mira. {MIRA} still mid-pantomime. {APT}. {STYLE}",
            "One clap; Ben leans forward; keep both faces locked.",
        ),
        (
            "JUN WATCHES BOTH",
            "PANTOMIME",
            6000,
            "Over-the-shoulder",
            "Jun's face from behind Mira's shoulder: fascination mixed with the first real unease about Ben's gaze.",
            "Dialogue: None.",
            "Room tone, faint fridge hum.",
            f"OTS past {MIRA}'s shoulder onto {JUN}'s unsettled face. {BEN} soft in background. {APT}. {STYLE}",
            "Jun's eyes track from Mira to Ben; subtle tension; continuity of positions.",
        ),
        (
            "PANTOMIME ENDS",
            "PANTOMIME",
            6000,
            "Medium two-shot",
            "Mira laughs it off, wiping imaginary juice, then reaches for a real orange and tosses it to Jun.",
            """MIRA:
"Your turn someday. When you're starving enough." """,
            "Soft laugh, orange catch.",
            f"{MIRA} tossing a real orange to {JUN}. Warm lamp. {APT}. {STYLE}",
            "Toss and catch; laughter; identities preserved.",
        ),
        (
            "ORANGE IN JUN'S HAND",
            "PANTOMIME",
            6000,
            "Insert close-up",
            "Jun holds the real orange too tightly, thumb pressing the peel until oil shines.",
            "Dialogue: None.",
            "Soft peel stress, room tone.",
            f"Close insert: {JUN}'s hand crushing an orange slightly, olive jacket cuff. {STYLE}",
            "Thumb press; oil glints; tiny tremor.",
        ),
        # --- THE HOBBY ---
        (
            "AFTER SHE SETTLES",
            "THE HOBBY",
            6000,
            "Wide shot",
            "Mira curls against the wall with juice. Ben and Jun face each other across the low table like a quiet duel.",
            "Dialogue: None.",
            "City hush, lamp buzz almost inaudible.",
            f"Wide: {MIRA} against wall, {BEN} in chair, {JUN} opposite. Oranges. {APT} dusk deeper. {STYLE}",
            "Slow push into the two men; Mira soft in frame edge.",
        ),
        (
            "BEN NAMES THE HOBBY",
            "THE HOBBY",
            7000,
            "Medium close-up",
            "Ben speaks lightly, almost bored, as if describing a golf handicap.",
            """BEN:
"I have a hobby. I burn greenhouses." """,
            "Ben's calm voice, no music swell.",
            f"Close on {BEN}, soft smile, charcoal sweater, gold watch. {APT}. {STYLE}",
            "Still face; precise lipsync; tiny head tilt; identity locked.",
        ),
        (
            "JUN DOESN'T LAUGH",
            "THE HOBBY",
            7000,
            "Medium close-up",
            "Jun waits for the joke. It doesn't arrive. His throat works.",
            """JUN:
"For money?" """,
            "Tight room tone.",
            f"Close on {JUN}, stunned, trying to stay polite. Olive jacket. {APT}. {STYLE}",
            "Swallow; eyes search Ben; lipsync; preserve face.",
        ),
        (
            "FOR THE LITTLE FLAME",
            "THE HOBBY",
            7000,
            "Medium close-up",
            "Ben shakes his head, amused at the misunderstanding.",
            """BEN:
"For the little flame. The moment the plastic skin collapses. It's... purifying." """,
            "Quiet voice, almost intimate.",
            f"{BEN} leaning slightly forward, eyes bright without heat. {APT}. {STYLE}",
            "Lean-in; soft gesture with hand; lipsync; keep identity.",
        ),
        (
            "HOW OFTEN",
            "THE HOBBY",
            7000,
            "Two-shot",
            "Jun stares. Ben peels another segment of orange and eats it without hurry.",
            """JUN:
"How often?"

BEN:
"When I get bored. Maybe once every month or two." """,
            "Chewing soft, dual voices.",
            f"Two-shot {JUN} and {BEN} across the table. Orange segments. {APT}. {STYLE}",
            "Ben eats; Jun still; lipsync both; continuity of wardrobe and set.",
        ),
        (
            "MIRA HALF-LISTENS",
            "THE HOBBY",
            7000,
            "Medium shot",
            "Mira, half-drowsy against the wall, smiles as if she's heard a charming exaggeration.",
            """MIRA:
"He says things like that. Rich people metaphors." """,
            "Her soft dismissive laugh.",
            f"{MIRA} against the wall with juice, sleepy smile. Mustard cardigan. {APT}. {STYLE}",
            "Small laugh; head tip; lipsync; identity locked.",
        ),
        (
            "NOT A METAPHOR",
            "THE HOBBY",
            7000,
            "Close-up",
            "Ben glances at Mira, then back to Jun — polite correction without heat.",
            """BEN:
"Sometimes a metaphor is also a map." """,
            "Dry delivery, room hush.",
            f"Close on {BEN}, eyes steady. {STYLE}",
            "Minimal movement; precise lipsync; hold tension.",
        ),
        (
            "GREENHOUSE LIGHTS OUTSIDE",
            "THE HOBBY",
            5000,
            "Point-of-view wide",
            "Jun looks to the window. Far ridge greenhouse lights blink. One cluster seems darker than the rest.",
            "Dialogue: None.",
            "Distant city, almost wind.",
            f"POV through Mira's window at dusk: distant greenhouse lights on a ridge, one dark patch. Reflection of room lamp. {STYLE}",
            "Subtle light flicker; slow push toward glass; no new characters.",
        ),
        # --- THE WELL ---
        (
            "AFTERTHOUGHT",
            "THE HOBBY",
            6000,
            "Medium shot",
            "Ben wipes his fingers with a napkin, then adds something like a footnote.",
            """BEN:
"There's also the wells." """,
            "Napkin soft sound, calm voice.",
            f"{BEN} wiping fingers, conversational. {JUN} rigid. {APT}. {STYLE}",
            "Wipe hands; casual tone; lipsync; identities locked.",
        ),
        (
            "WELLS WHERE GIRLS VANISH",
            "THE WELL",
            7000,
            "Medium close-up",
            "Ben continues as if discussing weather, eyes on Jun for reaction more than confession.",
            """BEN:
"Korea is full of abandoned wells. Useful places. Girls go missing near them sometimes." """,
            "Even quieter voice; no score.",
            f"Tight on {BEN}, soft lamp key light, charcoal sweater. {STYLE}",
            "Stillness; measured lipsync; micro-smile at the end.",
        ),
        (
            "JUN PUSHES",
            "THE WELL",
            7000,
            "Medium close-up",
            "Jun's voice goes thin. He tries to keep it a writer's question.",
            """JUN:
"You're saying you put someone in a well." """,
            "Tight breath under dialogue.",
            f"Close on {JUN}, pale under lamp, jaw tight. {APT}. {STYLE}",
            "Breath hitch; lipsync; eyes wet without crying; preserve face.",
        ),
        (
            "MAYBE A JOKE",
            "THE WELL",
            7000,
            "Medium two-shot",
            "Ben shrugs, offering Jun an exit ramp and a trap at once.",
            """BEN:
"I'm saying the world has unused holes. What people do with them is... flexible. Maybe I'm joking." """,
            "Shrug, soft fabric, calm voice.",
            f"{BEN} shrugging opposite {JUN}. Table between them. {APT}. {STYLE}",
            "Shrug; Jun leans in; lipsync; continuity.",
        ),
        (
            "MIRA CHECKS HER PHONE",
            "THE WELL",
            6000,
            "Medium shot",
            "Mira's phone buzzes. She glances, stands, suddenly in another story.",
            """MIRA:
"Work. They want me downstairs in ten." """,
            "Phone buzz, her brighter tone returning.",
            f"{MIRA} standing with phone glow on her face. Mustard cardigan, bare feet. {APT}. {STYLE}",
            "Phone light; she stands; lipsync; identity locked.",
        ),
        (
            "SHE DOESN'T HEAR IT",
            "THE WELL",
            6000,
            "Over-the-shoulder",
            "Jun looks at Mira as if to warn her; she is already hunting for shoes, missing the undercurrent.",
            "Dialogue: None.",
            "Drawer rummage, room tone.",
            f"OTS {JUN} watching {MIRA} search for shoes near the door. {BEN} seated, watching both. {APT}. {STYLE}",
            "Mira moves; Jun frozen; Ben still; slow camera pan.",
        ),
        (
            "BEN SMILES AT THE CHAOS",
            "THE WELL",
            6000,
            "Close-up",
            "Ben watches Mira's exit prep with fond ownership, then flicks his eyes to Jun — checkmate energy without words.",
            "Dialogue: None.",
            "Room tone only.",
            f"Close on {BEN}, fond then sharp. Gold watch. {STYLE}",
            "Eye flick toward Jun; tiny smile; no dialogue.",
        ),
        # --- EXIT ---
        (
            "SHOES ON",
            "EXIT",
            5000,
            "Medium shot",
            "Mira pulls on white sneakers by the door, cardigan still on, necklace catching lamp light.",
            """MIRA:
"Don't interrogate each other. You're both weird in different fonts." """,
            "Shoe scuff, light laugh.",
            f"{MIRA} at the door putting on white sneakers. {JUN} and {BEN} behind her. {APT}. {STYLE}",
            "She ties laces; laugh; lipsync; identities preserved.",
        ),
        (
            "KISS ON THE CHEEK",
            "EXIT",
            6000,
            "Medium close-up",
            "Mira kisses Jun's cheek quickly, then squeezes Ben's shoulder like a hostess leaving her own party.",
            """MIRA:
"Eat the oranges. Somebody should." """,
            "Quick kiss sound, door approaching.",
            f"{MIRA} kissing {JUN}'s cheek; {BEN} nearby. Same wardrobe. {APT}. {STYLE}",
            "Kiss; step to Ben; squeeze shoulder; continuity.",
        ),
        (
            "DOOR CLOSES",
            "EXIT",
            6000,
            "Wide shot",
            "Door closes. The apartment feels smaller. Two men and the oranges remain.",
            "Dialogue: None.",
            "Door latch, sudden quieter room.",
            f"Wide after exit: {JUN} and {BEN} alone at the table. Empty space where Mira was. {APT}. {STYLE}",
            "Door closes; ambient drop; slow push on the two men.",
        ),
        (
            "SILENCE GAME",
            "EXIT",
            6000,
            "Two-shot",
            "They sit in silence. Ben finishes the orange. Jun does not blink enough.",
            "Dialogue: None.",
            "Chewing, city far away.",
            f"Two-shot silence. {BEN} eating; {JUN} staring. {APT} night deepening outside. {STYLE}",
            "Ben chews; Jun still; long held tension.",
        ),
        (
            "BEN OFFERS NORMAL",
            "EXIT",
            6000,
            "Medium close-up",
            "Ben breaks the silence with hospitality, as if the well never existed.",
            """BEN:
"She's special. People like her vanish if nobody pays attention." """,
            "Conversational, soft threat under care.",
            f"Close on {BEN}, caring tone that doesn't comfort. {STYLE}",
            "Slight lean; lipsync; eyes sincere and wrong.",
        ),
        (
            "JUN'S LINE",
            "EXIT",
            6000,
            "Medium close-up",
            "Jun answers carefully, like placing a glass on thin ice.",
            """JUN:
"Then maybe stop talking about wells." """,
            "Tight, controlled voice.",
            f"Close on {JUN}, controlled fear. Olive jacket. {APT}. {STYLE}",
            "Jaw set; lipsync; tiny eye twitch.",
        ),
        # --- OFFER ---
        (
            "LIFT OFFER",
            "OFFER",
            5000,
            "Medium shot",
            "Ben stands, smooth, collecting his keys from the table.",
            """BEN:
"I can drop you somewhere. My car's downstairs." """,
            "Keys lift, chair scrape.",
            f"{BEN} standing with keys. {JUN} still seated. {APT}. {STYLE}",
            "Ben stands; keys glint; lipsync; identity locked.",
        ),
        (
            "JUN DECLINES",
            "OFFER",
            5000,
            "Medium close-up",
            "Jun shakes his head once, too fast.",
            """JUN:
"I'll walk." """,
            "Short refusal.",
            f"Close on {JUN} refusing. {STYLE}",
            "Head shake; lipsync; held breath.",
        ),
        (
            "BEN ACCEPTS THE REFUSAL",
            "OFFER",
            5000,
            "Medium shot",
            "Ben smiles like a man who already won a different game.",
            """BEN:
"Good. Walking clears the metaphors." """,
            "Amused voice, keys pocketed.",
            f"{BEN} smiling, pocketing keys near the door. {APT}. {STYLE}",
            "Smile; pocket keys; step toward door; lipsync.",
        ),
        (
            "AT THE DOOR",
            "OFFER",
            5000,
            "Over-the-shoulder",
            "Ben pauses at the door, looking back at Jun still seated among oranges.",
            """BEN:
"If a greenhouse burns this month, don't write me into it." """,
            "Doorframe hush, dry joke.",
            f"OTS from {JUN} to {BEN} at the open door. Hall light colder. {APT}. {STYLE}",
            "Ben half-turns; lipsync; Jun frozen in FG.",
        ),
        (
            "BEN LEAVES",
            "OFFER",
            5000,
            "Wide shot",
            "Ben leaves. Door closes. Jun alone with fruit, lamp, and the window.",
            "Dialogue: None.",
            "Door latch, thick silence.",
            f"Wide: {JUN} alone in Mira's apartment. Oranges on table. Empty chair. {APT} night. {STYLE}",
            "Door closes; Jun doesn't move; slow push-in.",
        ),
        # --- RESIDUE ---
        (
            "ALONE WITH THE WINDOW",
            "RESIDUE",
            5000,
            "Wide shot",
            "Jun stands and goes to the window. Ridge lights glitter. One dark gap holds.",
            "Dialogue: None.",
            "City night murmur.",
            f"{JUN} at the window, back partly to camera, olive jacket. Distant greenhouse lights. {APT}. {STYLE}",
            "He approaches glass; city lights breathe; hold identity.",
        ),
        (
            "PHONE BUZZ",
            "RESIDUE",
            5000,
            "Insert close-up",
            "Jun's phone buzzes on the table beside the oranges.",
            "Dialogue: None.",
            "Harsh phone buzz in quiet room.",
            f"Insert: phone vibrating beside oranges and juice carton on the low table. Lamp glow. {STYLE}",
            "Phone buzzes; subtle table vibration.",
        ),
        (
            "THE MESSAGE",
            "RESIDUE",
            5000,
            "Over-the-shoulder phone",
            "On screen, a message from Mira: don't trust him. Then a second bubble typing… that never sends.",
            "Dialogue: None.",
            "Notification tap, room tone.",
            f"OTS {JUN} reading phone. Message text clearly readable: \"don't trust him.\" Typing dots appear then vanish. Keep Jun face consistent in profile. {STYLE}",
            "Thumb unlocks; message appears; typing dots fail; Jun's profile stiffens.",
        ),
        (
            "FINAL LOOK",
            "RESIDUE",
            5000,
            "Close-up",
            "Jun looks from the phone to the dark ridge. He does not call her. He only watches the place where a little flame could start.",
            "Dialogue: None.",
            "City hush, almost nothing.",
            f"Close on {JUN}'s face, phone light fading, eyes on off-screen window. Fear without spectacle. Olive jacket collar. {STYLE}",
            "Eyes shift to window; tiny exhale; hold; slow fade of phone light; end.",
        ),
    ]

    assert len(specs) == 50, len(specs)

    header = f"""GREENHOUSE

Genre: psychological mystery
Duration: 300 seconds
Format: 50 shots x ~6 seconds
Aspect ratio: 16:9

Character consistency

JUN (lead) — {JUN}. Soft-spoken Korean writer, socially careful, watches more than he speaks. Never changes clothes in this film.
MIRA (supporting) — {MIRA}. Warm, impulsive, theatrical when happy. Barefoot indoors until exit. Never changes clothes except adding white sneakers at the door.
BEN (antagonist) — {BEN}. Wealthy, gently predatory calm. Treats people like rare objects. Never changes clothes.

Location: {APT}. Entire film stays in or immediately outside this apartment door/stair landing. Maintain identical set dressing: low table, oranges and juice after Shot 5, floor lamp, thin grey curtains, blue-hour then night progression only through window light — no location changes, no costume changes, no new major props.

MAIN SEQUENCE

"""

    chunks = [header]
    cursor = 0
    for index, spec in enumerate(specs, start=1):
        title, scene, dur, framing, action, dialogue, sound, image, video = spec
        location = (
            "Apartment stair landing outside Mira's door"
            if index <= 2
            else "Mira's small Seoul apartment living room"
        )
        chunks.append(
            shot_block(
                index,
                title,
                scene,
                cursor,
                dur,
                framing,
                action.strip(),
                dialogue.strip(),
                sound.strip(),
                image.strip(),
                video.strip(),
                location=location,
                time_of_day="Evening",
            )
        )
        cursor += dur

    assert cursor == 300_000, cursor
    return "\n".join(chunks).strip() + "\n"


def request(method: str, path: str, body: dict | None = None) -> dict | list:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"{method} {path} failed ({exc.code}): {detail}") from exc


def wait_job(job_id: str, timeout: float = 180) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = request("GET", f"/api/jobs/{job_id}")
        status = job.get("status")
        if status in {"completed", "failed", "cancelled"}:
            return job
        time.sleep(0.8)
    raise TimeoutError(f"Job {job_id} did not finish")


def main() -> None:
    from backend.screenplay_parser import parse_structured_screenplay

    script = build_screenplay()
    SCRIPT_PATH.write_text(script, encoding="utf-8")
    print(f"Wrote {SCRIPT_PATH} ({len(script)} chars)")

    parsed = parse_structured_screenplay(script)
    assert parsed is not None, "Parser rejected screenplay"
    scenes = parsed["scenes"]
    shots = [shot for scene in scenes for shot in scene["shots"]]
    total_ms = shots[-1]["end_ms"]
    blocking = [s for s in parsed["suggestions"] if s.get("severity") == "blocking"]
    print(
        f"Parsed: {len(scenes)} scenes, {len(shots)} shots, "
        f"end={total_ms}ms, suggestions={len(parsed['suggestions'])}, blocking={len(blocking)}"
    )
    if blocking:
        for item in blocking[:12]:
            print(" BLOCKING:", item["title"], "-", item["reason"])
        raise SystemExit(1)
    for item in parsed["suggestions"][:8]:
        print(f"  {item['severity']}: {item['title']}")

    if "--parse-only" in sys.argv:
        return

    # Health
    request("GET", "/api/health")

    job = request(
        "POST",
        "/api/projects",
        {
            "title": "Greenhouse",
            "aspect_ratio": "16:9",
            "target_duration_seconds": 300,
            "text_provider": "mock",
            "script": script,
        },
    )
    print("Created analysis job:", job["id"])
    final = wait_job(job["id"])
    if final.get("status") != "completed":
        raise RuntimeError(f"Analysis failed: {final}")
    print("Analysis:", final.get("message"))

    projects = request("GET", "/api/projects")
    project = next(p for p in projects if p["id"] == job["project_id"] or p["title"] == "Greenhouse")
    # Prefer the job's project_id
    project_id = job.get("project_id") or project["id"]
    detail = request("GET", f"/api/projects/{project_id}")
    print(f"Project {project_id} status={detail['status']}")

    # Dismiss non-blocking suggestions so approve is clean if needed
    version = detail.get("current_version") or {}
    for suggestion in version.get("suggestions") or []:
        if suggestion.get("status") == "pending" and suggestion.get("severity") == "blocking":
            request(
                "POST",
                f"/api/suggestions/{suggestion['id']}/decision",
                {"decision": "rejected"},
            )

    approved = request(
        "POST",
        f"/api/projects/{project_id}/approve",
        {"approved": True},
    )
    print("Approved:", approved.get("status"))

    detail = request("GET", f"/api/projects/{project_id}")
    version = detail["current_version"]
    scenes = version["scenes"]

    continuity_by_scene = {
        "RETURN": {
            "set_notes": "Stair landing then Mira's living room. Same door, same low table, thin grey curtains, floor lamp off then on.",
            "lighting_notes": "Blue-hour dusk through window; warm floor lamp after Shot 5; no daylight reset.",
            "wardrobe_notes": "Jun olive jacket + grey crewneck. Mira mustard cardigan, white tee, jeans, barefoot. Ben not yet present until later scenes.",
            "props_notes": "Grocery bag → oranges and juice on low table. Keep oranges visible thereafter.",
        },
        "THE THIRD": {
            "set_notes": "Same living room. Ben enters from short inner hall. Only one chair.",
            "lighting_notes": "Lamp on; window blue hour continuing.",
            "wardrobe_notes": "Add Ben: charcoal sweater, black trousers, gold watch. No costume changes for Jun/Mira.",
            "props_notes": "Oranges, juice, Ben's water glass. Orange peeling begins.",
        },
        "PANTOMIME": {
            "set_notes": "Same room; Mira performs near the low table.",
            "lighting_notes": "Warm lamp key; soft night starting outside.",
            "wardrobe_notes": "Identical cast looks. Mira barefoot.",
            "props_notes": "Invisible fruit then one real orange tossed to Jun.",
        },
        "THE HOBBY": {
            "set_notes": "Same seating duel across low table; Mira against wall.",
            "lighting_notes": "Lamp practical; ridge greenhouse lights visible through window.",
            "wardrobe_notes": "No changes.",
            "props_notes": "Oranges; napkin for Ben.",
        },
        "THE WELL": {
            "set_notes": "Same room; tension between Ben and Jun; Mira checks phone near door.",
            "lighting_notes": "Night deepening at window; lamp steady.",
            "wardrobe_notes": "No changes until Mira finds sneakers in EXIT.",
            "props_notes": "Mira's phone glow.",
        },
        "EXIT": {
            "set_notes": "Door area then emptied living room.",
            "lighting_notes": "Lamp on; colder hall spill when door opens.",
            "wardrobe_notes": "Mira adds white sneakers; still mustard cardigan.",
            "props_notes": "Oranges remain; Mira leaves.",
        },
        "OFFER": {
            "set_notes": "Two men then Jun alone.",
            "lighting_notes": "Night exterior through window; lamp interior.",
            "wardrobe_notes": "Jun and Ben unchanged.",
            "props_notes": "Ben's keys; oranges.",
        },
        "RESIDUE": {
            "set_notes": "Jun alone at window and table.",
            "lighting_notes": "Night; phone screen as secondary light.",
            "wardrobe_notes": "Jun only.",
            "props_notes": "Phone message 'don't trust him'; oranges; distant greenhouse lights.",
        },
    }

    for scene in scenes:
        title = (scene.get("title") or "").strip().upper()
        # Group titles may be scene labels
        notes = continuity_by_scene.get(title)
        if not notes:
            # try matching shot-group title from first shot scene field — scenes use group titles
            for key, value in continuity_by_scene.items():
                if key in title:
                    notes = value
                    break
        if not notes:
            # Map by order if titles are shot titles grouped — parser groups by Scene: label
            print(" No continuity map for scene:", scene.get("title"))
            continue
        updated = request("PATCH", f"/api/scenes/{scene['id']}", notes)
        print(f" Continuity set: {updated.get('title')}")

    detail = request("GET", f"/api/projects/{project_id}")
    version = detail["current_version"]
    shot_count = sum(len(s["shots"]) for s in version["scenes"])
    cast = detail.get("characters") or []
    print(
        f"\nReady: http://127.0.0.1:5173/projects/{project_id}/shots\n"
        f"  scenes={len(version['scenes'])} shots={shot_count} cast={[c.get('name') for c in cast]} status={detail['status']}"
    )


if __name__ == "__main__":
    main()
