"""
core/image_generator.py

Generates images locally using HuggingFace diffusers library.
Runs directly on the GitHub Actions runner — no API credits needed, completely free.

Model: stabilityai/stable-diffusion-2-1-base (smaller, faster than SD XL)
Fallback: AI-colored gradient if generation fails.
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
ASPECT_HINTS = {"reel": "portrait 9:16 vertical", "feed": "portrait 4:5 vertical"}

MODEL_ID = "OFA-Sys/small-stable-diffusion-v0"

class ImageGenerator:

    def __init__(self):
        self.hf_token = os.environ.get("HF_TOKEN")

    async def generate(
        self,
        image_prompt: str,
        post_type: str = "reel",
        color_scheme: dict = None,
    ) -> Path:
        w, h = DIMENSIONS.get(post_type, (1080, 1920))
        slug = hashlib.md5(f"{image_prompt}{post_type}".encode()).hexdigest()[:12]
        out  = ASSETS_DIR / f"{slug}.png"

        if out.exists():
            log.info(f"Image cache hit: {out}")
            return out

        try:
            return await asyncio.to_thread(
                self._generate_local, image_prompt, post_type, out, w, h
            )
        except Exception as e:
            log.warning(f"Local generation failed: {e}")

        log.warning("Falling back to gradient.")
        return self._gradient(w, h, out, color_scheme or {})

    def _generate_local(
        self, prompt: str, post_type: str, out: Path, w: int, h: int
    ) -> Path:
        import torch
        from diffusers import StableDiffusionPipeline
        from PIL import Image as PILImage

        aspect   = ASPECT_HINTS.get(post_type, "portrait 9:16 vertical")
        enhanced = (
            f"{prompt}. {aspect}. "
            "Cinematic dramatic lighting, dark moody atmosphere, "
            "ultra high quality, professional photography, Instagram-ready. "
            "No text, no watermarks, no people."
        )
        negative = (
            "text, watermark, logo, blurry, low quality, ugly, "
            "distorted, nsfw, cartoon, anime"
        )

        log.info(f"Loading SD locally | {post_type} | target {w}x{h}")

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype  = torch.float16 if device == "cuda" else torch.float32

        pipe = StableDiffusionPipeline.from_pretrained(
            MODEL_ID,
            torch_dtype=dtype,
            use_auth_token=self.hf_token,
            safety_checker=None,
            requires_safety_checker=False,
        )
        pipe = pipe.to(device)
        pipe.enable_attention_slicing()

        log.info(f"Generating image on {device}...")

        result = pipe(
            prompt=enhanced,
            negative_prompt=negative,
            width=512,
            height=512,
            num_inference_steps=20,
            guidance_scale=7.5,
        )
        image = result.images[0]

        # Upscale to target Instagram dimensions
        image = image.resize((w, h), PILImage.LANCZOS)
        image.save(str(out))

        size_kb = out.stat().st_size // 1024
        log.info(f"Image saved: {out} ({size_kb} KB)")
        return out

    def _gradient(self, w: int, h: int, out: Path, color_scheme: dict) -> Path:
        raw   = color_scheme.get("shadow", "#0D1B2A").lstrip("#")
        color = raw if len(raw) == 6 else "0D1B2A"
        out   = out.with_suffix(".png")
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c=0x{color}:s={w}x{h}",
            "-frames:v", "1", str(out),
        ], capture_output=True)
        log.info(f"Gradient saved: {out} (color=#{color})")
        return out