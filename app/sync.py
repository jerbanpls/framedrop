"""Orchestrates the local mirror, the frame and (optionally) Immich."""

import asyncio
import logging

from starlette.concurrency import run_in_threadpool

from . import processor
from .config import Settings
from .frame_client import FrameClient, FrameError
from .immich import ImmichClient
from .library import Library, is_image_name, validate_name

log = logging.getLogger(__name__)

CONTENT_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".heic": "image/heic", ".heif": "image/heif",
}


def _content_type(name: str) -> str:
    return CONTENT_TYPES.get(name[name.rfind("."):].lower(), "application/octet-stream")


class SyncEngine:
    def __init__(self, settings: Settings, library: Library, frame: FrameClient, immich: ImmichClient | None):
        self.settings = settings
        self.library = library
        self.frame = frame
        self.immich = immich
        # Serialises operations that compare local vs remote state (delete, repair, Immich).
        self.lock = asyncio.Lock()

    async def add_photo(self, filename: str, data: bytes, mode: str) -> dict:
        name = processor.output_name(filename, data, mode)
        if self.library.exists(name):
            return {"name": name, "original": filename, "status": "duplicate"}

        processed = await run_in_threadpool(
            processor.process_image, data, self.settings.frame_width, self.settings.frame_height, mode
        )
        await run_in_threadpool(self.library.save, name, processed.jpeg, processed.thumb)
        self.library.clear_tombstone(name)

        try:
            await self.frame.upload(name, processed.jpeg)
        except FrameError as exc:
            log.warning("Saved %s locally; frame upload failed: %s", name, exc)
            return {"name": name, "original": filename, "status": "saved", "error": str(exc)}
        return {"name": name, "original": filename, "status": "uploaded"}

    async def _delete_one(self, name: str) -> bool:
        """Delete locally and on the frame. Returns True if the frame confirmed the delete."""
        self.library.remove(name)
        self.library.add_tombstone(name)
        try:
            await self.frame.delete(name)
        except FrameError as exc:
            log.warning("Deleted %s locally; frame delete pending: %s", name, exc)
            return False
        self.library.clear_tombstone(name)
        return True

    async def delete(self, names: list[str]) -> dict:
        report = {"deleted": [], "pending_on_frame": []}
        async with self.lock:
            for name in names:
                validate_name(name)
                confirmed = await self._delete_one(name)
                report["deleted"].append(name)
                if not confirmed:
                    report["pending_on_frame"].append(name)
        return report

    async def repair(self) -> dict:
        """Reconcile the local mirror with the frame's WebDAV folder.

        - finishes deletes that couldn't reach the frame earlier
        - uploads local photos missing on the frame
        - imports images found only on the frame into the local mirror
        - rebuilds missing thumbnails
        """
        report = {"deleted": [], "uploaded": [], "imported": [], "thumbs_rebuilt": 0, "errors": []}
        async with self.lock:
            remote = set(await self.frame.list_files())
            local = self.library.names()

            for name in self.library.tombstones:
                if name in local:  # re-added since it was deleted
                    self.library.clear_tombstone(name)
                    continue
                try:
                    if name in remote:
                        await self.frame.delete(name)
                        remote.discard(name)
                        report["deleted"].append(name)
                    self.library.clear_tombstone(name)
                except FrameError as exc:
                    report["errors"].append(str(exc))

            for name in sorted(local - remote):
                try:
                    data = await run_in_threadpool(self.library.read, name)
                    await self.frame.upload(name, data, _content_type(name))
                    report["uploaded"].append(name)
                except FrameError as exc:
                    report["errors"].append(str(exc))

            for name in sorted(remote - local):
                try:
                    validate_name(name)
                except ValueError:
                    continue
                if not is_image_name(name):
                    continue
                try:
                    data = await self.frame.download(name)
                    await run_in_threadpool(self.library.save, name, data, None)
                    report["imported"].append(name)
                except FrameError as exc:
                    report["errors"].append(str(exc))

            report["thumbs_rebuilt"] = await run_in_threadpool(self.library.rebuild_missing_thumbs)
        return report

    async def immich_sync(self) -> dict:
        """Mirror the configured Immich album onto the frame (adds new, removes dropped)."""
        if not self.immich:
            raise RuntimeError("Immich sync is not configured")
        report = {"added": [], "removed": [], "skipped": 0, "errors": []}
        async with self.lock:
            images = await self.immich.album_images()
            known = self.library.immich_assets
            current_ids = {a["id"] for a in images}

            for asset in images:
                if asset["id"] in known:
                    continue
                try:
                    data = await self.immich.download_original(asset["id"])
                    result = await self.add_photo(asset["originalFileName"], data, self.settings.immich_mode)
                except Exception as exc:  # one bad asset must not stop the whole sync
                    log.warning("Immich asset %s failed: %s", asset["id"], exc)
                    report["errors"].append(f"{asset['originalFileName']}: {exc}")
                    continue
                if result["status"] == "duplicate":
                    # Already on the frame from a manual upload; don't take ownership of it.
                    self.library.set_immich_asset(asset["id"], None)
                    report["skipped"] += 1
                else:
                    self.library.set_immich_asset(asset["id"], result["name"])
                    report["added"].append(result["name"])

            for asset_id, name in known.items():
                if asset_id in current_ids:
                    continue
                if name:
                    await self._delete_one(name)
                    report["removed"].append(name)
                self.library.drop_immich_asset(asset_id)
        log.info("Immich sync: %s", {k: (len(v) if isinstance(v, list) else v) for k, v in report.items()})
        return report
