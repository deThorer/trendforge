"""
Building samples in ONE streaming pass over the file.

Uniform (Bernoulli) sampling
    Every row is kept independently with probability `rate`. Simple and fast, but a
    rare group (say 0.5% of rows) gets very few sampled rows, so its estimates are noisy.

Hybrid stratified sampling (the default)
    Rows are split into strata by one column (e.g. region). For each stratum we keep
      (a) a Bernoulli sample at `rate`, and
      (b) a reservoir sample of fixed size `min_rows` (Algorithm R).
    At the end, each stratum uses (a) if it has at least `min_rows` rows, otherwise (b).
    Both are uniform random samples within the stratum, so the estimators stay valid,
    and rare strata are guaranteed `min_rows` rows (or all their rows if they are smaller).
    Stratum sizes N_h are counted exactly during the pass.

`rate` is the single tuning parameter: higher rate -> more rows -> lower error.
"""
import pickle
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from trendforge.stream import DEFAULT_CHUNKSIZE, read_chunks

ALL = "__all__"


def _compact(df: pd.DataFrame) -> pd.DataFrame:
    """Store text columns as pandas 'category' (much less memory than Python strings)."""
    for col in df.columns:
        if df[col].dtype == object or str(df[col].dtype).startswith(("str", "string")):
            df[col] = df[col].astype("category")
    return df


@dataclass
class Sample:
    data: pd.DataFrame                    # sampled rows (+ a "_stratum" column)
    population: Dict[str, int]            # N_h: rows in the full file per stratum
    method: str
    rate: float
    strata_column: Optional[object]       # column name, list of names, or None
    source: str
    total_rows: int
    build_seconds: float
    meta: dict = field(default_factory=dict)

    @property
    def sizes(self) -> Dict[str, int]:
        return self.data["_stratum"].astype(str).value_counts().to_dict()

    def save(self, path):
        with open(path, "wb") as f:
            pickle.dump(self, f, protocol=pickle.HIGHEST_PROTOCOL)

    @staticmethod
    def load(path) -> "Sample":
        with open(path, "rb") as f:
            return pickle.load(f)

    def summary(self) -> str:
        return (f"{self.method} sample of {self.source}: {len(self.data):,} of {self.total_rows:,} rows "
                f"({len(self.data) / self.total_rows:.3%}), {len(self.population)} strata, "
                f"built in {self.build_seconds:.1f}s")


def build_uniform(path, rate: float, columns: Optional[List[str]] = None,
                  chunksize: int = DEFAULT_CHUNKSIZE, seed: int = 0) -> Sample:
    if not 0 < rate <= 1:
        raise ValueError("rate must be in (0, 1]")
    rng = np.random.default_rng(seed)
    start = time.perf_counter()
    kept, total = [], 0
    for chunk in read_chunks(path, columns=columns, chunksize=chunksize):
        total += len(chunk)
        kept.append(chunk[rng.random(len(chunk)) < rate])
    data = pd.concat(kept, ignore_index=True)
    data["_stratum"] = ALL
    data = _compact(data)
    return Sample(data=data, population={ALL: total}, method="uniform", rate=rate,
                  strata_column=None, source=str(path), total_rows=total,
                  build_seconds=time.perf_counter() - start)


class _Reservoir:
    """Fixed-size uniform sample of a stream (Algorithm R), updated a chunk at a time."""

    def __init__(self, capacity: int, rng):
        self.capacity = capacity
        self.rng = rng
        self.seen = 0
        self.rows: Optional[pd.DataFrame] = None

    def add(self, rows: pd.DataFrame):
        n = len(rows)
        if n == 0:
            return
        # 1) fill empty slots first
        free = max(self.capacity - self.seen, 0)
        if free:
            head = rows.iloc[:free]
            self.rows = head.copy() if self.rows is None else pd.concat([self.rows, head], ignore_index=True)
        # 2) item number i (1-based, over the whole stream) replaces a random slot with prob capacity / i
        rest = rows.iloc[free:]
        if len(rest):
            positions = np.arange(self.seen + free + 1, self.seen + n + 1)      # 1-based stream index
            slot = self.rng.integers(0, positions)                              # uniform in [0, i)
            hit = np.flatnonzero(slot < self.capacity)
            if len(hit):
                slots = slot[hit]
                # if two rows hit the same slot, the later one wins (same as processing one by one)
                _, last = np.unique(slots[::-1], return_index=True)
                keep = len(slots) - 1 - last
                self.rows.iloc[slots[keep]] = rest.iloc[hit[keep]].to_numpy()
        self.seen += n


def _key(value) -> str:
    """Stratum key as a string; composite strata look like 'Europe | C07'."""
    return " | ".join(map(str, value)) if isinstance(value, tuple) else str(value)


def build_stratified(path, strata_column, rate: float, min_rows: int = 2000,
                     columns: Optional[List[str]] = None, chunksize: int = DEFAULT_CHUNKSIZE,
                     seed: int = 0) -> Sample:
    """strata_column can be one column name or a list (composite strata, e.g. region x category)."""
    if not 0 < rate <= 1:
        raise ValueError("rate must be in (0, 1]")
    by = [strata_column] if isinstance(strata_column, str) else list(strata_column)
    if columns is not None:
        columns = list(columns) + [c for c in by if c not in columns]
    rng = np.random.default_rng(seed)
    start = time.perf_counter()

    bernoulli: Dict[str, List[pd.DataFrame]] = {}
    reservoirs: Dict[str, _Reservoir] = {}
    counts: Dict[str, int] = {}
    total = 0

    for chunk in read_chunks(path, columns=columns, chunksize=chunksize):
        total += len(chunk)
        keep = rng.random(len(chunk)) < rate
        groups = chunk.groupby(by if len(by) > 1 else by[0], sort=False).indices
        for key, idx in groups.items():
            key = _key(key)
            rows = chunk.iloc[idx]
            counts[key] = counts.get(key, 0) + len(idx)
            bernoulli.setdefault(key, []).append(rows[keep[idx]])
            reservoirs.setdefault(key, _Reservoir(min_rows, rng)).add(rows)

    parts, used = [], {}
    for key in counts:
        bern = pd.concat(bernoulli[key], ignore_index=True)
        if len(bern) >= min_rows:
            chosen, used[key] = bern, "bernoulli"
        else:
            chosen, used[key] = reservoirs[key].rows, "reservoir"
        parts.append(chosen.assign(_stratum=key))
    data = _compact(pd.concat(parts, ignore_index=True))
    return Sample(data=data, population=counts, method="stratified", rate=rate,
                  strata_column=strata_column, source=str(path), total_rows=total,
                  build_seconds=time.perf_counter() - start,
                  meta={"min_rows": min_rows, "strategy_per_stratum": used})
