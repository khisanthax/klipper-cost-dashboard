"""Flask request-boundary integration for workspace resolution."""
from __future__ import annotations

from flask import Flask, g, jsonify, request

from core.demo.config import load_demo_configuration
from core.demo.manager import (
    DemoWorkspaceManager, WorkspaceError, WorkspaceExpired, WorkspaceNotFound,
)
from core.runtime import is_demo_runtime
from core.workspace_context import (
    bind_workspace_context, close_request_connections, normal_workspace_context,
    reset_workspace_context,
)


_DEMO_EXEMPT_ENDPOINTS = {"demo_foundation_create", "health", "static"}
_DEMO_BLOCKED_ENDPOINTS = {
    "log_print", "job_start", "job_update", "job_pause", "job_resume", "job_cancel",
    "job_end", "thumb_cache", "download_csv", "printer_diagnostics_page",
}
_DEMO_BLOCKED_SETTINGS_ACTIONS = {
    "backup_now", "update_backup_settings", "test_moonraker", "save_moonraker_url",
    "test_moonraker_url", "import_moonraker_history",
}


def install_workspace_runtime(app: Flask) -> DemoWorkspaceManager | None:
    manager = None
    if is_demo_runtime():
        manager = DemoWorkspaceManager(load_demo_configuration())
        manager.initialize()
        app.extensions["kcd_demo_manager"] = manager

        @app.post("/demo/foundation/session", endpoint="demo_foundation_create")
        def create_demo_foundation_session():
            session = manager.create_workspace()
            response = jsonify({"status": "created"})
            response.set_cookie(
                manager.configuration.cookie_name, session.token,
                httponly=True, secure=manager.configuration.cookie_secure,
                samesite="Lax", max_age=manager.configuration.absolute_ttl_hours * 3600,
                path="/",
            )
            return response, 201

        @app.post("/demo/foundation/reset", endpoint="demo_foundation_reset")
        def reset_demo_foundation_session():
            session = g.demo_workspace_session
            try:
                refreshed = manager.reset_workspace(session)
            except WorkspaceError:
                return jsonify({"status": "error", "error": "Workspace is busy."}), 409
            g.demo_workspace_session = refreshed
            g.workspace_context = refreshed.context
            return jsonify({"status": "reset"})

    @app.before_request
    def _resolve_kcd_workspace():
        if not is_demo_runtime():
            context = normal_workspace_context()
            g.workspace_context = context
            g.workspace_context_token = bind_workspace_context(context)
            return None
        if request.endpoint in _DEMO_EXEMPT_ENDPOINTS:
            return None
        if request.endpoint in _DEMO_BLOCKED_ENDPOINTS:
            return jsonify({"status": "error", "error": "Operation unavailable in demo mode."}), 403
        if request.method == "POST" and request.endpoint in {
            "settings_page", "settings_printers_page", "settings_profiles_page",
            "settings_other_page", "settings_pause_page",
        }:
            if str(request.form.get("action", "")).strip() in _DEMO_BLOCKED_SETTINGS_ACTIONS:
                return jsonify({"status": "error", "error": "Operation unavailable in demo mode."}), 403
        token = request.cookies.get(manager.configuration.cookie_name, "")  # type: ignore[union-attr]
        try:
            session = manager.resolve_workspace(token)  # type: ignore[union-attr]
        except WorkspaceNotFound:
            return jsonify({"status": "error", "error": "A valid demo session is required."}), 401
        except WorkspaceExpired:
            return jsonify({"status": "error", "error": "The demo session has expired."}), 410
        g.demo_workspace_session = session
        g.workspace_context = session.context
        if request.endpoint != "demo_foundation_reset":
            try:
                workspace_lock = manager.workspace_lock(session.record)  # type: ignore[union-attr]
                workspace_lock.__enter__()
            except WorkspaceError:
                return jsonify({"status": "error", "error": "Workspace is busy."}), 409
            g.demo_workspace_lock = workspace_lock
        g.workspace_context_token = bind_workspace_context(session.context)
        manager.touch_workspace(session.record)  # type: ignore[union-attr]
        return None

    @app.teardown_request
    def _close_kcd_workspace(_error):
        close_request_connections()
        token = getattr(g, "workspace_context_token", None)
        if token is not None:
            reset_workspace_context(token)
        workspace_lock = getattr(g, "demo_workspace_lock", None)
        if workspace_lock is not None:
            workspace_lock.__exit__(None, None, None)

    return manager
