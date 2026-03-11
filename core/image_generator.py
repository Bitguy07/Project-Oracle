"""
core/image_generator.py
Zero-cost image generation with a professional fallback chain.

Chain (in order):
  1. Pollinations.ai — turbo model, simplified prompt, random seed
  2. Pollinations.ai — retry with different model (flux-realism)
  3. HuggingFace — FLUX.1-schnell (newest active model)
  4. HuggingFace — SDXL base (fallback)
  5. Local gradient background (absolute last resort — never fails)
"""

import asyncio
import hashlib
import logging
import os
import random
import subprocess
import urllib.parse
from pathlib import Path

import httpx

log = logging.getLogger("oracle.image")

ASSETS_DIR = Path("assets/images")
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

# Dimensions per post type
DIMENSIONS = {
    "reel": (1080, 1920),
    "feed": (1080, 1350),
}

# HuggingFace models to try in order (most stable first as of 2026)
HF_MODELS = [
    "black-forest-labs/FLUX.1-schnell",
    "stabilityai/stable-diffusion-xl-base-1.0",
    "runwayml/stable-diffusion-v1-5",
]

# Pollinations models to try in order
POLLINATIONS_MODELS = ["turbo", "flux", "flux-realism"]


class ImageGenerator:
    def __init__(self):
        self.hf_token = os.environ.get("HF_TOKEN")

    async def generate(self, prompt: str, post_type: str = "reel") -> Path:
        """
        Generate image with full fallback chain.
        Never raises — worst case returns a local gradient background.
        """
        width, height = DIMENSIONS.get(post_type, (1080, 1920))
        slug = hashlib.md5(f"{prompt}{post_type}".encode()).hexdigest()[:12]
        output_path = ASSETS_DIR / f"{slug}.png"

        if output_path.exists():
            log.info(f"Image cache hit: {output_path}")
            return output_path

        # Simplify prompt to avoid URL length issues with Pollinations
        simple_prompt = self._simplify_prompt(prompt)

        # ── Chain 1: Pollinations with multiple models ─────────────────────
        for model in POLLINATIONS_MODELS:
            try:
                path = await self._pollinations(
                    simple_prompt, width, height, output_path, model
                )
                log.info(f"Pollinations ({model}) succeeded: {path}")
                return path
            except Exception as e:
                log.warning(f"Pollinations {model} failed: {e}")
                await asyncio.sleep(2)

        # ── Chain 2: HuggingFace with multiple models ──────────────────────
        if self.hf_token:
            for model in HF_MODELS:
                try:
                    path = await self._huggingface(
                        simple_prompt, width, height, output_path, model
                    )
                    log.info(f"HuggingFace ({model}) succeeded: {path}")
                    return path
                except Exception as e:
                    log.warning(f"HuggingFace {model} failed: {e}")
                    await asyncio.sleep(3)
        else:
            log.warning("HF_TOKEN not set, skipping HuggingFace.")

        # ── Chain 3: Local gradient background (never fails) ───────────────
        log.warning("All image APIs failed. Generating local gradient background.")
        return self._generate_gradient(width, height, output_path, prompt)

    def _simplify_prompt(self, prompt: str) -> str:
        """
        Shorten and clean prompt for URL safety.
        Pollinations fails on very long prompts due to URL length limits.
        """
        # Take first 150 chars, cut at last complete word
        if len(prompt) > 150:
            prompt = prompt[:150].rsplit(" ", 1)[0]
        return prompt.strip()

    async def _pollinations(
        self,
        prompt: str,
        width: int,
        height: int,
        output_path: Path,
        model: str = "turbo",
    ) -> Path:
        seed = random.randint(1, 999999)
        encoded = urllib.parse.quote(prompt)
        url = (
            f"https://image.pollinations.ai/prompt/{encoded}"
            f"?width={width}&height={height}"
            f"&model={model}&seed={seed}&nologo=true"
        )
        log.info(f"Trying Pollinations model={model} seed={seed}")

        async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            if len(r.content) < 5000:
                raise RuntimeError("Response too small — likely error page")

        output_path.write_bytes(r.content)
        return output_path

    async def _huggingface(
        self,
        prompt: str,
        width: int,
        height: int,
        output_path: Path,
        model: str,
    ) -> Path:
        if not self.hf_token:
            raise RuntimeError("No HF token")

        headers = {"Authorization": f"Bearer {self.hf_token}"}
        payload = {
            "inputs": prompt,
            "parameters": {
                "width": min(width, 1024),
                "height": min(height, 1024),
                "num_inference_steps": 4,
            },
        }

        api_url = f"https://api-inference.huggingface.co/models/{model}"
        log.info(f"Trying HuggingFace model={model}")

        async with httpx.AsyncClient(timeout=120.0) as client:
            for attempt in range(3):
                r = await client.post(api_url, headers=headers, json=payload)
                if r.status_code == 503:
                    log.info(f"HF model loading (attempt {attempt+1}/3)...")
                    await asyncio.sleep(20)
                    continue
                if r.status_code in (404, 410):
                    raise RuntimeError(f"Model gone/not found: HTTP {r.status_code}")
                r.raise_for_status()
                break
            else:
                raise RuntimeError("HF model never warmed up")

        if len(r.content) < 5000:
            raise RuntimeError("HF response too small")

        output_path.write_bytes(r.content)
        return output_path

    def _generate_gradient(
        self,
        width: int,
        height: int,
        output_path: Path,
        prompt: str,
    ) -> Path:
        """
        Generate a beautiful gradient background using FFmpeg.
        Uses topic keywords to pick colors.
        Never fails — pure local generation.
        """
        # Pick gradient colors based on prompt keywords
        color1, color2 = self._pick_colors(prompt)

        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", (
                f"gradients=s={width}x{height}:"
                f"c0={color1}:c1={color2}:"
                f"x0=0:y0=0:x1={width}:y1={height}:"
                f"nb_colors=2"
            ),
            "-frames:v", "1",
            "-f", "image2",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            # Ultra fallback: solid color image
            log.warning("Gradient failed, using solid color.")
            cmd2 = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", f"color=c={color1}:s={width}x{height}",
                "-frames:v", "1",
                str(output_path),
            ]
            subprocess.run(cmd2, capture_output=True)

        return output_path

    @staticmethod
    def _pick_colors(prompt: str) -> tuple[str, str]:
        """Pick gradient colors based on topic mood."""
        prompt_lower = prompt.lower()
        color_map = {
            "stoic": ("0D1B2A", "1B4332"),
            "philosoph": ("1A1A2E", "16213E"),
            "motivat": ("FF6B35", "F7C59F"),
            "mindful": ("2D6A4F", "52B788"),
            "success": ("1D3557", "457B9D"),
            "nature": ("1B4332", "40916C"),
            "space": ("03045E", "0077B6"),
            "tech": ("0D1B2A", "415A77"),
            "fitness": ("D62828", "F77F00"),
            "love": ("6D023A", "C77DFF"),
        }
        for keyword, colors in color_map.items():
            if keyword in prompt_lower:
                return colors
        # Default: deep navy to purple
        return ("0D1B2A", "341F97")
