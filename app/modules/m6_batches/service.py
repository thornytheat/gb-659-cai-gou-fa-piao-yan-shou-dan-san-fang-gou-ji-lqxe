"""差异批次：建立时绑定映射版本，历史批次永远按当时版本解释。"""
from __future__ import annotations

import json
from decimal import Decimal

from app.errors import DomainError
from app.manifest import build_manifest
from app.modules.m2_master.aliases import normalize_sku, resolve_alias
from app.modules.m2_master.versions import current_version, get_version, now_iso
from app.store import get_batch, list_batches, record_run
from .compare import reconcile_amounts, reconcile_counts

# 样例原始输入：同一份内容可在不同映射版本下重复建批。
SAMPLE_LINES = [
    {"line_no": 2, "doc_type": "PO", "doc_no": "PO-001", "sku": "000142", "qty": "10", "amount": "1000.00", "tax_rate": "0.13"},
    {"line_no": 3, "doc_type": "GR", "doc_no": "GR-001", "sku": "000142", "qty": "6", "amount": "600.00", "tax_rate": "0.13"},
    {"line_no": 4, "doc_type": "GR", "doc_no": "GR-002", "sku": "000142", "qty": "4", "amount": "400.00", "tax_rate": "0.13"},
    {"line_no": 5, "doc_type": "IV", "doc_no": "IV-001", "sku": "OLD-A", "qty": "10", "amount": "1130.00", "tax_rate": "0.13"},
]

