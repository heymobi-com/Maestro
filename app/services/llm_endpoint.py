"""The one place that decides what a remote LLM base URL means.

Maestro's settings field is a *base*: the code appends ``/v1/models`` and
``/v1/chat/completions`` itself. DeepSeek's and LM Studio's own documentation
instead show a base that already ends in ``/v1``, because that is what the OpenAI
SDK expects, so pasting it verbatim asked for
``https://api.deepseek.com/v1/v1/chat/completions``. The model list then never
populated and the symptom read as "the API key is not being picked up", which is
the wrong thing to go and debug.

Every shape now means the same thing: a trailing slash, a trailing ``/v1``, a
bare ``/chat/completions`` path, or the full completions URL all resolve to the
same base.
"""

_CHAT_PATH = "/v1/chat/completions"
_MODELS_PATH = "/v1/models"

# Longest first: "/v1/chat/completions" must be tried before "/v1".
_STRIPPABLE = ("/v1/chat/completions", "/chat/completions", "/v1")


def remote_base(url) -> str:
    """Normalize a pasted base URL, however much of the path came with it."""

    base = str(url or "").strip().rstrip("/")
    for suffix in _STRIPPABLE:
        if base.endswith(suffix):
            return base[: -len(suffix)].rstrip("/")
    return base


def chat_url(url) -> str:
    """The chat-completions URL Maestro will actually call."""

    return f"{remote_base(url)}{_CHAT_PATH}"


def models_url(url) -> str:
    """The model-list URL Maestro will actually call."""

    return f"{remote_base(url)}{_MODELS_PATH}"
