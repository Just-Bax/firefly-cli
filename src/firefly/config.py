from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from . import protocol, store
from .errors import FireflyError
from .paths import downloads_dir


@dataclass
class Config:
    download_dir: str = ""
    timeout_seconds: int = 120
    poll_seconds: float = 2.0
    image_wait_seconds: int = 300
    video_wait_seconds: int = 900
    max_retries: int = 5
    models_ttl_seconds: int = 86400
    color: bool = True
    watermark: bool = False
    # Google refuses OAuth in a browser it can tell is automated, so which
    # binary and profile sign-in runs in has to be the user's choice.
    browser_channel: str = ""
    browser_path: str = ""
    browser_profile: str = ""
    # Endpoints live in settings as well as protocol.py so a path Adobe moves
    # can be corrected on a running install without shipping a release.
    image_path: str = protocol.IMAGE_PATH
    video_path: str = protocol.VIDEO_PATH
    video_model_id: str = ""
    video_model_version: str = ""

    def resolved_download_dir(self) -> Path:
        if self.download_dir:
            path = Path(self.download_dir).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            return path
        return downloads_dir()

    def wait_seconds(self, kind: str) -> int:
        return self.video_wait_seconds if kind == "video" else self.image_wait_seconds

    def path_for(self, kind: str) -> str:
        return self.video_path if kind == "video" else self.image_path

    def video_model(self) -> protocol.Model | None:
        if not self.video_model_id:
            return None
        return protocol.Model(
            name="configured",
            kind="video",
            model_id=self.video_model_id,
            model_version=self.video_model_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _known() -> set[str]:
    return {f.name for f in fields(Config)}


def load() -> Config:
    data = store.read()
    known = _known()
    return Config(**{k: v for k, v in data.items() if k in known})


def save(config: Config) -> None:
    store.update(**config.to_dict())


def set_value(config: Config, key: str, raw: str) -> Config:
    known = _known()
    if key not in known:
        options = ", ".join(sorted(known))
        raise FireflyError(f"Unknown setting '{key}'. Valid settings: {options}")

    current = getattr(config, key)
    if isinstance(current, bool):
        value: Any = raw.strip().lower() in ("1", "true", "yes", "on")
    elif isinstance(current, int):
        try:
            value = int(raw)
        except ValueError:
            raise FireflyError(f"'{key}' expects a whole number, got {raw!r}") from None
    elif isinstance(current, float):
        try:
            value = float(raw)
        except ValueError:
            raise FireflyError(f"'{key}' expects a number, got {raw!r}") from None
    else:
        value = raw

    setattr(config, key, value)
    store.update(**{key: value})
    return config
