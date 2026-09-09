"""Configuration API."""
from __future__ import annotations
from fastapi import Body, HTTPException
from .. import port_rules
from .. import port_store, themes
from .. import settings as app_settings
from fastapi import APIRouter
from types import ModuleType

# Bound by the application after its monitor and services are initialized.
runtime: ModuleType | None = None

router = APIRouter()

@router.get("/api/settings")
def get_settings() -> dict:
    return runtime._settings_document()


@router.put("/api/settings")
def put_settings(body: dict = Body(...)) -> dict:
    try:
        result = app_settings.apply_patch(body)
        runtime._monitor.state_changed()
        return runtime._settings_document(result)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/custom-themes")
def get_custom_themes() -> dict:
    return {"themes": themes.list_themes()}


@router.get("/api/port-rules")
def list_port_rules() -> dict:
    return {"rules": port_store.get_port_rules(), "readonly": app_settings.settings_readonly()}


@router.put("/api/port-rules")
def replace_port_rules(body: port_rules.RuleDocument) -> dict:
    if app_settings.settings_readonly():
        raise HTTPException(status_code=403, detail="settings are read-only")
    rules = [r.model_dump() for r in body.rules]
    port_store.set_port_rules(rules)
    runtime._monitor.state_changed()
    return {"rules": rules}


@router.post("/api/custom-themes")
def post_custom_theme(body: dict = Body(...)) -> dict:
    if app_settings.settings_readonly():
        raise HTTPException(status_code=403,
                            detail="settings are locked by PORT_LIGHT_SETTINGS_SOURCE=env or SETTINGS_READONLY")
    try:
        return themes.add_theme(body)
    except themes.ThemeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/api/custom-themes/{theme_id}")
def put_custom_theme(theme_id: str, body: dict = Body(...)) -> dict:
    if app_settings.settings_readonly():
        raise HTTPException(status_code=403,
                            detail="settings are locked by PORT_LIGHT_SETTINGS_SOURCE=env or SETTINGS_READONLY")
    try:
        return themes.update_theme(theme_id, body)
    except themes.ThemeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/custom-themes/{theme_id}")
def delete_custom_theme(theme_id: str) -> dict:
    if app_settings.settings_readonly():
        raise HTTPException(status_code=403,
                            detail="settings are locked by PORT_LIGHT_SETTINGS_SOURCE=env or SETTINGS_READONLY")
    if not themes.delete_theme(theme_id):
        raise HTTPException(status_code=404, detail="no such theme")
    current, _ = app_settings.resolve()
    if current.get("theme_palette") == "@custom:" + theme_id:
        app_settings.apply_patch({"theme_palette": ""})
    return {"removed": theme_id}
