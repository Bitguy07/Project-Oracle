"""
core/instagram_publisher.py
Publishes Reels and Feed videos to Instagram via Graph API v19.0.
 
Flow:
  1. Upload video to GitHub Release (public URL, no redirect, always works)
  2. POST /{ig-user-id}/media with video_url → get container_id
  3. Poll container status until FINISHED
  4. POST /{ig-user-id}/media_publish → get ig_post_id
  5. Delete temp GitHub release
 
Video spec requirements (Instagram API):
  - Container: MP4, moov atom at front (faststart)
  - Video codec: H264, progressive, closed GOP, yuv420p
  - Audio codec: AAC, max 48kHz, max 128kbps
  - Aspect ratio: 9:16 for Reels
  - Min duration: 3s, Max: 15min
"""
 
import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Optional
 
import httpx
 
log = logging.getLogger("oracle.publisher")
 
GRAPH_API_BASE = "https://graph.facebook.com/v19.0"
POLL_INTERVAL = 10       # seconds between status polls
MAX_POLL_ATTEMPTS = 30   # 5 minutes max wait
 
 
class InstagramPublisher:
    def __init__(self):
        self.access_token = os.environ.get("IG_ACCESS_TOKEN")
        self.ig_user_id = os.environ.get("IG_USER_ID")
        self.github_token = os.environ.get("GIST_TOKEN") or os.environ.get("GITHUB_TOKEN")
        self.gh_repo = "Bitguy07/Project-Oracle"
 
        if not self.access_token or not self.ig_user_id:
            raise EnvironmentError("IG_ACCESS_TOKEN and IG_USER_ID must be set.")
 
        self._temp_release_id: Optional[int] = None
 
    async def post(
        self,
        video_path: Path,
        caption: str,
        post_type: str = "reel",
    ) -> dict:
        log.info(f"Publishing {post_type} to Instagram...")
 
        try:
            # Step 1: Upload to GitHub releases → get public URL
            video_url = await self._upload_to_github(video_path)
 
            # Step 2: Create Instagram container
            container_id = await self._create_container(
                video_url=video_url,
                caption=caption,
                is_reel=(post_type == "reel"),
            )
            log.info(f"Container created: {container_id}")
 
            # Step 3: Poll until FINISHED
            await self._wait_for_container(container_id)
            log.info(f"Container ready: {container_id}")
 
            # Step 4: Publish
            ig_post_id = await self._publish_container(container_id)
            log.info(f"Published! IG Post ID: {ig_post_id}")
 
            return {"ig_post_id": ig_post_id, "container_id": container_id}
 
        finally:
            # Always clean up temp GitHub release
            await self._delete_temp_release()
 
    async def _upload_to_github(self, video_path: Path) -> str:
        """
        Upload video as GitHub Release asset.
        Returns direct public download URL.
        GIST_TOKEN must have 'repo' scope.
        """
        if not self.github_token:
            raise EnvironmentError("GIST_TOKEN (with repo scope) must be set.")
 
        file_size_mb = video_path.stat().st_size / (1024 * 1024)
        log.info(f"Uploading {file_size_mb:.1f}MB video to GitHub releases...")
 
        headers = {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "ProjectOracle/1.0",
        }
        tag = f"temp-video-{int(time.time())}"
 
        async with httpx.AsyncClient(timeout=120.0) as client:
            # Create release
            r = await client.post(
                f"https://api.github.com/repos/{self.gh_repo}/releases",
                headers=headers,
                json={
                    "tag_name": tag,
                    "name": f"Temp {tag}",
                    "body": "Temporary video for Instagram upload.",
                    "draft": False,
                    "prerelease": True,
                },
            )
            release = r.json()
            if "id" not in release:
                raise RuntimeError(f"Failed to create release: {release}")
 
            self._temp_release_id = release["id"]
            upload_url = release["upload_url"].replace("{?name,label}", "")
            log.info(f"Created temp release: {self._temp_release_id}")
 
            # Upload video bytes
            video_bytes = video_path.read_bytes()
            r = await client.post(
                f"{upload_url}?name={video_path.name}",
                headers={**headers, "Content-Type": "video/mp4"},
                content=video_bytes,
                timeout=180.0,
            )
            asset = r.json()
            if "browser_download_url" not in asset:
                raise RuntimeError(f"Asset upload failed: {asset}")
 
            url = asset["browser_download_url"]
            log.info(f"Video hosted at: {url}")
            return url
 
    async def _create_container(
        self,
        video_url: str,
        caption: str,
        is_reel: bool,
    ) -> str:
        """Create Instagram media container with public video URL."""
        endpoint = f"{GRAPH_API_BASE}/{self.ig_user_id}/media"
 
        params = {
            "access_token": self.access_token,
            "video_url": video_url,
            "caption": caption,
            "media_type": "REELS" if is_reel else "VIDEO",
        }
 
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(endpoint, params=params)
            data = r.json()
 
        log.info(f"Container response: {data}")
        self._check_error(data, "create_container")
        return data["id"]
 
    async def _wait_for_container(self, container_id: str):
        """Poll container status until FINISHED."""
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
 
            status = data.get("status_code", "UNKNOWN")
            log.info(f"Container status [{attempt+1}/{MAX_POLL_ATTEMPTS}]: {status} | raw={data}")
 
            if status == "FINISHED":
                return
            elif status == "ERROR":
                raise RuntimeError(f"Container error: {data}")
            elif status in ("IN_PROGRESS", "PUBLISHED", "UNKNOWN"):
                continue
 
        raise TimeoutError(f"Container {container_id} never finished.")
 
    async def _publish_container(self, container_id: str) -> str:
        """Publish the ready container."""
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
        log.info(f"Publish response: {data}")
        self._check_error(data, "publish")
        return data["id"]
 
    async def _delete_temp_release(self):
        """Delete the temporary GitHub release after Instagram has fetched it."""
        if not self._temp_release_id or not self.github_token:
            return
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                await client.delete(
                    f"https://api.github.com/repos/{self.gh_repo}/releases/{self._temp_release_id}",
                    headers={
                        "Authorization": f"token {self.github_token}",
                        "Accept": "application/vnd.github.v3+json",
                    },
                )
            log.info(f"Deleted temp release {self._temp_release_id}")
        except Exception as e:
            log.warning(f"Failed to delete temp release: {e}")
 
    @staticmethod
    def _check_error(data: dict, stage: str):
        if "error" in data:
            err = data["error"]
            raise RuntimeError(
                f"IG API error at '{stage}': [{err.get('code')}] {err.get('message')}"
            )