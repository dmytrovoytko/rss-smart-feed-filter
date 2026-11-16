from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.observability import ObservabilityStore

router = APIRouter()

TRACE_KINDS = ("http", "job")
TRACE_STATUSES = ("ok", "error", "running")


def _store(request: Request) -> ObservabilityStore:
    return request.app.state.observability


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


def _pretty_json(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return value
    return json.dumps(parsed, indent=2, sort_keys=True)


def _decorate_rows(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    for row in rows:
        row[f"{field}_pretty"] = _pretty_json(row.get(field))
    return rows


@router.get("/observability", response_class=HTMLResponse)
def observability_page(
    request: Request,
    kind: str = "",
    status: str = "",
) -> HTMLResponse:
    store = _store(request)
    kind = kind if kind in TRACE_KINDS else ""
    status = status if status in TRACE_STATUSES else ""
    health = store.health()
    summary = store.summary()
    traces = store.list_traces(
        limit=50,
        kind=kind or None,
        status=status or None,
    )
    alerts = store.list_alerts(limit=20)

    return _templates(request).TemplateResponse(
        request=request,
        name="observability.html",
        context={
            "health": health,
            "summary": summary,
            "traces": traces,
            "alerts": alerts,
            "filter_kind": kind,
            "filter_status": status,
            "trace_kinds": TRACE_KINDS,
            "trace_statuses": TRACE_STATUSES,
        },
    )


@router.post("/observability/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int, request: Request) -> RedirectResponse:
    _store(request).acknowledge_alert(alert_id)
    return RedirectResponse("/observability", status_code=303)


@router.get("/observability/traces/{trace_id}", response_class=HTMLResponse)
def observability_trace(trace_id: str, request: Request) -> HTMLResponse:
    store = _store(request)
    trace = store.get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"unknown trace: {trace_id}")

    spans = _decorate_rows(store.list_spans(trace_id), "attributes_json")
    metrics = _decorate_rows(store.list_metrics(trace_id), "labels_json")
    logs = _decorate_rows(store.list_logs(trace_id), "fields_json")

    return _templates(request).TemplateResponse(
        request=request,
        name="observability_trace.html",
        context={
            "trace": trace,
            "spans": spans,
            "metrics": metrics,
            "logs": logs,
        },
    )
