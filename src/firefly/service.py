from __future__ import annotations

import mimetypes
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import catalogue, jobs, protocol
from .client import Client
from .config import Config
from .errors import FireflyError, JobFailed, NotFound
from .jobs import Job
from .paths import slugify

Progress = Callable[[str], None]

# Sniffed rather than trusted from the URL: presigned links routinely carry no
# extension at all.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF8", ".gif"),
    (b"%PDF", ".pdf"),
)


class FireflyService:
    """Everything the commands call, so behaviour lives in one place."""

    def __init__(self, client: Client, config: Config) -> None:
        self.client = client
        self.config = config

    def models(self, kind: str = "", refresh: bool = False) -> list[catalogue.RemoteModel]:
        found = catalogue.load(self.client, self.config.models_ttl_seconds, refresh)
        return [m for m in found if not kind or m.kind == kind]

    def _known(self) -> list[catalogue.RemoteModel]:
        """The catalogue for name resolution, or nothing if Adobe will not say.

        Discovery is a convenience: a broken or moved endpoint must not stop a
        generation whose model was named explicitly.
        """
        try:
            return self.models()
        except FireflyError:
            return []

    def resolve_model(
        self,
        kind: str,
        name: str = "",
        model_id: str = "",
        model_version: str = "",
    ) -> protocol.Model:
        if model_id:
            return protocol.Model("custom", kind, model_id, model_version)

        if name:
            found = catalogue.find(self._known(), name, kind)
            if found is not None:
                return found.to_model()

            static = protocol.find_model(name, kind)
            if static is not None:
                return static
            raise NotFound(
                f"No {kind} model {name!r}. Run 'firefly models --kind {kind}' to list them."
            )

        if kind == "video":
            configured = self.config.video_model()
            if configured is not None:
                return configured
            raise FireflyError(
                "Video needs a model chosen explicitly, because Adobe offers many and "
                "none is the obvious default. Run 'firefly models --kind video', then "
                "pass --model, or set one with 'firefly config set video_model_id'."
            )

        default = protocol.find_model(protocol.DEFAULT_MODEL[kind], kind)
        if default is None:
            raise FireflyError(f"No default {kind} model is known.")
        return default

    def upload_reference(self, path: Path) -> str:
        file = Path(path).expanduser()
        if not file.is_file():
            raise NotFound(f"No such reference image: {file}")
        mime = mimetypes.guess_type(file.name)[0] or "image/png"
        return self.client.upload(file.read_bytes(), mime)

    def submit(
        self,
        kind: str,
        prompt: str,
        references: list[Path] | None = None,
        size: str = "1:1",
        count: int = 1,
        seeds: list[int] | None = None,
        model: str = "",
        model_id: str = "",
        model_version: str = "",
        watermark: bool | None = None,
        audio: bool | None = None,
        on_progress: Progress | None = None,
    ) -> Job:
        report: Progress = on_progress or (lambda _message: None)
        if not prompt.strip():
            raise FireflyError("A prompt is required.")

        width, height = protocol.parse_size(size)
        chosen = self.resolve_model(kind, model, model_id, model_version)

        blobs: list[str] = []
        for reference in references or []:
            report(f"uploading reference {Path(reference).name}")
            blobs.append(self.upload_reference(Path(reference)))

        request = protocol.GenerateRequest(
            prompt=prompt.strip(),
            model=chosen,
            width=width,
            height=height,
            count=max(1, count),
            seeds=seeds or [],
            references=blobs,
            watermark=self.config.watermark if watermark is None else watermark,
            audio=audio,
        )
        payload = protocol.generate_payload(request, kind)

        report(f"submitting to {chosen.model_id}")
        result_url, cancel_url, _ = self.client.create(self.config.path_for(kind), payload)

        return jobs.save(
            Job(
                job_id=protocol.job_id_from(result_url),
                kind=kind,
                prompt=request.prompt,
                result_url=result_url,
                cancel_url=cancel_url,
            )
        )

    def wait(
        self,
        job: Job,
        timeout_seconds: int = 0,
        on_progress: Progress | None = None,
    ) -> protocol.JobResult:
        report: Progress = on_progress or (lambda _message: None)
        limit = timeout_seconds or self.config.wait_seconds(job.kind)
        deadline = time.monotonic() + limit
        last = -1

        while True:
            result = self.client.poll(job.result_url)
            if result.done:
                job.status = "SUCCEEDED" if result.assets else result.status
                jobs.save(job)
                if result.failed:
                    raise JobFailed(
                        f"Adobe finished the job as {result.status}"
                        + (f": {result.error}" if result.error else ".")
                    )
                return result

            if result.progress != last:
                last = result.progress
                report(f"{result.status.lower()} {result.progress}%")

            if time.monotonic() >= deadline:
                job.status = "TIMEOUT"
                jobs.save(job)
                raise FireflyError(
                    f"Job {job.job_id} was still running after {limit}s. "
                    f"It may still finish: 'firefly status {job.job_id}'."
                )

            time.sleep(self.config.poll_seconds)

    def save_assets(
        self,
        job: Job,
        result: protocol.JobResult,
        out_dir: Path | None = None,
        on_progress: Progress | None = None,
    ) -> list[Path]:
        report: Progress = on_progress or (lambda _message: None)
        target = Path(out_dir).expanduser() if out_dir else self.config.resolved_download_dir()
        target.mkdir(parents=True, exist_ok=True)

        stem = f"{time.strftime('%Y%m%d-%H%M%S')}-{slugify(job.prompt)}"
        saved: list[Path] = []
        for index, asset in enumerate(result.assets, start=1):
            report(f"downloading {index}/{len(result.assets)}")
            data = self.client.fetch(asset.url)
            suffix = "" if len(result.assets) == 1 else f"-{index}"
            path = _free(target / f"{stem}{suffix}{_extension(asset.url, data, job.kind)}")
            path.write_bytes(data)
            saved.append(path)

        job.files = [str(p) for p in saved]
        jobs.save(job)
        return saved

    def generate(
        self,
        kind: str,
        prompt: str,
        out_dir: Path | None = None,
        wait: bool = True,
        timeout_seconds: int = 0,
        on_progress: Progress | None = None,
        **options: Any,
    ) -> dict[str, Any]:
        job = self.submit(kind, prompt, on_progress=on_progress, **options)
        if not wait:
            return {"job_id": job.job_id, "kind": kind, "status": "IN_PROGRESS", "files": []}

        result = self.wait(job, timeout_seconds, on_progress)
        saved = self.save_assets(job, result, out_dir, on_progress)
        return {
            "job_id": job.job_id,
            "kind": kind,
            "status": job.status,
            "prompt": job.prompt,
            "files": [str(p) for p in saved],
        }

    def status(self, job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        result = self.client.poll(job.result_url)
        if result.assets and job.status != "SUCCEEDED":
            job.status = "SUCCEEDED"
            jobs.save(job)
        payload = job.summary()
        payload.update(
            {
                "status": result.status,
                "progress": result.progress,
                "assets": len(result.assets),
                "downloaded": bool(job.files),
            }
        )
        return payload

    def collect(self, job_id: str, out_dir: Path | None = None) -> dict[str, Any]:
        """Download a job that was submitted with wait=False."""
        job = jobs.get(job_id)
        result = self.client.poll(job.result_url)
        if not result.assets:
            raise FireflyError(
                f"Job {job.job_id} has produced nothing yet ({result.status.lower()}, "
                f"{result.progress}%)."
            )
        saved = self.save_assets(job, result, out_dir)
        return {"job_id": job.job_id, "status": job.status, "files": [str(p) for p in saved]}

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if not job.cancel_url:
            raise FireflyError(f"Job {job.job_id} cannot be cancelled: Adobe gave no cancel link.")
        self.client.cancel(job.cancel_url)
        job.status = "CANCELED"
        jobs.save(job)
        return job.summary()


def _extension(url: str, data: bytes, kind: str) -> str:
    for magic, suffix in _MAGIC:
        if data.startswith(magic):
            return suffix
    if data[4:12].startswith(b"ftyp"):
        return ".mp4"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"

    tail = Path(url.split("?", 1)[0]).suffix.lower()
    if tail in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm"):
        return tail
    return ".mp4" if kind == "video" else ".png"


def _free(path: Path) -> Path:
    """Never overwrite: two runs of the same prompt in one second are still two
    different pictures."""
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.stem}-{int(time.time())}{path.suffix}")
