"""
Image generation via Gemini native image output.

FREE TIER MODEL (confirmed working, March 2026):
    gemini-2.0-flash-exp-image-generation   ← THE ONLY FREE ONE
    — DO NOT use gemini-2.5-flash-image     ← limit: 0 on free tier (quota = 0)
    — DO NOT use generate_images()          ← that is Imagen 4, paid only

API:  client.models.generate_content() with response_modalities=["IMAGE", "TEXT"]
Fallback: FFmpeg solid-color gradient.
"""

import asyncio
import hashlib
import logging
import os
import subprocess
from pathlib import Path

from google import genai
from google.genai import types

log = logging.getLogger("oracle.image")

ASSETS_DIR = Path("assets/images")
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

DIMENSIONS   = {"reel": (1080, 1920), "feed": (1080, 1350)}
ASPECT_HINTS = {"reel": "portrait 9:16 vertical", "feed": "portrait 4:5 vertical"}

# gemini-2.5-flash-image      = free tier quota IS 0 — DO NOT USE
# gemini-2.0-flash-exp-image-generation = free tier, confirmed working
IMAGE_MODEL = "gemini-2.0-flash-exp-image-generation"

TOPIC_COLORS = {
    "stoic": "0D1B2A", "philosoph": "1A1A2E", "motivat": "1A0A2E",
    "mindful": "0D2818", "success": "0A1628", "nature": "0D2010",
    "space": "030418",  "tech": "0D1B2A",    "fitness": "1A0808",
    "wisdom": "1A1408", "life": "0D1020",     "love": "1A0818",
}


class ImageGenerator:
    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError("GEMINI_API_KEY not set.")
        self.client = genai.Client(api_key=api_key)

    async def generate(self, prompt: str, post_type: str = "reel") -> Path:
        w, h = DIMENSIONS.get(post_type, (1080, 1920))
        slug = hashlib.md5(f"{prompt}{post_type}".encode()).hexdigest()[:12]
        out  = ASSETS_DIR / f"{slug}.png"

        if out.exists():
            log.info(f"Image cache hit: {out}")
            return out

        try:
            return await self._call_with_retry(prompt, post_type, out)
        except Exception as e:
            log.warning(f"Image generation failed: {e}")

        log.warning("Falling back to gradient image.")
        return self._gradient(w, h, out, prompt)

    async def _call_with_retry(self, prompt: str, post_type: str, out: Path) -> Path:
        last_exc = None
        for attempt in range(3):
            try:
                return await self._call(prompt, post_type, out)
            except Exception as e:
                last_exc = e
                err_str = str(e)
                # Parse retry delay from 429 response if present
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    import re
                    match = re.search(r"retry[^\d]*(\d+)", err_str, re.IGNORECASE)
                    delay = int(match.group(1)) + 2 if match else 60
                    log.warning(f"Rate limited — waiting {delay}s before retry {attempt + 1}/3")
                    await asyncio.sleep(delay)
                else:
                    log.warning(f"Image attempt {attempt + 1}/3 failed: {e}")
                    if attempt < 2:
                        await asyncio.sleep(3.0)
        raise last_exc

    async def _call(self, prompt: str, post_type: str, out: Path) -> Path:
        aspect_hint = ASPECT_HINTS.get(post_type, "portrait 9:16 vertical")
        enhanced = (
            f"{prompt}. "
            f"Composition: {aspect_hint}. "
            "Cinematic dramatic lighting, dark moody atmosphere, "
            "ultra high quality, professional photography, Instagram-ready. "
            "No text or watermarks."
        )
        log.info(f"Calling {IMAGE_MODEL} | post_type={post_type}")

        response = await asyncio.to_thread(
            self.client.models.generate_content,
            model=IMAGE_MODEL,
            contents=enhanced,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )

        parts = []
        try:
            parts = response.candidates[0].content.parts
        except (AttributeError, IndexError):
            pass

        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                out.write_bytes(inline.data)
                log.info(f"Image saved: {out} ({len(inline.data) // 1024} KB)")
                return out

        raise RuntimeError(
            f"No image in response. Parts: {[type(p).__name__ for p in parts]}"
        )

    def _gradient(self, w: int, h: int, out: Path, prompt: str) -> Path:
        color = next(
            (c for kw, c in TOPIC_COLORS.items() if kw in prompt.lower()), "0D1B2A"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi",
             "-i", f"color=c=0x{color}:s={w}x{h}",
             "-frames:v", "1", str(out)],
            capture_output=True,
        )
        log.info(f"Gradient saved: {out}")
        return out