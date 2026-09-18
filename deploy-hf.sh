#!/bin/zsh
# Free deploy on Hugging Face Spaces (no credit card).
# Run in Terminal.app:
#   ./deploy-hf.sh
#
# Prerequisites:
#   1) Free account: https://huggingface.co/join
#   2) Write token:  https://huggingface.co/settings/tokens

set -euo pipefail
cd "$(dirname "$0")"

SPACE_NAME="${HF_SPACE_NAME:-vera-ai-challenge}"

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi

echo "==> Installing huggingface_hub..."
.venv/bin/pip install -q -U huggingface_hub

HF=".venv/bin/hf"

echo "==> Login to Hugging Face"
echo "    Create a Write token at: https://huggingface.co/settings/tokens"
echo "    Then paste it below."
$HF auth login

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export SPACE_NAME
export VERA_TEAM_NAME="${VERA_TEAM_NAME:-Lovepreet}"
export VERA_TEAM_MEMBERS="${VERA_TEAM_MEMBERS:-Lovepreet Singh}"
export VERA_CONTACT_EMAIL="${VERA_CONTACT_EMAIL:-lovepreetsingh40888@gmail.com}"
export VERA_LLM_PROVIDER="${VERA_LLM_PROVIDER:-groq}"
export VERA_LLM_MODEL="${VERA_LLM_MODEL:-llama-3.3-70b-versatile}"
# VERA_LLM_API_KEY comes from .env if set

echo "==> Creating/updating Space and uploading bot..."
.venv/bin/python <<'PY'
import os
from pathlib import Path
from huggingface_hub import HfApi, whoami

api = HfApi()
user = whoami()["name"]
space = os.environ["SPACE_NAME"]
repo_id = f"{user}/{space}"
print(f"Logged in as: {user}")
print(f"Space: {repo_id}")

api.create_repo(
    repo_id=repo_id,
    repo_type="space",
    space_sdk="docker",
    private=False,
    exist_ok=True,
)

files = [
    "Dockerfile",
    "README.md",
    "requirements.txt",
    "bot.py",
    "composer.py",
    "handlers.py",
    "state.py",
]
root = Path(".")
for name in files:
    path = root / name
    if not path.exists():
        raise SystemExit(f"Missing file: {name}")
    api.upload_file(
        path_or_fileobj=str(path),
        path_in_repo=name,
        repo_id=repo_id,
        repo_type="space",
    )
    print(f"  uploaded {name}")

secrets = {
    "VERA_TEAM_NAME": os.environ.get("VERA_TEAM_NAME", "Lovepreet"),
    "VERA_TEAM_MEMBERS": os.environ.get("VERA_TEAM_MEMBERS", "Lovepreet Singh"),
    "VERA_CONTACT_EMAIL": os.environ.get("VERA_CONTACT_EMAIL", ""),
    "VERA_LLM_PROVIDER": os.environ.get("VERA_LLM_PROVIDER", "groq"),
    "VERA_LLM_MODEL": os.environ.get("VERA_LLM_MODEL", "llama-3.3-70b-versatile"),
}
key = os.environ.get("VERA_LLM_API_KEY")
if key:
    secrets["VERA_LLM_API_KEY"] = key

for k, v in secrets.items():
    if not v:
        continue
    api.add_space_secret(repo_id=repo_id, key=k, value=v)
    print(f"  secret set: {k}")

runtime = f"https://{user}-{space}.hf.space".replace("_", "-")
# HF convention: username-spacename with spaces repo underscores often kept;
# actual runtime host replaces '/' with '-' only.
runtime = f"https://{repo_id.replace('/', '-')}.hf.space"
print()
print("=" * 50)
print(f" Space page: https://huggingface.co/spaces/{repo_id}")
print(f" Bot URL:    {runtime}")
print("=" * 50)
print("Wait 1–3 minutes for the build, then check:")
print(f"  {runtime}/v1/healthz")
print()
print("Submit this base URL on Magicpin:")
print(f"  {runtime}")
Path("/tmp/hf_bot_url.txt").write_text(runtime + "\n")
Path("/tmp/hf_repo_id.txt").write_text(repo_id + "\n")
PY
