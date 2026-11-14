#!/usr/bin/env python3
"""Command-line demonstration of the G=5 bridge-split example."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sessions import Store


def main():
    store = Store(5)
    events = [
        ("a", "x", 0, 2),
        ("b", "x", 5, -2),
        ("c", "x", 10, 3),
    ]
    for event_id, entity, time, amount in events:
        store.put(event_id, entity, time, amount)

    before = store.read("x")
    split = store.delete("b")
    after = store.read("x")
    merged = store.put("b", "x", 5, -2)
    restored = store.read("x")

    print(
        json.dumps(
            {
                "G": 5,
                "events": [
                    {"id": item[0], "entity": item[1], "time": item[2], "amount": item[3]}
                    for item in events
                ],
                "bridge_session": before,
                "delete_b": split,
                "after_bridge_deletion": after,
                "reinsert_b": merged,
                "restored_session": restored,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
