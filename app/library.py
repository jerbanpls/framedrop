"""Local mirror of the frame: processed photos, thumbnails and a small JSON state file."""

import json
import logging
import os
import re
from pathlib import Path

from . import processor

log = logging.getLogger(__name__)

SAFE_NAME = re.compile(r"^[^/\\\x00]+$")


def validate_name(name: str) -> str:
    if not name or not SAFE_NAME.match(name) or name.startswith(".") or name != Path(name).name:
        raise ValueError(f"Invalid file name: {name!r}")
    return name


def is_image_name(name: str) -> bool:
    return Path(name).suffix.lower() in processor.IMAGE_EXTENSIONS


def thumb_name(name: str) -> str:
    return name if name.lower().endswith((".jpg", ".jpeg")) else f"{name}.jpg"


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class Library:
    def __init__(self, photos: Path, thumbs: Path, state_file: Path):
        self.photos = photos
        self.thumbs = thumbs
        self.state_file = state_file
        for d in (photos, thumbs, state_file.parent):
            d.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()

    # --- photos -----------------------------------------------------------

    def photo_path(self, name: str) -> Path:
        return self.photos / validate_name(name)

    def thumb_path(self, name: str) -> Path:
        return self.thumbs / thumb_name(validate_name(name))

    def exists(self, name: str) -> bool:
        return self.photo_path(name).is_file()

    def names(self) -> set[str]:
        return {p.name for p in self.photos.iterdir() if p.is_file() and is_image_name(p.name) and not p.name.startswith(".")}

    def list(self) -> list[dict]:
        items = []
        for name in self.names():
            st = (self.photos / name).stat()
            items.append({
                "name": name,
                "url": f"/photos/{name}",
                "thumb": f"/thumbs/{thumb_name(name)}",
                "size": st.st_size,
                "mtime": st.st_mtime,
            })
        items.sort(key=lambda i: i["mtime"], reverse=True)
        return items

    def save(self, name: str, data: bytes, thumb: bytes | None) -> None:
        _atomic_write(self.photo_path(name), data)
        if thumb is not None:
            _atomic_write(self.thumb_path(name), thumb)

    def read(self, name: str) -> bytes:
        return self.photo_path(name).read_bytes()

    def remove(self, name: str) -> None:
        self.photo_path(name).unlink(missing_ok=True)
        self.thumb_path(name).unlink(missing_ok=True)

    def rebuild_missing_thumbs(self) -> int:
        built = 0
        for name in self.names():
            if self.thumb_path(name).exists():
                continue
            try:
                thumb = processor.thumbnail_from_bytes(self.read(name))
                _atomic_write(self.thumb_path(name), thumb)
                built += 1
            except processor.ProcessingError as exc:
                log.warning("Could not thumbnail %s: %s", name, exc)
        return built

    # --- state ------------------------------------------------------------
    # tombstones: deleted locally but not yet confirmed deleted on the frame
    # immich: Immich asset id -> local file name (None = already present, not owned by sync)

    def _load_state(self) -> dict:
        try:
            state = json.loads(self.state_file.read_text())
        except FileNotFoundError:
            state = {}
        except (OSError, json.JSONDecodeError):
            log.exception("Corrupt state file %s, starting fresh", self.state_file)
            state = {}
        state.setdefault("tombstones", [])
        state.setdefault("immich", {})
        return state

    def _save_state(self) -> None:
        _atomic_write(self.state_file, json.dumps(self._state, indent=2).encode())

    @property
    def tombstones(self) -> set[str]:
        return set(self._state["tombstones"])

    def add_tombstone(self, name: str) -> None:
        if name not in self._state["tombstones"]:
            self._state["tombstones"].append(name)
            self._save_state()

    def clear_tombstone(self, name: str) -> None:
        if name in self._state["tombstones"]:
            self._state["tombstones"].remove(name)
            self._save_state()

    @property
    def immich_assets(self) -> dict[str, str | None]:
        return dict(self._state["immich"])

    def set_immich_asset(self, asset_id: str, name: str | None) -> None:
        self._state["immich"][asset_id] = name
        self._save_state()

    def drop_immich_asset(self, asset_id: str) -> None:
        if self._state["immich"].pop(asset_id, ...) is not ...:
            self._save_state()
