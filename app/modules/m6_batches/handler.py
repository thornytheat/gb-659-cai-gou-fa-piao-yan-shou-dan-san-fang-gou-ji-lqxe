from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel
from app.web import page
from . import service
from .exporter import differences_csv


def _current_version() -> int:
    from app.modules.m2_master.versions import current_version
    return current_version()["version"]

router = APIRouter()


class BatchRequest(BaseModel):
    lines: list[dict] | None = None
    mapping_version: int | None = None


@router.get("/batches", response_class=HTMLResponse)
def view(request: Request):
    batches = service.list_all()
    rows = [
        {
            "key": batch["batch_id"],
            "status": f"映射 v{batch.get('mapping_version')}",
            "value": batch.get("amount", "0"),
        }
        for batch in batches
    ] or [{"key": "—", "status": "暂无批次", "value": "0.00"}]
    return page(request, "m6.html", "差异批次", "/api/reconcile", rows,
                extra={"batches": batches, "current_version": _current_version()})


@router.get("/api/batches")
def list_batches(limit: int = 200):
    return {"batches": service.list_all(limit)}


@router.post("/api/batches")
def create_batch(payload: BatchRequest | None = None):
    payload = payload or BatchRequest()
    return service.create_batch(payload.lines, payload.mapping_version)


@router.get("/api/batches/compare")
def compare(left: str, right: str):
    return service.compare_batches(left, right)


@router.get("/api/batches/{batch_id}")
def get_batch(batch_id: str):
    return service.get(batch_id)


@router.post("/api/reconcile")
def execute(payload: BatchRequest | None = None):
    payload = payload or BatchRequest()
    return service.create_batch(payload.lines, payload.mapping_version)


@router.get("/api/export/differences.csv")
def export_differences():
    body = differences_csv([{"business_key": "SKU-C|0.13", "line_no": 9, "reason_code": "UNINVOICED", "amount_cny": "226.00"}])
    return PlainTextResponse(body, media_type="text/csv", headers={"content-disposition": "attachment; filename=differences.csv"})
