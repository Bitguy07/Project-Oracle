"""
Gemini Intelligence Engine.
Generates: quote, image prompt, caption, hashtags, text layers.

Video design philosophy (MUST be followed):
  - ONE quote (hook), centered, properly wrapped, nothing else on video.
  - Body + CTA go in the Instagram CAPTION only — NOT rendered on video.
  - Clean, cinematic, minimal aesthetic.
"""

import asyncio
import json
import logging
import os
import re
import textwrap

from google import genai
from google.genai import types

log = logging.getLogger("oracle.intelligence")

MODEL_NAME = "gemini-2.5-flash"

# CRITICAL FIX: gemini-2.5-flash uses tokens for internal reasoning (thinking)
# BEFORE it writes output. 512 or 1500 gets eaten by reasoning alone — the
# actual JSON response never completes. 8192 is safe and still fast.
MAX_OUTPUT_TOKENS = 8192

CONTENT_PROMPT = """You are a viral Instagram content creator.

Return ONLY a single valid JSON object. No explanation, no markdown, no ``` fences, no text outside the braces.

Task: Generate content for a {post_type} about: "{topic}"

Return exactly this JSON and nothing else:

{{
  "hook": "One punchy sentence. Max 8 words. No colons.",
  "body": "2 sentences max. Max 20 words total.",
  "cta": "One action. Max 6 words. E.g.: Save this. or Tag someone.",
  "caption": "Max 100 chars. Natural combo of hook plus body plus cta.",
  "hashtags": ["#tag1","#tag2","#tag3","#tag4","#tag5","#broad"],
  "image_prompt": "Cinematic scene, max 20 words, dramatic lighting, no text in image.",
  "color_scheme": {{
    "primary": "#FFFFFF",
    "accent": "#FFD700",
    "shadow": "#000000"
  }}
}}"""

_FALLBACK = {
    "hook": "Silence speaks what words cannot.",
    "body": "In stillness, answers emerge.",
    "cta": "Save this.",
    "caption": "Silence speaks what words cannot. In stillness, answers emerge. Save this.",
    "image_prompt": "Dark cinematic landscape, storm clouds, single beam of light.",
    "color_scheme": {"primary": "#FFFFFF", "accent": "#FFD700", "shadow": "#000000"},
    "hashtags": ["#mindset", "#wisdom", "#motivation", "#clarity", "#growth", "#inspiration"],
}


class IntelligenceEngine:

    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError("GEMINI_API_KEY not set.")
        self.client = genai.Client(api_key=api_key)

    async def generate_content(self, topic: str, post_type: str = "reel") -> dict:
        prompt = CONTENT_PROMPT.format(topic=topic, post_type=post_type)
        log.info(f"Generating content for topic='{topic}' ({post_type})")

        data = None
        try:
            raw     = await self._generate_with_retry(prompt)
            cleaned = self._extract_json(raw)
            data    = json.loads(cleaned)
            log.info(f"JSON parsed OK — hook='{data.get('hook', '')[:40]}'")
        except json.JSONDecodeError as e:
            log.error(f"JSON parse failed — using fallback. raw={raw[:300]!r} err={e}")
        except Exception as e:
            log.error(f"Content generation failed — using fallback. err={e}")

        if data is None:
            data = dict(_FALLBACK)

        data = self._normalize_schema(data)

        color_scheme  = data.get("color_scheme", dict(_FALLBACK["color_scheme"]))
        text_layers   = self._build_text_layers(hook=data["hook"], color_scheme=color_scheme)
        hashtags_list = data.get("hashtags", [])
        full_caption  = f"{data['caption'].strip()}\n\n{' '.join(hashtags_list)}".strip()

        return {
            "hook":         data["hook"],
            "body":         data["body"],
            "cta":          data["cta"],
            "caption":      full_caption,
            "hashtags":     hashtags_list,
            "image_prompt": data["image_prompt"],
            "color_scheme": color_scheme,
            "text_layers":  text_layers,
        }

    async def _generate_with_retry(self, prompt: str) -> str:
        last_exc = None
        for attempt in range(3):
            try:
                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=MODEL_NAME,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.75,
                        topP=0.92,
                        maxOutputTokens=MAX_OUTPUT_TOKENS,
                        response_mime_type="application/json",
                    ),
                )
                raw = self._extract_text(response)
                if not raw:
                    raise ValueError("Empty response from Gemini")
                # Strip any accidental markdown fences
                raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
                raw = re.sub(r"\s*```$", "", raw)
                return raw.strip()
            except Exception as e:
                last_exc = e
                log.warning(f"Gemini attempt {attempt + 1}/3 failed: {e}")
                if attempt < 2:
                    await asyncio.sleep(2.0 * (attempt + 1))
        raise last_exc

    def _extract_text(self, response) -> str:
        # Try the SDK shortcut first
        try:
            if response.text:
                return response.text.strip()
        except Exception:
            pass
        # Walk candidates manually
        try:
            for candidate in (response.candidates or []):
                parts = getattr(candidate.content, "parts", []) or []
                text  = "".join(p.text for p in parts if hasattr(p, "text") and p.text).strip()
                if text:
                    return text
        except Exception as e:
            log.warning(f"Part extraction failed: {e}")
        return ""

    def _extract_json(self, text: str) -> str:
        start = text.find("{")
        end   = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"No JSON object in response: {text[:300]!r}")
        return text[start : end + 1]

    def _normalize_schema(self, data: dict) -> dict:
        for key in ("hook", "body", "cta", "caption", "image_prompt"):
            if not data.get(key):
                log.warning(f"Missing '{key}' — substituting fallback.")
                data[key] = _FALLBACK[key]

        raw_tags = data.get("hashtags") or []
        if not isinstance(raw_tags, list) or not raw_tags:
            raw_tags = _FALLBACK["hashtags"]
        data["hashtags"] = [("#" + t.lower().strip().lstrip("#")) for t in raw_tags]

        cs = data.get("color_scheme")
        if not isinstance(cs, dict):
            data["color_scheme"] = dict(_FALLBACK["color_scheme"])
        else:
            for k, v in _FALLBACK["color_scheme"].items():
                cs.setdefault(k, v)

        return data

    def _build_text_layers(self, hook: str, color_scheme: dict) -> list[dict]:
        accent     = color_scheme.get("accent", "#FFD700")
        shadow     = color_scheme.get("shadow", "#000000")
        wrapped    = "\n".join(textwrap.wrap(hook, width=18, break_long_words=False))
        line_count = wrapped.count("\n") + 1
        font_size  = {1: 96, 2: 84, 3: 72}.get(line_count, 64)
        return [{
            "text":          wrapped,
            "y_position":    0.5,
            "font_size":     font_size,
            "color":         accent,
            "shadow_color":  shadow,
            "shadow_offset": (4, 4),
            "shadow_blur":   8,
            "appear_at":     0.6,
            "bold":          True,
            "align":         "center",
        }]