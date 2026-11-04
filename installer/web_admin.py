"""Opt-in, loopback-only administration for an installed KCD Master."""

import argparse
from dataclasses import dataclass
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from flask import Flask, abort, render_template, request, session
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .remote_workflow import remote_operation


PLAN_LIFETIME = 300


@dataclass(frozen=True)
class Target:
    name: str
    config_dir: str
    moonraker: str
    ssh_alias: str | None = None
    relocate: bool = False
    reactivate: bool = False


def http_url(value):
    if not isinstance(value, str):
        raise ValueError("Endpoint must be a URL")
    parts = urlsplit(value)
    if (
        parts.scheme not in {"http", "https"} or not parts.hostname
        or parts.username or parts.password or parts.query or parts.fragment
        or parts.path not in {"", "/"} or any(char.isspace() for char in value)
    ):
        raise ValueError("Use an HTTP(S) endpoint without credentials, path or query")
    parts.port  # Validate an explicitly configured port.
    return value.rstrip("/")


def load_configuration(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    master = http_url(data["master"])
    entries = data["targets"]
    if not isinstance(entries, dict) or not 1 <= len(entries) <= 32:
        raise ValueError("Configure between 1 and 32 named targets")
    targets = {}
    for identifier, entry in entries.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", identifier):
            raise ValueError("Invalid target identifier")
        target = Target(**entry)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,79}", target.name):
            raise ValueError("Invalid printer identity")
        if not all(type(value) is bool for value in (target.relocate, target.reactivate)):
            raise ValueError("Relocate and reactivate must be boolean")
        if target.ssh_alias is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", target.ssh_alias):
            raise ValueError("Remote targets require a configured SSH alias")
        directory = PurePosixPath(target.config_dir) if target.ssh_alias else Path(target.config_dir)
        if not directory.is_absolute() or directory == directory.parent or ".." in directory.parts:
            raise ValueError("Target config directories must be absolute, non-root paths")
        targets[identifier] = Target(
            target.name, str(directory), http_url(target.moonraker), target.ssh_alias,
            target.relocate, target.reactivate,
        )
    if len({target.name for target in targets.values()}) != len(targets):
        raise ValueError("Configure each printer identity once")
    return master, targets


