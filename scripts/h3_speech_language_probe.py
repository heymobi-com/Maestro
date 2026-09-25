"""Measure whether MiniMax H3 speaks a non-English line intelligibly.

The question this answers: when H3 *generates* speech (rather than lip-syncing
to a soundtrack), does a Spanish line come back as Spanish, or as something
unintelligible? A real project's clips could not answer it -- their audio was
the project's own song, so no H3 speech was ever generated in them (measured:
the clips' audio matches the ACE-Step song word for word).

Three probes are generated through the running backend, all audio-only, so a
result costs seconds of GPU instead of a video render:

  1. tagged    ``<d>[Spanish] <line></d>``      -> the intended path
  2. untagged  the same line with no language   -> what the English default
                 tag does (the H3 Voice Audio path labels untagged text
                 ``[English]``, so Spanish words get English phonetics)
  3. english   an English control line          -> the ceiling, for comparison

Each result is transcribed with Whisper (the same model the H3 audio path uses
for its own trimming) and compared against the words that were asked for, so the
verdict is a number rather than an impression.

Usage:
    python scripts/h3_speech_language_probe.py --list
    python scripts/h3_speech_language_probe.py                 # all three probes
    python scripts/h3_speech_language_probe.py --file path.wav # transcribe only

The backend must be running for the probes; ``--file`` works offline.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))

# Enough for intonation and a couple of clauses: shorter lines give the model
# less chance to drift, and the question is the language, not the content.
SPANISH_LINE = "La dignidad humana no se negocia, y eso es lo que defendemos hoy."
ENGLISH_LINE = "Human dignity is not negotiable, and that is what we defend today."

PROBES = (
    {
        "name": "tagged-spanish",
        "prompt": f"Speaker 1: [Spanish] {SPANISH_LINE}",
        "expected": SPANISH_LINE,
        "language": "es",
    },
    {
        "name": "untagged-spanish",
        # No language direction: this is the case the tool's own default turns
        # into [English] while the words stay Spanish.
        "prompt": f"Speaker 1: {SPANISH_LINE}",
        "expected": SPANISH_LINE,
        "language": "es",
    },
    {
        "name": "english-control",
        "prompt": f"Speaker 1: [English] {ENGLISH_LINE}",
        "expected": ENGLISH_LINE,
        "language": "en",
    },
)


def _normalized_words(text: str) -> list[str]:
    decomposed = unicodedata.normalize("NFD", str(text))
    plain = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return re.findall(r"[a-z0-9]+", plain.casefold())


def _coverage(expected: str, heard: str) -> float:
    wanted = _normalized_words(expected)
    if not wanted:
        return 0.0
    got = set(_normalized_words(heard))
    return sum(word in got for word in wanted) / len(wanted)


def _strip_directions(line: str) -> str:
    return re.sub(r"\]\s*", " ", re.sub(r"^\s*\[[^\]]*\]", "", line)).strip()


def _tagged_lines(prompt: str) -> list[str]:
    return [_strip_directions(line) for line in re.findall(r"<d>(.*?)</d>", prompt or "", re.S)]


def _whisper():
    from faster_whisper import WhisperModel

    cache_dir = os.path.join(_ROOT, "app", "ckpts", "whisper")
    return WhisperModel("small", device="cpu", compute_type="int8", download_root=cache_dir)


def _transcribe(media_path: str, language: str | None):
    model = _whisper()
    segments, info = model.transcribe(media_path, language=language, beam_size=5, vad_filter=True)
    heard = " ".join(segment.text.strip() for segment in segments).strip()
    return heard, info


def _post(base_url: str, path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _get(base_url: str, path: str) -> dict:
    with urllib.request.urlopen(base_url.rstrip("/") + path, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _pick_output(status: dict) -> str | None:
    for key in ("output_filename", "filename", "output", "file", "outputs"):
        value = status.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, list) and value and isinstance(value[0], str):
            return value[0]
    return None


def _resolve_output(name: str) -> str | None:
    if os.path.isabs(name) and os.path.isfile(name):
        return name
    candidates = glob.glob(os.path.join(_ROOT, "app", "outputs", "**", os.path.basename(name)), recursive=True)
    return candidates[0] if candidates else None


def _generate(base_url: str, prompt: str, seconds: float, timeout_s: float) -> str:
    job = _post(base_url, "/api/v1/generate", {
        "model_type": "minimax_h3_voice_audio",
        "prompt": prompt,
        "duration_seconds": seconds,
        "num_inference_steps": 20,
    })
    job_id = job.get("job_id") or job.get("id") or ""
    if not job_id:
        raise RuntimeError(f"the backend did not return a job id: {job}")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = _get(base_url, f"/api/v1/status/{job_id}")
        state = str(status.get("status") or "").casefold()
        if state in ("completed", "complete", "done", "success"):
            name = _pick_output(status)
            if not name:
                raise RuntimeError(f"completed job has no output filename: {status}")
            path = _resolve_output(name)
            if not path:
                raise RuntimeError(f"output {name!r} was not found under app/outputs")
            return path
        if state in ("failed", "error", "cancelled", "canceled"):
            raise RuntimeError(f"job {job_id} ended as {state}: {status.get('message') or status}")
        time.sleep(3)
    raise RuntimeError(f"job {job_id} did not finish within {timeout_s:.0f}s")


def _report(name: str, expected: str, media_path: str, language: str | None) -> dict:
    heard, info = _transcribe(media_path, language)
    coverage = _coverage(expected, heard)
    print("=" * 100)
    print(f"{name}  ({os.path.basename(media_path)})")
    print(f"  pedido      : {expected}")
    print(f"  oido        : {heard or '(no speech detected)'}")
    print(f"  cobertura   : {coverage:.0%} of the requested words were recognised")
    print(f"  detectado   : {info.language} (p={info.language_probability:.2f})")
    return {
        "probe": name,
        "file": media_path,
        "expected": expected,
        "heard": heard,
        "coverage": round(coverage, 3),
        "detected_language": info.language,
        "detected_probability": round(float(info.language_probability), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("MAESTRO_URL", "http://127.0.0.1:42000"))
    parser.add_argument("--file", help="transcribe an existing audio or video file and stop")
    parser.add_argument("--language", default="es", help="language hint for --file (default es)")
    parser.add_argument("--seconds", type=float, default=15.0, help="requested audio duration per probe")
    parser.add_argument("--timeout", type=float, default=900.0, help="seconds to wait per generation")
    parser.add_argument("--json", help="write the measurements to this JSON file")
    parser.add_argument("--list", action="store_true", help="print the probes and exit")
    args = parser.parse_args()

    if args.list:
        for probe in PROBES:
            print(f"{probe['name']}: {probe['prompt']}")
        return 0

    if args.file:
        expected = SPANISH_LINE
        prompt = _tagged_lines(str(_read_prompt_sidecar(args.file)))
        if prompt:
            expected = " ".join(prompt)
        results = [_report(os.path.basename(args.file), expected, args.file, args.language or None)]
    else:
        results = []
        for probe in PROBES:
            media = _generate(args.base_url, probe["prompt"], args.seconds, args.timeout)
            results.append(_report(probe["name"], probe["expected"], media, probe["language"]))

    print("=" * 100)
    for result in results:
        print(f"{result['probe']:<18} coverage {result['coverage']:.0%}  detected {result['detected_language']}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as writer:
            json.dump(results, writer, ensure_ascii=False, indent=2)
        print(f"written: {args.json}")
    return 0


def _read_prompt_sidecar(media_path: str):
    """The prompt beside a rendered file, when Maestro wrote one."""

    for candidate in (os.path.splitext(media_path)[0] + ".meta.json", media_path + ".meta.json"):
        if not os.path.isfile(candidate):
            continue
        try:
            data = json.load(open(candidate, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        params = data.get("params") if isinstance(data, dict) else None
        if isinstance(params, dict):
            return params.get("prompt") or ""
    return ""


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.URLError as error:
        print(f"the backend is not reachable: {error}", file=sys.stderr)
        raise SystemExit(2)
