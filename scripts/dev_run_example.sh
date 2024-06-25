#!/usr/bin/env bash
set -euo pipefail
python -m timetrace.cli run -- echo hello
python -m timetrace.cli session start demo
python -m timetrace.cli run -- echo inside
python -m timetrace.cli session stop
python -m timetrace.cli report --today
