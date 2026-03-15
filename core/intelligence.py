"""
core/intelligence.py

Two modes, one pipeline:
  AUTONOMOUS  — AI generates content based on time of day (IST UTC+5:30).
                Morning: struggle/strategy, Afternoon: sarcasm/humor,
                Evening: spiritual/family/love, Night: heartbreak/longing.
  MANUAL      — User provides free-form topic/music hints in any language/style.

Both modes return identical structured output so downstream code is unchanged.
"""

import asyncio
import json
import logging
import os
import re
import textwrap
from datetime import datetime, timezone, timedelta

from google import genai
from google.genai import types

log = logging.getLogger("oracle.intelligence")

MODEL      = "gemini-2.5-flash"
MAX_TOKENS = 8192

IST = timezone(timedelta(hours=5, minutes=30))


def _get_time_directive() -> str:
    hour = datetime.now(IST).hour

    if 6 <= hour < 12:
        return """TIME SLOT: Morning (people are starting their day, ambitious, motivated)
CONTENT DIRECTION: Raw struggle and strategy. Stories of people who fought hard for success
against all odds. Practical wisdom on protecting yourself from people who want to pull you down.
Real talk about hustle, sacrifice, and winning in silence. Think street-smart advice,
lessons learned the hard way, how winners think differently. Tone: fierce, direct, empowering.
Music should be: epic, motivational, building intensity, powerful drums."""

    elif 12 <= hour < 18:
        return """TIME SLOT: Afternoon (people are taking breaks, scrolling casually, need entertainment)
CONTENT DIRECTION: Sharp sarcasm and dark humor about everyday life situations everyone relates to.
Funny observations about modern life, relationships, work, social media, society double standards.
The kind of content that makes someone laugh and immediately share with a friend.
Tone: witty, cutting, unexpectedly deep. Commentary that stings because it is true.
Music should be: upbeat, quirky, playful with attitude, light percussion."""

    elif 18 <= hour < 22:
        return """TIME SLOT: Evening (people are winding down, feeling reflective and emotional)
CONTENT DIRECTION: Warmth, connection, and depth. Stories about family bonds, the kind of love
that stays quiet but never leaves, spiritual moments of gratitude and peace, the beauty of
simple human connections. Makes someone feel less alone and more grateful.
Tone: tender, meaningful, grounding. Things people wish they had said to their parents.
Music should be: soft, warm, gentle piano or strings, peaceful, soothing."""

    else:
        return """TIME SLOT: Night (people are alone with their thoughts, emotionally open)
CONTENT DIRECTION: Heartbreak, longing, and the kind of love that never fully healed.
Stories of people who still remember small details about someone who left. The 3am thoughts
nobody talks about. Love that was real but was not enough. Missing someone who has moved on.
Unrequited love. The version of someone you still carry inside you years later.
Tone: raw, aching, beautifully melancholic. Diary entries, things said too late.
Music should be: slow, melancholic, haunting, minimal piano or ambient, deeply emotional."""


AUTONOMOUS_PROMPT = """You are a master Instagram content creator who deeply understands human psychology.
Your content stops people mid-scroll because it touches something real inside them.

{time_directive}

HISTORY (topics already used — do NOT repeat these themes):
{history}

Create completely original content for a {post_type} that fits the time slot direction above.
Do NOT mention the time slot explicitly. Just create content that naturally fits that emotional space.

HOOK RULES — the hook is the text shown on the video:
- Must be 12-20 words — long enough to carry full meaning and emotional weight
- Should feel like a complete thought, a realization, a confession, or a story opener
- Vary the style — sometimes a statement, sometimes an observation, sometimes a revelation
- Never just a question alone — that is lazy writing
- No colons. Write like a human being, not a headline writer.
- Good examples:
  "The day I stopped explaining myself to everyone, everything in my life became clearer."
  "Some people leave your life but never leave your mind, no matter how many years pass."
  "Nobody talks about how lonely success feels when you have nobody to call at night."
  "Your parents worked themselves to exhaustion so you could have options they never had."
  "The funniest thing about life is how seriously we take people who do not even think about us."

Return ONLY a single valid JSON object, nothing else:
{{
  "topic": "2-4 word topic label for history tracking",
  "hook": "Video text. 12-20 words. Complete thought. No colons. Emotionally resonant.",
  "body": "2 sentences expanding the idea for caption. Max 25 words.",
  "cta": "One natural action. Max 6 words.",
  "caption": "Max 150 chars. Natural hook + body + cta.",
  "hashtags": ["#tag1","#tag2","#tag3","#tag4","#tag5","#broad"],
  "image_prompt": "Cinematic scene matching the emotional tone. Max 20 words. No text in image.",
  "music_prompt": "Specific MusicGen prompt. Name instruments, tempo, emotion. No vocals.",
  "video_style": "One of: slow_zoom, static, pulse, fade_drift",
  "color_scheme": {{
    "primary": "#FFFFFF",
    "accent": "#FFD700",
    "shadow": "#000000"
  }}
}}"""

