from hashlib import sha256
from app.config import RULE_VERSION

def input_digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()

def run_id(payload: bytes, mapping_version: int | None = None) -> str:
    """同一输入在不同映射版本下必须得到不同批次标识，历史批次才可按原版解释。"""
    suffix = input_digest(payload)[:12]
    if mapping_version is None:
        return f"{RULE_VERSION}-{suffix}"
    return f"{RULE_VERSION}-m{mapping_version}-{suffix}"

def build_manifest(
    payload: bytes,
    accepted: int,
    quarantined: int,
    mapping_version: int | None = None,
    mapping_content_sha256: str | None = None,
    created_at: str | None = None,
) -> dict:
    manifest = {
        "run_id": run_id(payload, mapping_version),
        "batch_id": run_id(payload, mapping_version),
        "input_sha256": input_digest(payload),
        "rule_version": RULE_VERSION,
        "accepted": accepted,
        "quarantined": quarantined,
    }
    if mapping_version is not None:
        manifest["mapping_version"] = mapping_version
    if mapping_content_sha256 is not None:
        manifest["mapping_content_sha256"] = mapping_content_sha256
    if created_at is not None:
        manifest["created_at"] = created_at
    return manifest
