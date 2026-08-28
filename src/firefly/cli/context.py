from __future__ import annotations

import argparse
from pathlib import Path

from .. import config as config_module
from .. import session as session_store
from ..client import Client
from ..config import Config
from ..service import FireflyService
from ..session import Session


def renew(session: Session) -> Session:
    """Mint a fresh token from the stored browser profile and keep it."""
    from ..login import refresh

    return session_store.save(refresh(session))


def build_client(config: Config, session: Session) -> Client:
    """The one place a Client is constructed, so settings apply uniformly and
    tests have a single seam to substitute a transport."""
    return Client(
        session,
        timeout=float(config.timeout_seconds),
        max_retries=config.max_retries,
        on_refresh=renew,
    )


class Context:
    """Per-invocation state: settings, output mode and lazily built API access.

    Built lazily so commands that never touch the network (config, setup, help)
    do not require a stored session.
    """

    def __init__(self, args: argparse.Namespace, config: Config | None = None) -> None:
        self.args = args
        self.config = config if config is not None else config_module.load()
        self._session: Session | None = None
        self._client: Client | None = None
        self._service: FireflyService | None = None

    @property
    def as_json(self) -> bool:
        return bool(getattr(self.args, "json", False))

    @property
    def use_color(self) -> bool:
        return self.config.color and not getattr(self.args, "no_color", False)

    @property
    def session(self) -> Session:
        if self._session is None:
            self._session = session_store.load()
        return self._session

    @property
    def client(self) -> Client:
        if self._client is None:
            self._client = build_client(self.config, self.session)
        return self._client

    @property
    def service(self) -> FireflyService:
        if self._service is None:
            self._service = FireflyService(self.client, self.config)
        return self._service

    def out_path(self) -> Path | None:
        out = getattr(self.args, "out", None)
        return Path(out).expanduser() if out else None

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
