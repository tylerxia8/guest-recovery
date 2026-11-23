"""Local byte, image, and video checks.

The class name and scanner label are a development fixture. They are not a
production malware product, and production settings cannot use them to mark
an upload ready.
"""

import hashlib
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

register_heif_opener(thumbnails=False)

SCANNER_NAME = "fixture-byte-scanner"
IMAGE_LIMIT = 20 * 1024 * 1024
VIDEO_LIMIT = 200 * 1024 * 1024
MAX_VIDEO_SECONDS = 120
MAX_IMAGE_PIXELS = 40_000_000
HEIC_BRANDS = (b"heic", b"heix", b"mif1", b"msf1", b"hevc")
PHOTO_FORMATS = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/heic": "HEIF",
    "image/heif": "HEIF",
    "image/webp": "WEBP",
    "image/gif": "GIF",
    "image/bmp": "BMP",
    "image/tiff": "TIFF",
    "image/avif": "AVIF",
}
MEDIA_TYPES = PHOTO_FORMATS.keys() | {"video/mp4", "video/quicktime"}


@dataclass(frozen=True)
class ScanResult:
    ready: bool
    code: str
    detected_type: str | None
    reason: str | None
    scanner_name: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_type(header: bytes) -> str | None:
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"BM"):
        return "image/bmp"
    if header.startswith((b"II\x2a\x00", b"MM\x00\x2a", b"II\x2b\x00", b"MM\x00\x2b")):
        return "image/tiff"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    if header.startswith(b"MZ") or header.startswith(b"\x7fELF"):
        return "application/x-executable"
    if header.startswith(b"PK\x03\x04"):
        return "application/zip"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        brand = header[8:12]
        brands = [brand, *(header[offset:offset + 4] for offset in range(16, min(len(header), int.from_bytes(header[:4], "big")), 4))]
        if b"avif" in brands or b"avis" in brands:
            return "image/avif"
        if brand in HEIC_BRANDS or brand.startswith(b"hei"):
            return "image/heic"
        if brand in {b"isom", b"mp42", b"mp41", b"avc1", b"iso2"} or brand.startswith(b"mp4"):
            return "video/mp4"
        if brand in {b"qt  ", b"M4V "}:
            return "video/quicktime"
        return "video/mp4"
    return None


def scan_stored_file(
    *,
    path: Path,
    declared_content_type: str,
    expected_sha256: str,
    fixture_allowed: bool,
) -> ScanResult:
    if not fixture_allowed:
        return ScanResult(False, "SCANNER_NOT_CONFIGURED", None, "Production fixture scanning is disabled.", "not-configured")
    with path.open("rb") as handle:
        detected = detect_type(handle.read(32))
    actual_hash = sha256_file(path)
    if actual_hash != expected_sha256:
        return ScanResult(False, "CHECKSUM_MISMATCH", detected, "The stored bytes do not match the declared checksum.", SCANNER_NAME)
    if detected in {"application/x-executable", "application/zip"} or detected is None:
        return ScanResult(False, "UNSUPPORTED_OR_DANGEROUS", detected, "The file type is not an allowed photo or video.", SCANNER_NAME)
    if detected != declared_content_type and not _compatible(declared_content_type, detected):
        return ScanResult(False, "MIME_MISMATCH", detected, "The declared type does not match the file contents.", SCANNER_NAME)
    limit = VIDEO_LIMIT if detected.startswith("video/") else IMAGE_LIMIT
    if path.stat().st_size > limit:
        return ScanResult(False, "TOO_LARGE", detected, "The file exceeds the configured size limit.", SCANNER_NAME)
    if detected.startswith("image/"):
        try:
            _write_image_derivatives(path, PHOTO_FORMATS[detected])
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            return ScanResult(False, "IMAGE_DECODE_FAILED", detected, "The image could not be decoded.", SCANNER_NAME)
        return ScanResult(True, "FIXTURE_CHECKED", detected, None, SCANNER_NAME)
    try:
        duration = _video_duration(path)
    except (subprocess.TimeoutExpired, OSError):
        duration = None
    if duration is None or not math.isfinite(duration) or duration <= 0 or duration > MAX_VIDEO_SECONDS:
        return ScanResult(False, "VIDEO_DURATION_UNVERIFIED", detected, "Video duration could not be accepted.", SCANNER_NAME)
    try:
        transcoded = _transcode_video(path)
    except (subprocess.TimeoutExpired, OSError):
        transcoded = False
    if not transcoded:
        return ScanResult(False, "VIDEO_TRANSCODE_FAILED", detected, "A metadata-stripped video derivative was not produced.", SCANNER_NAME)
    return ScanResult(True, "FIXTURE_CHECKED", detected, None, SCANNER_NAME)


def _compatible(declared: str, detected: str) -> bool:
    if declared == detected:
        return True
    return (declared, detected) in {
        ("image/heif", "image/heic"),
        ("video/quicktime", "video/mp4"),
    }


def _write_image_derivatives(path: Path, image_format: str) -> None:
    with Image.open(path, formats=[image_format]) as image:
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise ValueError("Image pixel limit exceeded")
        image.load()
        clean = ImageOps.exif_transpose(image).convert("RGB")
        clean.info.clear()
    derivative = path.parent / "derivative.jpg"
    clean.save(derivative, format="JPEG", quality=85)
    thumb = clean.copy()
    thumb.thumbnail((128, 128))
    thumb.save(path.parent / "thumbnail.jpg", format="JPEG", quality=80)


def _video_duration(path: Path) -> float | None:
    if shutil.which("ffprobe") is None:
        return None
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        return None
    try:
        return float(completed.stdout.strip())
    except ValueError:
        return None


def _transcode_video(path: Path) -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    derivative = path.parent / "derivative.mp4"
    completed = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-map_metadata",
            "-1",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(derivative),
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    return completed.returncode == 0 and derivative.exists()
