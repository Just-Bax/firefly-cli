"""Everything about the shape of Adobe's private Firefly API.

Every host, path, header name, payload key and model id lives here, so when
Adobe changes something the blast radius is one file. Nothing else in the
package should contain an adobe.io URL.

Derived from the requests the Firefly web app itself makes at
https://firefly.adobe.com/generate/image.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any

HOST = "https://firefly-3p.ff.adobe.io"
UPLOAD_PATH = "/v2/storage/image"
IMAGE_PATH = "/v2/3p-images/generate-async"
DISCOVERY_PATH = "/v2/models/discovery"

VIDEO_PATH = "/v2/3p-videos/generate-async"

WEB_ORIGIN = "https://firefly.adobe.com"
IMAGE_APP_URL = "https://firefly.adobe.com/generate/image"
VIDEO_APP_URL = "https://firefly.adobe.com/generate/video"

DEFAULT_API_KEY = "clio-playground-web"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)

AUTH_HEADER = "authorization"
API_KEY_HEADER = "x-api-key"
IMS_CLIENT_HEADER = "x-ims-clientid"
ACCOUNT_HEADER = "x-account-id"
ARP_HEADER = "x-arp-session-id"
NONCE_HEADER = "x-nonce"

# What a captured request must carry before a session can be built from it.
REQUIRED_HEADERS = (AUTH_HEADER, API_KEY_HEADER)

# Adobe's anti-abuse pair. Only a real generation carries these, and without
# them the generate endpoint answers 408 "system under load" rather than saying
# what is wrong, so a session lacking them looks healthy and works for nothing.
GENERATE_HEADERS = (ARP_HEADER, NONCE_HEADER)
SESSION_HEADERS = (
    AUTH_HEADER,
    API_KEY_HEADER,
    IMS_CLIENT_HEADER,
    ACCOUNT_HEADER,
    ARP_HEADER,
    NONCE_HEADER,
    "user-agent",
)

# Adobe answers 408 "system under load" on a healthy service, so it is a retry
# signal rather than a failure.
RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

SEED_MAX = 99999

# Which content-credentials claim format the app asks Adobe to stamp on output.
CAI_CLAIM_VERSION = 2


def looks_like_token(value: str) -> bool:
    """Whether an authorization value carries a real IMS token.

    The app fires its first calls before the token has resolved, so the header
    is briefly present with nothing after "Bearer". Taking one of those stores a
    six character credential that fails on the next call.
    """
    text = (value or "").strip()
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    parts = text.split(".")
    return len(parts) == 3 and all(parts)


@dataclass(frozen=True)
class Model:
    name: str
    kind: str
    model_id: str
    model_version: str


MODELS: tuple[Model, ...] = (Model("nano-banana", "image", "gemini-flash", "nano-banana-3"),)

DEFAULT_MODEL = {"image": "nano-banana", "video": ""}

# The sizes the web app offers, keyed by the aspect label it shows.
ASPECTS: dict[str, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "4:3": (1408, 1024),
    "3:4": (1024, 1408),
    "16:9": (1792, 1024),
    "9:16": (1024, 1792),
}

_SIZE = re.compile(r"^\s*(\d{2,5})\s*x\s*(\d{2,5})\s*$", re.IGNORECASE)


def find_model(name: str, kind: str) -> Model | None:
    wanted = (name or "").strip().lower()
    for model in MODELS:
        if model.kind == kind and wanted in (model.name, model.model_id):
            return model
    return None


def models_for(kind: str) -> list[Model]:
    return [m for m in MODELS if m.kind == kind]


def parse_size(value: str) -> tuple[int, int]:
    """Accept either an aspect label the web app uses or explicit pixels."""
    text = (value or "").strip()
    if text in ASPECTS:
        return ASPECTS[text]

    match = _SIZE.match(text)
    if not match:
        options = ", ".join(ASPECTS)
        raise ValueError(f"Not a size: {value!r}. Use WIDTHxHEIGHT or one of: {options}")
    return int(match.group(1)), int(match.group(2))


@dataclass
class GenerateRequest:
    prompt: str
    model: Model
    width: int = 1024
    height: int = 1024
    count: int = 1
    seeds: list[int] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    watermark: bool = False
    audio: bool | None = None

    def seed_list(self) -> list[int]:
        if self.seeds:
            return list(self.seeds)[: self.count]
        return [random.randint(1, SEED_MAX) for _ in range(self.count)]


def generate_payload(request: GenerateRequest, kind: str = "image") -> dict[str, Any]:
    """The body the web app posts to generate.

    generateAudio is sent only when asked for. Adobe defaults it to false on every
    model that has it, so a video generated without it comes back with no audio
    track at all, however much the prompt describes sound.
    """
    payload: dict[str, Any] = {
        "n": request.count,
        "seeds": request.seed_list(),
        "output": {"storeInputs": True},
        "prompt": request.prompt,
        "size": {"width": request.width, "height": request.height},
        "referenceBlobs": [{"id": blob, "usage": "general"} for blob in request.references],
        "caiClaimVersion": CAI_CLAIM_VERSION,
        "modelSpecificPayload": {"parameters": {"addWatermark": request.watermark}},
        "modelId": request.model.model_id,
        "modelVersion": request.model.model_version,
        "generationMetadata": _metadata(kind, bool(request.references)),
        "groundSearch": False,
    }
    if request.audio is not None:
        payload["generateAudio"] = request.audio
    return payload


def _metadata(kind: str, has_references: bool) -> dict[str, str]:
    """The app labels a generation by which of its surfaces produced it.

    Working from a reference image is the editor rather than the generator, and
    the label travels with the asset's content credentials.
    """
    if kind == "video":
        return {"module": "text2video", "submodule": "ff-video-generate"}
    submodule = "ff-image-editor" if has_references else "ff-image-generate"
    return {"module": "text2image", "submodule": submodule}


_ID_KEYS = ("id", "assetId", "blobId", "imageId", "fileId", "storageId", "resourceId")


def find_blob_id(payload: Any) -> str | None:
    """Pull the uploaded asset's id out of the storage response.

    The key it arrives under is not stable across upload types, so the whole
    document is searched rather than one path being trusted.
    """
    if isinstance(payload, dict):
        for key in _ID_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for child in payload.values():
            found = find_blob_id(child)
            if found:
                return found
    elif isinstance(payload, list):
        for child in payload:
            found = find_blob_id(child)
            if found:
                return found
    return None


def job_links(payload: Any) -> tuple[str, str]:
    """The result and cancel URLs Adobe hands back with a 202.

    The result URL is used exactly as given: it is region-scoped, so rebuilding
    it from HOST would poll the wrong cluster.
    """
    links = payload.get("links") or {} if isinstance(payload, dict) else {}
    result = ((links.get("result") or {}).get("href") or "").strip()
    cancel = ((links.get("cancel") or {}).get("href") or "").strip()
    return result, cancel


def job_id_from(result_url: str) -> str:
    return result_url.rstrip("/").rsplit("/", 1)[-1]


@dataclass
class Asset:
    url: str
    asset_id: str = ""
    kind: str = ""


@dataclass
class JobResult:
    status: str
    progress: int = 0
    assets: list[Asset] = field(default_factory=list)
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def done(self) -> bool:
        return bool(self.assets) or self.status in ("SUCCEEDED", "FAILED", "CANCELED")

    @property
    def failed(self) -> bool:
        return not self.assets and self.status in ("FAILED", "CANCELED")


def read_result(payload: Any) -> JobResult:
    """Read one poll response.

    Outputs are scanned for a presigned URL at any depth rather than read from
    outputs[].image, because the wrapper key differs between images and video.
    """
    if not isinstance(payload, dict):
        return JobResult(status="UNKNOWN")

    assets: list[Asset] = []
    for output in payload.get("outputs") or []:
        assets.extend(_assets_in(output))

    status = str(payload.get("status") or ("SUCCEEDED" if assets else "IN_PROGRESS"))
    error = str(payload.get("message") or payload.get("error") or "")
    try:
        progress = int(payload.get("progress") or (100 if assets else 0))
    except (TypeError, ValueError):
        progress = 100 if assets else 0

    return JobResult(status=status, progress=progress, assets=assets, error=error, raw=payload)


def _assets_in(node: Any, kind: str = "") -> list[Asset]:
    found: list[Asset] = []
    if isinstance(node, dict):
        url = node.get("presignedUrl") or node.get("url")
        if isinstance(url, str) and url.startswith("http"):
            return [Asset(url=url, asset_id=str(node.get("id") or ""), kind=kind)]
        for key, child in node.items():
            found.extend(_assets_in(child, key if isinstance(child, dict) else kind))
    elif isinstance(node, list):
        for child in node:
            found.extend(_assets_in(child, kind))
    return found
