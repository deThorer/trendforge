"""
Streaming CSV reader. Files can be larger than RAM, so they are read in chunks
(default 1,000,000 rows) and only the columns a query needs are parsed.
"""
import operator
from typing import Iterator, List, Optional

import numpy as np
import pandas as pd

DEFAULT_CHUNKSIZE = 1_000_000

_OPS = {"=": operator.eq, "!=": operator.ne, "<": operator.lt,
        "<=": operator.le, ">": operator.gt, ">=": operator.ge}


def read_chunks(path, columns: Optional[List[str]] = None,
                chunksize: int = DEFAULT_CHUNKSIZE) -> Iterator[pd.DataFrame]:
    """Yield DataFrames of at most `chunksize` rows with only `columns` parsed."""
    reader = pd.read_csv(path, usecols=columns, chunksize=chunksize, engine="c", low_memory=True)
    for chunk in reader:
        yield chunk


def file_columns(path) -> List[str]:
    return list(pd.read_csv(path, nrows=0).columns)


def filter_mask(df: pd.DataFrame, filters) -> np.ndarray:
    """Boolean mask for a list of (col, op, value) conditions joined with AND."""
    mask = np.ones(len(df), dtype=bool)
    for col, op, value in filters:
        if col not in df.columns:
            raise KeyError(f"unknown column in WHERE: {col}")
        mask &= _OPS[op](df[col], value).to_numpy()
    return mask


def check_columns(query, available: List[str]):
    missing = [c for c in query.columns if c not in available]
    if missing:
        raise KeyError(f"unknown column(s): {', '.join(missing)}. Available: {', '.join(available)}")
