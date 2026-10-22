"""FrameDrop: upload, optimise and manage photos on an sk.mimac.slideshow picture frame."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import processor
from .config import settings
from .frame_client import FrameClient, FrameError
from .immich import ImmichClient, ImmichError
from .library import Library
from .sync import SyncEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("framedrop")

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

library = Library(settings.storage_path, settings.thumbs_path, settings.state_file)
frame = FrameClient(settings.frame_url, settings.frame_user, settings.frame_pass)
immich = (
    ImmichClient(settings.immich_url, settings.immich_api_key, settings.immich_album_id)
    if settings.immich_enabled
    else None
)
engine = SyncEngine(settings, library, frame, immich)


async def immich_loop() -> None:
    while True:
        try:
            await engine.immich_sync()
        except Exception:
            log.exception("Scheduled Immich sync failed")
        await asyncio.sleep(settings.sync_interval_minutes * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(immich_loop()) if immich else None
    log.info("Frame: %s (%dx%d), Immich sync: %s", settings.frame_url,
             settings.frame_width, settings.frame_height, "on" if immich else "off")
    yield
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    await frame.close()
    if immich:
        await immich.close()


app = FastAPI(title="FrameDrop", lifespan=lifespan)


class DeleteRequest(BaseModel):
    names: list[str]


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/status")
async def status():
    return {
        "frame_reachable": await frame.is_reachable(),
        "frame_url": settings.frame_url,
        "width": settings.frame_width,
        "height": settings.frame_height,
        "immich_enabled": settings.immich_enabled,
        "photo_count": len(library.names()),
    }


@app.get("/api/photos")
async def list_photos():
    photos = library.list()
    try:
        remote = set(await asyncio.wait_for(frame.list_files(), timeout=5))
    except (FrameError, asyncio.TimeoutError):
        remote = None
    for p in photos:
        p["on_frame"] = None if remote is None else p["name"] in remote
    return {"count": len(photos), "frame_reachable": remote is not None, "photos": photos}


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...), mode: str = Form("blur")):
    if mode not in processor.MODES:
        raise HTTPException(400, f"mode must be one of {sorted(processor.MODES)}")
    results = []
    for f in files:
        data = await f.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            results.append({"original": f.filename, "status": "error", "error": "File too large"})
            continue
        try:
            results.append(await engine.add_photo(f.filename or "photo", data, mode))
        except processor.ProcessingError as exc:
            results.append({"original": f.filename, "status": "error", "error": str(exc)})
    return {"results": results}


@app.post("/api/delete")
async def delete_photos(req: DeleteRequest):
    try:
        return await engine.delete(req.names)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/photos/{name}")
async def delete_photo(name: str):
    return await delete_photos(DeleteRequest(names=[name]))


@app.post("/api/sync")
async def sync_repair():
    try:
        return await engine.repair()
    except FrameError as exc:
        raise HTTPException(502, str(exc))


@app.post("/api/immich/sync")
async def immich_sync():
    if not immich:
        raise HTTPException(404, "Immich sync is not configured")
    try:
        return await engine.immich_sync()
    except (ImmichError, FrameError) as exc:
        raise HTTPException(502, str(exc))


app.mount("/photos", StaticFiles(directory=settings.storage_path), name="photos")
app.mount("/thumbs", StaticFiles(directory=settings.thumbs_path), name="thumbs")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
