from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from . import store
from .errors import NotFound

# Long enough to still find yesterday's render, short enough that config.json
# stays a file a human can read.
MAX_JOBS = 50


@dataclass
class Job:
    """A submitted generation, kept on disk because every CLI run is a new process.

    The result URL is stored verbatim rather than rebuilt from the job id: it is
    region-scoped, and a rebuilt one would poll the wrong cluster.
    """

    job_id: str
    kind: str = "image"
    prompt: str = ""
    result_url: str = ""
    cancel_url: str = ""
    status: str = "IN_PROGRESS"
    files: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "prompt": self.prompt,
            "files": self.files,
            "created": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.created_at)),
        }


def _from_dict(data: dict[str, Any]) -> Job:
    known = {f.name for f in fields(Job)}
    return Job(**{k: v for k, v in data.items() if k in known})


def all_jobs() -> list[Job]:
    raw = store.read().get(store.JOBS_KEY)
    if not isinstance(raw, list):
        return []
    jobs = [_from_dict(item) for item in raw if isinstance(item, dict) and item.get("job_id")]
    return sorted(jobs, key=lambda j: j.created_at, reverse=True)


def save(job: Job) -> Job:
    kept = [j for j in all_jobs() if j.job_id != job.job_id]
    kept.insert(0, job)
    store.update(**{store.JOBS_KEY: [j.to_dict() for j in kept[:MAX_JOBS]]})
    return job


def get(job_id: str) -> Job:
    needle = job_id.strip()
    jobs = all_jobs()
    for job in jobs:
        if job.job_id == needle:
            return job

    partial = [j for j in jobs if j.job_id.startswith(needle)]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        raise NotFound(f"{job_id!r} matches {len(partial)} jobs. Use the full id.")
    raise NotFound(f"No job {job_id!r}. Run 'firefly jobs' to list recent ones.")


def clear() -> int:
    count = len(all_jobs())
    store.update(**{store.JOBS_KEY: None})
    return count
