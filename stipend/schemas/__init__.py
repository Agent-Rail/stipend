"""Bundled JSON Schemas used by Stipend.

The schema files are shipped inside the wheel via the
``[tool.hatch.build.targets.wheel.force-include]`` table in pyproject.toml.
At import time we resolve them with :func:`importlib.resources` so they
remain accessible whether Stipend is installed normally or as an editable
install from source.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any


def load_policy_schema() -> dict[str, Any]:
    """Return the policy JSON schema as a Python dict.

    Cached implicitly by :mod:`importlib.resources`; cheap to call repeatedly.
    """
    schema_text = resources.files(__name__).joinpath("policy.schema.json").read_text(
        encoding="utf-8"
    )
    return json.loads(schema_text)
