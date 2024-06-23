#!/usr/bin/env bash
set -euo pipefail
python -m timetrace.cli run -- echo hello
python -m timetrace.cli report --today
