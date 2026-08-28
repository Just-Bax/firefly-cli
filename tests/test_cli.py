from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import PNG

from firefly import session as session_store
from firefly.cli.main import main
from firefly.client import Client


def run(capsys, *argv):
    code = main(list(argv))
    return code, capsys.readouterr()


def test_help_without_a_command(capsys):
    code, out = run(capsys)
    assert code == 2
    assert "firefly image" in out.out


def test_version(capsys):
    with pytest.raises(SystemExit) as caught:
        main(["--version"])
    assert caught.value.code == 0


def test_whoami_when_not_signed_in(capsys):
    code, out = run(capsys, "whoami", "--json")
    assert code == 3
    assert json.loads(out.out) == {"signed_in": False}


def test_whoami_after_signing_in(capsys, session):
    session_store.save(session)
    code, out = run(capsys, "whoami", "--json")
    payload = json.loads(out.out)

    assert code == 0
    assert payload["signed_in"] is True
    assert payload["account_id"] == "ABC@AdobeID"
    assert session.bearer not in out.out


def test_config_show_lists_settings_and_paths(capsys):
    code, out = run(capsys, "config", "show", "--json")
    payload = json.loads(out.out)

    assert code == 0
    assert payload["poll_seconds"] == 2.0
    assert "downloads" in payload["paths"]


def test_config_show_never_prints_the_token(capsys, session):
    session_store.save(session)
    _, out = run(capsys, "config", "show", "--json")
    assert session.bearer not in out.out


def test_config_set_persists(capsys):
    run(capsys, "config", "set", "video_wait_seconds", "1200")
    _, out = run(capsys, "config", "show", "--json")
    assert json.loads(out.out)["video_wait_seconds"] == 1200


def test_config_set_rejects_an_unknown_key(capsys):
    code, out = run(capsys, "config", "set", "nope", "1", "--json")
    assert code == 1
    assert "Unknown setting" in json.loads(out.out)["error"]


def test_config_set_rejects_a_non_number(capsys):
    code, out = run(capsys, "config", "set", "max_retries", "many", "--json")
    assert code == 1
    assert "whole number" in json.loads(out.out)["error"]


def test_models_lists_what_adobe_returns(capsys, session, monkeypatch):
    from conftest import DISCOVERY, Recorder, json_response

    from firefly import protocol

    session_store.save(session)
    recorder = Recorder([(protocol.DISCOVERY_PATH, json_response(200, DISCOVERY))])
    monkeypatch.setattr(
        "firefly.cli.context.build_client",
        lambda config, current: Client(current, transport=recorder.transport()),
    )

    code, out = run(capsys, "models", "--kind", "video", "--json")
    payload = json.loads(out.out)

    assert code == 0
    assert [m["name"] for m in payload] == ["veo:3.1-generate"]


def test_image_without_a_session_reports_it(capsys):
    code, out = run(capsys, "image", "a fox", "--json")
    assert code == 3
    assert "firefly login" in json.loads(out.out)["error"]


def test_image_writes_a_file(capsys, session, monkeypatch, happy_path, tmp_path):
    session_store.save(session)
    monkeypatch.setattr(
        "firefly.cli.context.build_client",
        lambda config, current: Client(current, transport=happy_path.transport()),
    )

    code, out = run(capsys, "image", "a red fox", "--out", str(tmp_path), "--json")
    payload = json.loads(out.out)

    assert code == 0
    assert Path(payload["files"][0]).read_bytes() == PNG


def test_jobs_is_empty_before_anything_runs(capsys):
    code, out = run(capsys, "jobs", "--json")
    assert code == 0
    assert json.loads(out.out) == []


def test_setup_check_reports_readiness(capsys, monkeypatch):
    monkeypatch.setattr("firefly.cli.commands.setup.browser_is_installed", lambda: False)
    code, out = run(capsys, "setup", "--check", "--json")
    assert code == 1
    assert json.loads(out.out)["ready"] is False


def test_logout_when_there_was_nothing(capsys):
    code, out = run(capsys, "logout", "--json")
    assert code == 0
    assert json.loads(out.out) == {"signed_out": False}
