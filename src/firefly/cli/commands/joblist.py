from __future__ import annotations

import argparse

from ... import jobs as job_store
from ..context import Context
from ..output import build_table, emit


def register(subparsers: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    listing = subparsers.add_parser(
        "jobs", parents=[common], help="recent generations and where they were saved"
    )
    listing.add_argument("--clear", action="store_true", help="forget the job log")
    listing.set_defaults(func=cmd_jobs)

    status = subparsers.add_parser("status", parents=[common], help="ask Adobe about one job")
    status.add_argument("job_id", help="a job id, or an unambiguous prefix")
    status.set_defaults(func=cmd_status)

    collect = subparsers.add_parser(
        "collect", parents=[common], help="download a job submitted with --no-wait"
    )
    collect.add_argument("job_id", help="a job id, or an unambiguous prefix")
    collect.add_argument("--out", metavar="DIR", help="where to save")
    collect.set_defaults(func=cmd_collect)

    cancel = subparsers.add_parser("cancel", parents=[common], help="stop a running job")
    cancel.add_argument("job_id", help="a job id, or an unambiguous prefix")
    cancel.set_defaults(func=cmd_cancel)


def cmd_jobs(ctx: Context) -> int:
    if ctx.args.clear:
        removed = job_store.clear()
        emit(ctx, {"cleared": removed}, text=f"Forgot {removed} job(s).")
        return 0

    rows = [job.summary() for job in job_store.all_jobs()]

    def table():
        return build_table(
            None,
            [
                {"header": "Job", "style": "cyan"},
                {"header": "Kind"},
                {"header": "Status"},
                {"header": "Created"},
                {"header": "Prompt", "overflow": "fold"},
            ],
            [
                [r["job_id"][:12], r["kind"], r["status"], r["created"], r["prompt"][:60]]
                for r in rows
            ],
        )

    emit(ctx, rows, table, empty="No jobs yet.")
    return 0


def cmd_status(ctx: Context) -> int:
    data = ctx.service.status(ctx.args.job_id)
    emit(ctx, data, lambda: _fields(data))
    return 0


def cmd_collect(ctx: Context) -> int:
    data = ctx.service.collect(ctx.args.job_id, ctx.out_path())
    emit(
        ctx,
        data,
        lambda: build_table(
            None,
            [{"header": "Saved", "style": "green", "overflow": "fold"}],
            [[path] for path in data["files"]],
        ),
    )
    return 0


def cmd_cancel(ctx: Context) -> int:
    data = ctx.service.cancel(ctx.args.job_id)
    emit(ctx, data, lambda: _fields(data), text=None)
    return 0


def _fields(data: dict) -> object:
    return build_table(
        None,
        [{"header": "Field", "style": "cyan"}, {"header": "Value", "overflow": "fold"}],
        [[key, str(value)] for key, value in data.items()],
    )
