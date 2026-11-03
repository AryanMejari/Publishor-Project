import os
from io import BytesIO
from typing import Optional

import cloudinary
import cloudinary.uploader
import cloudinary.utils
import requests
from urllib.parse import urlparse


def _configure_cloudinary():
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
        secure=True,
    )


def upload_bytes(data: bytes, path: str, content_type: str) -> str:
    _configure_cloudinary()
    result = cloudinary.uploader.upload(
        BytesIO(data),
        public_id=path,
        resource_type="raw",
        overwrite=True,
        use_filename=False,
        unique_filename=False,
    )
    return result["secure_url"]


def download_bytes(path: str) -> Optional[bytes]:
    if not path:
        return None
    if not path.startswith("https://res.cloudinary.com/"):
        return None
    response = requests.get(path, timeout=30)
    if response.status_code in (401, 403):
        signed_path = _signed_delivery_url(path)
        if signed_path:
            response = requests.get(signed_path, timeout=30)
        if response.status_code in (401, 403):
            private_path = _private_download_url(path)
            if private_path:
                response = requests.get(private_path, timeout=30)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content


def _signed_delivery_url(path: str) -> Optional[str]:
    """Create a signed URL for an existing Cloudinary raw asset."""
    parsed = urlparse(path)
    parts = parsed.path.strip("/").split("/")
    try:
        raw_index = parts.index("raw")
        upload_index = parts.index("upload", raw_index)
    except ValueError:
        return None

    public_id_parts = parts[upload_index + 1:]
    if public_id_parts and public_id_parts[0].startswith("v"):
        public_id_parts = public_id_parts[1:]
    if not public_id_parts:
        return None

    public_id = "/".join(public_id_parts)
    _configure_cloudinary()
    signed_url, _ = cloudinary.utils.cloudinary_url(
        public_id,
        resource_type="raw",
        type="upload",
        secure=True,
        sign_url=True,
    )
    return signed_url


def _private_download_url(path: str) -> Optional[str]:
    parsed = urlparse(path)
    parts = parsed.path.strip("/").split("/")
    try:
        raw_index = parts.index("raw")
        upload_index = parts.index("upload", raw_index)
    except ValueError:
        return None

    public_id_parts = parts[upload_index + 1:]
    if public_id_parts and public_id_parts[0].startswith("v"):
        public_id_parts = public_id_parts[1:]
    if not public_id_parts:
        return None

    public_id_with_format = "/".join(public_id_parts)
    public_id, separator, file_format = public_id_with_format.rpartition(".")
    if not separator:
        public_id = public_id_with_format
        file_format = ""

    _configure_cloudinary()
    try:
        return cloudinary.utils.private_download_url(
            public_id,
            file_format,
            resource_type="raw",
            type="upload",
            attachment=True,
        )
    except ValueError:
        return None
