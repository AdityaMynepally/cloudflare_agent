"""Real Lighthouse performance scoring.

Feature 1 from the pitch: give clients a trusted, industry-standard
performance score instead of the homemade heuristic in `performance.py`.
Runs actual Lighthouse 3x mobile + 3x desktop against the homepage, takes
the median of each, and bands it against auto-industry norms.

Production version of `lighthouse_poc.py` — same retry/UTF-8 hardening,
wrapped so it never blocks the FastAPI event loop (each form factor's 3
sequential runs happen in one background thread via asyncio.to_thread;
kept sequential rather than parallelized so the runs stay CPU-uncontended,
same as the standalone POC).
"""

import asyncio
import json
import logging
import os
import shutil
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)

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

# Headless Chrome refuses to launch as root ("Running as root without
# --no-sandbox is not supported") unless told otherwise, which is exactly
# how containers (e.g. this app's own Docker image) run by default. Only
# relax the sandbox when we're actually root — keep the extra protection on
# dev machines where we're not. --disable-dev-shm-usage avoids crashes from
# containers' small default /dev/shm.
_CHROME_FLAGS = ["--headless=new"]
if hasattr(os, "geteuid") and os.geteuid() == 0:
    _CHROME_FLAGS += ["--no-sandbox", "--disable-dev-shm-usage"]
CHROME_FLAGS = " ".join(_CHROME_FLAGS)

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


def _run_lighthouse_once(url: str, form_factor: FormFactor) -> int:
    """Run one Lighthouse pass, return the performance score 0-100.

    Retries transient failures (e.g. Windows' chrome-launcher cleanup race)
    a few times before giving up. Synchronous — always call via a thread
    (see `run_lighthouse_audit`), never directly from async code.
    """
    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return _run_lighthouse_attempt(url, form_factor)
        except (RuntimeError, OSError, json.JSONDecodeError) as e:
            last_error = e
            if attempt < MAX_ATTEMPTS:
                delay = RETRY_BACKOFF_SECONDS * attempt
                logger.warning(
                    f"[Lighthouse] {form_factor} attempt {attempt} failed ({e}), retrying in {delay}s"
                )
                time.sleep(delay)

    raise last_error


def _run_lighthouse_attempt(url: str, form_factor: FormFactor) -> int:
    """One Lighthouse subprocess invocation, no retry logic."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        out_path = Path(tmp.name)

    cmd = [
        NPX_PATH, "--yes", "lighthouse@12", url,
        "--output=json", f"--output-path={out_path}",
        f"--chrome-flags={CHROME_FLAGS}",
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


def _run_form_factor_sync(url: str, form_factor: FormFactor) -> list[int]:
    """Run RUNS_PER_FORM_FACTOR sequential passes for one form factor."""
    return [_run_lighthouse_once(url, form_factor) for _ in range(RUNS_PER_FORM_FACTOR)]


async def run_lighthouse_audit(url: str, on_progress=None) -> Optional[dict]:
    """Run 3x mobile + 3x desktop Lighthouse passes without blocking the event loop.

    Each form factor's 3 runs happen sequentially inside one background
    thread (kept sequential to avoid resource-contention variance between
    concurrent Chrome instances — same reasoning as the standalone POC).

    `on_progress`, if given, is awaited as `await on_progress(form_factor, result_dict)`
    after each form factor finishes, letting the caller emit a progress event.

    Returns {"mobile": {runs, median, band}, "desktop": {runs, median, band}},
    or None if Node/npx isn't available (non-fatal — caller should just skip
    this phase, not fail the whole audit).
    """
    if NPX_PATH is None:
        logger.warning("[Lighthouse] npx not found on PATH — skipping Lighthouse phase")
        return None

    results: dict = {}
    for form_factor in ("mobile", "desktop"):
        runs = await asyncio.to_thread(_run_form_factor_sync, url, form_factor)
        median = round(statistics.median(runs))
        form_result = {"runs": runs, "median": median, "band": band_score(median, form_factor)}
        results[form_factor] = form_result
        if on_progress:
            await on_progress(form_factor, form_result)

    return results
