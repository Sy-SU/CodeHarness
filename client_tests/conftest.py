from __future__ import annotations

import json
from pathlib import Path

import pytest


FIXTURES = Path(__file__).parent / "fixtures" / "protocol"


@pytest.fixture()
def protocol_fixture():
    def load(name: str):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return load
