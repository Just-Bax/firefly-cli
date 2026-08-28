from __future__ import annotations

import httpx
from conftest import DISCOVERY, Recorder, json_response

from firefly import catalogue, protocol
from firefly.client import Client
from firefly.config import Config
from firefly.service import FireflyService


def catalogue_client(session):
    recorder = Recorder([(protocol.DISCOVERY_PATH, json_response(200, DISCOVERY))])
    return Client(session, transport=recorder.transport()), recorder


def test_parse_flattens_families_into_versions():
    models = catalogue.parse(DISCOVERY)
    assert [m.name for m in models] == [
        "gemini-flash:nano-banana",
        "gemini-flash:nano-banana-3",
        "veo:2.0-generate",
        "veo:3.1-generate",
    ]


def test_parse_drops_modalities_this_cli_cannot_use():
    # Audio has no command, so offering it would only produce confusing errors.
    assert all(m.kind in ("image", "video") for m in catalogue.parse(DISCOVERY))


def test_parse_survives_a_malformed_document():
    assert catalogue.parse(None) == []
    assert catalogue.parse({"models": [{"modelId": "x"}, "junk"]}) == []


def test_find_matches_an_exact_family_and_version():
    models = catalogue.parse(DISCOVERY)
    assert catalogue.find(models, "veo:3.1-generate", "video").model_version == "3.1-generate"


def test_find_on_a_bare_family_prefers_an_enabled_version():
    models = catalogue.parse(DISCOVERY)
    assert catalogue.find(models, "veo", "video").model_version == "3.1-generate"
    assert catalogue.find(models, "gemini-flash", "image").model_version == "nano-banana-3"


def test_find_respects_the_modality():
    models = catalogue.parse(DISCOVERY)
    assert catalogue.find(models, "veo", "image") is None


def test_find_returns_none_for_an_unknown_name():
    assert catalogue.find(catalogue.parse(DISCOVERY), "dall-e", "image") is None


def test_the_catalogue_is_fetched_then_cached(session):
    client, recorder = catalogue_client(session)
    service = FireflyService(client, Config())

    first = service.models()
    second = service.models()

    assert len(first) == len(second) == 4
    assert len(recorder.sent_to(protocol.DISCOVERY_PATH)) == 1


def test_refresh_goes_back_to_adobe(session):
    client, recorder = catalogue_client(session)
    service = FireflyService(client, Config())

    service.models()
    service.models(refresh=True)

    assert len(recorder.sent_to(protocol.DISCOVERY_PATH)) == 2


def test_an_expired_cache_is_refetched(session):
    client, recorder = catalogue_client(session)
    service = FireflyService(client, Config(models_ttl_seconds=0))

    service.models()
    service.models()

    assert len(recorder.sent_to(protocol.DISCOVERY_PATH)) == 2


def test_resolving_a_video_model_by_family(session):
    client, _ = catalogue_client(session)
    service = FireflyService(client, Config())

    model = service.resolve_model("video", "veo")
    assert model.model_id == "veo"
    assert model.model_version == "3.1-generate"


def test_video_without_a_model_points_at_the_list(session):
    client, _ = catalogue_client(session)
    service = FireflyService(client, Config())

    try:
        service.resolve_model("video")
    except Exception as exc:
        assert "firefly models --kind video" in str(exc)
    else:
        raise AssertionError("expected a refusal")


def test_a_catalogue_lookup_never_reaches_the_network_for_a_raw_id(session):
    recorder = Recorder([])
    with Client(session, transport=recorder.transport()) as client:
        service = FireflyService(client, Config())
        model = service.resolve_model("video", model_id="kling", model_version="kling_v3")

    assert model.model_id == "kling"
    assert not recorder.requests


def test_service_models_filters_by_kind(session):
    client, _ = catalogue_client(session)
    service = FireflyService(client, Config())
    assert {m.kind for m in service.models("video")} == {"video"}


def test_a_non_json_discovery_response_is_not_fatal(session):
    recorder = Recorder([(protocol.DISCOVERY_PATH, httpx.Response(200, text="<html>"))])
    with Client(session, transport=recorder.transport()) as client:
        assert catalogue.fetch(client) == []
