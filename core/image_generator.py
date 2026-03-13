"""
Model: gemini-2.5-flash-image  (stable, FREE tier, works in India, 500/day)
API:   client.models.generate_content() with response_modalities=["IMAGE"]
       DO NOT use generate_images() — that's Imagen 3 (paid only, 404s)

Aspect ratio: 9:16 for Reels, 4:5 for Feed.
Fallback: FFmpeg solid color gradient.
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

DIMENSIONS    = {"reel": (1080, 1920), "feed": (1080, 1350)}
ASPECT_RATIOS = {"reel": "9:16",       "feed": "4:5"}

MODELS = [
    "gemini-2.5-flash-image",          # stable, free tier ✓
    "gemini-3.1-flash-image-preview",  # newest Nano Banana 2 (may need paid)
]

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
        w, h         = DIMENSIONS.get(post_type, (1080, 1920))
        aspect_ratio = ASPECT_RATIOS.get(post_type, "9:16")
        slug         = hashlib.md5(f"{prompt}{post_type}".encode()).hexdigest()[:12]
        out          = ASSETS_DIR / f"{slug}.png"

        if out.exists():
            log.info(f"Image cache hit: {out}")
            return out

        for model in MODELS:
            try:
                return await self._call(prompt, aspect_ratio, out, model)
            except Exception as e:
                log.warning(f"Imagen [{model}] failed: {e}")

        log.warning("All image APIs failed — using gradient fallback.")
        return self._gradient(w, h, out, prompt)

    async def _call(self, prompt: str, aspect_ratio: str, out: Path, model: str) -> Path:
        enhanced = (
            f"{prompt}. "
            "Cinematic dramatic lighting, dark moody atmosphere, "
            "ultra high quality, professional photography, Instagram-ready."
        )
        log.info(f"Calling {model}, ratio={aspect_ratio}")

        # CORRECT API: generate_content with IMAGE modality
        response = await asyncio.to_thread(
            self.client.models.generate_content,
            model=model,
            contents=enhanced,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio=aspect_ratio,
                ),
            ),
        )

        for part in response.parts:
            if part.inline_data is not None:
                out.write_bytes(part.inline_data.data)
                log.info(f"Image saved: {out} ({len(part.inline_data.data)//1024} KB)")
                return out

        raise RuntimeError(f"No image in response from {model}")

    def _gradient(self, w: int, h: int, out: Path, prompt: str) -> Path:
        color = next(
            (c for kw, c in TOPIC_COLORS.items() if kw in prompt.lower()), "0D1B2A"
        )
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c=0x{color}:s={w}x{h}",
            "-frames:v", "1", str(out),
        ], capture_output=True)
        log.info(f"Gradient saved: {out}")
        return out