"""
Project Oracle — Main Orchestrator
Headless, zero-cost Instagram Reel/Feed Factory.
Entry point for both GitHub Actions CRON runs and Telegram webhook triggers.
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

from core.state_manager import StateManager
from core.intelligence import IntelligenceEngine
from core.image_generator import ImageGenerator
from core.audio_fetcher import AudioFetcher
from core.video_renderer import VideoRenderer
from core.instagram_publisher import InstagramPublisher
from core.telegram_bot import TelegramBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("oracle.main")


async def run_pipeline(topic: str, post_type: str = "reel") -> dict:
    """
    Full end-to-end pipeline for a single post.
    Returns a result dict with status and metadata.
    """
    state = StateManager()
    intel = IntelligenceEngine()
    img_gen = ImageGenerator()
    audio = AudioFetcher()
    renderer = VideoRenderer()
    publisher = InstagramPublisher()

    # ── 1. Quota Guard ────────────────────────────────────────────────────────
    if not state.has_quota():
        log.warning("Daily quota exhausted. Exiting gracefully.")
        return {"status": "quota_exceeded", "topic": topic}

    # ── 2. Duplicate Guard ────────────────────────────────────────────────────
    if state.was_recently_posted(topic):
        log.info(f"Topic '{topic}' was recently used. Skipping.")
        return {"status": "duplicate_skipped", "topic": topic}

    log.info(f"Starting pipeline for topic='{topic}' type='{post_type}'")

    # ── 3. Generate Content via Gemini ────────────────────────────────────────
    content = await intel.generate_content(topic, post_type)
    log.info(f"Generated content: hook='{content['hook'][:50]}...'")

    # ── 4. Generate Image via Pollinations.ai ─────────────────────────────────
    image_path = await img_gen.generate(content["image_prompt"], post_type)
    log.info(f"Image saved to {image_path}")

    # ── 5. Fetch Background Audio ─────────────────────────────────────────────
    audio_path = await audio.fetch(topic)
    log.info(f"Audio saved to {audio_path}")

    # ── 6. Render Video with FFmpeg ───────────────────────────────────────────
    video_path = renderer.render(
        image_path=image_path,
        audio_path=audio_path,
        text_layers=content["text_layers"],
        post_type=post_type,
    )
    log.info(f"Video rendered to {video_path}")

    # ── 7. Publish to Instagram ───────────────────────────────────────────────
    result = await publisher.post(
        video_path=video_path,
        caption=content["caption"],
        post_type=post_type,
    )

    # ── 8. Update State ───────────────────────────────────────────────────────
    state.record_post(topic, content["caption"], result.get("ig_post_id"))
    state.decrement_quota()

    log.info(f"Post published! IG ID: {result.get('ig_post_id')}")
    return {"status": "success", "ig_post_id": result.get("ig_post_id"), "topic": topic}


async def continuous_run():
    """
    Drain the topic queue, posting until quota is exhausted.
    Called by GitHub Actions CRON.
    """
    state = StateManager()
    queue = state.get_topic_queue()

    if not queue:
        log.info("Topic queue is empty. Nothing to do.")
        return

    log.info(f"Queue has {len(queue)} topic(s). Starting continuous run...")
    results = []

    for item in queue:
        if not state.has_quota():
            log.warning("Quota hit. Stopping batch run.")
            break

        result = await run_pipeline(topic=item["topic"], post_type=item.get("type", "reel"))
        results.append(result)
        state.remove_from_queue(item["id"])

        if result["status"] == "success":
            # Respectful delay between posts
            await asyncio.sleep(30)

    log.info(f"Batch complete. Results: {json.dumps(results, indent=2)}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "cron"

    if mode == "cron":
        asyncio.run(continuous_run())
    elif mode == "webhook":
        # Start Telegram webhook listener (used in local dev / long-running container)
        bot = TelegramBot()
        asyncio.run(bot.start_webhook())
    elif mode == "single" and len(sys.argv) >= 3:
        topic = sys.argv[2]
        post_type = sys.argv[3] if len(sys.argv) > 3 else "reel"
        asyncio.run(run_pipeline(topic, post_type))
    else:
        print("Usage: python main.py [cron|webhook|single <topic> [reel|feed]]")
