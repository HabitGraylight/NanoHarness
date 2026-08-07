import sys
from pathlib import Path


EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = EXAMPLE_ROOT.parents[1]

sys.path.insert(0, str(REPOSITORY_ROOT))
sys.path.insert(0, str(EXAMPLE_ROOT))

import pytest

from app.models import HermesJob


@pytest.fixture
def demo_job():
    return HermesJob.from_file(EXAMPLE_ROOT / "jobs" / "demo.yaml")
