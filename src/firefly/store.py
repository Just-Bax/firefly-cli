from __future__ import annotations

import json
from typing import Any

from .paths import config_file, restrict

SESSION_KEY = "session"
JOBS_KEY = "jobs"


def read() -> dict[str, Any]:
    path = config_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def update(**changes: Any) -> dict[str, Any]:
    """Merge keys into config.json, leaving every other key alone.

    Settings, credentials and the job log share one file, so a blind overwrite
    from any one of them would drop the others.
    """
    data = read()
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value

    path = config_file()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    # The file holds a live Adobe bearer token, so it is credentials, not just
    # settings.
    restrict(path)
    return data
