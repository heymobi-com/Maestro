"""Confirm a weights download before a job spends minutes fetching it."""
from __future__ import annotations

GIB = 1024 ** 3

# Every transformer checkpoint is a multi-gigabyte file, so a missing variant is
# always worth one question. Auxiliary assets (tokenizers, configs) are small
# and keep downloading silently.
CONFIRM_THRESHOLD_GIB = 1.0


def should_confirm_missing_variant(local_alternatives) -> bool:
    """Whether a missing checkpoint deserves a question.

    The exact variant is not on disk, so whatever gets downloaded is a full
    transformer file. Ask, and show the variant that *is* installed as the
    alternative when one exists.
    """

    return True


def describe_download(
    *,
    model_name: str,
    download_filename: str,
    quantization: str,
    local_alternatives=(),
) -> dict:
    """Build the user-facing question for an unconfirmed weights download."""

    alternatives = [
        {
            "filename": str(item.get("filename") or ""),
            "size_gb": float(item.get("size_gb") or 0.0),
        }
        for item in local_alternatives
        if isinstance(item, dict) and item.get("filename")
    ]
    lines = [
        f"{model_name} is not installed in the {quantization} variant Maestro is using, "
        f"so the next generation would download {download_filename}.",
    ]
    if alternatives:
        installed = ", ".join(
            f"{item['filename']} ({item['size_gb']:.1f} GB)" if item["size_gb"] else item["filename"]
            for item in alternatives
        )
        lines.append(f"You already have: {installed}.")
        lines.append(
            "To reuse it instead of downloading, cancel and set "
            "Settings → Performance → Transformer Quantization to match that file."
        )
    else:
        lines.append("This is the first download of this model and it takes several minutes.")
    message = " ".join(lines)
    return {
        "message": message,
        "model_name": model_name,
        "download_filename": download_filename,
        "quantization": quantization,
        "local_alternatives": alternatives,
    }
