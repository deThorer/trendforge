import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.generate_data import make_chunk  # noqa: E402


@pytest.fixture(scope="session")
def small_csv(tmp_path_factory):
    """20k-row orders file shared by the tests."""
    path = tmp_path_factory.mktemp("data") / "orders.csv"
    # seed differs from the sampler seeds on purpose: with the same seed, numpy's choice()
    # and random() consume the same uniform draws, so the "random" sample would line up
    # exactly with one region (found this the hard way)
    make_chunk(np.random.default_rng(12345), 0, 20_000).to_csv(path, index=False)
    return path
