"""
core/audio_fetcher.py
Fetches royalty-free background music using Mixkit direct CDN URLs.
 
Mixkit License: Completely free, no attribution required, commercial use OK.
No API key, no signup, no rate limits.
Direct CDN pattern: https://assets.mixkit.co/music/download/mixkit-{name}-{id}.mp3
 
Falls back to silent audio if all downloads fail.
"""
 
import hashlib
import logging
import random
import subprocess
from pathlib import Path
 
import httpx
 
log = logging.getLogger("oracle.audio")
 
ASSETS_DIR = Path("assets/audio")
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
 
AUDIO_DURATION = {"reel": 30, "feed": 15}
 
BASE = "https://assets.mixkit.co/music/download"
 
# Verified Mixkit direct CDN URLs — royalty free, no attribution needed
# Format: https://assets.mixkit.co/music/download/mixkit-{slug}-{id}.mp3
CC0_TRACKS = {
    "meditation": [
        f"{BASE}/mixkit-serene-view-443.mp3",
        f"{BASE}/mixkit-silence-between-the-notes-562.mp3",
        f"{BASE}/mixkit-a-very-happy-christmas-897.mp3",
        f"{BASE}/mixkit-dreaming-big-31.mp3",
    ],
    "ambient": [
        f"{BASE}/mixkit-deep-urban-623.mp3",
        f"{BASE}/mixkit-valley-sunset-127.mp3",
        f"{BASE}/mixkit-an-eternity-142.mp3",
        f"{BASE}/mixkit-serene-view-443.mp3",
    ],
    "uplifting": [
        f"{BASE}/mixkit-positive-vibrations-693.mp3",
        f"{BASE}/mixkit-uplift-me-532.mp3",
        f"{BASE}/mixkit-raising-me-higher-34.mp3",
        f"{BASE}/mixkit-spirit-in-the-woods-138.mp3",
    ],
    "cinematic": [
        f"{BASE}/mixkit-deep-urban-623.mp3",
        f"{BASE}/mixkit-an-eternity-142.mp3",
        f"{BASE}/mixkit-valley-sunset-127.mp3",
        f"{BASE}/mixkit-dreaming-big-31.mp3",
    ],
    "energetic": [
        f"{BASE}/mixkit-positive-vibrations-693.mp3",
        f"{BASE}/mixkit-raising-me-higher-34.mp3",
        f"{BASE}/mixkit-uplift-me-532.mp3",
        f"{BASE}/mixkit-hip-hop-02-738.mp3",
    ],
}
 
TOPIC_MOOD_MAP = {
    "stoicism":    "meditation",
    "philosophy":  "ambient",
    "motivation":  "uplifting",
    "mindfulness": "meditation",
    "success":     "uplifting",
    "nature":      "ambient",
    "space":       "cinematic",
    "technology":  "cinematic",
    "fitness":     "energetic",
    "wisdom":      "meditation",
    "life":        "ambient",
}
 
 
class AudioFetcher:
    def __init__(self):
        pass  # No API key needed
 
    async def fetch(self, topic: str, post_type: str = "reel") -> Path:
        mood = self._get_mood(topic)
        tracks = CC0_TRACKS.get(mood, CC0_TRACKS["ambient"])
        shuffled = random.sample(tracks, len(tracks))
 
        for url in shuffled:
            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            cached = ASSETS_DIR / f"{url_hash}.mp3"
 
            if cached.exists():
                log.info(f"Audio cache hit: {cached}")
                return cached
 
            try:
                log.info(f"Downloading [{mood}]: {url.split('/')[-1]}")
                async with httpx.AsyncClient(
                    timeout=60.0, follow_redirects=True
                ) as client:
                    r = await client.get(url)
                    if r.status_code == 200 and len(r.content) > 10_000:
                        cached.write_bytes(r.content)
                        log.info(
                            f"Audio saved: {cached} ({len(r.content)//1024}KB)"
                        )
                        return cached
                    log.warning(
                        f"Bad response: HTTP {r.status_code} "
                        f"size={len(r.content)}"
                    )
            except Exception as e:
                log.warning(f"Download failed: {e}")
 
        log.info("All audio downloads failed. Generating silent fallback.")
        return self._generate_silent(post_type)
 
    def _get_mood(self, topic: str) -> str:
        topic_lower = topic.lower()
        for keyword, mood in TOPIC_MOOD_MAP.items():
            if keyword in topic_lower:
                return mood
        return "ambient"
 
    def _generate_silent(self, post_type: str) -> Path:
        duration = AUDIO_DURATION.get(post_type, 30)
        output_path = ASSETS_DIR / f"silent_{duration}s.mp3"
        if output_path.exists():
            return output_path
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-t", str(duration),
            "-q:a", "9",
            "-acodec", "libmp3lame",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed: {result.stderr}")
        return output_path