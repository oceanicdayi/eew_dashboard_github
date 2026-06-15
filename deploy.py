#!/usr/bin/env python3
"""Deploy this EEW dashboard repository to a Hugging Face Space.

The Hugging Face API can occasionally return 429 Too Many Requests during
preupload. This script retries uploads with exponential backoff so a temporary
rate limit does not fail the whole GitHub Actions run immediately.
"""
import os
import sys
import time
import subprocess

from huggingface_hub import HfApi

try:
    from huggingface_hub.errors import HfHubHTTPError
except Exception:  # pragma: no cover - compatibility with older hub versions
    HfHubHTTPError = Exception

SPACE_ID = os.environ.get("EEW_SPACE_ID", "oceanicdayi/Eew_dashboard")
HERE = os.path.dirname(os.path.abspath(__file__))
MAX_RETRIES = int(os.environ.get("EEW_DEPLOY_MAX_RETRIES", "6"))
BASE_WAIT_SECONDS = int(os.environ.get("EEW_DEPLOY_BASE_WAIT_SECONDS", "30"))

hf_token = os.environ.get("HF_TOKEN")
if not hf_token:
    sys.exit("Missing HF_TOKEN environment variable.")


def is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    return status_code == 429 or "429" in text or "too many requests" in text


def with_retry(label, fn):
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"{label}: attempt {attempt}/{MAX_RETRIES}")
            result = fn()
            print(f"{label}: success")
            return result
        except HfHubHTTPError as exc:
            last_error = exc
            if not is_rate_limit_error(exc) or attempt >= MAX_RETRIES:
                raise
            wait_seconds = BASE_WAIT_SECONDS * (2 ** (attempt - 1))
            print(f"{label}: HF rate limited. Waiting {wait_seconds}s before retry.")
            time.sleep(wait_seconds)
        except Exception as exc:
            last_error = exc
            if not is_rate_limit_error(exc) or attempt >= MAX_RETRIES:
                raise
            wait_seconds = BASE_WAIT_SECONDS * (2 ** (attempt - 1))
            print(f"{label}: rate limited. Waiting {wait_seconds}s before retry.")
            time.sleep(wait_seconds)
    raise RuntimeError(f"{label} failed after retries: {last_error}")


if os.environ.get("EEW_SKIP_PREDEPLOY_TEST"):
    print("Skipping pre-deploy test.")
else:
    print("Running pre-deploy replay test...")
    env = dict(os.environ, EEW_DATA_SOURCE="replay")
    result = subprocess.run([sys.executable, os.path.join(HERE, "test_loop.py")], env=env)
    if result.returncode != 0:
        sys.exit("Pre-deploy test failed; deployment stopped.")

api = HfApi(token=hf_token)
for fname in ["app.py", "requirements.txt", "test_loop.py"]:
    src = os.path.join(HERE, fname)
    with_retry(
        f"Upload {fname}",
        lambda src=src, fname=fname: api.upload_file(
            path_or_fileobj=src,
            path_in_repo=fname,
            repo_id=SPACE_ID,
            repo_type="space",
        ),
    )

with_retry(
    "Upload fixtures folder",
    lambda: api.upload_folder(
        folder_path=os.path.join(HERE, "fixtures"),
        path_in_repo="fixtures",
        repo_id=SPACE_ID,
        repo_type="space",
    ),
)
print(f"Deployment requested for https://huggingface.co/spaces/{SPACE_ID}")
