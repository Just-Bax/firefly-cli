from __future__ import annotations

import pytest

from firefly import protocol


def test_parse_size_accepts_aspect_labels():
    assert protocol.parse_size("16:9") == (1792, 1024)
    assert protocol.parse_size(" 1:1 ") == (1024, 1024)


def test_parse_size_accepts_explicit_pixels():
    assert protocol.parse_size("1408x1024") == (1408, 1024)
    assert protocol.parse_size("512X512") == (512, 512)


def test_parse_size_rejects_nonsense():
    with pytest.raises(ValueError, match="Not a size"):
        protocol.parse_size("huge")


def test_generate_payload_matches_the_captured_shape():
    model = protocol.find_model("nano-banana", "image")
    request = protocol.GenerateRequest(
        prompt="a fox", model=model, width=1024, height=1024, seeds=[7]
    )
    payload = protocol.generate_payload(request, "image")

    assert payload["prompt"] == "a fox"
    assert payload["seeds"] == [7]
    assert payload["modelId"] == "gemini-flash"
    assert payload["modelVersion"] == "nano-banana-3"
    assert payload["generationMetadata"] == {
        "module": "text2image",
        "submodule": "ff-image-generate",
    }
    assert payload["referenceBlobs"] == []
    assert payload["caiClaimVersion"] == 2
    assert payload["modelSpecificPayload"]["parameters"]["addWatermark"] is False


def test_a_reference_makes_it_the_editor_not_the_generator():
    # Adobe labels the two surfaces differently and the label is stamped into
    # the asset's content credentials.
    model = protocol.find_model("nano-banana", "image")
    plain = protocol.generate_payload(protocol.GenerateRequest(prompt="x", model=model), "image")
    edited = protocol.generate_payload(
        protocol.GenerateRequest(prompt="x", model=model, references=["blob-1"]), "image"
    )

    assert plain["generationMetadata"]["submodule"] == "ff-image-generate"
    assert edited["generationMetadata"]["submodule"] == "ff-image-editor"


def test_video_metadata_is_unaffected_by_references():
    model = protocol.Model("v", "video", "veo", "3.1-generate")
    payload = protocol.generate_payload(
        protocol.GenerateRequest(prompt="x", model=model, references=["blob-1"]), "video"
    )
    assert payload["generationMetadata"] == {
        "module": "text2video",
        "submodule": "ff-video-generate",
    }


def test_audio_is_absent_unless_asked_for():
    model = protocol.find_model("nano-banana", "image")
    payload = protocol.generate_payload(protocol.GenerateRequest(prompt="x", model=model))

    assert "generateAudio" not in payload


def test_audio_is_sent_when_asked_for():
    model = protocol.find_model("nano-banana", "image")
    request = protocol.GenerateRequest(prompt="x", model=model, audio=True)

    assert protocol.generate_payload(request, "video")["generateAudio"] is True


def test_generate_payload_attaches_references():
    model = protocol.find_model("nano-banana", "image")
    request = protocol.GenerateRequest(prompt="x", model=model, references=["blob-1", "blob-2"])
    payload = protocol.generate_payload(request, "image")

    assert payload["referenceBlobs"] == [
        {"id": "blob-1", "usage": "general"},
        {"id": "blob-2", "usage": "general"},
    ]


def test_seeds_are_generated_when_not_given():
    model = protocol.find_model("nano-banana", "image")
    seeds = protocol.GenerateRequest(prompt="x", model=model, count=3).seed_list()
    assert len(seeds) == 3
    assert all(1 <= seed <= protocol.SEED_MAX for seed in seeds)


def test_find_blob_id_digs_through_the_response():
    assert protocol.find_blob_id({"images": [{"id": "blob-9"}]}) == "blob-9"
    assert protocol.find_blob_id({"data": {"nested": {"assetId": "  a1  "}}}) == "a1"
    assert protocol.find_blob_id({"images": [{"width": 10}]}) is None


def test_job_links_reads_the_202():
    payload = {"links": {"result": {"href": "https://x/y/1"}, "cancel": {"href": "https://x/c/1"}}}
    assert protocol.job_links(payload) == ("https://x/y/1", "https://x/c/1")
    assert protocol.job_links({}) == ("", "")


def test_job_id_is_the_last_segment_of_the_result_url():
    assert protocol.job_id_from("https://x.adobe.io/v2/status/job-abc/") == "job-abc"


def test_read_result_finds_an_image():
    result = protocol.read_result(
        {"status": "SUCCEEDED", "outputs": [{"image": {"id": "i1", "presignedUrl": "https://a/b"}}]}
    )
    assert result.done
    assert not result.failed
    assert [a.url for a in result.assets] == ["https://a/b"]
    assert result.assets[0].kind == "image"


def test_read_result_finds_a_video_under_a_different_key():
    result = protocol.read_result(
        {"status": "SUCCEEDED", "outputs": [{"video": {"presignedUrl": "https://a/v.mp4"}}]}
    )
    assert [a.url for a in result.assets] == ["https://a/v.mp4"]
    assert result.assets[0].kind == "video"


def test_read_result_while_running():
    result = protocol.read_result({"status": "IN_PROGRESS", "progress": 40})
    assert not result.done
    assert result.progress == 40


def test_read_result_marks_a_failure():
    result = protocol.read_result({"status": "FAILED", "message": "content policy"})
    assert result.done
    assert result.failed
    assert result.error == "content policy"
