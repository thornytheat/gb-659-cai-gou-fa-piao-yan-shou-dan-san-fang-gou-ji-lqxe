import pytest
from fastapi.testclient import TestClient

import app.store as store
import app.modules.m2_master.versions as versions
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATABASE_PATH", tmp_path / "api.sqlite3")
    versions.ensure_baseline()
    with TestClient(app) as test_client:
        yield test_client


def test_versions_endpoint_exposes_current(client):
    body = client.get("/api/master/versions").json()
    assert body["current"]["version"] == 1
    assert body["versions"][0]["alias_count"] >= 1


def test_publish_endpoint_and_stale_conflict(client):
    response = client.post("/api/master/publish", json={"aliases": {"OLD-A": "SKU-B"}, "base_version": 1})
    assert response.status_code == 200 and response.json()["version"] == 2
    stale = client.post("/api/master/publish", json={"aliases": {"OLD-A": "SKU-C"}, "base_version": 1})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "STALE_VERSION"


def test_publish_endpoint_rejects_cycle(client):
    response = client.post("/api/master/publish", json={"aliases": {"A": "B", "B": "A"}, "base_version": 1})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "ALIAS_CYCLE"
    assert client.get("/api/master/versions").json()["current"]["version"] == 1


def test_publish_retry_is_idempotent(client):
    payload = {"aliases": {"OLD-A": "SKU-B"}, "base_version": 1, "idempotency_key": "ui-key"}
    first = client.post("/api/master/publish", json=payload).json()
    second = client.post("/api/master/publish", json=payload).json()
    assert first["created"] and not second["created"] and first["version"] == second["version"]


def test_resolve_uses_requested_version(client):
    client.post("/api/master/publish", json={"aliases": {"OLD-A": "SKU-B"}, "base_version": 1})
    assert client.get("/api/master/resolve", params={"sku": "OLD-A", "mapping_version": 1}).json()["canonical"] == "SKU-A"
    assert client.get("/api/master/resolve", params={"sku": "OLD-A"}).json()["canonical"] == "SKU-B"


def test_batches_page_lists_real_batches(client):
    first = client.post("/api/batches", json={}).json()
    client.post("/api/master/publish", json={"aliases": {"OLD-A": "SKU-B"}, "base_version": 1})
    second = client.post("/api/batches", json={}).json()

    page = client.get("/batches")
    assert page.status_code == 200
    assert first["batch_id"] in page.text and second["batch_id"] in page.text
    listing = client.get("/api/batches").json()["batches"]
    assert {batch["mapping_version"] for batch in listing} == {1, 2}


def test_batch_detail_and_compare_endpoints(client):
    client.post("/api/batches", json={})
    client.post("/api/master/publish", json={"aliases": {"OLD-A": "SKU-B"}, "base_version": 1})
    new_batch = client.post("/api/batches", json={}).json()
    old_batch = client.post("/api/batches", json={"mapping_version": 1}).json()

    detail = client.get(f"/api/batches/{old_batch['batch_id']}")
    assert detail.status_code == 200 and detail.json()["mapping_version"] == 1

    diff = client.get("/api/batches/compare", params={"left": old_batch["batch_id"], "right": new_batch["batch_id"]})
    assert diff.status_code == 200
    assert any(item["line_no"] == 5 for item in diff.json()["line_differences"])


def test_unknown_batch_returns_404(client):
    response = client.get("/api/batches/missing-batch")
    assert response.status_code == 404 and response.json()["detail"]["code"] == "UNKNOWN_BATCH"


def test_batch_creation_is_idempotent_per_version(client):
    first = client.post("/api/batches", json={}).json()
    second = client.post("/api/batches", json={}).json()
    assert first["batch_id"] == second["batch_id"]
    assert first["created"] and not second["created"]