def create_app(master, targets, password, master_key, *, backup_data=None):
    if len(password) < 24 or not master_key or password == master_key:
        raise ValueError("Configure a separate admin password of at least 24 characters and a Master API key")
    if backup_data is not None:
        backup_data = Path(backup_data).resolve(strict=True)
        database = backup_data / "kcd.db"
        if backup_data == backup_data.parent or database.is_symlink() or not database.is_file():
            raise ValueError("Backup directory must contain the local Master's SQL database")
    from . import workflow

    app = Flask(__name__, template_folder="web_templates")
    app.config.update(
        SECRET_KEY=secrets.token_bytes(32), TRUSTED_HOSTS=["127.0.0.1", "localhost"],
        MAX_CONTENT_LENGTH=4096, SESSION_COOKIE_NAME="kcd_admin_session",
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
    )
    signer = URLSafeTimedSerializer(app.secret_key, salt="kcd-reviewed-installer-plan")
    operation_lock = threading.Lock()
    plan_lock = threading.Lock()
    pending_plans = {}
    last_backup = -float("inf")

    @app.before_request
    def authorize():
        # Host validation also rejects DNS rebinding to this loopback service.
        auth = request.authorization
        if (
            not auth or auth.type != "basic" or auth.username != "admin"
            or not hmac.compare_digest((auth.password or "").encode(), password.encode())
        ):
            return "Administrator login required", 401, {
                "WWW-Authenticate": 'Basic realm="KCD Installer", charset="UTF-8"'
            }
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        if request.method == "POST":
            if request.headers.get("Origin") != request.host_url.rstrip("/"):
                abort(403)
            if not hmac.compare_digest(request.form.get("csrf", "").encode(), session["csrf"].encode()):
                abort(403)

    @app.after_request
    def protect(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    def page(**context):
        return render_template(
            "admin.html", targets=targets, csrf=session["csrf"], backup_data=backup_data, **context,
        )

    def operation(identifier, *, revision=None):
        target = targets[identifier]
        payload = {
            "config_dir": target.config_dir, "name": target.name, "moonraker": target.moonraker,
            "master": master, "key": master_key, "relocate": target.relocate,
            "reactivate": target.reactivate, "web_safe": True, "apply": revision is not None,
            "expected_revision": revision,
        }
        with operation_lock:
            if target.ssh_alias:
                payload.update(host=target.ssh_alias, installation_type="remote")
                return remote_operation(target.ssh_alias, "repair", payload)
            return workflow.repair_client(**payload)

    @app.get("/")
    def index():
        return page()

    @app.get("/clients")
    def clients():
        try:
            info = workflow.master_request(master, master_key)
            registered = sorted(
                ({"name": client["printer_name"], "retired": bool(client.get("hidden"))}
                 for client in info["clients"]), key=lambda client: client["name"].casefold(),
            )
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            return page(error="Master could not be verified. Check the configured URL, credential and readiness."), 502
        return page(
            clients=registered, backend=info.get("backend"),
            ready=info.get("readiness", {}).get("ready") is True,
        )

    @app.post("/preview")
    def preview():
        identifier = request.form.get("target")
        if identifier not in targets:
            abort(404)
        try:
            result = operation(identifier)
            revision = result["revision"]
            if not isinstance(revision, str) or not revision:
                raise ValueError("Installer did not return a plan revision")
        except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError):
            return page(error="Preview failed. Verify Master access, the Moonraker instance, configured paths and SSH permissions."), 409
        nonce = secrets.token_urlsafe(24)
        with plan_lock:
            now = time.monotonic()
            for old_nonce, expiry in list(pending_plans.items()):
                if expiry <= now or old_nonce == session.get("plan"):
                    del pending_plans[old_nonce]
            if len(pending_plans) >= 128:
                return page(error="Too many outstanding previews. Retry after a preview expires."), 429
            pending_plans[nonce] = now + PLAN_LIFETIME
        session["plan"] = nonce
        ticket = signer.dumps({"target": identifier, "revision": revision, "nonce": nonce})
        return page(selected=targets[identifier], result=result, ticket=ticket)

    @app.post("/apply")
    def apply():
        try:
            reviewed = signer.loads(request.form.get("ticket", ""), max_age=PLAN_LIFETIME)
        except BadSignature:
            return page(error="The preview is invalid or expired. Review a new preview."), 409
        if reviewed["nonce"] != session.pop("plan", None) or reviewed["target"] not in targets:
            return page(error="Review a new preview before applying."), 409
        with plan_lock:
            expiry = pending_plans.pop(reviewed["nonce"], 0)
        if expiry <= time.monotonic():
            return page(error="The preview was already used or expired. Review a new preview."), 409
        try:
            result = operation(reviewed["target"], revision=reviewed["revision"])
        except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError):
            return page(error="Apply could not be verified. Check printer activity and permissions, then review a new preview. If a write was interrupted, review the installer backups and Master registration."), 409
        if result.get("applied") is not True:
            return page(error="The installer did not confirm application. Review registration and try a new preview."), 409
        return page(selected=targets[reviewed["target"]], result=result)

    @app.post("/backup")
    def backup():
        nonlocal last_backup
        if backup_data is None:
            abort(404)
        try:
            with operation_lock:
                if time.monotonic() - last_backup < 2:
                    return page(error="A backup was just requested. Wait before trying again."), 409
                if workflow.master_request(master, master_key).get("backend") != "sql":
                    return page(error="The configured Master must use SQL for this backup action."), 409
                last_backup = time.monotonic()
                result = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve().parents[1] / "tools" / "kcd_backup.py")],
                    env=dict(os.environ, KCD_STORAGE_BACKEND="sql", KCD_DATA_DIR=str(backup_data),
                             KCD_API_KEY=master_key),
                    capture_output=True, text=True, timeout=120, check=True,
                )
                name = Path(result.stdout.strip().removeprefix("Backup created: ")).name
                if not re.fullmatch(r"kcd_backup_[0-9]{8}_[0-9]{6}\.tar\.gz", name):
                    raise ValueError("Unexpected backup result")
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            return page(error="Backup could not be verified. Check SQL readiness, local data access and disk space."), 409
        return page(backup_name=name)

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", required=True, help="Path to the administrator's target allowlist JSON")
    parser.add_argument("--port", type=int, default=6061)
    parser.add_argument("--backup-data", help="Enable backups for this local SQL Master data directory")
    args = parser.parse_args(argv)
    try:
        master, targets = load_configuration(args.targets)
        app = create_app(
            master, targets, os.environ.get("KCD_WEB_ADMIN_PASSWORD", ""), os.environ.get("KCD_API_KEY", ""),
            backup_data=args.backup_data,
        )
    except (OSError, ValueError, TypeError, KeyError):
        parser.error("Invalid target configuration or missing credentials. See docs/web-admin.md.")
    from waitress import serve
    serve(app, host="127.0.0.1", port=args.port, threads=4, max_request_body_size=4096)


if __name__ == "__main__":
    main()
