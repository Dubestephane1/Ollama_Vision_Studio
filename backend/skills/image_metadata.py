import os
from pathlib import Path
from typing import Any, Dict, Optional

from PIL import Image, ExifTags

from backend.config import UPLOAD_DIR
from backend.skills.base import BaseSkill, SkillContext


class ImageMetadataSkill(BaseSkill):
    name: str = "image_metadata"
    display_name: str = "Image Analyzer & Metadata"
    enabled_by_default: bool = False  # Opt-in: switch it on in the Tools modal.
    description: str = (
        "Extract dimensions, format, color mode, file size, and EXIF tags from an image. "
        "Call this when the user asks about an image's resolution, format, quality, "
        "dimensions, or camera settings. Works on the image the user just attached "
        "when no path is given."
    )
    parameters: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "image_path": {
                "type": "string",
                "description": "Path of an uploaded image. OMIT this entirely to analyse the image attached to the current message - do not guess a filename.",
            }
        },
    }

    def _resolve_uploaded(self, path: str) -> Optional[str]:
        """Return the path only if it points inside the uploads directory."""
        if not path:
            return None
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = UPLOAD_DIR / candidate.name
        try:
            resolved = candidate.resolve()
            resolved.relative_to(UPLOAD_DIR.resolve())
        except (ValueError, OSError):
            return None
        return str(resolved) if resolved.is_file() else None

    async def execute(
        self,
        ctx: Optional[SkillContext] = None,
        image_path: str = "",
        **kwargs,
    ) -> Dict[str, Any]:
        # Only files the user actually uploaded are readable. Small models
        # often invent a path instead of omitting the argument, so an unknown
        # path falls back to the image attached to this turn.
        target = self._resolve_uploaded(image_path)

        if target is None and ctx is not None and ctx.image_paths:
            target = self._resolve_uploaded(ctx.image_paths[0])

        if target is None:
            if image_path:
                return {
                    "error": f"'{image_path}' is not an uploaded image in this chat.",
                    "hint": "Omit image_path to analyse the image attached to the current message.",
                }
            return {"error": "No image specified and none attached to this message."}

        image_path = str(target)

        try:
            p = Path(image_path)
            file_size_kb = round(p.stat().st_size / 1024, 2)

            with Image.open(p) as img:
                width, height = img.size
                img_format = img.format or p.suffix.upper().replace(".", "")
                mode = img.mode
                aspect_ratio = round(width / height, 2) if height else None

                exif_data: Dict[str, Any] = {}
                gps: Dict[str, Any] = {}
                try:
                    raw_exif = img.getexif()
                    if raw_exif:
                        for tag_id, value in raw_exif.items():
                            tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                            if tag_name == "GPSInfo":
                                try:
                                    gps = {
                                        ExifTags.GPSTAGS.get(k, str(k)): str(v)
                                        for k, v in (value or {}).items()
                                    }
                                except Exception:
                                    pass
                                continue
                            if isinstance(value, (bytes, bytearray)):
                                value = value.decode(errors="ignore")
                            elif not isinstance(value, (int, float, str, bool, list, dict)):
                                value = str(value)
                            exif_data[tag_name] = value
                except Exception as exif_err:
                    exif_data = {"note": f"EXIF extraction error: {exif_err}"}

                result = {
                    "filename": p.name,
                    "format": img_format,
                    "dimensions": f"{width}x{height}",
                    "width": width,
                    "height": height,
                    "megapixels": round(width * height / 1_000_000, 2),
                    "aspect_ratio": aspect_ratio,
                    "color_mode": mode,
                    "file_size_kb": file_size_kb,
                    "exif_summary": dict(list(exif_data.items())[:10]) if exif_data else {},
                }
                if gps:
                    result["gps_present"] = True
                    result["gps"] = gps
                return result
        except Exception as e:
            return {"error": f"Failed analyzing image: {e}"}