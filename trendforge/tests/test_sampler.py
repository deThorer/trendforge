import numpy as np
import pandas as pd

from trendforge.sampler import _Reservoir, build_stratified, build_uniform


def test_reservoir_keeps_everything_if_stream_is_small():
    r = _Reservoir(100, np.random.default_rng(0))
    r.add(pd.DataFrame({"x": range(30)}))
    r.add(pd.DataFrame({"x": range(30, 60)}))
    assert sorted(r.rows["x"]) == list(range(60))


def test_reservoir_is_uniform():
    # 100 items streamed in chunks of 7, keep 10: each item should be kept ~10% of the time
    counts = np.zeros(100)
    rng = np.random.default_rng(1)
    runs = 3000
    for _ in range(runs):
        r = _Reservoir(10, rng)
        for start in range(0, 100, 7):
            r.add(pd.DataFrame({"x": range(start, min(start + 7, 100))}))
        counts[r.rows["x"].to_numpy()] += 1
    freq = counts / runs
    assert abs(freq.mean() - 0.10) < 1e-9          # exactly 10 kept per run
    assert freq.min() > 0.075 and freq.max() < 0.125


def test_uniform_rate(small_csv):
    s = build_uniform(small_csv, 0.1, chunksize=3_000, seed=1)
    assert s.total_rows == 20_000
    assert 1_700 < len(s.data) < 2_300
    assert s.population == {"__all__": 20_000}


def test_stratified_guarantees_min_rows(small_csv):
    df = pd.read_csv(small_csv)
    s = build_stratified(small_csv, "region", rate=0.01, min_rows=150, chunksize=3_000, seed=1)
    true_counts = df.region.value_counts().to_dict()
    assert s.population == true_counts                     # stratum sizes counted exactly
    for region, n in s.sizes.items():
        assert n >= min(150, true_counts[region])


def test_composite_strata(small_csv):
    s = build_stratified(small_csv, ["region", "category"], rate=0.05, min_rows=5, chunksize=5_000)
    assert all(" | " in k for k in s.population)
    assert sum(s.population.values()) == 20_000
