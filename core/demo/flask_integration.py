"""Flask request boundary for isolated, default-deny demo workspaces."""
from __future__ import annotations

import logging
import re
import secrets
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Flask, Response, g, jsonify, redirect, render_template, request, url_for
from markupsafe import escape
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from core.demo.config import load_demo_configuration
from core.demo.registry import WorkspaceCapacityReached
from core.demo.manager import (
    DemoWorkspaceManager, WorkspaceError, WorkspaceExpired, WorkspaceNotFound,
)
from core.demo.security import (
    DemoAccessPolicy, DemoPolicyClass, DemoSecurityError, csrf_token,
    consume_rate, reserve_quota, validate_csrf, validate_request_input,
    validate_same_origin,
)
from core.runtime import is_demo_runtime
from core.workspace_context import (
    bind_workspace_context, close_request_connections, normal_workspace_context,
    reset_workspace_context,
)


logger = logging.getLogger(__name__)
_POST_FORM = re.compile(
    r"(<form\b(?=[^>]*\bmethod\s*=\s*['\"]post['\"])[^>]*>)",
    re.IGNORECASE,
)


def _wants_html() -> bool:
    accepted = request.accept_mimetypes
    return not request.is_json and accepted["text/html"] > accepted["application/json"]


def _wants_json() -> bool:
    if _wants_html():
        return False
    if request.is_json or request.path.startswith("/demo/") or request.path.startswith("/job-"):
        return True
    if request.path == "/log-print":
        return True
    return request.accept_mimetypes.best == "application/json"


def _error_response(
    status: int,
    code: str,
    message: str,
    *,
    retry_after: int = 0,
) -> Response:
    if _wants_json():
        response = jsonify({"status": "error", "code": code, "error": message})
    else:
        response = Response(
            "<!doctype html><html><head><title>Demo request unavailable</title></head>"
            f"<body><h1>Request unavailable</h1><p>{escape(message)}</p></body></html>",
            content_type="text/html; charset=utf-8",
        )
    response.status_code = status
    if retry_after:
        response.headers["Retry-After"] = str(retry_after)
    return response


