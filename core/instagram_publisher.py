"""
core/instagram_publisher.py
Publishes Reels and Feed videos to Instagram via the Graph API.
Requires: Instagram Business/Creator account linked to a Facebook Page.

Flow for video posts:
  1. Upload video → get upload_id
  2. Create media container (with caption) → get container_id
  3. Wait for container to finish processing (poll status)
  4. Publish container → get ig_post_id
"""

import asyncio
import logging
import mimetypes
import os
import time
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger("oracle.publisher")

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"
POLL_INTERVAL = 5      # seconds between status polls
MAX_POLL_ATTEMPTS = 60  # 5 minutes max wait


class InstagramPublisher:
    def __init__(self):
        self.access_token = os.environ.get("IG_ACCESS_TOKEN")
        self.ig_user_id = os.environ.get("IG_USER_ID")

        if not self.access_token or not self.ig_user_id:
            raise EnvironmentError(
                "IG_ACCESS_TOKEN and IG_USER_ID environment variables must be set."
            )

    async def post(
        self,
        video_path: Path,
        caption: str,
        post_type: str = "reel",
    ) -> dict:
        """
        Full publish flow. Returns dict with ig_post_id on success.
        post_type: 'reel' or 'feed'
        """
        log.info(f"Publishing {post_type} to Instagram...")

        is_reel = post_type == "reel"

        # ── Step 1: Create media container ────────────────────────────────────
        container_id = await self._create_container(
            video_path=video_path,
            caption=caption,
            is_reel=is_reel,
        )
        log.info(f"Container created: {container_id}")

        # ── Step 2: Poll until container is ready ──────────────────────────────
        await self._wait_for_container(container_id)
        log.info(f"Container ready: {container_id}")

        # ── Step 3: Publish ────────────────────────────────────────────────────
        ig_post_id = await self._publish_container(container_id)
        log.info(f"Published! IG Post ID: {ig_post_id}")

        return {"ig_post_id": ig_post_id, "container_id": container_id}

    async def _create_container(
        self,
        video_path: Path,
        caption: str,
        is_reel: bool,
    ) -> str:
        """
        Upload video and create a media container.
        Instagram accepts a publicly accessible video URL OR file upload.
        We use the resumable upload API for local files.
        """
        # Get an upload URL from Facebook
        upload_url = await self._init_resumable_upload(video_path)

        # Upload the file bytes
        await self._upload_file(upload_url, video_path)

        # Create the container
        endpoint = f"{GRAPH_API_BASE}/{self.ig_user_id}/media"
        params = {
            "access_token": self.access_token,
            "caption": caption,
            "upload_id": upload_url.split("upload_id=")[-1].split("&")[0],
        }

        if is_reel:
            params["media_type"] = "REELS"
            params["video_url"] = upload_url   # After upload, IG resolves it
        else:
            params["media_type"] = "VIDEO"

        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(endpoint, params=params)
            data = r.json()
            self._check_error(data, "create_container")
            return data["id"]

    async def _init_resumable_upload(self, video_path: Path) -> str:
        """Initialise a resumable upload session and return the upload URL."""
        file_size = video_path.stat().st_size
        endpoint = f"{GRAPH_API_BASE}/{self.ig_user_id}/media"

        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                endpoint,
                params={
                    "access_token": self.access_token,
                    "upload_phase": "start",
                    "media_type": "VIDEO",
                    "file_size": file_size,
                },
            )
            data = r.json()
            self._check_error(data, "init_upload")
            return data.get("video_upload_urls", [data.get("upload_url", "")])[0]

    async def _upload_file(self, upload_url: str, video_path: Path):
        """Upload video bytes to the resumable upload URL."""
        video_bytes = video_path.read_bytes()
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(
                upload_url,
                headers={
                    "Authorization": f"OAuth {self.access_token}",
                    "Content-Type": "application/octet-stream",
                    "offset": "0",
                    "file_size": str(len(video_bytes)),
                },
                content=video_bytes,
            )
            if r.status_code not in (200, 204):
                raise RuntimeError(f"Upload failed: HTTP {r.status_code} — {r.text[:200]}")

    async def _wait_for_container(self, container_id: str):
        """Poll container status until FINISHED or error."""
        endpoint = f"{GRAPH_API_BASE}/{container_id}"
        params = {
            "fields": "status_code,status",
            "access_token": self.access_token,
        }
        for attempt in range(MAX_POLL_ATTEMPTS):
            await asyncio.sleep(POLL_INTERVAL)
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.get(endpoint, params=params)
                data = r.json()

            status = data.get("status_code", "")
            log.debug(f"Container status [{attempt+1}]: {status}")

            if status == "FINISHED":
                return
            elif status == "ERROR":
                raise RuntimeError(f"Container processing error: {data.get('status')}")
            elif status in ("IN_PROGRESS", "PUBLISHED"):
                continue

        raise TimeoutError(f"Container {container_id} never finished processing.")

    async def _publish_container(self, container_id: str) -> str:
        endpoint = f"{GRAPH_API_BASE}/{self.ig_user_id}/media_publish"
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                endpoint,
                params={
                    "creation_id": container_id,
                    "access_token": self.access_token,
                },
            )
            data = r.json()
            self._check_error(data, "publish")
            return data["id"]

    @staticmethod
    def _check_error(data: dict, stage: str):
        if "error" in data:
            err = data["error"]
            raise RuntimeError(
                f"IG API error at '{stage}': "
                f"[{err.get('code')}] {err.get('message')}"
            )
