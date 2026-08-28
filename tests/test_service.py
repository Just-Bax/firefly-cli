from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from conftest import MP4, PNG, RESULT_URL, Recorder, json_response

from firefly import jobs, protocol
from firefly.client import Client
from firefly.config import Config
from firefly.errors import FireflyError, JobFailed, NotFound
from firefly.service import FireflyService, _extension, _free


def test_generate_writes_a_file_and_records_the_job(service, tmp_path):
    result = service.generate("image", "a red fox", out_dir=tmp_path)

    assert result["status"] == "SUCCEEDED"
    saved = Path(result["files"][0])
    assert saved.read_bytes() == PNG
    assert saved.suffix == ".png"
    assert "a-red-fox" in saved.name

    recorded = jobs.get(result["job_id"])
    assert recorded.prompt == "a red fox"
    assert recorded.files == result["files"]


def test_generate_polls_until_the_job_finishes(service, happy_path, tmp_path):
    service.generate("image", "a fox", out_dir=tmp_path)
    assert len(happy_path.sent_to("/v2/status/")) == 2


def test_references_are_uploaded_before_the_job(service, happy_path, tmp_path):
    reference = tmp_path / "ref.png"
    reference.write_bytes(PNG)

    service.generate("image", "make it winter", references=[reference], out_dir=tmp_path)

    assert happy_path.sent_to(protocol.UPLOAD_PATH)
    submitted = happy_path.sent_to(protocol.IMAGE_PATH)[0]
    assert b'"blob-9"' in submitted.content


def test_a_missing_reference_is_caught_before_any_network_call(service, happy_path, tmp_path):
    with pytest.raises(NotFound, match="reference image"):
        service.generate("image", "x", references=[tmp_path / "nope.png"], out_dir=tmp_path)
    assert not happy_path.requests


def test_no_wait_returns_a_job_id_without_polling(service, happy_path, tmp_path):
    result = service.generate("image", "a fox", out_dir=tmp_path, wait=False)

    assert result["job_id"]
    assert result["files"] == []
    assert not happy_path.sent_to("/v2/status/")


def test_collect_downloads_a_job_submitted_without_waiting(session, finished_path, tmp_path):
    with Client(session, transport=finished_path.transport()) as client:
        service = FireflyService(client, Config(poll_seconds=0))
        submitted = service.generate("image", "a fox", out_dir=tmp_path, wait=False)
        collected = service.collect(submitted["job_id"], tmp_path)

    assert Path(collected["files"][0]).read_bytes() == PNG


def test_an_empty_prompt_is_refused(service):
    with pytest.raises(FireflyError, match="prompt is required"):
        service.generate("image", "   ")


def test_video_without_a_discovered_model_says_so(service):
    with pytest.raises(FireflyError, match="firefly models --kind video"):
        service.generate("video", "a candle")


def test_video_uses_the_configured_model(session, tmp_path):
    recorder = Recorder(
        [
            (
                protocol.VIDEO_PATH,
                json_response(202, {"links": {"result": {"href": RESULT_URL}}}),
            ),
            (
                "/v2/status/",
                json_response(
                    200,
                    {
                        "status": "SUCCEEDED",
                        "outputs": [{"video": {"presignedUrl": "https://cdn.invalid/v"}}],
                    },
                ),
            ),
            ("cdn.invalid", httpx.Response(200, content=MP4)),
        ]
    )
    config = Config(poll_seconds=0, video_model_id="firefly-video", video_model_version="v2")
    with Client(session, transport=recorder.transport()) as client:
        service = FireflyService(client, config)
        result = service.generate("video", "a candle", out_dir=tmp_path)

    saved = Path(result["files"][0])
    assert saved.suffix == ".mp4"
    submitted = recorder.sent_to(protocol.VIDEO_PATH)[0]
    assert b'"firefly-video"' in submitted.content
    assert b'"text2video"' in submitted.content


def test_a_failed_job_is_reported_with_adobes_reason(session, tmp_path):
    recorder = Recorder(
        [
            (protocol.IMAGE_PATH, json_response(202, {"links": {"result": {"href": RESULT_URL}}})),
            ("/v2/status/", json_response(200, {"status": "FAILED", "message": "content policy"})),
        ]
    )
    with Client(session, transport=recorder.transport()) as client:
        service = FireflyService(client, Config(poll_seconds=0))
        with pytest.raises(JobFailed, match="content policy"):
            service.generate("image", "x", out_dir=tmp_path)


def test_waiting_gives_up_and_points_at_the_job(session, tmp_path):
    recorder = Recorder(
        [
            (protocol.IMAGE_PATH, json_response(202, {"links": {"result": {"href": RESULT_URL}}})),
            ("/v2/status/", json_response(200, {"status": "IN_PROGRESS", "progress": 10})),
        ]
    )
    with Client(session, transport=recorder.transport()) as client:
        service = FireflyService(client, Config(poll_seconds=0, image_wait_seconds=0))
        with pytest.raises(FireflyError, match="firefly status"):
            service.generate("image", "x", out_dir=tmp_path)


def test_status_reports_progress_without_downloading(service, tmp_path):
    submitted = service.generate("image", "a fox", out_dir=tmp_path, wait=False)
    status = service.status(submitted["job_id"])
    assert status["status"] == "IN_PROGRESS"
    assert status["progress"] == 40
    assert status["downloaded"] is False


def test_an_unknown_model_points_at_the_list(service):
    with pytest.raises(NotFound, match="firefly models"):
        service.resolve_model("image", "dall-e")


def test_a_broken_discovery_endpoint_does_not_block_a_named_model(service):
    # The happy-path transport has no discovery route, so this also covers a 404.
    assert service.resolve_model("image", "nano-banana").model_id == "gemini-flash"


def test_a_raw_model_id_bypasses_the_catalogue(service):
    model = service.resolve_model("image", model_id="some-new-model", model_version="v9")
    assert model.model_id == "some-new-model"


def test_extension_is_sniffed_from_the_bytes():
    assert _extension("https://x/y?sig=1", PNG, "image") == ".png"
    assert _extension("https://x/y?sig=1", MP4, "video") == ".mp4"
    assert _extension("https://x/y.webp", b"unknown", "image") == ".webp"
    assert _extension("https://x/y", b"unknown", "video") == ".mp4"


def test_saving_never_overwrites(tmp_path):
    first = tmp_path / "a.png"
    first.write_bytes(PNG)
    assert _free(first).name == "a-2.png"
