from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Simple, transparent categorization rules for v1:
# - Deterministic and easy to adjust later.
# - Based on common command prefixes / keywords.

TEST_PREFIXES = {"pytest", "nosetests", "tox", "npm", "pnpm", "yarn", "mvn", "gradle", "dotnet", "go", "cargo"}
BUILD_KEYWORDS = {"build", "compile", "package", "bundle"}
TEST_KEYWORDS = {"test", "tests"}
LINT_KEYWORDS = {"lint", "format", "fmt", "ruff", "flake8", "black", "prettier", "eslint"}
GIT_PREFIXES = {"git"}
DOCKER_PREFIXES = {"docker", "podman"}

def categorize(command_str: str) -> str:
    """Return a broad category label for a command string."""
    if not command_str:
        return "other"
    first = command_str.split()[0].strip("'"")
    low = command_str.lower()

    if first in GIT_PREFIXES:
        return "git"
    if first in DOCKER_PREFIXES:
        return "container"

    # Testing/build tools often share a single entrypoint
    if first in {"npm", "pnpm", "yarn"}:
        if " test" in low or low.endswith(" test"):
            return "testing"
        if any(f" {k}" in low for k in LINT_KEYWORDS):
            return "lint"
        if any(f" {k}" in low for k in BUILD_KEYWORDS):
            return "build"
        return "node"
    if first in {"mvn", "gradle", "dotnet"}:
        if any(k in low for k in TEST_KEYWORDS):
            return "testing"
        if any(k in low for k in BUILD_KEYWORDS):
            return "build"
        return "build"
    if first in {"pytest", "tox", "nosetests"}:
        return "testing"
    if any(k in low for k in LINT_KEYWORDS):
        return "lint"
    if any(k in low for k in TEST_KEYWORDS):
        return "testing"
    if any(k in low for k in BUILD_KEYWORDS):
        return "build"

    return "other"