MANUAL_PROMPT = """You are a master Instagram content creator who deeply understands human psychology.

The user gave this instruction (may be Hinglish, broken English, or emotional):
TOPIC: "{topic_raw}"
MUSIC HINT: "{music_raw}"

Interpret their intent deeply. Create content for a {post_type} that captures exactly
what they are feeling or trying to express.

HOOK RULES:
- The hook is the text shown on the video itself
- 12-20 words — long enough to carry full meaning and emotional weight
- Complete thought, realization, confession, or story opener
- No colons. Write like a human.

Return ONLY a single valid JSON object, nothing else:
{{
  "topic": "2-4 word topic label",
  "hook": "Video text. 12-20 words. Complete thought. Emotionally resonant. No colons.",
  "body": "2 sentences. Max 25 words. Caption only.",
  "cta": "One natural action. Max 6 words.",
  "caption": "Max 150 chars. Natural hook + body + cta.",
  "hashtags": ["#tag1","#tag2","#tag3","#tag4","#tag5","#broad"],
  "image_prompt": "Cinematic scene matching topic emotion. Max 20 words. No text in image.",
  "music_prompt": "Specific MusicGen prompt. Instruments, tempo, emotion. No vocals.",
  "video_style": "One of: slow_zoom, static, pulse, fade_drift",
  "color_scheme": {{
    "primary": "#FFFFFF",
    "accent": "#FFD700",
    "shadow": "#000000"
  }}
}}"""

_FALLBACK = {
    "topic":        "late night thoughts",
    "hook":         "Some people leave your life but never really leave your mind no matter how hard you try.",
    "body":         "The ones who mattered most stay with you in ways you cannot explain.",
    "cta":          "Save this if you felt it.",
    "caption":      "Some people leave your life but never really leave your mind. Save this if you felt it.",
    "image_prompt": "Dark cinematic landscape, single light in distance, solitary figure, emotional atmosphere.",
    "music_prompt": "slow melancholic piano, minimal, haunting, no vocals, late night emotional mood",
    "video_style":  "slow_zoom",
    "color_scheme": {"primary": "#FFFFFF", "accent": "#FFD700", "shadow": "#000000"},
    "hashtags":     ["#feelings", "#latenightthoughts", "#heartfelt", "#realness", "#emotions", "#relatable"],
}


