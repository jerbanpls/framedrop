"""Runtime configuration, read once from environment variables (.env)."""

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    frame_url: str
    frame_user: str
    frame_pass: str
    frame_width: int
    frame_height: int
    storage_path: Path
    thumbs_path: Path
    state_file: Path
    immich_url: str
    immich_api_key: str
    immich_album_id: str
    immich_mode: str
    sync_interval_minutes: int

    @property
    def immich_enabled(self) -> bool:
        return bool(self.immich_url and self.immich_api_key and self.immich_album_id)


def load_settings() -> Settings:
    storage = Path(os.getenv("STORAGE_PATH", "/data/photos"))
    data_root = storage.parent
    return Settings(
        frame_url=os.getenv("FRAME_URL", "http://192.168.1.50:8080").rstrip("/"),
        frame_user=os.getenv("FRAME_USER", "admin"),
        frame_pass=os.getenv("FRAME_PASS", ""),
        frame_width=_int("FRAME_WIDTH", 1920),
        frame_height=_int("FRAME_HEIGHT", 1200),
        storage_path=storage,
        thumbs_path=Path(os.getenv("THUMBS_PATH", str(data_root / "thumbs"))),
        state_file=Path(os.getenv("STATE_FILE", str(data_root / "state.json"))),
        immich_url=os.getenv("IMMICH_URL", "").rstrip("/"),
        immich_api_key=os.getenv("IMMICH_API_KEY", ""),
        immich_album_id=os.getenv("IMMICH_ALBUM_ID", ""),
        immich_mode=os.getenv("IMMICH_RESIZE_MODE", "blur"),
        sync_interval_minutes=max(1, _int("SYNC_INTERVAL_MINUTES", 60)),
    )


settings = load_settings()
