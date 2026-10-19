"""Upload handling: validation, deduplication and garbage collection."""

import hashlib
import io
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError

from backend.config import (
    ALLOWED_IMAGE_EXTS,
    MAX_IMAGES_PER_MESSAGE,
    MAX_UPLOAD_BYTES,
    UPLOAD_DIR,
    UPLOAD_RETENTION_DAYS,
)


class UploadError(Exception):
    """Raised for user-correctable upload problems (bad format, too big)."""


def _validate_image_bytes(data: bytes) -> Tuple[str, int, int]:
    """Verify the payload really is an image. Returns (format, w, h)."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()  # structural check
        with Image.open(io.BytesIO(data)) as img:
            return (img.format or "UNKNOWN", img.width, img.height)
    except UnidentifiedImageError:
        raise UploadError("That file is not a readable image.")
    except Exception as e:
        raise UploadError(f"Corrupt or unsupported image file: {e}")


async def save_upload(file: UploadFile) -> Dict[str, Any]:
    """Validate, hash and persist a single uploaded image."""
    original_name = file.filename or "image.png"
    ext = Path(original_name).suffix.lower()

    if ext not in ALLOWED_IMAGE_EXTS:
        raise UploadError(
            "Unsupported format. Allowed: "
            + ", ".join(sorted(e.lstrip(".").upper() for e in ALLOWED_IMAGE_EXTS))
        )

    # Stream to disk in chunks so an oversized upload never sits in RAM.
    tmp = UPLOAD_DIR / f".tmp-{uuid.uuid4().hex}"
    total = 0
    digest = hashlib.sha256()

    try:
        with open(tmp, "wb") as out:
            while True:
                chunk = await file.read(1024 * 256)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise UploadError(
                        f"Image is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit."
                    )
                digest.update(chunk)
                out.write(chunk)

        if total == 0:
            raise UploadError("The uploaded file was empty.")

        data = tmp.read_bytes()
        fmt, width, height = _validate_image_bytes(data)
    except UploadError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise UploadError(f"Failed to process upload: {e}")

    # Content-addressed storage: identical images are stored once.
    sha = digest.hexdigest()
    existing = UPLOAD_DIR / f"{sha}{ext}"
    if existing.exists():
        tmp.unlink(missing_ok=True)
        stored_name = existing.name
    else:
        stored_name = existing.name
        tmp.replace(existing)

    return {
        "filename": stored_name,
        "url": f"/uploads/{stored_name}",
        "original_name": original_name,
        "size": total,
        "format": fmt,
        "width": width,
        "height": height,
        "local_path": str(UPLOAD_DIR / stored_name),
    }


async def enforce_image_limit(names: List[str]) -> None:
    if len(names) > MAX_IMAGES_PER_MESSAGE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"A message can carry at most {MAX_IMAGES_PER_MESSAGE} images "
                f"(got {len(names)})."
            ),
        )


async def sweep_orphaned_uploads(referenced: set, retention_days: int = UPLOAD_RETENTION_DAYS) -> int:
    """Delete uploads that no message references and are older than the retention window.

    Content-addressed dedupe means a file may legitimately be referenced under a
    different message, hence the explicit ``referenced`` set.
    """
    cutoff = time.time() - (retention_days * 86400)
    removed = 0

    for path in UPLOAD_DIR.iterdir():
        if not path.is_file():
            continue
        if path.name.startswith(".tmp-"):
            # Abandoned partial upload.
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                pass
            continue
        if path.name in referenced:
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            pass

    return removed


def resolve_upload_path(name: str) -> Optional[Path]:
    """Map a stored filename to a path inside UPLOAD_DIR, or None if it escapes."""
    if not name:
        return None
    candidate = (UPLOAD_DIR / Path(name).name).resolve()
    try:
        candidate.relative_to(UPLOAD_DIR.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None