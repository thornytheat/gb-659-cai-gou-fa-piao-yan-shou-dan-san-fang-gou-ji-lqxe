import json
import threading

import pytest

import app.store as store
import app.modules.m2_master.versions as versions
import app.modules.m6_batches.service as batches
from app.errors import DomainError


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATABASE_PATH", tmp_path / "versions.sqlite3")
    versions.ensure_baseline()


def test_baseline_is_version_one():
    assert versions.current_version()["version"] == 1
    assert versions.current_version()["aliases"] == versions.BASELINE_ALIASES


def test_publish_appends_and_preserves_history():
    row, created = versions.publish({"OLD-A": "SKU-A", "A-01": "SKU-B"}, 1, note="纠错")
    assert created and row["version"] == 2
    history = versions.list_versions()
    assert [item["version"] for item in history] == [2, 1]
    # 旧版本内容没有被覆盖。
    assert versions.get_version(1)["aliases"]["A-01"] == "SKU-A"
    assert versions.get_version(2)["aliases"]["A-01"] == "SKU-B"


def test_alias_cycle_rejected_and_nothing_published():
    with pytest.raises(DomainError, match="别名成环"):
        versions.publish({"X": "Y", "Y": "X"}, 1)
    assert versions.current_version()["version"] == 1


def test_longer_alias_cycle_rejected():
    with pytest.raises(DomainError) as exc:
        versions.publish({"A": "B", "B": "C", "C": "A"}, 1)
    assert exc.value.code == "ALIAS_CYCLE"


def test_stale_base_version_rejected():
    versions.publish({"OLD-A": "SKU-B"}, 1)
    with pytest.raises(DomainError) as exc:
        versions.publish({"OLD-A": "SKU-C"}, 1)
    assert exc.value.code == "STALE_VERSION" and exc.value.status == 409
    assert versions.current_version()["version"] == 2


def test_concurrent_publishes_only_one_wins():
    results: list[tuple] = []
    barrier = threading.Barrier(2)

    def attempt(alias):
        barrier.wait()
        try:
            row, created = versions.publish({alias: "SKU-Z"}, 1)
            results.append(("ok", row["version"], created))
        except DomainError as exc:
            results.append(("error", exc.code))

    threads = [threading.Thread(target=attempt, args=(alias,)) for alias in ("L-A", "L-B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(result[0] for result in results) == ["error", "ok"]
    assert [item["version"] for item in versions.list_versions()] == [2, 1]


def test_retry_after_interrupted_write_creates_single_version(monkeypatch):
    def crash(version):
        raise RuntimeError("interrupted before commit")

    monkeypatch.setattr(versions, "_before_commit", crash)
    with pytest.raises(RuntimeError):
        versions.publish({"OLD-A": "SKU-Q"}, 1, idempotency_key="retry-key")
    assert versions.current_version()["version"] == 1

    monkeypatch.setattr(versions, "_before_commit", lambda version: None)
    row, created = versions.publish({"OLD-A": "SKU-Q"}, 1, idempotency_key="retry-key")
    assert created and row["version"] == 2
    again, created_again = versions.publish({"OLD-A": "SKU-Q"}, 1, idempotency_key="retry-key")
    assert not created_again and again["version"] == 2
    assert [item["version"] for item in versions.list_versions()] == [2, 1]


def test_same_source_retry_without_key_is_idempotent():
    first, created = versions.publish({"OLD-A": "SKU-B"}, 1)
    second, created_again = versions.publish({"OLD-A": "SKU-B"}, 1)
    assert created and not created_again and first["version"] == second["version"]


def test_same_idempotency_key_with_different_content_rejected():
    versions.publish({"OLD-A": "SKU-B"}, 1, idempotency_key="fixed")
    with pytest.raises(DomainError) as exc:
        versions.publish({"OLD-A": "SKU-C"}, 1, idempotency_key="fixed")
    assert exc.value.code == "IDEMPOTENCY_MISMATCH"


def test_existing_batch_keeps_original_mapping_after_publish():
    old_batch = batches.create_batch(None, version=1)
    versions.publish({"OLD-A": "SKU-B"}, 1)
    new_batch = batches.create_batch(None)

    assert old_batch["mapping_version"] == 1
    assert new_batch["mapping_version"] == 2
    assert old_batch["batch_id"] != new_batch["batch_id"]

    # 历史批次读取时仍按 v1 解释，没有被新版本改写。
    stored_old = batches.get(old_batch["batch_id"])
    assert stored_old["mapping_aliases"]["OLD-A"] == "SKU-A"
    assert {line["line_no"]: line["canonical"] for line in stored_old["lines"]}[5] == "SKU-A"
    assert {line["line_no"]: line["canonical"] for line in batches.get(new_batch["batch_id"])["lines"]}[5] == "SKU-B"


def test_same_input_can_be_rebuilt_under_new_version():
    first = batches.create_batch(None)
    versions.publish({"OLD-A": "SKU-B"}, 1)
    second = batches.create_batch(None)
    assert first["input_sha256"] == second["input_sha256"]
    assert first["mapping_version"] != second["mapping_version"]
    assert first["created"] and second["created"]


def test_compare_reports_attribution_differences():
    old_batch = batches.create_batch(None)
    versions.publish({"OLD-A": "SKU-B"}, 1)
    new_batch = batches.create_batch(None)
    diff = batches.compare_batches(old_batch["batch_id"], new_batch["batch_id"])
    assert not diff["identical_attribution"]
    changed = {item["line_no"]: item for item in diff["line_differences"]}
    assert changed[5]["old_canonical"] == "SKU-A"
    assert changed[5]["new_canonical"] == "SKU-B"
    moved = {item["canonical"]: item for item in diff["group_differences"]}
    assert moved["SKU-A"]["delta"] == "-1130.00"
    assert moved["SKU-B"]["delta"] == "1130.00"


def test_compare_rejects_different_inputs():
    first = batches.create_batch(None)
    other_payload = json.dumps([{"line_no": 2, "sku": "OLD-A", "amount": "1.00"}]).encode()
    second = batches.create_batch(other_payload)
    with pytest.raises(DomainError) as exc:
        batches.compare_batches(first["batch_id"], second["batch_id"])
    assert exc.value.code == "INPUT_MISMATCH"


def test_unknown_version_rejected():
    with pytest.raises(DomainError) as exc:
        versions.get_version(99)
    assert exc.value.code == "UNKNOWN_VERSION"
