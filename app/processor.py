"""Image pipeline: decode (incl. HEIC), auto-rotate, fit to the frame, encode JPEG + thumbnail."""

import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path

import pillow_heif
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

pillow_heif.register_heif_opener()

# Modern phone sensors (50–200 MP) trip Pillow's default decompression-bomb guard.
Image.MAX_IMAGE_PIXELS = 250_000_000

ALLOWED_FORMATS = {"JPEG", "MPO", "PNG", "WEBP", "HEIF"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
MODES = {"blur", "crop"}

JPEG_QUALITY = 88
THUMB_SIZE = 300
THUMB_QUALITY = 80
BLUR_RADIUS = 40
BACKGROUND_BRIGHTNESS = 0.7
ASPECT_TOLERANCE = 0.01


class ProcessingError(Exception):
    pass


@dataclass
class ProcessedImage:
    jpeg: bytes
    thumb: bytes
    width: int
    height: int


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode == "RGB":
        return img
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (0, 0, 0))
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    return img.convert("RGB")


def load_image(data: bytes, size: tuple[int, int]) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
    except Exception as exc:
        raise ProcessingError("Not a recognised image file") from exc
    if img.format not in ALLOWED_FORMATS:
        raise ProcessingError(f"Unsupported format: {img.format}")

    # Let the JPEG decoder downscale by a power of two while staying at least frame-sized
    # on both axes (a square request so a later 90° rotation still has enough pixels).
    if img.format in ("JPEG", "MPO"):
        edge = max(size)
        img.draft("RGB", (edge, edge))

    try:
        img = ImageOps.exif_transpose(img)
        return _to_rgb(img)
    except Exception as exc:
        raise ProcessingError("Could not decode image") from exc


def center_crop(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(img, size, Image.Resampling.LANCZOS)


def blur_fill(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    frame_ratio = size[0] / size[1]
    img_ratio = img.width / img.height
    if abs(img_ratio - frame_ratio) / frame_ratio < ASPECT_TOLERANCE:
        return center_crop(img, size)

    background = ImageOps.fit(img, size, Image.Resampling.BILINEAR)
    background = background.filter(ImageFilter.GaussianBlur(radius=BLUR_RADIUS))
    background = ImageEnhance.Brightness(background).enhance(BACKGROUND_BRIGHTNESS)

    foreground = ImageOps.contain(img, size, Image.Resampling.LANCZOS)
    offset = ((size[0] - foreground.width) // 2, (size[1] - foreground.height) // 2)
    background.paste(foreground, offset)
    return background


def _encode_jpeg(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def make_thumbnail(img: Image.Image) -> bytes:
    thumb = img.copy()
    thumb.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)
    return _encode_jpeg(thumb, THUMB_QUALITY)


def thumbnail_from_bytes(data: bytes) -> bytes:
    """Thumbnail for an arbitrary image (e.g. one found on the frame but not created here)."""
    img = load_image(data, (THUMB_SIZE * 2, THUMB_SIZE * 2))
    return make_thumbnail(img)


def process_image(data: bytes, width: int, height: int, mode: str = "blur") -> ProcessedImage:
    if mode not in MODES:
        raise ProcessingError(f"Unknown resize mode: {mode}")
    size = (width, height)
    img = load_image(data, size)
    out = blur_fill(img, size) if mode == "blur" else center_crop(img, size)
    return ProcessedImage(
        jpeg=_encode_jpeg(out, JPEG_QUALITY),
        thumb=make_thumbnail(out),
        width=out.width,
        height=out.height,
    )


def output_name(original_filename: str, data: bytes, mode: str) -> str:
    """Deterministic, filesystem/URL-safe name: same photo + same mode => same file."""
    stem = Path(original_filename or "photo").stem
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-_")[:40] or "photo"
    digest = hashlib.sha1(data + mode.encode()).hexdigest()[:10]
    return f"{slug}_{digest}.jpg"
