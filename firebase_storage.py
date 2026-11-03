import os
from io import BytesIO
from typing import Optional

import cloudinary
import cloudinary.uploader
import requests


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
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content
