# TrendForge design notes

## Goal
Answer aggregate queries (COUNT / SUM / AVG with filters and GROUP BY) on CSV files that
are too big to load into memory, fast enough to explore interactively, and say how
accurate each answer is.

## Architecture

```
                 ┌──────────────┐
  SQL string ──▶ │  query.py    │  regex parser → Query(aggregates, filters, group_by)
                 └──────┬───────┘
                        │
              ┌─────────┴──────────┐
              ▼                    ▼
      ┌──────────────┐     ┌────────────────┐
      │  exact.py    │     │ estimators.py  │  estimates + 95% CI from the sample
      │ full stream  │     └───────▲────────┘
      └──────▲───────┘             │
             │              ┌──────┴───────┐
             │              │  sampler.py  │  one streaming pass → Sample (in memory / pickle)
             │              └──────▲───────┘
             └──────────┬──────────┘
                 ┌──────┴───────┐
                 │  stream.py   │  pandas read_csv(chunksize=1M, usecols=needed columns)
                 └──────────────┘
                        ▲
                   big CSV file

  engine.py ties these together; cli.py and app/streamlit_app.py are the front ends.
```

## Streaming (memory)
- `read_csv(chunksize=1_000_000, usecols=...)` → only one chunk in memory at a time,
  and only the columns the query uses are converted.
- Exact queries keep **partial aggregates** per chunk (sum, count per group) and merge
  them. AVG = total sum / total count (averaging chunk averages would be wrong).
- Memory is O(chunk size + number of groups), independent of file size.
- Samples store text columns as pandas `category`: a 500k-row sample is ~17 MB.

## Sampling
**Uniform (Bernoulli):** keep each row with probability `rate`. One pass, trivially
parallel, but rare groups get few rows. Error ∝ 1/√(sampled rows in the group).

**Hybrid stratified** (default): per stratum, keep a Bernoulli sample *and* a reservoir
of `min_rows` rows (Algorithm R). At the end, use the Bernoulli sample if it reached
`min_rows`, else the reservoir. Both are simple random samples of the stratum, so
the same estimators apply; rare strata are guaranteed rows. Stratum sizes N_h are
counted exactly during the pass, so COUNT per stratum is exact.

*Vectorised reservoir:* for a chunk, item number i replaces slot `randint(0, i)` if it
is < capacity. When two items in one chunk hit the same slot the later one wins, which
gives the same result as processing items one by one. Tested for uniformity.

*Composite strata:* `strata=["region", "category"]` stratifies on combinations. More
strata = more guaranteed coverage, but a bigger sample and a slower build
(~2x on 400 strata in my tests).

## Estimators (estimators.py)
Per stratum h: N_h rows in the file, n_h in the sample, f_h = n_h/N_h.

- Total of Y: T̂ = Σ_h N_h · ȳ_h,  Var = Σ_h N_h² (1 − f_h) s_h² / n_h
- COUNT: Y = indicator(row matches WHERE and group); SUM: Y = indicator · x
- AVG: ratio R̂ = SUM/COUNT; variance by linearisation with Z = indicator · (x − R̂)
- 95% CI = ±1.96 √Var

The (1 − f_h) finite population correction makes the CI exactly 0 when the whole
stratum is in the sample.

All computed from per-(stratum, group) Σy and Σy², so it is one groupby, no loops.

## Validation
- Unit tests: parser, exact engine vs plain pandas (with odd chunk sizes), reservoir
  uniformity, estimator = exact answer at rate 1, CI checks.
- Empirical CI coverage over 60 repeated samples: ~93-94% for groups with >100 sample rows;
  lower (73-88%) for groups with <30 rows of a skewed column like revenue, because the
  normal approximation breaks down. The engine returns `sample_rows` and warns below 30.

## Known limitations
- Highly selective filters on rare groups (e.g. `price > 100` inside a rare category):
  few sample rows match, so the error is large whatever the sampling method. The warning
  tells the user to run exact.
- No joins, no ORDER BY / LIMIT, one GROUP BY column, no expressions in aggregates.
- CSV parsing is the bottleneck (single-threaded pandas C parser). Parquet would cut the
  build pass time a lot (needs pyarrow).
- Samples go stale if the file changes (no incremental maintenance).
