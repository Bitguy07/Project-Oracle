"""

Strategy (in order of reliability):
  1. BUNDLED LOCAL MP3s — files committed to repo at assets/bundled_audio/
     Zero failure. No network. Works forever. Add once, forget it.
     Source: https://pixabay.com/music/ — free commercial use, no attribution needed.

  2. PIXABAY CDN — direct CDN MP3 URLs, no API key, no auth.

  3. SILENT FALLBACK — FFmpeg generated silence.

ONE-TIME SETUP (do this once locally):
  mkdir -p assets/bundled_audio
  # Download 2-3 tracks from https://pixabay.com/music/search/ambient/
  # Save them as: assets/bundled_audio/meditation_1.mp3, ambient_1.mp3, etc.
  git add assets/bundled_audio/
  git commit -m "Add bundled background music"
  git push
  # Done — GitHub Actions will always have them available
"""

import hashlib
import logging
import subprocess
from pathlib import Path

import httpx

log = logging.getLogger("oracle.audio")

ASSETS_DIR  = Path("assets/audio")
BUNDLED_DIR = Path("assets/bundled_audio")
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
BUNDLED_DIR.mkdir(parents=True, exist_ok=True)

VIDEO_DURATION = 30

TOPIC_MOOD = {
    "stoicism": "meditation", "philosophy": "ambient",
    "motivation": "uplifting", "mindfulness": "meditation",
    "success": "uplifting", "nature": "ambient",
    "space": "cinematic", "technology": "cinematic",
    "fitness": "energetic", "wisdom": "meditation",
    "life": "ambient", "love": "ambient",
}

# Pixabay CDN direct MP3s — no API key needed, real audio files
PIXABAY_CDN = {
    "meditation": [
        "https://cdn.pixabay.com/audio/2022/10/16/audio_38e54f1849.mp3",
        "https://cdn.pixabay.com/audio/2022/03/15/audio_1e5c87d0fd.mp3",
    ],
    "ambient": [
        "https://cdn.pixabay.com/audio/2022/05/27/audio_1808fbf07a.mp3",
        "https://cdn.pixabay.com/audio/2022/01/18/audio_d0c6ff1bab.mp3",
    ],
    "uplifting": [
        "https://cdn.pixabay.com/audio/2023/01/25/audio_a511c54232.mp3",
        "https://cdn.pixabay.com/audio/2022/08/02/audio_884fe92c21.mp3",
    ],
    "cinematic": [
        "https://cdn.pixabay.com/audio/2022/10/25/audio_946b3fd28a.mp3",
        "https://cdn.pixabay.com/audio/2023/03/09/audio_c8c8a73467.mp3",
    ],
    "energetic": [
        "https://cdn.pixabay.com/audio/2022/09/08/audio_23cd6b8714.mp3",
        "https://cdn.pixabay.com/audio/2022/11/22/audio_ea70ad08ca.mp3",
    ],
}


class AudioFetcher:
    def __init__(self):
        pass

    async def fetch(self, topic: str, post_type: str = "reel") -> Path:
        mood = self._get_mood(topic)

        # 1. Bundled local files (most reliable)
        bundled = self._find_bundled(mood)
        if bundled:
            log.info(f"Using bundled audio: {bundled}")
            return bundled

        # 2. Pixabay CDN direct URLs
        for url in PIXABAY_CDN.get(mood, PIXABAY_CDN["ambient"]):
            try:
                path = await self._download(url)
                if path:
                    return path
            except Exception as e:
                log.warning(f"CDN error: {e}")

        # 3. Silent fallback
        log.info("All audio sources failed — silent fallback.")
        return self._make_silent()

    def _find_bundled(self, mood: str) -> Path | None:
        # Try mood-specific first
        for f in BUNDLED_DIR.glob(f"{mood}*.mp3"):
            if f.stat().st_size > 50_000:
                return f
        # Any valid MP3
        for f in BUNDLED_DIR.glob("*.mp3"):
            if f.stat().st_size > 50_000:
                return f
        return None

    async def _download(self, url: str) -> Path | None:
        cached = ASSETS_DIR / f"{hashlib.md5(url.encode()).hexdigest()[:10]}.mp3"
        if cached.exists() and cached.stat().st_size > 10_000:
            log.info(f"Cache hit: {cached}")
            return cached

        log.info(f"Downloading CDN: {url[-40:]}")
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
            r = await c.get(url)

        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and "audio" in ct and len(r.content) > 10_000:
            cached.write_bytes(r.content)
            log.info(f"Saved: {cached} ({len(r.content)//1024} KB)")
            return cached

        log.warning(f"Failed: HTTP {r.status_code} ct={ct} size={len(r.content)}")
        return None

    def _get_mood(self, topic: str) -> str:
        t = topic.lower()
        for kw, mood in TOPIC_MOOD.items():
            if kw in t:
                return mood
        return "ambient"

    def _make_silent(self) -> Path:
        out = ASSETS_DIR / "silent_30s.mp3"
        if not out.exists():
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-t", str(VIDEO_DURATION),
                "-q:a", "9", "-acodec", "libmp3lame", str(out),
            ], capture_output=True)
        return out