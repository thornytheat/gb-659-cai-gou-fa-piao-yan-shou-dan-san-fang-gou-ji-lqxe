from .aliases import normalize_supplier_code
from .evolve import migrate_fields
from .versions import BASELINE_ALIASES, current_version, resolve_for_version

# 兼容旧调用方：默认解析始终读取已发布的最新版本，而不是进程内常量。
ALIASES = BASELINE_ALIASES


def run(payload: dict | None = None, version: int | None = None) -> dict:
    payload = migrate_fields(payload or {"item_code": " old-a ", "vendor_code": "000142"})
    resolved = resolve_for_version(payload["sku"], version)
    resolved["supplier_code"] = normalize_supplier_code(payload["supplier_code"])
    return resolved


def publish_correction(aliases: dict | None, base_version: int, note: str | None = None,
                       idempotency_key: str | None = None) -> dict:
    from .versions import publish
    version, created = publish(aliases, base_version, note, idempotency_key)
    return {**version, "created": created}


def version_summary() -> dict:
    return current_version()


def demo():
    return run()
