#!/usr/bin/env python3
"""
Reusable Motion Graphic Benefit Feed Generator for Cutline.

Generates transparent WebM (VP9 alpha) animated floating cards with
sequential bullet points, uploads them to Cutline's timeline assets,
and places them on the video overlay track.
"""

import argparse
import json
import math
import os
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from uuid import uuid4
from PIL import Image, ImageDraw, ImageFont

CUTLINE_API_BASE = "http://127.0.0.1:8000"

PRENATAL_CARDS = [
    {
        "shot_index": 0,
        "start_ms": 0,
        "title": "March in Place",
        "badge": "PRENATAL CARE · BENEFIT FEED",
        "bullets": [
            ("Stimulates calf muscle pump", 0.6),
            ("Promotes venous circulation", 2.0),
            ("Relieves swelling & cramps", 3.4),
        ],
    },
    {
        "shot_index": 1,
        "start_ms": 6000,
        "title": "Shoulder Circles",
        "badge": "PRENATAL CARE · BENEFIT FEED",
        "bullets": [
            ("Eases upper back tension", 0.6),
            ("Counters forward shoulder roll", 2.0),
            ("Opens chest for deep breathing", 3.4),
        ],
    },
    {
        "shot_index": 2,
        "start_ms": 12000,
        "title": "Hip Circles",
        "badge": "PRENATAL CARE · BENEFIT FEED",
        "bullets": [
            ("Mobilizes SI & pelvic joints", 0.6),
            ("Releases lower back stiffness", 2.0),
            ("Relaxes pelvic floor muscles", 3.4),
        ],
    },
    {
        "shot_index": 3,
        "start_ms": 18000,
        "title": "Standing Cat-Cow",
        "badge": "PRENATAL CARE · BENEFIT FEED",
        "bullets": [
            ("Spinal decompression (no floor)", 0.6),
            ("Hands on thighs supports bump", 2.0),
            ("Relieves lumbar spine pressure", 3.4),
        ],
    },
    {
        "shot_index": 4,
        "start_ms": 24000,
        "title": "Wide-Stance Squats",
        "badge": "PRENATAL CARE · BENEFIT FEED",
        "bullets": [
            ("Wide stance clears baby bump", 0.6),
            ("Strengthens glutes & quads", 2.0),
            ("Opens pelvic outlet for labor", 3.4),
        ],
    },
    {
        "shot_index": 5,
        "start_ms": 30000,
        "title": "Wall Push-Ups",
        "badge": "PRENATAL CARE · BENEFIT FEED",
        "bullets": [
            ("Upper-body chest & arm tone", 0.6),
            ("Zero intra-abdominal pressure", 2.0),
            ("100% diastasis recti safe", 3.4),
        ],
    },
    {
        "shot_index": 6,
        "start_ms": 36000,
        "title": "Standing Hip Extensions",
        "badge": "PRENATAL CARE · BENEFIT FEED",
        "bullets": [
            ("Strengthens posterior chain", 0.6),
            ("Replaces floor glute bridges", 2.0),
            ("Avoids supine vena cava pressure", 3.4),
        ],
    },
    {
        "shot_index": 7,
        "start_ms": 42000,
        "title": "Calf Raises",
        "badge": "PRENATAL CARE · BENEFIT FEED",
        "bullets": [
            ("Engages lower leg muscle pump", 0.6),
            ("Improves return circulation", 2.0),
            ("Prevents nocturnal leg cramps", 3.4),
        ],
    },
    {
        "shot_index": 8,
        "start_ms": 48000,
        "title": "Standing Cross-Body March",
        "badge": "PRENATAL CARE · BENEFIT FEED",
        "bullets": [
            ("Transverse abdominis stability", 0.6),
            ("Replaces floor crunches/twists", 2.0),
            ("Improves single-leg balance", 3.4),
        ],
    },
    {
        "shot_index": 9,
        "start_ms": 54000,
        "title": "Side-to-Side Weight Shifts",
        "badge": "PRENATAL CARE · BENEFIT FEED",
        "bullets": [
            ("Pelvic ring & adductor balance", 0.6),
            ("Avoids lunge shearing & SPD", 2.0),
            ("Gentle rhythmic hip comfort", 3.4),
        ],
    },
    {
        "shot_index": 10,
        "start_ms": 60000,
        "title": "Chest & Shoulder Stretch",
        "badge": "PRENATAL CARE · BENEFIT FEED",
        "bullets": [
            ("Expands ribcage & thoracic room", 0.6),
            ("Relieves postural shoulder roll", 2.0),
            ("Supports full lung expansion", 3.4),
        ],
    },
    {
        "shot_index": 11,
        "start_ms": 66000,
        "title": "Supported Hamstring Stretch",
        "badge": "PRENATAL CARE · BENEFIT FEED",
        "bullets": [
            ("Releases tight posterior chain", 0.6),
            ("Hands on thigh keeps spine neutral", 2.0),
            ("Prevents anterior pelvic tilt", 3.4),
        ],
    },
    {
        "shot_index": 12,
        "start_ms": 72000,
        "title": "Slow Walk & Breathing",
        "badge": "PRENATAL CARE · BENEFIT FEED",
        "bullets": [
            ("Calms heart rate & nervous system", 0.6),
            ("Diaphragmatic belly breathing", 2.0),
            ("Conscious pelvic floor release", 3.4),
        ],
    },
]


