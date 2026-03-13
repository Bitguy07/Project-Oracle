"""

Uses Google Imagen 3 via the Gemini API (generate_images, NOT generate_content).
Model:  imagen-3.0-generate-001        (stable, high quality, free tier India ✓)
        imagen-3.0-fast-generate-001   (faster fallback)

Free tier: ~50 images/day via Google AI Studio key. No credit card needed.
Aspect ratio: 9:16 for Reels, 4:5 for Feed.
Falls back to FFmpeg gradient if quota exceeded or API unavailable.
"""

import asyncio
import hashlib
import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger("oracle.image")

ASSETS_DIR = Path("assets/images")
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

DIMENSIONS   = {"reel": (1080, 1920), "feed": (1080, 1350)}
ASPECT_RATIOS = {"reel": "9:16",      "feed": "4:5"}

IMAGEN_MODELS = [
    "imagen-3.0-generate-001",
    "imagen-3.0-fast-generate-001",
]

TOPIC_COLORS = {
    "stoic": "0D1B2A", "philosoph": "1A1A2E", "motivat": "1A0A2E",
    "mindful": "0D2818", "success": "0A1628", "nature": "0D2010",
    "space": "030418",  "tech": "0D1B2A",    "fitness": "1A0808",
    "wisdom": "1A1408", "life": "0D1020",     "love": "1A0818",
}


class ImageGenerator:
    def __init__(self):
        self._api_key = os.environ.get("GEMINI_API_KEY")
        if not self._api_key:
            raise EnvironmentError("GEMINI_API_KEY not set.")
        self._client = None

    def _client_obj(self):
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    async def generate(self, prompt: str, post_type: str = "reel") -> Path:
        w, h         = DIMENSIONS.get(post_type, (1080, 1920))
        aspect_ratio = ASPECT_RATIOS.get(post_type, "9:16")
        slug         = hashlib.md5(f"{prompt}{post_type}".encode()).hexdigest()[:12]
        out          = ASSETS_DIR / f"{slug}.png"

        if out.exists():
            log.info(f"Image cache hit: {out}")
            return out

        for model in IMAGEN_MODELS:
            try:
                return await self._imagen(prompt, aspect_ratio, out, model)
            except Exception as e:
                log.warning(f"Imagen [{model}] failed: {e}")

        log.warning("All image APIs failed — using gradient fallback.")
        return self._gradient(w, h, out, prompt)

    async def _imagen(self, prompt: str, aspect_ratio: str, out: Path, model: str) -> Path:
        from google.genai import types
        enhanced = (
            f"{prompt}. Cinematic dramatic lighting, dark moody atmosphere, "
            "ultra high quality, professional photography, Instagram-ready."
        )
        log.info(f"Calling {model}, ratio={aspect_ratio}")
        client = self._client_obj()
        resp = await asyncio.to_thread(
            client.models.generate_images,
            model=model,
            prompt=enhanced,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio=aspect_ratio,
                safety_filter_level="block_only_high",
                person_generation="dont_allow",
            ),
        )
        if not resp.generated_images:
            raise RuntimeError("No images returned")
        img_bytes = resp.generated_images[0].image.image_bytes
        out.write_bytes(img_bytes)
        log.info(f"Image saved: {out} ({len(img_bytes)//1024} KB)")
        return out

    def _gradient(self, w: int, h: int, out: Path, prompt: str) -> Path:
        color = next((c for kw, c in TOPIC_COLORS.items() if kw in prompt.lower()), "0D1B2A")
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c=0x{color}:s={w}x{h}",
            "-frames:v", "1", str(out),
        ], capture_output=True)
        log.info(f"Gradient saved: {out}")
        return out