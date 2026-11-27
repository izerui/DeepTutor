#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
source .venv/bin/activate
exec .venv/bin/deeptutor start --home . --dev
