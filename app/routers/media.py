"""
Handles file uploads for post media and custom thumbnails.

Video uploads are stored in the local /app/media volume for development.
Thumbnail uploads are also stored there and are restricted to JPG/PNG
images within the size limit supported by the YouTube thumbnail API.

The returned paths are treated as opaque media keys by the rest of the
application.

Authentication:
    Uploads require both the application API key and an authenticated
    DB-backed user session. Uploading is available to both administrators
    and content managers; post creation later determines which social
    accounts the content may target.
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.dependencies import get_current_user, verify_api_key

router = APIRouter(
    prefix="/media",
    tags=["media"],
    dependencies=[
        Depends(verify_api_key),
        Depends(get_current_user),
    ],
)

MEDIA_DIR = Path("/app/media")

# Video uploads
MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB
ALLOWED_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
}

# Custom YouTube thumbnails
THUMBNAIL_MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MB
ALLOWED_THUMBNAIL_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
}
ALLOWED_THUMBNAIL_MIME_TYPES = {
    "image/jpeg",
    "image/png",
}


def _ensure_media_dir() -> None:
    """Make sure the local media directory exists before writing files."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)


def _safe_filename(original_name: str) -> str:
    """
    Never trust a client-supplied filename.

    Only the extension is retained; the actual stored filename is a
    cryptographically random UUID-derived value.
    """
    ext = Path(original_name).suffix.lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext or '(none)'}",
        )

    return f"{uuid.uuid4().hex}{ext}"


def _safe_thumbnail_filename(
    original_name: str,
    content_type: str | None,
) -> str:
    """
    Validate a custom thumbnail and generate a safe stored filename.
    """
    ext = Path(original_name).suffix.lower()

    if ext not in ALLOWED_THUMBNAIL_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported thumbnail type. Use JPG or PNG.",
        )

    if content_type and content_type not in ALLOWED_THUMBNAIL_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Unsupported thumbnail MIME type. Use image/jpeg or image/png.",
        )

    # Normalize .jpeg to .jpg so stored thumbnail extensions stay consistent.
    safe_ext = ".jpg" if ext == ".jpeg" else ext

    return f"{uuid.uuid4().hex}{safe_ext}"


async def _write_upload_with_limit(
    file: UploadFile,
    dest_path: Path,
    max_bytes: int,
    too_large_message: str,
) -> int:
    """
    Stream an UploadFile to disk while enforcing a hard byte limit.

    The file is deleted when the limit is exceeded.
    """
    size = 0

    try:
        with dest_path.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)

                if size > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=too_large_message,
                    )

                output.write(chunk)

    except HTTPException:
        dest_path.unlink(missing_ok=True)
        raise
    except Exception:
        dest_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    return size


@router.post("/upload")
async def upload_media(file: UploadFile = File(...)):
    """
    Upload a video file for a post.

    Returns the generated local media path that can be stored as
    media_s3_key.
    """
    _ensure_media_dir()

    safe_name = _safe_filename(file.filename or "")
    dest_path = MEDIA_DIR / safe_name

    size = await _write_upload_with_limit(
        file=file,
        dest_path=dest_path,
        max_bytes=MAX_UPLOAD_BYTES,
        too_large_message="File too large (max 500MB)",
    )

    return {
        "media_s3_key": str(dest_path),
        "original_filename": file.filename,
        "size_bytes": size,
    }


@router.post("/upload-thumbnail")
async def upload_thumbnail(file: UploadFile = File(...)):
    """
    Upload a custom YouTube thumbnail.

    Only JPG/PNG files are accepted and the upload is limited to 2 MB.
    """
    _ensure_media_dir()

    safe_name = _safe_thumbnail_filename(
        file.filename or "",
        file.content_type,
    )
    dest_path = MEDIA_DIR / safe_name

    size = await _write_upload_with_limit(
        file=file,
        dest_path=dest_path,
        max_bytes=THUMBNAIL_MAX_UPLOAD_BYTES,
        too_large_message="Thumbnail too large (max 2MB)",
    )

    return {
        "media_s3_key": str(dest_path),
        "original_filename": file.filename,
        "size_bytes": size,
    }
