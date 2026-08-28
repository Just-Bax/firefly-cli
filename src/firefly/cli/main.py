from __future__ import annotations

import argparse
import json
import sys

from .. import __version__
from ..errors import EXIT_USAGE, ApiError, FireflyError
from ..paths import home
from .commands import GROUPS
from .context import Context
from .output import console

DESCRIPTION = "Generate images and video with Adobe Firefly, from the command line."

EPILOG = """\
Sign in once with 'firefly login', then:
  firefly image "a red fox in snow" --size 16:9
  firefly image "the same fox, watercolour" --ref fox.png
  firefly video "a candle guttering" --model veo:3.1-fast-generate --audio

This drives the endpoints firefly.adobe.com uses, with your account and your
credits. It is not a supported Adobe interface and can break without notice.
"""


def build_common() -> argparse.ArgumentParser:
    """Flags every leaf command shares, so they appear in each command's own
    help rather than only at the root."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine readable output")
    common.add_argument("--no-color", action="store_true", help="disable coloured output")
    return common


def build_parser() -> argparse.ArgumentParser:
    common = build_common()
    parser = argparse.ArgumentParser(
        prog="firefly",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"firefly {__version__}")
    parser.add_argument("--home", action="store_true", help="print the config directory and exit")

    subparsers = parser.add_subparsers(dest="command")
    for module in GROUPS:
        module.register(subparsers, common)
    return parser


def force_utf8_output() -> None:
    """Windows consoles and redirected pipes default to a legacy codepage, so a
    single non-ASCII value would otherwise abort a render part-written."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.home:
        print(home())
        return 0
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_USAGE

    ctx = Context(args)
    try:
        return args.func(ctx)
    except FireflyError as exc:
        _report(ctx, exc)
        return exc.exit_code
    except KeyboardInterrupt:
        _report(ctx, FireflyError("Cancelled."))
        return 130
    finally:
        ctx.close()


def _report(ctx: Context, exc: FireflyError) -> None:
    if ctx.as_json:
        payload = (
            exc.to_dict()
            if isinstance(exc, ApiError)
            else {"error": str(exc), "exit_code": exc.exit_code}
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return

    out = console(ctx, stderr=True)
    out.print(f"[red]{exc}[/red]")
    if isinstance(exc, ApiError) and exc.body:
        out.print(f"[dim]{json.dumps(exc.body, ensure_ascii=False, default=str)[:2000]}[/dim]")


if __name__ == "__main__":
    sys.exit(main())
