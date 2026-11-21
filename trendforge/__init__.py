"""TrendForge: an approximate query processing (AQP) engine for large CSV files."""
from trendforge.engine import Engine, QueryResult
from trendforge.query import QueryError, parse

__all__ = ["Engine", "QueryResult", "QueryError", "parse"]
__version__ = "0.3.0"
