"""Installation-wide presentation preference, independent of business data."""

import json
from pathlib import Path

from core.config import DATA_DIR
from core.file_io import atomic_write
from core.sql_only import is_sql_only, require_file_writes_allowed
from core.storage import _load_user_settings_sql, _save_user_settings_sql, load_json_file


MODES = ("personal", "advanced", "business")
DEFAULT_MODE = "advanced"
SETTING_KEY = "application_mode"
SETTING_FILE = "application_mode.json"


def get_application_mode():
    if is_sql_only():
        settings = _load_user_settings_sql(SETTING_KEY)
    else:
        settings = load_json_file(Path(DATA_DIR) / SETTING_FILE)
    mode = settings.get("mode") if isinstance(settings, dict) else None
    return mode if mode in MODES else DEFAULT_MODE


def set_application_mode(mode):
    if mode not in MODES:
        raise ValueError("Choose Personal, Advanced, or Business.")
    settings = {"mode": mode}
    if is_sql_only():
        _save_user_settings_sql(SETTING_KEY, settings)
    else:
        require_file_writes_allowed(SETTING_FILE, caller_hint="core.application_mode.set_application_mode")
        atomic_write(Path(DATA_DIR) / SETTING_FILE, json.dumps(settings) + "\n", backup=False)