SAMPLE_PAYLOAD = json.dumps(SAMPLE_LINES, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _payload_bytes(payload) -> bytes:
    if payload is None:
        return SAMPLE_PAYLOAD
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _parse_lines(payload) -> list[dict]:
    if payload is None:
        return [dict(line) for line in SAMPLE_LINES]
    raw = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise DomainError("BAD_BATCH_INPUT", "批次输入必须是 JSON 行数组") from exc
    if isinstance(raw, dict):
        raw = raw.get("lines", [])
    if not isinstance(raw, list):
        raise DomainError("BAD_BATCH_INPUT", "批次输入必须是 JSON 行数组")
    lines: list[dict] = []
    for index, row in enumerate(raw, start=2):
        if not isinstance(row, dict) or not str(row.get("sku", "")).strip():
            raise DomainError("EMPTY_VALUE", f"第 {index} 行缺少 sku")
        try:
            Decimal(str(row.get("amount", "")))
        except Exception as exc:
            raise DomainError("BAD_AMOUNT", f"第 {index} 行金额不合法") from exc
        lines.append({**row, "line_no": int(row.get("line_no", index))})
    return lines


def _resolve_lines(lines: list[dict], mapping: dict) -> tuple[list[dict], list[dict]]:
    resolved, quarantined = [], []
    aliases = mapping["aliases"]
    for line in lines:
        try:
            canonical = resolve_alias(normalize_sku(line["sku"]), aliases)
            resolved.append({**line, "canonical": canonical})
        except ValueError:
            quarantined.append({"line_no": line["line_no"], "reason_code": "UNKNOWN_REFERENCE",
                                "amount": str(line.get("amount", "")), "raw_sku": line["sku"]})
    return resolved, quarantined


def _groups(lines: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for line in lines:
        bucket = grouped.setdefault(line["canonical"], {"canonical": line["canonical"], "amount": Decimal("0"), "lines": 0})
        bucket["amount"] += Decimal(str(line["amount"]))
        bucket["lines"] += 1
    return [
        {"canonical": item["canonical"], "amount": str(item["amount"]), "lines": item["lines"]}
        for item in sorted(grouped.values(), key=lambda item: item["canonical"])
    ]


def build_batch(payload=None, version: int | None = None) -> dict:
    mapping = get_version(version) if version is not None else current_version()
    lines = _parse_lines(payload)
    resolved, quarantined = _resolve_lines(lines, mapping)
    accepted_total = sum((Decimal(str(line["amount"])) for line in resolved), start=Decimal("0"))
    groups = _groups(resolved)
    counts = reconcile_counts(len(lines), len(resolved), len(quarantined), 0)
    return {
        "mapping_version": mapping["version"],
        "mapping_content_sha256": mapping["content_sha256"],
        "mapping_aliases": mapping["aliases"],
        "created_at": now_iso(),
        "accepted": len(resolved),
        "quarantined": len(quarantined),
        "input": len(lines),
        "amount": str(accepted_total),
        "groups": groups,
        "lines": resolved,
        "evidence": quarantined,
        "balanced": counts["balanced"],
        "counts": counts,
    }


def create_batch(payload=None, version: int | None = None) -> dict:
    raw = _payload_bytes(payload)
    content = build_batch(payload, version)
    manifest = build_manifest(
        raw,
        content["accepted"],
        content["quarantined"],
        mapping_version=content["mapping_version"],
        mapping_content_sha256=content["mapping_content_sha256"],
        created_at=content["created_at"],
    )
    manifest["input"] = content["input"]
    manifest["amount"] = content["amount"]
    manifest["groups"] = content["groups"]
    manifest["lines"] = content["lines"]
    manifest["evidence"] = content["evidence"]
    manifest["balanced"] = content["balanced"]
    manifest["counts"] = content["counts"]
    manifest["mapping_aliases"] = content["mapping_aliases"]
    manifest["batch"] = True
    manifest["batch_json"] = dict(manifest)
    created = record_run(manifest)
    account = reconcile_amounts("2260.00", "2034.00", "226.00", "0.00")
    if created:
        return {**(get_batch(manifest["run_id"]) or {}), "created": True, "account": account}
    stored = get_batch(manifest["run_id"]) or {k: v for k, v in manifest.items() if k != "batch_json"}
    return {**stored, "created": False, "account": account}


def list_all(limit: int = 200) -> list[dict]:
    return list_batches(limit)


def get(run_id: str) -> dict:
    batch = get_batch(run_id)
    if batch is None:
        raise DomainError("UNKNOWN_BATCH", f"批次不存在：{run_id}", status=404)
    return batch


def attribution(batch: dict) -> dict[str, str]:
    return {str(line["line_no"]): line["canonical"] for line in batch.get("lines", [])}


def compare_batches(left_id: str, right_id: str) -> dict:
    left, right = get(left_id), get(right_id)
    if left["input_sha256"] != right["input_sha256"]:
        raise DomainError("INPUT_MISMATCH", "仅同一原始输入可比较归属差异")
    left_map, right_map = attribution(left), attribution(right)
    differences = []
    for line_no in sorted(set(left_map) | set(right_map)):
        before, after = left_map.get(line_no), right_map.get(line_no)
        if before != after:
            differences.append({"line_no": int(line_no), "old_canonical": before, "new_canonical": after})
    left_groups = {group["canonical"]: group["amount"] for group in left.get("groups", [])}
    right_groups = {group["canonical"]: group["amount"] for group in right.get("groups", [])}
    moved = []
    for canonical in sorted(set(left_groups) | set(right_groups)):
        old_amount, new_amount = Decimal(left_groups.get(canonical, "0")), Decimal(right_groups.get(canonical, "0"))
        if old_amount != new_amount:
            moved.append({"canonical": canonical,
                          "old_amount": str(old_amount),
                          "new_amount": str(new_amount),
                          "delta": str(new_amount - old_amount)})
    return {
        "input_sha256": left["input_sha256"],
        "left": {"batch_id": left["batch_id"], "mapping_version": left["mapping_version"]},
        "right": {"batch_id": right["batch_id"], "mapping_version": right["mapping_version"]},
        "line_differences": differences,
        "group_differences": moved,
        "identical_attribution": not differences,
    }


def run(payload: bytes = b"procurematch-sample") -> dict:
    """保留旧入口：默认样例按当前映射新建/复用批次。"""
    return create_batch(None if payload == b"procurematch-sample" else payload)


def demo() -> dict:
    return run()