class IntelligenceEngine:

    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError("GEMINI_API_KEY not set.")
        self.client = genai.Client(api_key=api_key)

    async def generate_autonomous(self, post_type: str, history: list[str]) -> dict:
        """AI invents everything based on current IST time of day."""
        history_str    = "\n".join(f"- {t}" for t in history[-10:]) if history else "None yet."
        time_directive = _get_time_directive()
        ist_time       = datetime.now(IST).strftime("%H:%M IST")
        log.info(f"Time-based content | {ist_time} | directive applied")
        prompt = AUTONOMOUS_PROMPT.format(
            time_directive=time_directive,
            history=history_str,
            post_type=post_type,
        )
        return await self._run(prompt, post_type)

    async def generate_manual(self, topic_raw: str, music_raw: str, post_type: str) -> dict:
        """Interprets user free-form input in any language/style."""
        prompt = MANUAL_PROMPT.format(
            topic_raw=topic_raw,
            music_raw=music_raw or "",
            post_type=post_type,
        )
        return await self._run(prompt, post_type)

    async def _run(self, prompt: str, post_type: str) -> dict:
        data = None
        for attempt in range(2):
            try:
                raw  = await self._call_gemini(prompt)
                data = json.loads(self._extract_json(raw))
                log.info(f"Content OK — hook='{data.get('hook','')[:60]}'")
                break
            except Exception as e:
                log.warning(f"Attempt {attempt+1}/2 failed: {e}")
                if attempt == 0:
                    await asyncio.sleep(2)

        if data is None:
            log.error("Both attempts failed — using fallback.")
            data = dict(_FALLBACK)

        data         = self._normalize(data)
        hashtags     = data.get("hashtags", [])
        full_caption = f"{data['caption'].strip()}\n\n{' '.join(hashtags)}".strip()

        return {
            "topic":        data["topic"],
            "hook":         data["hook"],
            "body":         data["body"],
            "cta":          data["cta"],
            "caption":      full_caption,
            "hashtags":     hashtags,
            "image_prompt": data["image_prompt"],
            "music_prompt": data["music_prompt"],
            "video_style":  data.get("video_style", "slow_zoom"),
            "color_scheme": data["color_scheme"],
            "text_layers":  self._build_text_layers(data["hook"], data["color_scheme"]),
        }

    async def _call_gemini(self, prompt: str) -> str:
        response = await asyncio.to_thread(
            self.client.models.generate_content,
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.9,
                topP=0.95,
                maxOutputTokens=MAX_TOKENS,
                response_mime_type="application/json",
            ),
        )
        raw = ""
        try:
            raw = response.text or ""
        except Exception:
            pass
        if not raw:
            for c in (getattr(response, "candidates", None) or []):
                parts = getattr(getattr(c, "content", None), "parts", []) or []
                raw   = "".join(p.text for p in parts if hasattr(p, "text") and p.text)
                if raw:
                    break
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
        if not raw:
            raise ValueError("Empty Gemini response")
        return raw.strip()

    def _extract_json(self, text: str) -> str:
        s, e = text.find("{"), text.rfind("}")
        if s == -1 or e <= s:
            raise ValueError(f"No JSON: {text[:200]!r}")
        return text[s:e+1]

    def _normalize(self, data: dict) -> dict:
        for k in ("topic", "hook", "body", "cta", "caption", "image_prompt", "music_prompt"):
            if not data.get(k):
                data[k] = _FALLBACK[k]
        tags = data.get("hashtags") or []
        if not isinstance(tags, list) or not tags:
            tags = list(_FALLBACK["hashtags"])
        data["hashtags"] = [("#" + t.lower().strip().lstrip("#")) for t in tags]
        cs = data.get("color_scheme")
        if not isinstance(cs, dict):
            data["color_scheme"] = dict(_FALLBACK["color_scheme"])
        else:
            for k, v in _FALLBACK["color_scheme"].items():
                cs.setdefault(k, v)
        if data.get("video_style") not in ("slow_zoom", "static", "pulse", "fade_drift"):
            data["video_style"] = "slow_zoom"
        return data

    def _build_text_layers(self, hook: str, color_scheme: dict) -> list[dict]:
        accent     = color_scheme.get("accent", "#FFD700")
        shadow     = color_scheme.get("shadow", "#000000")
        wrapped    = "\n".join(textwrap.wrap(hook, width=10, break_long_words=False))
        line_count = wrapped.count("\n") + 1
        font_size  = {1: 64, 2: 54, 3: 46, 4: 40}.get(line_count, 36)
        return [{
            "text":         wrapped,
            "y_position":   0.30,
            "font_size":    font_size,
            "color":        accent,
            "shadow_color": shadow,
            "appear_at":    0.6,
            "bold":         True,
        }]