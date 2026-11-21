"""
TrendForge engine: register CSV tables, build samples, run exact or approximate queries.

    from trendforge import Engine
    tf = Engine()
    tf.register("sales", "data/sales.csv")
    tf.build_sample("sales", rate=0.01, strata="region")
    tf.query("SELECT AVG(price) FROM sales GROUP BY region")          # approximate
    tf.query("SELECT AVG(price) FROM sales GROUP BY region", exact=True)
"""
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from trendforge.estimators import estimate
from trendforge.exact import exact_query
from trendforge.query import Query, QueryError, parse
from trendforge.sampler import Sample, build_stratified, build_uniform
from trendforge.stream import DEFAULT_CHUNKSIZE, check_columns, file_columns


@dataclass
class QueryResult:
    table: pd.DataFrame
    mode: str                 # "exact" or "approx"
    seconds: float
    rows_scanned: int
    query: Query

    def __repr__(self):
        return f"<{self.mode} result: {len(self.table)} rows in {self.seconds:.3f}s>\n{self.table}"


class Engine:
    def __init__(self, chunksize: int = DEFAULT_CHUNKSIZE):
        self.chunksize = chunksize
        self.tables: Dict[str, Path] = {}
        self.samples: Dict[str, Sample] = {}

    # ---------- tables
    def register(self, name: str, path) -> None:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)
        self.tables[name] = path

    def columns(self, name: str):
        return file_columns(self._path(name))

    def _path(self, name: str) -> Path:
        if name not in self.tables:
            raise QueryError(f"unknown table {name!r}; registered: {', '.join(self.tables) or 'none'}")
        return self.tables[name]

    # ---------- samples
    def build_sample(self, name: str, rate: float = 0.01, strata=None,
                     min_rows: int = 2000, columns: Optional[List[str]] = None, seed: int = 0) -> Sample:
        """
        One pass over the file.
        strata : None (uniform), a column name, or a list of columns (composite strata)
        columns: limit which columns are kept in the sample (faster build, smaller sample)
        """
        path = self._path(name)
        if strata:
            sample = build_stratified(path, strata, rate, min_rows=min_rows, columns=columns,
                                      chunksize=self.chunksize, seed=seed)
        else:
            sample = build_uniform(path, rate, columns=columns, chunksize=self.chunksize, seed=seed)
        self.samples[name] = sample
        return sample

    def load_sample(self, name: str, sample_path) -> Sample:
        self._path(name)
        self.samples[name] = Sample.load(sample_path)
        return self.samples[name]

    # ---------- queries
    def query(self, sql: str, exact: bool = False) -> QueryResult:
        q = parse(sql)
        path = self._path(q.table)
        check_columns(q, self.columns(q.table))
        start = time.perf_counter()
        if exact:
            table, rows = exact_query(path, q, chunksize=self.chunksize)
            mode = "exact"
        else:
            if q.table not in self.samples:
                raise QueryError(f"no sample for {q.table!r}: call build_sample() first or use exact=True")
            sample = self.samples[q.table]
            missing = [c for c in q.columns if c not in sample.data.columns]
            if missing:
                raise QueryError(f"sample does not contain {missing}; rebuild it with these columns or use exact=True")
            table, rows = estimate(sample, q), len(sample.data)
            mode = "approx"
        return QueryResult(table=table, mode=mode, seconds=time.perf_counter() - start,
                           rows_scanned=rows, query=q)
