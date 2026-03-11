"""
core/video_renderer.py
FFmpeg-powered video renderer.
Features:
  - Ken Burns (slow zoom) effect
  - Dynamic text overlays with drop shadows (Hook / Body / CTA)
  - Audio merge with fade in/out
  - Correct aspect ratios: 1080×1920 (Reel) or 1080×1350 (Feed)
"""

import hashlib
import logging
import os
import subprocess
import textwrap
from pathlib import Path
from typing import Optional

log = logging.getLogger("oracle.renderer")

OUTPUT_DIR = Path("assets/output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_PATH_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# Post type dimensions
DIMENSIONS = {
    "reel": (1080, 1920),
    "feed": (1080, 1350),
}

# Video settings
VIDEO_DURATION = 30       # seconds
AUDIO_FADE_DURATION = 2   # seconds for audio fade in/out
KEN_BURNS_ZOOM = 0.04     # 4% zoom over the full duration (subtle)
FRAMERATE = 30
VIDEO_BITRATE = "4M"
AUDIO_BITRATE = "128k"


class VideoRenderer:
    def render(
        self,
        image_path: Path,
        audio_path: Path,
        text_layers: list[dict],
        post_type: str = "reel",
    ) -> Path:
        """
        Render the final video. Returns path to output .mp4 file.
        """
        width, height = DIMENSIONS.get(post_type, (1080, 1920))
        img_hash = hashlib.md5(str(image_path).encode()).hexdigest()[:10]
        output_path = OUTPUT_DIR / f"{img_hash}_{post_type}.mp4"

        filter_complex = self._build_filter_complex(
            width, height, text_layers, post_type
        )

        cmd = self._build_ffmpeg_cmd(
            image_path=image_path,
            audio_path=audio_path,
            output_path=output_path,
            filter_complex=filter_complex,
            width=width,
            height=height,
        )

        log.info(f"Rendering video: {output_path.name}")
        log.debug(f"FFmpeg command:\n{' '.join(cmd)}")

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            log.error(f"FFmpeg stderr:\n{result.stderr}")
            raise RuntimeError(f"FFmpeg rendering failed (exit {result.returncode})")

        size_mb = output_path.stat().st_size / (1024 * 1024)
        log.info(f"Render complete: {output_path} ({size_mb:.1f} MB)")
        return output_path

    def _build_filter_complex(
        self, width: int, height: int, text_layers: list[dict], post_type: str
    ) -> str:
        """
        Build the FFmpeg -filter_complex string.
        Chain: scale → Ken Burns zoom → text overlays → audio fade
        """
        filters = []

        # ── 1. Scale and pad image to target dimensions ────────────────────────
        filters.append(
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            f"setsar=1"
            f"[scaled]"
        )

        # ── 2. Ken Burns zoom effect ───────────────────────────────────────────
        # zoompan: start at zoom=1.0, end at zoom=1+KEN_BURNS_ZOOM
        # fps controls framerate; d=total_frames
        total_frames = VIDEO_DURATION * FRAMERATE
        zoom_increment = KEN_BURNS_ZOOM / total_frames
        filters.append(
            f"[scaled]zoompan="
            f"z='min(zoom+{zoom_increment:.6f},1+{KEN_BURNS_ZOOM})':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':"
            f"d={total_frames}:"
            f"s={width}x{height}:"
            f"fps={FRAMERATE}"
            f"[zoomed]"
        )

        # ── 3. Text overlays ───────────────────────────────────────────────────
        prev_label = "zoomed"
        for i, layer in enumerate(text_layers):
            next_label = f"text{i}" if i < len(text_layers) - 1 else "vout"
            font = FONT_PATH if layer.get("bold") else FONT_PATH_REGULAR

            # Escape special characters for FFmpeg drawtext
            text = self._escape_drawtext(layer["text"])
            color = layer.get("color", "#FFFFFF").lstrip("#")
            shadow = layer.get("shadow_color", "#000000").lstrip("#")
            font_size = layer.get("font_size", 60)
            y_pct = layer.get("y_position", 0.5)
            y_expr = f"(h*{y_pct:.3f})"
            appear_at = layer.get("appear_at", 0)

            drawtext = (
                f"drawtext="
                f"fontfile='{font}':"
                f"text='{text}':"
                f"fontsize={font_size}:"
                f"fontcolor=0x{color}FF:"
                f"x=(w-text_w)/2:"          # Always centered horizontally
                f"y={y_expr}-text_h/2:"
                f"shadowcolor=0x{shadow}CC:"
                f"shadowx=3:"
                f"shadowy=3:"
                f"enable='gte(t,{appear_at})'"
            )

            filters.append(f"[{prev_label}]{drawtext}[{next_label}]")
            prev_label = next_label

        # If no text layers, rename zoomed → vout
        if not text_layers:
            filters[-1] = filters[-1].replace("[zoomed]", "[zoomed]null[vout]")
            # Actually just alias
            filters.append("[zoomed]null[vout]")

        # ── 4. Audio: trim to VIDEO_DURATION + fade in/out ────────────────────
        filters.append(
            f"[1:a]atrim=0:{VIDEO_DURATION},"
            f"asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d={AUDIO_FADE_DURATION},"
            f"afade=t=out:st={VIDEO_DURATION - AUDIO_FADE_DURATION}:d={AUDIO_FADE_DURATION},"
            f"volume=0.4"     # Keep music subtle, not overpowering
            f"[aout]"
        )

        return ";".join(filters)

    def _build_ffmpeg_cmd(
        self,
        image_path: Path,
        audio_path: Path,
        output_path: Path,
        filter_complex: str,
        width: int,
        height: int,
    ) -> list[str]:
        return [
            "ffmpeg", "-y",
            # Input 0: image (loop for VIDEO_DURATION seconds)
            "-loop", "1",
            "-framerate", str(FRAMERATE),
            "-i", str(image_path),
            # Input 1: audio
            "-i", str(audio_path),
            # Filter graph
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "[aout]",
            # Video codec
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "22",
            "-b:v", VIDEO_BITRATE,
            "-maxrate", VIDEO_BITRATE,
            "-bufsize", "8M",
            "-pix_fmt", "yuv420p",   # Required for Instagram compatibility
            # Audio codec
            "-c:a", "aac",
            "-b:a", AUDIO_BITRATE,
            "-ar", "44100",
            # Duration
            "-t", str(VIDEO_DURATION),
            # Instagram metadata
            "-movflags", "+faststart",
            str(output_path),
        ]

    @staticmethod
    def _escape_drawtext(text: str) -> str:
        """Escape characters that break FFmpeg drawtext filter."""
        # Replace characters that FFmpeg drawtext cannot handle
        replacements = {
            "'": "\u2019",   # Smart apostrophe
            ":": "\\:",
            "\\": "\\\\",
            "%": "\\%",
            "[": "\\[",
            "]": "\\]",
            "{": "\\{",
            "}": "\\}",
        }
        for char, replacement in replacements.items():
            text = text.replace(char, replacement)
        return text
