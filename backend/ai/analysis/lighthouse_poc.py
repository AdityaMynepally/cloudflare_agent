"""Proof-of-concept: real Lighthouse performance scoring.

Feature 1 from the pitch: replace the homemade performance heuristic in
`performance.py` with the actual Lighthouse tool, run 3x mobile + 3x
desktop, median score, banded against auto-industry norms.

Standalone prototype - not wired into orchestrator.py yet. Requires
Node.js + npx on PATH (Lighthouse itself is fetched on demand via
`npx lighthouse`, no local install needed).

Usage:
    python3 lighthouse_poc.py https://example.com
"""

import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Literal

FormFactor = Literal["mobile", "desktop"]

RUNS_PER_FORM_FACTOR = 3

# chrome-launcher's post-run profile cleanup is flaky on Windows: it races
# against Chrome releasing its file handles and against antivirus actively
# scanning the temp profile, and loses often enough to fail with
# "EBUSY: resource busy or locked" even though the audit itself succeeded.
# It's transient, so a short retry clears it almost every time.
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2

# `shutil.which` resolves through PATHEXT on Windows (finds npx.cmd, not just
# "npx"), which `subprocess.run` cannot locate on its own without shell=True.
# Resolving it once up front lets us pass the real executable path and keep
# shell=False everywhere (avoids shell-quoting issues with the URL argument).
NPX_PATH = shutil.which("npx")

# Auto-industry performance bands, as specified in the pitch.
BANDS: dict[FormFactor, list[tuple[int, int, str]]] = {
    "mobile": [
        (0, 24, "subpar"),
        (25, 44, "average"),
        (45, 100, "excellent"),
    ],
    "desktop": [
        (0, 24, "subpar"),
        (25, 74, "average"),
        (75, 100, "excellent"),
    ],
}


def band_score(score: int, form_factor: FormFactor) -> str:
    for low, high, label in BANDS[form_factor]:
        if low <= score <= high:
            return label
    return "unknown"


def run_lighthouse_once(url: str, form_factor: FormFactor) -> int:
    """Run one Lighthouse pass, return the performance score 0-100.

    Retries transient failures (e.g. Windows' chrome-launcher cleanup race)
    a few times before giving up.
    """
    if NPX_PATH is None:
        raise RuntimeError(
            "npx not found on PATH — Node.js is required to run Lighthouse.\n"
            "Install it from https://nodejs.org (LTS), or:\n"
            "  Windows : winget install OpenJS.NodeJS.LTS\n"
            "  macOS   : brew install node\n"
            "  Linux   : curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - && sudo apt-get install -y nodejs\n"
            "Then open a new terminal and re-run this script."
        )

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return _run_lighthouse_attempt(url, form_factor)
        except (RuntimeError, OSError, json.JSONDecodeError) as e:
            last_error = e
            if attempt < MAX_ATTEMPTS:
                delay = RETRY_BACKOFF_SECONDS * attempt
                print(f"(attempt {attempt} failed, retrying in {delay}s) ", end="", flush=True)
                time.sleep(delay)

    raise last_error


def _run_lighthouse_attempt(url: str, form_factor: FormFactor) -> int:
    """One Lighthouse subprocess invocation, no retry logic."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        out_path = Path(tmp.name)

    cmd = [
        NPX_PATH, "--yes", "lighthouse@12", url,
        "--output=json", f"--output-path={out_path}",
        "--chrome-flags=--headless=new",
        "--only-categories=performance",
        "--quiet",
    ]
    if form_factor == "desktop":
        cmd.append("--preset=desktop")

    try:
        # Explicit UTF-8 everywhere: Lighthouse writes its report (and Chrome's
        # own stdout/stderr chatter) as UTF-8, but Windows' default locale
        # encoding is cp1252, not UTF-8 — decoding either stream without
        # forcing UTF-8 raises UnicodeDecodeError the moment the page/report
        # contains a byte sequence cp1252 can't represent.
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError(f"lighthouse exited {result.returncode}: {result.stderr[-2000:]}")

        report = json.loads(out_path.read_text(encoding="utf-8"))
        raw_score = report["categories"]["performance"]["score"]
        return round(raw_score * 100)
    finally:
        out_path.unlink(missing_ok=True)


def score_url(url: str) -> dict:
    """Run RUNS_PER_FORM_FACTOR passes for mobile and desktop, return medians + bands."""
    results: dict = {"url": url, "mobile": {}, "desktop": {}}

    for form_factor in ("mobile", "desktop"):
        runs = []
        for i in range(1, RUNS_PER_FORM_FACTOR + 1):
            print(f"  [{form_factor}] run {i}/{RUNS_PER_FORM_FACTOR}...", end=" ", flush=True)
            score = run_lighthouse_once(url, form_factor)
            print(f"score={score}")
            runs.append(score)

        median = round(statistics.median(runs))
        results[form_factor] = {
            "runs": runs,
            "median": median,
            "band": band_score(median, form_factor),
        }

    return results


def print_report(results: dict) -> None:
    print()
    print(f"Lighthouse performance report — {results['url']}")
    print("-" * 60)
    for form_factor in ("mobile", "desktop"):
        r = results[form_factor]
        print(f"{form_factor.capitalize():8} runs: {r['runs']}  "
              f"median: {r['median']:3}  band: {r['band'].upper()}")
    print("-" * 60)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 lighthouse_poc.py <url>")
        sys.exit(1)

    target_url = sys.argv[1]
    results = score_url(target_url)
    print_report(results)
