from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from app.web import page
from .service import demo, publish_correction, run
from .versions import current_version, list_versions, resolve_for_version

router = APIRouter()


class ResolveRequest(BaseModel):
    sku: str = "OLD-A"
    vendor_code: str = "000142"
    mapping_version: int | None = None


class PublishRequest(BaseModel):
    aliases: dict[str, str] = Field(default_factory=dict)
    base_version: int
    note: str | None = None
    idempotency_key: str | None = None


@router.get("/master", response_class=HTMLResponse)
def view(request: Request):
    versions = list_versions()
    latest = versions[0] if versions else current_version()
    rows = [
        {"key": source, "status": "→ " + target, "value": f"v{latest['version']}"}
        for source, target in latest["aliases"].items()
    ] or [{"key": "—", "status": "暂无别名", "value": f"v{latest['version']}"}]
    return page(request, "m2.html", "主数据映射", "/api/master/resolve", rows,
                extra={"versions": versions, "latest": latest})


@router.get("/api/master/versions")
def get_versions():
    versions = list_versions()
    return {"current": versions[0] if versions else current_version(), "versions": versions}


@router.get("/api/master/resolve")
def resolve(sku: str = "OLD-A", mapping_version: int | None = None):
    return resolve_for_version(sku, mapping_version)


@router.post("/api/master/publish")
def publish(request: PublishRequest):
    return publish_correction(request.aliases, request.base_version, request.note, request.idempotency_key)


@router.post("/api/master/resolve")
def execute(payload: ResolveRequest | None = None):
    if payload is None:
        return demo()
    return run({"item_code": payload.sku, "vendor_code": payload.vendor_code}, payload.mapping_version)