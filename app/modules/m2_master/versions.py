"""映射版本发布：只追加、带并发校验与幂等键的历史快照。"""
from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from zoneinfo import ZoneInfo

import app.store as store
from app.config import TIMEZONE
from app.errors import DomainError
from .aliases import normalize_sku, resolve_alias

BASELINE_VERSION = 1
BASELINE_PUBLICATION_KEY = "baseline:procurematch:1"
BASELINE_ALIASES = {"OLD-A": "SKU-A", "A-01": "SKU-A"}

ALIAS_ERRORS = {"ALIAS_CYCLE": "别名成环", "ALIAS_DUPLICATE": "别名重复", "EMPTY_ALIAS": "别名为空"}


def now_iso() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).isoformat(timespec="seconds")


def _content_sha(aliases: dict[str, str]) -> str:
    payload = json.dumps(aliases, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def prepare_aliases(raw: dict | None) -> dict[str, str]:
    if not isinstance(raw, dict) or not raw:
        raise DomainError("EMPTY_ALIASES", "别名表不能为空")
    prepared: dict[str, str] = {}
    for key, value in raw.items():
        source, target = normalize_sku(key), normalize_sku(value)
        if not source or not target:
            raise DomainError("EMPTY_ALIAS", "别名键或目标不能为空")
        if source in prepared:
            raise DomainError("ALIAS_DUPLICATE", f"归一后别名重复：{source}")
        prepared[source] = target
    detect_cycles(prepared)
    return dict(sorted(prepared.items()))


def detect_cycles(aliases: dict[str, str]) -> None:
    """对每一条别名链独立检测，环在任何入口都应拒绝发布。"""
    for start in aliases:
        current, visited = start, set()
        while current in aliases:
            if current in visited:
                raise DomainError("ALIAS_CYCLE", f"别名成环：{start}")
            visited.add(current)
            current = aliases[current]


def ensure_baseline() -> None:
    store.init_db()
    aliases = prepare_aliases(BASELINE_ALIASES)
    _with_immediate(db=lambda conn: _insert_baseline(conn, aliases))


def _insert_baseline(conn, aliases: dict[str, str]) -> None:
    if conn.execute(
        "SELECT 1 FROM mapping_versions WHERE publication_key = ?", (BASELINE_PUBLICATION_KEY,)
    ).fetchone():
        return
    conn.execute(
        "INSERT OR IGNORE INTO mapping_versions "
        "(version, content_sha256, aliases_json, base_version, publication_key, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            BASELINE_VERSION,
            _content_sha(aliases),
            json.dumps(aliases, ensure_ascii=False, sort_keys=True),
            0,
            BASELINE_PUBLICATION_KEY,
            "初始映射",
            now_iso(),
        ),
    )


def _with_immediate(db):
    """单连接写事务；遇到中断整体回滚，重试可重新完成。"""
    conn = store.connect()
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        db(conn)
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _before_commit(version: int) -> None:
    """故障注入点：测试可在提交前抛出，验证回滚与重试。"""


def publish(
    raw_aliases: dict | None,
    base_version: int,
    note: str | None = None,
    idempotency_key: str | None = None,
) -> tuple[dict, bool]:
    aliases = prepare_aliases(raw_aliases)
    content_sha = _content_sha(aliases)
    publication_key = idempotency_key or f"content:{base_version}:{content_sha}"
    ensure_baseline()
    outcome: dict = {}

    def write(conn):
        existing = conn.execute(
            "SELECT * FROM mapping_versions WHERE publication_key = ?", (publication_key,)
        ).fetchone()
        if existing is not None:
            if existing["content_sha256"] != content_sha:
                raise DomainError("IDEMPOTENCY_MISMATCH", "同一幂等键对应了不同映射内容", status=409)
            outcome.update(row=_version_dict(conn, existing), created=False)
            return
        current = conn.execute(
            "SELECT * FROM mapping_versions ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if current is None:
            raise DomainError("MAPPING_VERSION_MISSING", "映射版本缺失", status=500)
        if int(base_version) != int(current["version"]):
            raise DomainError(
                "STALE_VERSION",
                f"映射版本已变化：当前 v{current['version']}，提交基于 v{base_version}",
                status=409,
            )
        if current["content_sha256"] == content_sha:
            outcome.update(row=_version_dict(conn, current), created=False)
            return
        version = int(current["version"]) + 1
        _before_commit(version)
        conn.execute(
            "INSERT INTO mapping_versions "
            "(version, content_sha256, aliases_json, base_version, publication_key, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                version,
                content_sha,
                json.dumps(aliases, ensure_ascii=False, sort_keys=True),
                int(current["version"]),
                publication_key,
                note or "",
                now_iso(),
            ),
        )
        row = conn.execute("SELECT * FROM mapping_versions WHERE version = ?", (version,)).fetchone()
        outcome.update(row=_version_dict(conn, row), created=True)

    _with_immediate(write)
    return outcome["row"], outcome["created"]


def _version_dict(conn, row) -> dict:
    aliases = json.loads(row["aliases_json"])
    batch_count = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE mapping_version = ? AND batch_json IS NOT NULL",
        (row["version"],),
    ).fetchone()[0]
    return {
        "version": int(row["version"]),
        "aliases": aliases,
        "alias_count": len(aliases),
        "content_sha256": row["content_sha256"],
        "base_version": row["base_version"],
        "note": row["note"],
        "created_at": row["created_at"],
        "batch_count": batch_count,
    }


def current_row(conn=None) -> dict:
    ensure_baseline()
    if conn is None:
        with store.connect() as db:
            row = db.execute("SELECT * FROM mapping_versions ORDER BY version DESC LIMIT 1").fetchone()
            return _version_dict(db, row)
    row = conn.execute("SELECT * FROM mapping_versions ORDER BY version DESC LIMIT 1").fetchone()
    return _version_dict(conn, row)


def current_version() -> dict:
    return current_row()


def get_version(version: int) -> dict:
    ensure_baseline()
    with store.connect() as db:
        row = db.execute("SELECT * FROM mapping_versions WHERE version = ?", (int(version),)).fetchone()
        if row is None:
            raise DomainError("UNKNOWN_VERSION", f"映射版本不存在：v{version}", status=404)
        return _version_dict(db, row)


def list_versions(limit: int = 100) -> list[dict]:
    ensure_baseline()
    with store.connect() as db:
        rows = db.execute(
            "SELECT * FROM mapping_versions ORDER BY version DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_version_dict(db, row) for row in rows]


def resolve_for_version(sku: str, version: int | None = None) -> dict:
    mapping = get_version(version) if version is not None else current_version()
    normalized = normalize_sku(sku)
    try:
        canonical = resolve_alias(normalized, mapping["aliases"])
    except ValueError as exc:
        raise DomainError(str(exc), f"别名无法解析：{sku}") from exc
    return {
        "sku": normalized,
        "canonical": canonical,
        "mapping_version": mapping["version"],
        "mapping_content_sha256": mapping["content_sha256"],
    }
