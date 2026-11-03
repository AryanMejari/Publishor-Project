import json
import os
from pathlib import Path
from typing import Dict, List, Optional

import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

COLLECTION_NAME = "isirpalldata"
USERS_COLLECTION = "community_users"
CHALLENGES_COLLECTION = "paper_challenges"
REPUTATION_COLLECTION = "reputation_events"
_FIRESTORE_CLIENT = None


def _build_firebase_options() -> Dict[str, str]:
    options: Dict[str, str] = {}
    project_id = os.getenv("FIREBASE_PROJECT_ID")
    database_url = os.getenv("FIREBASE_DATABASE_URL")

    if project_id:
        options["projectId"] = project_id
    if database_url:
        options["databaseURL"] = database_url

    return options


def initialize_firebase() -> None:
    """Initialize Firebase Admin SDK exactly once."""
    global _FIRESTORE_CLIENT

    if _FIRESTORE_CLIENT is not None:
        return

    options = _build_firebase_options()
    service_account_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")

    if not firebase_admin._apps:
        if service_account_path:
            credential_value = service_account_path.strip()
            if credential_value.startswith("{"):
                try:
                    credential_info = json.loads(credential_value)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        "FIREBASE_SERVICE_ACCOUNT_JSON contains invalid JSON"
                    ) from exc
                cred = credentials.Certificate(credential_info)
            else:
                credential_path = Path(credential_value).expanduser()
                if not credential_path.is_absolute():
                    credential_path = BASE_DIR / credential_path
                credential_path = credential_path.resolve()

                if not credential_path.exists():
                    raise RuntimeError(
                        f"FIREBASE_SERVICE_ACCOUNT_JSON not found: {credential_path}"
                    )
                cred = credentials.Certificate(str(credential_path))
            if os.getenv("FIREBASE_STORAGE_BUCKET"):
                options["storageBucket"] = os.getenv("FIREBASE_STORAGE_BUCKET")
            firebase_admin.initialize_app(cred, options=options)
        else:
            # This requires GOOGLE_APPLICATION_CREDENTIALS or runtime ADC.
            cred = credentials.ApplicationDefault()
            if os.getenv("FIREBASE_STORAGE_BUCKET"):
                options["storageBucket"] = os.getenv("FIREBASE_STORAGE_BUCKET")
            firebase_admin.initialize_app(cred, options=options)

    _FIRESTORE_CLIENT = firestore.client()


def get_firestore_client():
    if _FIRESTORE_CLIENT is None:
        initialize_firebase()
    return _FIRESTORE_CLIENT


def _collection_ref():
    return get_firestore_client().collection(COLLECTION_NAME)


def get_paper_by_id(paper_id: str) -> Optional[Dict]:
    doc = _collection_ref().document(paper_id).get()
    if not doc.exists:
        return None
    data = doc.to_dict() or {}
    data.setdefault("paper_id", paper_id)
    return data


def get_paper_by_credentials(paper_id: str, email: str) -> Optional[Dict]:
    paper = get_paper_by_id(paper_id)
    if not paper:
        return None

    stored_email = (paper.get("corresponding_author_email") or "").strip().lower()
    if stored_email != (email or "").strip().lower():
        return None

    return paper


def create_paper(paper_id: str, paper_data: Dict) -> None:
    payload = dict(paper_data)
    payload["paper_id"] = paper_id
    _collection_ref().document(paper_id).set(payload)


def update_paper(paper_id: str, updates: Dict) -> None:
    _collection_ref().document(paper_id).set(updates, merge=True)


def _named_collection(name: str):
    return get_firestore_client().collection(name)


def get_community_user_by_email(email: str) -> Optional[Dict]:
    normalized_email = (email or "").strip().lower()
    if not normalized_email:
        return None
    for doc in _named_collection(USERS_COLLECTION).where(
        "email", "==", normalized_email
    ).limit(1).stream():
        user = doc.to_dict() or {}
        user["user_id"] = doc.id
        return user
    return None


def get_community_user(user_id: str) -> Optional[Dict]:
    if not user_id:
        return None
    doc = _named_collection(USERS_COLLECTION).document(user_id).get()
    if not doc.exists:
        return None
    user = doc.to_dict() or {}
    user["user_id"] = doc.id
    return user


def create_community_user(user_id: str, user_data: Dict) -> None:
    _named_collection(USERS_COLLECTION).document(user_id).set(user_data)


def update_community_user(user_id: str, updates: Dict) -> None:
    _named_collection(USERS_COLLECTION).document(user_id).set(updates, merge=True)


def create_challenge(challenge_id: str, challenge_data: Dict) -> None:
    _named_collection(CHALLENGES_COLLECTION).document(challenge_id).set(challenge_data)


def get_challenge(challenge_id: str) -> Optional[Dict]:
    doc = _named_collection(CHALLENGES_COLLECTION).document(challenge_id).get()
    if not doc.exists:
        return None
    challenge = doc.to_dict() or {}
    challenge["challenge_id"] = doc.id
    return challenge


def update_challenge(challenge_id: str, updates: Dict) -> None:
    _named_collection(CHALLENGES_COLLECTION).document(challenge_id).set(
        updates, merge=True
    )


def list_challenges_for_paper(paper_id: str, limit: int = 50) -> List[Dict]:
    challenges = []
    query = _named_collection(CHALLENGES_COLLECTION).where(
        "paper_id", "==", paper_id
    ).limit(limit)
    for doc in query.stream():
        challenge = doc.to_dict() or {}
        challenge["challenge_id"] = doc.id
        challenges.append(challenge)
    return sorted(
        challenges,
        key=lambda item: item.get("created_at", ""),
        reverse=True,
    )


def list_public_papers(limit: int = 50) -> List[Dict]:
    papers = []
    for doc in _collection_ref().limit(limit).stream():
        paper = doc.to_dict() or {}
        if paper.get("review_status") not in {"completed", "accepted"}:
            continue
        papers.append(
            {
                "paper_id": doc.id,
                "paper_title": paper.get("paper_title") or "Untitled paper",
                "corresponding_author_name": paper.get(
                    "corresponding_author_name"
                )
                or "Researcher",
                "submission_date": paper.get("submission_date"),
                "review_scores": paper.get("review_scores", {}),
            }
        )
    return sorted(
        papers,
        key=lambda item: item.get("submission_date") or "",
        reverse=True,
    )


def create_reputation_event(event_id: str, event_data: Dict) -> None:
    _named_collection(REPUTATION_COLLECTION).document(event_id).set(event_data)
