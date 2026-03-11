"""
core/audio_fetcher.py
Fetches CC0-licensed background music from Pixabay Audio API.
All tracks on Pixabay are royalty-free for commercial use.
Falls back to generating a simple silent audio track via FFmpeg.
"""

import asyncio
import hashlib
import logging
import os
import random
import subprocess
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger("oracle.audio")

ASSETS_DIR = Path("assets/audio")
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

# Pixabay Audio API (free, requires API key from pixabay.com)
PIXABAY_AUDIO_URL = "https://pixabay.com/api/music/"

# Topic → music mood/genre mapping for relevance
TOPIC_MOOD_MAP = {
    "stoicism": ["meditation", "ambient"],
    "philosophy": ["ambient", "cinematic"],
    "motivation": ["uplifting", "inspiring"],
    "mindfulness": ["meditation", "lo-fi"],
    "success": ["uplifting", "corporate"],
    "nature": ["ambient", "nature"],
    "space": ["cinematic", "ambient"],
    "technology": ["electronic", "corporate"],
    "fitness": ["upbeat", "hip-hop"],
    "default": ["lo-fi", "ambient", "meditation"],
}

# Duration for post types (seconds)
AUDIO_DURATION = {"reel": 30, "feed": 15}


class AudioFetcher:
    def __init__(self):
        self.pixabay_key = os.environ.get("PIXABAY_API_KEY")

    async def fetch(self, topic: str, post_type: str = "reel") -> Path:
        """
        Fetch a background audio track relevant to the topic.
        Returns path to downloaded .mp3 file.
        """
        # Determine mood from topic
        mood = self._get_mood(topic)
        cache_key = hashlib.md5(f"{mood}".encode()).hexdigest()[:8]
        cached_files = list(ASSETS_DIR.glob(f"{cache_key}_*.mp3"))

        if cached_files:
            chosen = random.choice(cached_files)
            log.info(f"Audio cache hit: {chosen}")
            return chosen

        # Try Pixabay
        if self.pixabay_key:
            try:
                path = await self._fetch_pixabay(mood, cache_key)
                log.info(f"Pixabay audio saved: {path}")
                return path
            except Exception as e:
                log.warning(f"Pixabay audio fetch failed: {e}")

        # Fallback: generate a silent audio track with FFmpeg
        log.info("Generating silent audio fallback.")
        return self._generate_silent(post_type, cache_key)

    def _get_mood(self, topic: str) -> str:
        topic_lower = topic.lower()
        for keyword, moods in TOPIC_MOOD_MAP.items():
            if keyword in topic_lower:
                return random.choice(moods)
        return random.choice(TOPIC_MOOD_MAP["default"])

    async def _fetch_pixabay(self, mood: str, cache_key: str) -> Path:
        params = {
            "key": self.pixabay_key,
            "q": mood,
            "music_genre": mood,
            "per_page": 10,
            "safesearch": "true",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(PIXABAY_AUDIO_URL, params=params)
            r.raise_for_status()
            data = r.json()

        hits = data.get("hits", [])
        if not hits:
            raise ValueError(f"No Pixabay results for mood='{mood}'")

        # Pick a random track from results
        track = random.choice(hits)
        audio_url = track.get("audio", {}).get("mp3", {}).get("url")
        if not audio_url:
            raise ValueError("No MP3 URL in Pixabay response")

        track_id = track.get("id", "unknown")
        output_path = ASSETS_DIR / f"{cache_key}_{track_id}.mp3"

        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.get(audio_url, follow_redirects=True)
            r.raise_for_status()

        output_path.write_bytes(r.content)
        return output_path

    def _generate_silent(self, post_type: str, cache_key: str) -> Path:
        """Generate a silent MP3 track as absolute last-resort fallback."""
        duration = AUDIO_DURATION.get(post_type, 30)
        output_path = ASSETS_DIR / f"{cache_key}_silent.mp3"

        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100",
            "-t", str(duration),
            "-q:a", "9",
            "-acodec", "libmp3lame",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg silent audio failed: {result.stderr}")
        return output_path
