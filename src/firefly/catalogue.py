"""The live model list, read from Adobe rather than hardcoded.

The web app asks /v2/models/discovery on load and gets every model it can
offer, with the modality and health of each. Reading the same endpoint means a
model Adobe adds tomorrow is usable today, and it is the only place the video
model ids can be learned without watching the app generate one.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from . import protocol
from .paths import cache_dir, restrict

CACHE_FILE = "models.json"
DEFAULT_TTL_SECONDS = 86400


@dataclass(frozen=True)
class RemoteModel:
    model_id: str
    model_version: str
    kind: str
    display: str = ""
    enabled: bool = True

    @property
    def name(self) -> str:
        return f"{self.model_id}:{self.model_version}"

    def to_model(self) -> protocol.Model:
        return protocol.Model(self.name, self.kind, self.model_id, self.model_version)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "display": self.display,
            "enabled": self.enabled,
        }


def parse(payload: Any) -> list[RemoteModel]:
    """Flatten the discovery document into one row per usable model version.

    A family carries several versions and each names its own modality, so the
    version is the unit that can actually be asked for, not the family.
    """
    models: list[RemoteModel] = []
    if not isinstance(payload, dict):
        return models

    for family in payload.get("models") or []:
        if not isinstance(family, dict):
            continue
        model_id = str(family.get("modelId") or "")
        versions = family.get("modelVersions")
        if not model_id or not isinstance(versions, dict):
            continue

        for version, info in versions.items():
            if not isinstance(info, dict):
                continue
            modality = info.get("outputModality") or []
            kind = str(modality[0]) if modality else ""
            if kind not in ("image", "video"):
                continue
            models.append(
                RemoteModel(
                    model_id=model_id,
                    model_version=str(version),
                    kind=kind,
                    display=str(info.get("modelDisplayName") or ""),
                    enabled=bool(info.get("enabled", True)),
                )
            )

    models.sort(key=lambda m: (m.kind, m.model_id, m.model_version))
    return models


def fetch(client: Any) -> list[RemoteModel]:
    response = client.call("GET", protocol.DISCOVERY_PATH)
    try:
        payload = response.json()
    except ValueError:
        return []
    models = parse(payload)
    if models:
        _write_cache(models)
    return models


def load(client: Any, ttl_seconds: int = DEFAULT_TTL_SECONDS, refresh: bool = False):
    """The catalogue, from disk when it is fresh enough to trust.

    Model resolution happens on every generate, and the list changes on the
    order of weeks, so paying a round trip each time would be pure latency.
    """
    if not refresh:
        cached = _read_cache(ttl_seconds)
        if cached is not None:
            return cached
    return fetch(client)


def find(models: list[RemoteModel], wanted: str, kind: str) -> RemoteModel | None:
    """Resolve what the user typed against the catalogue.

    Accepts 'family:version' for an exact answer, or a bare family name, which
    picks that family's first enabled version of the right modality.
    """
    needle = (wanted or "").strip().lower()
    if not needle:
        return None

    of_kind = [m for m in models if m.kind == kind]
    for model in of_kind:
        if model.name.lower() == needle:
            return model

    family = [m for m in of_kind if m.model_id.lower() == needle]
    enabled = [m for m in family if m.enabled]
    if enabled:
        return enabled[0]
    if family:
        return family[0]

    for model in of_kind:
        if model.display.lower() == needle:
            return model
    return None


def _cache_path():
    return cache_dir() / CACHE_FILE


def _write_cache(models: list[RemoteModel]) -> None:
    path = _cache_path()
    payload = {"fetched_at": time.time(), "models": [m.to_dict() for m in models]}
    try:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        restrict(path)
    except OSError:
        pass


def _read_cache(ttl_seconds: int) -> list[RemoteModel] | None:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None

    # A ttl of zero means do not trust the cache at all, matching how the other
    # ttl settings read.
    if time.time() - float(payload.get("fetched_at") or 0) > ttl_seconds:
        return None

    models = []
    for row in payload.get("models") or []:
        if not isinstance(row, dict):
            continue
        models.append(
            RemoteModel(
                model_id=str(row.get("model_id") or ""),
                model_version=str(row.get("model_version") or ""),
                kind=str(row.get("kind") or ""),
                display=str(row.get("display") or ""),
                enabled=bool(row.get("enabled", True)),
            )
        )
    return models or None
