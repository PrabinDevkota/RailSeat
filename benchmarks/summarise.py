"""Stack the experiment JSON into one table. Pandas is the reporting tool, not the database."""

import json
from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parent / "results"
frames = []
for name in ("allocation.json", "read_scaling.json", "index_impact.json", "failover.json"):
    payload = json.loads((OUT / name).read_text(encoding="utf-8"))
    rows = payload if isinstance(payload, list) else [payload]
    frame = pd.DataFrame(rows)
    frame.insert(0, "experiment", name.removesuffix(".json"))
    frames.append(frame)
table = pd.concat(frames, ignore_index=True)
table.to_csv(OUT / "summary.csv", index=False)
print(table.to_string(index=False))
