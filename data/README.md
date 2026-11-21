# Data

CSV files are generated, not committed:

```bash
python scripts/generate_data.py --rows 10_000_000  --out data/orders_10m.csv    # ~0.5 GB, ~30 s
python scripts/generate_data.py --rows 100_000_000 --out data/orders_100m.csv   # ~5.2 GB, ~5 min
python scripts/generate_data.py --rows 200_000_000 --out data/orders_200m.csv   # ~10.4 GB
```

Columns: `order_id, order_date, region, category, price, quantity, discount, revenue`.
Skewed on purpose: 8 regions (Oceania is 0.5% of rows), 50 categories with a Zipf-like
long tail, log-normal prices.
