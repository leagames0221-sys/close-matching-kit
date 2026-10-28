"""The one place that decides which environment a request is about.

The environment is part of the URL: `/sandbox/...` and `/production/...`; the paths without a head are the sandbox
(the ones the first releases used). A POST also carries the environment in a hidden field, and the server checks on every
POST that the two are the same (`web.post_form`), so a page that was opened in one environment cannot change the other.
The session does not hold the environment: another tab, switched to the other environment, must not redirect a POST of
an old tab.
"""
from __future__ import annotations

from typing import Any

from . import config

# path head -> environment. The empty head is the sandbox (kept for the paths that existed before).
HEADS: dict[str, str] = {"": config.ENV_SANDBOX, "/sandbox": config.ENV_SANDBOX, "/production": config.ENV_PRODUCTION}
FIELD = "env"      # name of the hidden form field


def split(path: str) -> tuple[str, str, str]:
    """(environment, head, rest) of a URL path. `/production/journals/3` -> ('production', '/production', '/journals/3')."""
    for head in ("/production", "/sandbox"):
        if path == head or path.startswith(head + "/"):
            rest = path[len(head):] or "/"
            return HEADS[head], head, rest
    return config.ENV_SANDBOX, "", path


def env_of(request: Any) -> str:
    """The environment a request is about (the URL decides)."""
    return split(request.url.path)[0]


def base_of(request: Any) -> str:
    """The head to put before the paths of links and forms on a page of this request."""
    return split(request.url.path)[1]
