"""A model id the chosen provider can actually run.

The provider and the model are two separate fields, and nothing keeps them
agreeing. The failures that result never name their cause:

  * a hosted model name (``deepseek-flash``) in a field resolved under the *local*
    provider. A local server reads a bare name as a Hugging Face repository, so it
    asks for ``huggingface.co/deepseek-flash/resolve/main/deepseek-flash-Q4_K_S.gguf``
    and reports "401 Client Error ... Invalid username or password", which says
    nothing about the provider being wrong;
  * a local repository id (``owner/name-GGUF``) under a hosted provider, sent as the
    ``model`` field and refused by the service as unknown.

Both are contradictions rather than preferences, so they are refused with the field
to go and fix instead of being attempted.
"""

import os

# Local weights are a Hugging Face repo ("owner/name"), a path to a .gguf file, or a
# curated registry entry. A bare name is none of those.
def is_local_repo(model_id) -> bool:
    """Whether a local llama-server could resolve this id to weights."""

    text = str(model_id or "").strip()
    if not text:
        return False
    if text.casefold().endswith(".gguf"):
        return True
    return "/" in text or os.sep in text or (os.altsep or "") in text


def known_local(model_id) -> bool:
    """True when the curated local catalog lists this id."""

    try:
        from services import llm_service
    except Exception:
        return False
    return str(model_id or "").strip() in getattr(llm_service, "MODEL_REGISTRY", {})


def local_model_problem(model_id) -> str:
    """Why a local server cannot load this id, or an empty string when it can."""

    text = str(model_id or "").strip()
    if not text:
        # An empty id means "use the configured model"; that decision is not this rule's.
        return ""
    if is_local_repo(text) or known_local(text):
        return ""
    return (
        f"{text!r} cannot be loaded locally: a local server reads a bare name as a "
        "Hugging Face repository, which is why it ends in an authentication error. "
        "Set the LLM Provider to the hosted service that serves this model, or pick a "
        "local one (owner/name, or a .gguf file). The LLM Model and Correction LLM "
        "Model fields have to belong to the provider that is selected."
    )


def require_local_repo(model_id) -> str:
    """The id, or a RuntimeError naming the contradiction."""

    problem = local_model_problem(model_id)
    if problem:
        raise RuntimeError(problem)
    return str(model_id or "").strip()
