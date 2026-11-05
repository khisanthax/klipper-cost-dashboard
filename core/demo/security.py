"""Central application-security policy for the public-demo runtime."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
import hashlib
import hmac
import ipaddress
import math
import re
from typing import Iterable, Mapping, Optional
from urllib.parse import urlsplit
import uuid

from flask import Flask, Request
from werkzeug.exceptions import BadRequest

from core import db as db_module
from core.demo.config import DemoConfiguration
from core.demo.manager import WorkspaceRequestLease, WorkspaceSession


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
NETWORK_FIELD = re.compile(
    r"(^|_)(url|uri|host|hostname|ip|port|api_?key|ssh|callback|webhook|socket)($|_)",
    re.IGNORECASE,
)
URL_VALUE = re.compile(r"^(?:https?|ftp|file|ssh)://", re.IGNORECASE)
NONFINITE_VALUE = re.compile(r"^[+-]?(?:nan|inf(?:inity)?)$", re.IGNORECASE)
NONNEGATIVE_FIELD = re.compile(
    r"(?:rate|cost|hours?|duration|filament|grams?|meters?|seconds?|keep|limit)$",
    re.IGNORECASE,
)


class DemoPolicyClass(str, Enum):
    PUBLIC_SAFE = "public_safe"
    WORKSPACE_READ = "workspace_read"
    WORKSPACE_MUTATE = "workspace_mutate"
    WORKSPACE_LIFECYCLE = "workspace_lifecycle"
    OPERATOR_ONLY = "operator_only"
    BLOCKED_DEMO = "blocked_demo"


@dataclass(frozen=True)
class DemoRouteRule:
    classification: DemoPolicyClass
    actions: Optional[frozenset[str]] = None


INDEX_ACTIONS = frozenset({"complete_rows", "recalc_costs", "delete_rows", "delete"})
PROJECT_ACTIONS = frozenset({
    "update_projects_display", "create_project", "update_project", "delete_project",
    "assign_jobs", "unassign_job", "unassign_selected", "recalc_costs",
    "create_manual_job", "update_manual_job", "delete_manual_job", "update_plan_item",
    "fulfill_plan_item", "delete_plan_item", "convert_plan_to_manual",
})
SETTINGS_ACTIONS = frozenset({
    "delete_printer", "update_pause_settings", "save_printer_defaults", "update_columns",
    "rename_printer", "merge_printers", "add_filament_profile",
    "update_filament_profile", "delete_filament_profile", "set_active_filament_profile",
    "add_rate_profile", "update_rate_profile", "delete_rate_profile",
    "set_active_rate_profile",
})


APPLICATION_ROUTE_POLICIES: Mapping[tuple[str, str], DemoRouteRule] = {
    ("demo_welcome", "GET"): DemoRouteRule(DemoPolicyClass.PUBLIC_SAFE),
    ("demo_foundation_create", "POST"): DemoRouteRule(DemoPolicyClass.PUBLIC_SAFE),
    ("demo_foundation_reset", "POST"): DemoRouteRule(DemoPolicyClass.WORKSPACE_LIFECYCLE),
    ("health", "GET"): DemoRouteRule(DemoPolicyClass.PUBLIC_SAFE),
    ("static", "GET"): DemoRouteRule(DemoPolicyClass.PUBLIC_SAFE),
    ("index", "GET"): DemoRouteRule(DemoPolicyClass.WORKSPACE_READ),
    ("index", "POST"): DemoRouteRule(DemoPolicyClass.WORKSPACE_MUTATE, INDEX_ACTIONS),
    ("reports_page", "GET"): DemoRouteRule(DemoPolicyClass.WORKSPACE_READ),
    ("system_events_page", "GET"): DemoRouteRule(DemoPolicyClass.WORKSPACE_READ),
    ("recalculate_page", "GET"): DemoRouteRule(DemoPolicyClass.WORKSPACE_READ),
    ("recalculate_preview", "POST"): DemoRouteRule(DemoPolicyClass.WORKSPACE_MUTATE),
    ("recalculate_run", "POST"): DemoRouteRule(DemoPolicyClass.WORKSPACE_MUTATE),
    ("projects_page", "GET"): DemoRouteRule(DemoPolicyClass.WORKSPACE_READ),
    ("projects_page", "POST"): DemoRouteRule(DemoPolicyClass.WORKSPACE_MUTATE, PROJECT_ACTIONS),
    ("settings_page", "GET"): DemoRouteRule(DemoPolicyClass.WORKSPACE_READ),
    ("settings_page", "POST"): DemoRouteRule(DemoPolicyClass.WORKSPACE_MUTATE, SETTINGS_ACTIONS),
    ("settings_printers_page", "GET"): DemoRouteRule(DemoPolicyClass.WORKSPACE_READ),
    ("settings_printers_page", "POST"): DemoRouteRule(DemoPolicyClass.WORKSPACE_MUTATE, SETTINGS_ACTIONS),
    ("settings_profiles_page", "GET"): DemoRouteRule(DemoPolicyClass.WORKSPACE_READ),
    ("settings_profiles_page", "POST"): DemoRouteRule(DemoPolicyClass.WORKSPACE_MUTATE, SETTINGS_ACTIONS),
    ("settings_other_page", "GET"): DemoRouteRule(DemoPolicyClass.WORKSPACE_READ),
    ("settings_other_page", "POST"): DemoRouteRule(DemoPolicyClass.WORKSPACE_MUTATE, SETTINGS_ACTIONS),
    ("settings_pause_page", "GET"): DemoRouteRule(DemoPolicyClass.WORKSPACE_READ),
    ("settings_pause_page", "POST"): DemoRouteRule(DemoPolicyClass.WORKSPACE_MUTATE, SETTINGS_ACTIONS),
    ("log_print", "POST"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("job_start", "POST"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("job_update", "POST"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("job_pause", "POST"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("job_resume", "POST"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("job_cancel", "POST"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("job_end", "POST"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("thumb_cache", "GET"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("download_csv", "GET"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("printer_diagnostics_page", "GET"): DemoRouteRule(DemoPolicyClass.OPERATOR_ONLY),
    ("installer_registration", "GET"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("installer_registration", "POST"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("job_thumbnail", "GET"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("archived_thumbnail", "GET"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("printer_status", "GET"): DemoRouteRule(DemoPolicyClass.WORKSPACE_READ),
    ("project_pricing_page", "GET"): DemoRouteRule(DemoPolicyClass.WORKSPACE_READ),
    ("project_pricing_page", "POST"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    # Forward-compatible denial for separately reviewed milestone surfaces.
    ("printer_detail", "GET"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("orders_page", "GET"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("orders_page", "POST"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("inventory_page", "GET"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("inventory_page", "POST"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("estimate_page", "GET"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("estimate_page", "POST"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("save_estimate_plan", "POST"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
    ("export_project", "GET"): DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO),
}


QUOTA_ACTIONS = {
    "add_filament_profile": ("filament_profiles", "filament_profiles", "max_filament_profiles"),
    "add_rate_profile": ("hourly_rate_profiles", "hourly_rate_profiles", "max_hourly_profiles"),
    "create_project": ("projects", "projects", "max_projects"),
}


class DemoSecurityError(RuntimeError):
    def __init__(self, status: int, code: str, message: str, *, retry_after: int = 0):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after


class DemoAccessPolicy:
    def __init__(self, rules: Optional[Mapping[tuple[str, str], DemoRouteRule]] = None):
        self.rules = dict(rules or APPLICATION_ROUTE_POLICIES)

    def register(
        self,
        endpoint: str,
        method: str,
        classification: DemoPolicyClass,
        *,
        actions: Optional[Iterable[str]] = None,
    ) -> None:
        self.rules[(endpoint, method.upper())] = DemoRouteRule(
            classification,
            frozenset(actions) if actions is not None else None,
        )

    def classify(self, endpoint: Optional[str], method: str, action: str = "") -> DemoRouteRule:
        rule = self.rules.get((str(endpoint or ""), method.upper()))
        if rule is None:
            return DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO)
        if rule.actions is not None and action not in rule.actions:
            return DemoRouteRule(DemoPolicyClass.BLOCKED_DEMO)
        return rule

    def missing_routes(self, app: Flask) -> list[tuple[str, str, str]]:
        missing = []
        for url_rule in app.url_map.iter_rules():
            for method in sorted(url_rule.methods - {"HEAD", "OPTIONS"}):
                if (url_rule.endpoint, method) not in self.rules:
                    missing.append((url_rule.endpoint, method, url_rule.rule))
        return missing


def csrf_token(session_token: str) -> str:
    return hmac.new(
        session_token.encode("utf-8"),
        b"kcd-demo-csrf-v1",
        hashlib.sha256,
    ).hexdigest()


def validate_csrf(request: Request, session_token: str) -> None:
    supplied = request.headers.get("X-CSRF-Token", "")
    if not supplied and request.mimetype == "application/x-www-form-urlencoded":
        supplied = str(request.form.get("_csrf_token", ""))
    expected = csrf_token(session_token)
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise DemoSecurityError(403, "csrf_rejected", "The security token is missing or invalid.")


def validate_same_origin(request: Request, configuration: DemoConfiguration) -> None:
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if not source:
        if configuration.expect_https:
            raise DemoSecurityError(403, "origin_rejected", "The request origin could not be verified.")
        return
    try:
        parsed = urlsplit(source)
    except ValueError as exc:
        raise DemoSecurityError(403, "origin_rejected", "The request origin is invalid.") from exc
    expected_scheme, expected_host = request_origin(request, configuration)
    if parsed.scheme.lower() != expected_scheme.lower() or parsed.netloc.lower() != expected_host.lower():
        raise DemoSecurityError(403, "origin_rejected", "Cross-origin changes are not allowed.")


def trusted_proxy(request: Request, configuration: DemoConfiguration) -> bool:
    if not configuration.trust_proxy:
        return False
    try:
        address = str(ipaddress.ip_address(request.remote_addr or ""))
    except ValueError:
        return False
    return address in configuration.trusted_proxy_addresses


def request_origin(request: Request, configuration: DemoConfiguration) -> tuple[str, str]:
    if trusted_proxy(request, configuration):
        scheme = request.headers.get("X-Forwarded-Proto", request.scheme).strip().lower()
        host = request.headers.get("X-Forwarded-Host", request.host).strip().lower()
        return scheme, host
    return request.scheme, request.host


def validate_transport(request: Request, configuration: DemoConfiguration) -> None:
    scheme, authority = request_origin(request, configuration)
    try:
        parsed = urlsplit("//" + authority)
        host = parsed.hostname
        if not host or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("Invalid authority")
        parsed.port
    except ValueError as exc:
        raise DemoSecurityError(403, "host_rejected", "This demo hostname is unavailable.") from exc
    if host.lower() not in configuration.allowed_hosts:
        raise DemoSecurityError(403, "host_rejected", "This demo hostname is unavailable.")
    if scheme not in {"http", "https"} or (configuration.expect_https and scheme != "https"):
        raise DemoSecurityError(403, "https_required", "Use the secure demo address.")


def _validate_scalar(name: str, value, configuration: DemoConfiguration) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        try:
            numeric = float(value)
        except (OverflowError, ValueError) as exc:
            raise DemoSecurityError(422, "invalid_input", "A numeric value is outside the allowed range.") from exc
        if not math.isfinite(numeric) or abs(numeric) > 1_000_000_000:
            raise DemoSecurityError(422, "invalid_input", "A numeric value is outside the allowed range.")
        if numeric < 0 and NONNEGATIVE_FIELD.search(name):
            raise DemoSecurityError(422, "invalid_input", "A numeric value must not be negative.")
        return
    text = str(value)
    limit = configuration.max_notes_length if re.search(r"notes?|description", name, re.I) else configuration.max_text_length
    if len(text) > limit:
        raise DemoSecurityError(422, "invalid_input", "A text value exceeds the allowed length.")
    stripped = text.strip()
    if NONFINITE_VALUE.fullmatch(stripped):
        raise DemoSecurityError(422, "invalid_input", "A numeric value must be finite.")
    if URL_VALUE.match(stripped) or NETWORK_FIELD.search(name):
        raise DemoSecurityError(403, "network_input_blocked", "Network destinations are unavailable in the temporary demo.")
    if NONNEGATIVE_FIELD.search(name) and stripped:
        try:
            numeric = float(stripped)
        except ValueError:
            return
        if not math.isfinite(numeric) or numeric < 0 or numeric > 1_000_000_000:
            raise DemoSecurityError(422, "invalid_input", "A numeric value is outside the allowed range.")


def validate_request_input(request: Request, configuration: DemoConfiguration) -> None:
    if request.content_length is not None and request.content_length > configuration.max_request_bytes:
        raise DemoSecurityError(413, "payload_too_large", "The request is too large.")
    if request.mimetype and request.mimetype.startswith("multipart/"):
        raise DemoSecurityError(403, "uploads_blocked", "File uploads are unavailable in the temporary demo.")
    if request.is_json:
        try:
            payload = request.get_json(silent=False)
        except (BadRequest, ValueError, RecursionError) as exc:
            raise DemoSecurityError(400, "malformed_json", "The JSON request is malformed.") from exc

        visited = 0
        def visit(name: str, value, depth=0) -> None:
            nonlocal visited
            visited += 1
            if depth > 8 or visited > configuration.max_list_length:
                raise DemoSecurityError(422, "invalid_input", "The request contains too many nested values.")
            if isinstance(value, dict):
                if len(value) > configuration.max_list_length:
                    raise DemoSecurityError(422, "invalid_input", "The request contains too many values.")
                for child_name, child in value.items():
                    visit(str(child_name), child, depth + 1)
            elif isinstance(value, list):
                if len(value) > configuration.max_list_length:
                    raise DemoSecurityError(422, "invalid_input", "The request contains too many values.")
                for child in value:
                    visit(name, child, depth + 1)
            else:
                _validate_scalar(name, value, configuration)

        visit("request", payload)
        return
    if request.content_length and request.mimetype not in {
        "application/x-www-form-urlencoded", "application/json",
    }:
        raise DemoSecurityError(415, "unsupported_media_type", "The request content type is unsupported.")
    if sum(len(values) for _name, values in request.form.lists()) > configuration.max_list_length:
        raise DemoSecurityError(422, "invalid_input", "The request contains too many values.")
    for name in request.form:
        values = request.form.getlist(name)
        if len(values) > configuration.max_list_length:
            raise DemoSecurityError(422, "invalid_input", "The request contains too many values.")
        for value in values:
            if name != "_csrf_token":
                _validate_scalar(name, value, configuration)


def rate_scope(value: str) -> str:
    return hashlib.sha256(("kcd-demo-rate:" + value).encode("utf-8")).hexdigest()


def consume_rate(
    session: Optional[WorkspaceSession],
    request: Request,
    manager,
    category: str,
) -> None:
    config = manager.configuration
    if session is None:
        forwarded = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        identity = forwarded if trusted_proxy(request, config) and forwarded else request.remote_addr or "unknown"
        limit = config.rate_invalid_session_limit
        window = config.rate_invalid_session_window_seconds
        category = "invalid_session"
    else:
        identity = session.record.workspace_id
        if category == "lifecycle":
            limit, window = config.rate_lifecycle_limit, config.rate_lifecycle_window_seconds
        elif category == "mutation":
            limit, window = config.rate_mutation_limit, config.rate_mutation_window_seconds
        else:
            limit, window = config.rate_general_limit, config.rate_general_window_seconds
    allowed, retry_after = manager.registry.consume_rate(
        rate_scope(identity), category, limit=limit, window_seconds=window, now=manager._now()
    )
    if not allowed:
        raise DemoSecurityError(
            429, "rate_limited", "Too many demo requests. Please try again shortly.",
            retry_after=retry_after,
        )


def reserve_quota(
    action: str,
    session: WorkspaceSession,
    lease: WorkspaceRequestLease,
    manager,
) -> None:
    spec = QUOTA_ACTIONS.get(action)
    if spec is None:
        return
    entity, table, limit_attribute = spec
    limit = int(getattr(manager.configuration, limit_attribute))
    with closing(db_module.connect_db(session.context)) as conn:
        current_count = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    reservation_id = uuid.uuid4().hex
    now = manager._now()
    expires = now + timedelta(seconds=manager.configuration.request_lease_seconds)
    if not manager.registry.acquire_quota_reservation(
        reservation_id,
        session.record.workspace_id,
        entity,
        current_count=current_count,
        limit=limit,
        expires_at=expires,
        now=now,
    ):
        raise DemoSecurityError(409, "quota_exceeded", "This demo workspace has reached its item limit.")
    lease.add_quota_reservation(reservation_id)
