from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

VERSION_HASH_LENGTH = 16

CLASSIFICATION_FIELDS = (
    "name",
    "description",
    "criteria_true",
    "criteria_false",
    "reason_hint",
)


def normalize_text(value: object) -> str:
    return " ".join(str(value or "").split())


def category_snapshot(category: Mapping[str, object]) -> dict[str, str]:
    return {field: normalize_text(category.get(field, "")) for field in CLASSIFICATION_FIELDS}


def category_version_hash(category: Mapping[str, object]) -> str:
    payload = category_snapshot(category)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:VERSION_HASH_LENGTH]


def category_snapshot_json(category: Mapping[str, object]) -> str:
    payload = category_snapshot(category)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