def install_workspace_runtime(app: Flask) -> DemoWorkspaceManager | None:
    manager = None
    if is_demo_runtime():
        manager = DemoWorkspaceManager(load_demo_configuration())
        manager.initialize()
        policy = DemoAccessPolicy()
        app.extensions["kcd_demo_manager"] = manager
        app.extensions["kcd_demo_policy"] = policy
        app.config["MAX_CONTENT_LENGTH"] = manager.configuration.max_request_bytes
        app.debug = False

        @app.context_processor
        def _demo_template_context():
            return {
                "kcd_demo_mode": True,
                "kcd_demo_has_session": bool(getattr(g, "demo_workspace_session", None)),
            }

        @app.get("/demo/", endpoint="demo_welcome")
        def demo_welcome():
            return render_template("demo_welcome.html")

        @app.post("/demo/foundation/session", endpoint="demo_foundation_create")
        def create_demo_foundation_session():
            try:
                session = manager.create_workspace()
            except WorkspaceCapacityReached:
                return _error_response(503, "capacity_reached", "The demo is full. Please try again shortly.", retry_after=60)
            response = (
                redirect(url_for("index"), code=303)
                if _wants_html()
                else jsonify({"status": "created"})
            )
            response.set_cookie(
                manager.configuration.cookie_name,
                session.token,
                httponly=True,
                secure=manager.configuration.cookie_secure,
                samesite="Lax",
                max_age=manager.configuration.absolute_ttl_hours * 3600,
                path="/",
            )
            return response if response.status_code == 303 else (response, 201)

        @app.post("/demo/foundation/reset", endpoint="demo_foundation_reset")
        def reset_demo_foundation_session():
            session = g.demo_workspace_session
            try:
                refreshed = manager.reset_workspace(session)
            except WorkspaceError:
                return _error_response(409, "workspace_busy", "The workspace is busy. Please retry.")
            g.demo_workspace_session = refreshed
            g.workspace_context = refreshed.context
            if _wants_html():
                return redirect(url_for("index"), code=303)
            return jsonify({"status": "reset"})

        @app.errorhandler(DemoSecurityError)
        def _demo_security_error(error: DemoSecurityError):
            return _error_response(
                error.status, error.code, error.message, retry_after=error.retry_after
            )

        @app.errorhandler(RequestEntityTooLarge)
        def _demo_payload_error(_error):
            return _error_response(413, "payload_too_large", "The request is too large.")

        @app.errorhandler(HTTPException)
        def _demo_http_error(error: HTTPException):
            messages = {
                400: ("malformed_request", "The request could not be understood."),
                403: ("operation_blocked", "This operation is unavailable in the temporary demo."),
                404: ("not_found", "The requested demo resource was not found."),
                405: ("method_not_allowed", "This request method is unavailable."),
                413: ("payload_too_large", "The request is too large."),
            }
            code, message = messages.get(
                error.code or 500,
                ("request_failed", "The demo request could not be completed."),
            )
            return _error_response(error.code or 500, code, message)

        @app.errorhandler(Exception)
        def _demo_unexpected_error(error: Exception):
            correlation = secrets.token_hex(6)
            logger.error("Unexpected demo request failure [%s]", correlation)
            return _error_response(
                500, "internal_error", f"The demo request failed. Reference: {correlation}"
            )

    @app.before_request
    def _resolve_kcd_workspace():
        if not is_demo_runtime():
            from core import db
            context = normal_workspace_context(db.DATA_DIR)
            g.workspace_context = context
            g.workspace_context_token = bind_workspace_context(context)
            return None

        assert manager is not None
        policy = app.extensions["kcd_demo_policy"]
        endpoint = request.endpoint
        method = request.method.upper()
        base_rule = policy.rules.get((str(endpoint or ""), method))
        if base_rule is None or base_rule.classification in {
            DemoPolicyClass.BLOCKED_DEMO,
            DemoPolicyClass.OPERATOR_ONLY,
        }:
            consume_rate(None, request, manager, "invalid_session")
            return _error_response(
                403, "operation_blocked",
                "This operation is unavailable in the temporary demo.",
            )

        if endpoint == "health":
            return jsonify({"status": "available"})
        if endpoint == "static":
            return None
        if endpoint == "demo_welcome":
            consume_rate(None, request, manager, "invalid_session")
            return None

        if method not in {"GET", "HEAD", "OPTIONS"}:
            validate_request_input(request, manager.configuration)
        action = str(request.form.get("action", "")).strip() if request.form else ""
        rule = policy.classify(endpoint, method, action)
        if rule.classification in {DemoPolicyClass.BLOCKED_DEMO, DemoPolicyClass.OPERATOR_ONLY}:
            consume_rate(None, request, manager, "invalid_session")
            return _error_response(
                403, "operation_blocked",
                "This operation is unavailable in the temporary demo.",
            )

        if endpoint == "demo_foundation_create":
            validate_same_origin(request, manager.configuration)
            consume_rate(None, request, manager, "invalid_session")
            return None

        token = request.cookies.get(manager.configuration.cookie_name, "")
        try:
            session = manager.resolve_workspace(token)
        except WorkspaceNotFound:
            consume_rate(None, request, manager, "invalid_session")
            if method == "GET" and _wants_html():
                return redirect(url_for("demo_welcome"))
            return _error_response(401, "session_required", "A valid demo session is required.")
        except WorkspaceExpired:
            consume_rate(None, request, manager, "invalid_session")
            if method == "GET" and _wants_html():
                return redirect(url_for("demo_welcome", expired=1))
            return _error_response(410, "session_expired", "The demo session has expired.")

        g.demo_workspace_session = session
        g.workspace_context = session.context
        g.demo_csrf_token = csrf_token(token)
        consume_rate(session, request, manager, "general")

        if rule.classification in {
            DemoPolicyClass.WORKSPACE_MUTATE,
            DemoPolicyClass.WORKSPACE_LIFECYCLE,
        }:
            validate_same_origin(request, manager.configuration)
            validate_csrf(request, token)
            consume_rate(
                session, request, manager,
                "lifecycle" if rule.classification == DemoPolicyClass.WORKSPACE_LIFECYCLE else "mutation",
            )

        if rule.classification != DemoPolicyClass.WORKSPACE_LIFECYCLE:
            try:
                workspace_lease = manager.acquire_request_lease(session.record)
            except WorkspaceExpired:
                return _error_response(410, "session_expired", "The demo session has expired.")
            g.demo_workspace_lease = workspace_lease
            try:
                session = manager.resolve_workspace(token)
            except (WorkspaceNotFound, WorkspaceExpired):
                return _error_response(410, "session_expired", "The demo session has expired.")
            g.demo_workspace_session = session
            g.workspace_context = session.context
            if rule.classification == DemoPolicyClass.WORKSPACE_MUTATE:
                if manager.workspace_storage_bytes(session.context) >= manager.configuration.max_workspace_bytes:
                    raise DemoSecurityError(
                        409, "quota_exceeded",
                        "This demo workspace has reached its storage limit.",
                    )
                reserve_quota(action, session, workspace_lease, manager)

        g.workspace_context_token = bind_workspace_context(session.context)
        manager.touch_workspace(session.record)
        return None

    @app.after_request
    def _secure_demo_response(response):
        if not is_demo_runtime():
            return response
        g.demo_request_succeeded = response.status_code < 400
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        if manager is not None and manager.configuration.expect_https:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if 300 <= response.status_code < 400 and response.headers.get("Location"):
            location = urlsplit(response.headers["Location"])
            query = []
            for key, value in parse_qsl(location.query, keep_blank_values=True):
                if key == "error":
                    value = "The demo request could not be completed."
                query.append((key, value))
            response.headers["Location"] = urlunsplit(
                (location.scheme, location.netloc, location.path, urlencode(query), location.fragment)
            )
        token = getattr(g, "demo_csrf_token", "")
        if token and response.mimetype == "text/html" and response.status_code < 400:
            body = response.get_data(as_text=True)
            hidden = f'<input type="hidden" name="_csrf_token" value="{token}">'
            response.set_data(_POST_FORM.sub(lambda match: match.group(1) + hidden, body))
        if response.mimetype in {"text/html", "application/json", "text/plain"}:
            session = getattr(g, "demo_workspace_session", None)
            secrets_to_redact = []
            raw_token = request.cookies.get(
                manager.configuration.cookie_name if manager is not None else "", ""
            )
            if raw_token:
                secrets_to_redact.extend([raw_token, manager.hash_token(raw_token)])
            if session is not None:
                secrets_to_redact.extend([
                    session.record.workspace_id,
                    str(session.context.workspace_root),
                    str(session.context.database_path),
                ])
            if secrets_to_redact:
                body = response.get_data(as_text=True)
                for secret in secrets_to_redact:
                    if secret:
                        body = body.replace(secret, "[redacted]")
                response.set_data(body)
        return response

    @app.teardown_request
    def _close_kcd_workspace(_error):
        close_request_connections()
        token = getattr(g, "workspace_context_token", None)
        if token is not None:
            reset_workspace_context(token)
        workspace_lease = getattr(g, "demo_workspace_lease", None)
        if workspace_lease is not None:
            workspace_lease.release(successful=bool(getattr(g, "demo_request_succeeded", False)))

    return manager
