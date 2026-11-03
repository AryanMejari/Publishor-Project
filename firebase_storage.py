import io
import os
from typing import Optional

from firebase_admin import storage

from firebase_store import get_firestore_client


def _bucket():
    bucket_name = os.getenv("FIREBASE_STORAGE_BUCKET")
    if bucket_name:
        return storage.bucket(bucket_name)
    return storage.bucket()


def upload_bytes(data: bytes, path: str, content_type: str) -> str:
    blob = _bucket().blob(path)
    blob.upload_from_string(data, content_type=content_type)
    return path


def download_bytes(path: str) -> Optional[bytes]:
    if not path:
        return None
    blob = _bucket().blob(path)
    if not blob.exists():
        return None
    return blob.download_as_bytes()

