import json
import sqlite3
from app.config import DATABASE_PATH

# 新增列使用增量迁移，兼容旧库中已存在的 runs 表。
BATCH_COLUMNS = {
    "mapping_version": "INTEGER",
    "rule_version": "TEXT",
    "batch_json": "TEXT",
    "created_at": "TEXT",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    input_sha256 TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    quarantined INTEGER NOT NULL
)
"""

MAPPING_SCHEMA = """
CREATE TABLE IF NOT EXISTS mapping_versions (
    version INTEGER PRIMARY KEY,
    content_sha256 TEXT NOT NULL,
    aliases_json TEXT NOT NULL,
    base_version INTEGER,
    publication_key TEXT NOT NULL UNIQUE,
    note TEXT,
    created_at TEXT NOT NULL
)
"""


def connect():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout = 10000")
    return db


def init_db():
    with connect() as db:
        db.execute(SCHEMA)
        existing = {row["name"] for row in db.execute("PRAGMA table_info(runs)")}
        for name, ddl in BATCH_COLUMNS.items():
            if name not in existing:
                db.execute(f"ALTER TABLE runs ADD COLUMN {name} {ddl}")
        # 同一原始输入在同一映射版本下只能有一个批次；旧库的空映射版本不参与约束。
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_input_version "
            "ON runs(input_sha256, mapping_version) WHERE mapping_version IS NOT NULL"
        )
        db.execute(MAPPING_SCHEMA)
        db.execute("CREATE INDEX IF NOT EXISTS idx_mapping_created ON mapping_versions(created_at)")


def record_run(manifest: dict) -> bool:
    init_db()
    batch_json = manifest.get("batch_json")
    if isinstance(batch_json, (dict, list)):
        batch_json = json.dumps(batch_json, ensure_ascii=False, sort_keys=True)
    with connect() as db:
        before = db.total_changes
        db.execute(
            "INSERT OR IGNORE INTO runs "
            "(run_id, input_sha256, accepted, quarantined, mapping_version, rule_version, batch_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                manifest["run_id"],
                manifest["input_sha256"],
                manifest["accepted"],
                manifest["quarantined"],
                manifest.get("mapping_version"),
                manifest.get("rule_version"),
                batch_json,
                manifest.get("created_at"),
            ),
        )
        return db.total_changes > before


def run_count() -> int:
    init_db()
    with connect() as db:
        return db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]


def batch_count() -> int:
    init_db()
    with connect() as db:
        return db.execute("SELECT COUNT(*) FROM runs WHERE batch_json IS NOT NULL").fetchone()[0]


def get_batch(run_id: str) -> dict | None:
    init_db()
    with connect() as db:
        row = db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return _batch_row(row)


def list_batches(limit: int = 200) -> list[dict]:
    init_db()
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM runs WHERE batch_json IS NOT NULL ORDER BY created_at DESC, run_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [batch for batch in (_batch_row(row) for row in rows) if batch]


def _batch_row(row) -> dict | None:
    if row is None or not row["batch_json"]:
        return None
    try:
        batch = json.loads(row["batch_json"])
    except (TypeError, ValueError):
        return None
    batch.setdefault("run_id", row["run_id"])
    batch.setdefault("batch_id", row["run_id"])
    batch.setdefault("input_sha256", row["input_sha256"])
    batch.setdefault("accepted", row["accepted"])
    batch.setdefault("quarantined", row["quarantined"])
    batch.setdefault("mapping_version", row["mapping_version"])
    return batch
