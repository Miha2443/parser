"""JSON-safe report rows without converting missing numbers to zero."""
import math


def records(frame):
    return [{key: None if isinstance(value, float) and not math.isfinite(value) else value
             for key, value in row.items()} for row in frame.to_dict("records")]
