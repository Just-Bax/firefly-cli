from __future__ import annotations

import argparse
from pathlib import Path

from ... import protocol
from ..context import Context
from ..output import build_table, emit, note


def register(subparsers: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
    image = subparsers.add_parser(
        "image",
        parents=[common],
        help="generate an image from a prompt",
        description=(
            "A bare prompt is text-to-image. Adding --ref makes it image-to-image: the "
            "reference is uploaded first and the prompt describes what to do with it."
        ),
    )
    _shared(image)
    image.add_argument(
        "--size",
        default="1:1",
        help=f"WIDTHxHEIGHT, or one of: {', '.join(protocol.ASPECTS)} (default 1:1)",
    )
    image.add_argument("-n", "--count", type=int, default=1, help="how many variations")
    image.set_defaults(func=lambda ctx: _generate(ctx, "image"))

    video = subparsers.add_parser(
        "video",
        parents=[common],
        help="generate a video from a prompt",
        description=(
            "Video takes minutes rather than seconds. Use --no-wait to get a job id "
            "back immediately and collect it later."
        ),
    )
    _shared(video)
    video.add_argument("--size", default="16:9", help="WIDTHxHEIGHT or an aspect (default 16:9)")
    video.add_argument(
        "--audio",
        action="store_true",
        help="generate a soundtrack; off unless asked for, so video is otherwise silent",
    )
    video.set_defaults(func=lambda ctx: _generate(ctx, "video"))


def _shared(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("prompt", help="what to generate")
    parser.add_argument(
        "--ref",
        action="append",
        default=[],
        metavar="FILE",
        help="a reference image to condition on; repeatable",
    )
    parser.add_argument(
        "--seed", action="append", type=int, default=[], help="fix the seed; repeatable"
    )
    parser.add_argument("--model", default="", help="model name; see 'firefly models'")
    parser.add_argument("--model-id", default="", help="raw modelId, bypassing the catalogue")
    parser.add_argument("--model-version", default="", help="raw modelVersion")
    parser.add_argument("--out", metavar="DIR", help="where to save (default ~/.firefly/downloads)")
    parser.add_argument("--watermark", action="store_true", help="ask Adobe to watermark output")
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="submit and print the job id without waiting for the result",
    )
    parser.add_argument("--timeout", type=int, default=0, metavar="SECONDS", help="give up after")


def _generate(ctx: Context, kind: str) -> int:
    args = ctx.args
    result = ctx.service.generate(
        kind,
        args.prompt,
        out_dir=ctx.out_path(),
        wait=not args.no_wait,
        timeout_seconds=args.timeout,
        on_progress=lambda message: note(ctx, f"[dim]{message}[/dim]"),
        references=[Path(r) for r in args.ref],
        size=args.size,
        count=getattr(args, "count", 1),
        seeds=args.seed,
        model=args.model,
        model_id=args.model_id,
        model_version=args.model_version,
        watermark=True if args.watermark else None,
        audio=True if getattr(args, "audio", False) else None,
    )

    if not result["files"]:
        emit(
            ctx,
            result,
            text=(
                f"Submitted as {result['job_id']}.\n"
                f"Collect it with: firefly collect {result['job_id']}"
            ),
        )
        return 0

    emit(
        ctx,
        result,
        lambda: build_table(
            None,
            [{"header": "Saved", "style": "green", "overflow": "fold"}],
            [[path] for path in result["files"]],
        ),
    )
    return 0