def find_font():
    paths = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFPro-Bold.otf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def ease_out_cubic(x: float) -> float:
    return 1.0 - math.pow(1.0 - max(0.0, min(1.0, x)), 3)


def render_card_video(
    title: str,
    badge: str,
    bullets: list[tuple[str, float]],
    output_path: str,
    card_w: int = 520,
    card_h: int = 340,
    fps: int = 30,
    duration_sec: float = 6.0,
):
    font_path = find_font()
    badge_font = ImageFont.truetype(font_path, 14) if font_path else ImageFont.load_default()
    title_font = ImageFont.truetype(font_path, 25) if font_path else ImageFont.load_default()
    body_font = ImageFont.truetype(font_path, 18) if font_path else ImageFont.load_default()
    check_font = ImageFont.truetype(font_path, 16) if font_path else ImageFont.load_default()

    total_frames = int(fps * duration_sec)

    with tempfile.TemporaryDirectory(prefix="cutline_card_") as tmpdir:
        for f in range(total_frames):
            t = f / fps
            im = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(im)

            # Card background entrance: 0.1s to 0.5s
            card_t = max(0.0, min(1.0, (t - 0.1) / 0.4))
            card_alpha = ease_out_cubic(card_t)
            card_offset = int((1.0 - ease_out_cubic(card_t)) * -30)

            if card_alpha > 0.01:
                cx = card_offset
                cy = 0

                # Dark slate frosted card with glowing cyan border
                bg_color = (15, 23, 42, int(220 * card_alpha))
                border_color = (56, 189, 248, int(150 * card_alpha))
                draw.rounded_rectangle(
                    [cx, cy, cx + card_w - 2, cy + card_h - 2],
                    radius=22,
                    fill=bg_color,
                    outline=border_color,
                    width=2,
                )

                # Header Pill
                draw.text(
                    (cx + 28, cy + 24),
                    badge,
                    fill=(56, 189, 248, int(255 * card_alpha)),
                    font=badge_font,
                )

                # Title
                draw.text(
                    (cx + 28, cy + 48),
                    title,
                    fill=(255, 255, 255, int(255 * card_alpha)),
                    font=title_font,
                )
                draw.line(
                    [cx + 28, cy + 86, cx + card_w - 28, cy + 86],
                    fill=(255, 255, 255, int(40 * card_alpha)),
                    width=1,
                )

                # Bullets
                for i, (b_text, start_time) in enumerate(bullets):
                    bt = max(0.0, min(1.0, (t - start_time) / 0.4))
                    b_ease = ease_out_cubic(bt)
                    if b_ease > 0.01:
                        by = cy + 108 + i * 66
                        bx = cx + 28 + int((1.0 - b_ease) * -20)
                        b_alpha = int(255 * b_ease * card_alpha)

                        # Pill check icon
                        draw.rounded_rectangle(
                            [bx, by, bx + 26, by + 26],
                            radius=13,
                            fill=(56, 189, 248, int(45 * b_ease)),
                            outline=(56, 189, 248, int(180 * b_ease)),
                            width=1,
                        )
                        draw.text(
                            (bx + 7, by + 4),
                            "✓",
                            fill=(56, 189, 248, b_alpha),
                            font=check_font,
                        )
                        draw.text(
                            (bx + 38, by + 4),
                            b_text,
                            fill=(241, 245, 249, b_alpha),
                            font=body_font,
                        )

            im.save(os.path.join(tmpdir, f"frame_{f:04d}.png"))

        cmd = [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            os.path.join(tmpdir, "frame_%04d.png"),
            "-c:v",
            "libvpx-vp9",
            "-pix_fmt",
            "yuva420p",
            "-b:v",
            "800k",
            output_path,
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def upload_asset_to_cutline(project_id: str, video_path: str, label: str, episode_id: str) -> str:
    boundary = "----WebKitFormBoundary" + uuid4().hex
    with open(video_path, "rb") as f:
        video_bytes = f.read()

    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(b'Content-Disposition: form-data; name="label"\r\n\r\n')
    body.extend(f"{label}\r\n".encode("utf-8"))
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(b'Content-Disposition: form-data; name="kind"\r\n\r\n')
    body.extend(b"video\r\n")
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(video_path)}"\r\n'.encode(
            "utf-8"
        )
    )
    body.extend(b"Content-Type: video/webm\r\n\r\n")
    body.extend(video_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    req = urllib.request.Request(
        f"{CUTLINE_API_BASE}/api/projects/{project_id}/timeline/assets?episode_id={episode_id}",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    with urllib.request.urlopen(req) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        last_asset = res["assets"][-1]
        return last_asset["id"]


def build_overlay_dict(asset_id: str, label: str, start_ms: int, duration_ms: int, position: str = "top-left") -> dict:
    return {
        "id": str(uuid4()),
        "label": label,
        "source": {"kind": "asset", "ref_id": asset_id},
        "start_ms": start_ms,
        "duration_ms": duration_ms,
        "source_in_ms": 0,
        "opacity": 1.0,
        "volume": 0.0,
        "mute_audio": True,
        "variant": {
            "landscape": {
                "position": position,
                "scale": 1.0,
                "offset_x": 0.0,
                "offset_y": 0.0,
                "zoom": 1.0,
                "focal_x": 0.5,
                "focal_y": 0.5,
                "crop_top": 0.0,
                "crop_bottom": 0.0,
                "crop_left": 0.0,
                "crop_right": 0.0,
                "lut": "none",
                "lut_strength": 1.0,
                "frame_style": "none",
                "frame_radius": 0.0,
                "frame_width": 0.008,
                "frame_color": "#FFFFFF",
            }
        },
    }


def update_timeline_overlays(project_id: str, episode_id: str, overlays: list[dict]):
    with urllib.request.urlopen(
        f"{CUTLINE_API_BASE}/api/projects/{project_id}/timeline?episode_id={episode_id}"
    ) as resp:
        tl = json.loads(resp.read().decode("utf-8"))

    doc = tl["document"]
    revision = tl["revision"]
    doc["video_overlays"] = overlays

    put_req = urllib.request.Request(
        f"{CUTLINE_API_BASE}/api/projects/{project_id}/timeline?episode_id={episode_id}",
        data=json.dumps({"revision": revision, "document": doc, "edit": "landscape"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )

    with urllib.request.urlopen(put_req) as resp:
        saved = json.loads(resp.read().decode("utf-8"))
        return saved["revision"]


def main():
    parser = argparse.ArgumentParser(description="Generate and upload motion benefit cards to Cutline")
    parser.add_argument(
        "--project-id", default="f8101316-05f4-4433-9d3c-8feafdd06d94", help="Cutline Project ID"
    )
    parser.add_argument(
        "--episode-id", default="fdbbacb5-d1f9-4851-be59-6a2b865697a7", help="Cutline Episode ID"
    )
    parser.add_argument(
        "--position", default="top-left", choices=["top-left", "top-right", "bottom-left", "bottom-right", "center", "top", "bottom"],
        help="Default screen position for the cards"
    )
    parser.add_argument(
        "--all-prenatal", action="store_true", help="Generate all 13 prenatal exercise cards"
    )
    args = parser.parse_args()

    if args.all_prenatal:
        print(f"Generating all {len(PRENATAL_CARDS)} prenatal motion benefit cards...")
        all_overlays = []
        with tempfile.TemporaryDirectory(prefix="all_cards_") as out_dir:
            for item in PRENATAL_CARDS:
                title = item["title"]
                print(f"[{item['shot_index'] + 1}/13] Rendering card for: {title}...")
                filename = f"card_shot_{item['shot_index'] + 1:02d}.webm"
                out_path = os.path.join(out_dir, filename)
                render_card_video(
                    title=title,
                    badge=item["badge"],
                    bullets=item["bullets"],
                    output_path=out_path,
                )

                label = f"{title} - Benefit Card"
                print(f"       Uploading to Cutline assets...")
                asset_id = upload_asset_to_cutline(args.project_id, out_path, label, args.episode_id)

                overlay = build_overlay_dict(
                    asset_id=asset_id,
                    label=label,
                    start_ms=item["start_ms"],
                    duration_ms=6000,
                    position=args.position,
                )
                all_overlays.append(overlay)

        print(f"Attaching {len(all_overlays)} overlays to Episode 2 timeline...")
        new_rev = update_timeline_overlays(args.project_id, args.episode_id, all_overlays)
        print(f"SUCCESS! All 13 overlays attached to timeline (revision {new_rev})!")


if __name__ == "__main__":
    main()
