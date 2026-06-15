#!/usr/bin/env python3
"""Deploy this EEW dashboard repository to a Hugging Face Space."""
import os
import sys
import subprocess
from huggingface_hub import HfApi

SPACE_ID = os.environ.get("EEW_SPACE_ID", "oceanicdayi/Eew_dashboard")
HERE = os.path.dirname(os.path.abspath(__file__))

hf_token = os.environ.get("HF_TOKEN")
if not hf_token:
    sys.exit("Missing HF_TOKEN environment variable.")

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
    api.upload_file(
        path_or_fileobj=os.path.join(HERE, fname),
        path_in_repo=fname,
        repo_id=SPACE_ID,
        repo_type="space",
    )
    print(f"Uploaded {fname}")

api.upload_folder(
    folder_path=os.path.join(HERE, "fixtures"),
    path_in_repo="fixtures",
    repo_id=SPACE_ID,
    repo_type="space",
)
print(f"Deployment requested for https://huggingface.co/spaces/{SPACE_ID}")
