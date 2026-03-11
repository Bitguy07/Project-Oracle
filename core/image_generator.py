"""
core/image_generator.py
Image generation using Gemini's native image generation API.
Model: gemini-3.1-flash-image-preview (Nano Banana 2)
Free tier: 5,000 prompts/month via Google AI Studio
Falls back to gradient background if API fails.
"""

import asyncio
import base64
import hashlib
import logging
import os
import random
import subprocess
from pathlib import Path

from google import genai
from google.genai import types

log = logging.getLogger("oracle.image")

ASSETS_DIR = Path("assets/images")
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

# Gemini image models to try in order
GEMINI_IMAGE_MODELS = [
    "gemini-3.1-flash-image-preview",   # Nano Banana 2 — free tier, best quality
    "gemini-2.5-flash-image",           # Nano Banana — fallback
]

# Dimensions per post type
DIMENSIONS = {
    "reel": (1080, 1920),
    "feed": (1080, 1350),
}


class ImageGenerator:
    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError("GEMINI_API_KEY not set.")
        self.client = genai.Client(api_key=api_key)

    async def generate(self, prompt: str, post_type: str = "reel") -> Path:
        """
        Generate image using Gemini's native image generation.
        Falls back to local gradient if all APIs fail.
        """
        width, height = DIMENSIONS.get(post_type, (1080, 1920))
        slug = hashlib.md5(f"{prompt}{post_type}".encode()).hexdigest()[:12]
        output_path = ASSETS_DIR / f"{slug}.png"

        if output_path.exists():
            log.info(f"Image cache hit: {output_path}")
            return output_path

        # ── Try Gemini image generation ────────────────────────────────────
        for model in GEMINI_IMAGE_MODELS:
            try:
                path = await self._gemini_image(prompt, output_path, model)
                log.info(f"Gemini image ({model}) succeeded: {path}")
                return path
            except Exception as e:
                log.warning(f"Gemini image {model} failed: {e}")
                await asyncio.sleep(2)

        # ── Fallback: local gradient background ────────────────────────────
        log.warning("All image APIs failed. Generating local gradient background.")
        return self._generate_gradient(width, height, output_path, prompt)

    async def _gemini_image(
        self, prompt: str, output_path: Path, model: str
    ) -> Path:
        """Generate image using Gemini native image generation."""
        
        # Enhance prompt for Instagram-quality visuals
        enhanced_prompt = (
            f"{prompt}. "
            f"Ultra HD, cinematic lighting, dramatic composition, "
            f"professional photography style, Instagram-ready, "
            f"9:16 portrait aspect ratio, dark moody aesthetic."
        )

        log.info(f"Calling Gemini image model: {model}")

        response = await asyncio.to_thread(
            self.client.models.generate_content,
            model=model,
            contents=enhanced_prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )

        # Extract image from response
        for part in response.candidates[0].content.parts:
            if part.inline_data is not None:
                image_data = part.inline_data.data
                # Handle both bytes and base64 string
                if isinstance(image_data, str):
                    image_data = base64.b64decode(image_data)
                output_path.write_bytes(image_data)
                log.info(f"Image saved: {output_path} ({len(image_data)/1024:.1f} KB)")
                return output_path

        raise RuntimeError("No image in Gemini response")

    def _generate_gradient(
        self,
        width: int,
        height: int,
        output_path: Path,
        prompt: str,
    ) -> Path:
        """Generate a gradient background using FFmpeg. Never fails."""
        color1, color2 = self._pick_colors(prompt)

        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c=0x{color1}:s={width}x{height}",
            "-frames:v", "1",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            # Ultra fallback
            cmd2 = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", f"color=c=black:s={width}x{height}",
                "-frames:v", "1",
                str(output_path),
            ]
            subprocess.run(cmd2, capture_output=True)

        return output_path

    @staticmethod
    def _pick_colors(prompt: str) -> tuple:
        prompt_lower = prompt.lower()
        color_map = {
            "stoic": ("0D1B2A", "1B4332"),
            "philosoph": ("1A1A2E", "16213E"),
            "motivat": ("FF6B35", "F7C59F"),
            "mindful": ("2D6A4F", "52B788"),
            "success": ("1D3557", "457B9D"),
            "nature": ("1B4332", "40916C"),
            "space": ("03045E", "0077B6"),
            "fitness": ("D62828", "F77F00"),
        }
        for keyword, colors in color_map.items():
            if keyword in prompt_lower:
                return colors
        return ("0D1B2A", "341F97")