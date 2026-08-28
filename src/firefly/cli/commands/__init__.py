from . import auth, discover, generate, joblist, models, raw, settings, setup

GROUPS = (auth, generate, joblist, models, discover, raw, settings, setup)

__all__ = ["GROUPS"]
