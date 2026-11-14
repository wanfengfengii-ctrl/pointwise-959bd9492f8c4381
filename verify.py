#!/usr/bin/env python3
"""Correctness checks for sessions.Store.

The random tests keep an independent flat event model.  After every mutation,
all sessions are recomputed from scratch and compared with the store.  The
operation diff is also checked as a before/after full-set difference.
"""

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sessions import Store


def naive_sessions(G, events):
    """Recompute every session independently from flat current events."""
    grouped = {}
    for event_id, (entity, time, amount) in events.items():
        grouped.setdefault(entity, []).append((time, event_id, amount))

    result = {}
    for entity, records in grouped.items():
        records.sort(key=lambda item: (item[0], item[1]))
        sessions = []
        current = None
        previous_time = None

        for time, event_id, amount in records:
            if current is None or time - previous_time > G:
                if current is not None:
                    sessions.append(current)
                current = {
                    "entity": entity,
                    "first_id": event_id,
                    "start": time,
                    "end": time,
                    "count": 1,
                    "sum": amount,
                }
            else:
                current["end"] = time
                current["count"] += 1
                current["sum"] += amount
            previous_time = time

        if current is not None:
            sessions.append(current)
        result[entity] = sessions
    return result


def canonical(session):
    return tuple(sorted(session.items()))


def session_set(model_sessions):
    return {
        canonical(session)
        for sessions in model_sessions.values()
        for session in sessions
    }


def expected_diff(before, after):
    before_set = session_set(before)
    after_set = session_set(after)
    removed = [dict(item) for item in sorted(before_set - after_set)]
    added = [dict(item) for item in sorted(after_set - before_set)]
    return {"removed": removed, "added": added}


def same_diff(actual, expected):
    actual_norm = {
        key: sorted(canonical(item) for item in value)
        for key, value in actual.items()
    }
    expected_norm = {
        key: sorted(canonical(item) for item in value)
        for key, value in expected.items()
    }
    return actual_norm == expected_norm


class SessionStoreTests(unittest.TestCase):
    def test_specified_bridge_and_split(self):
        store = Store(5)
        for event_id, time, amount in [
            ("a", 0, 2),
            ("b", 5, -2),
            ("c", 10, 3),
        ]:
            store.put(event_id, "x", time, amount)

        self.assertEqual(
            store.read("x"),
            [
                {
                    "entity": "x",
                    "first_id": "a",
                    "start": 0,
                    "end": 10,
                    "count": 3,
                    "sum": 3,
                }
            ],
        )

        diff = store.delete("b")
        self.assertEqual(
            diff,
            {
                "removed": [
                    {
                        "entity": "x",
                        "first_id": "a",
                        "start": 0,
                        "end": 10,
                        "count": 3,
                        "sum": 3,
                    }
                ],
                "added": [
                    {
                        "entity": "x",
                        "first_id": "a",
                        "start": 0,
                        "end": 0,
                        "count": 1,
                        "sum": 2,
                    },
                    {
                        "entity": "x",
                        "first_id": "c",
                        "start": 10,
                        "end": 10,
                        "count": 1,
                        "sum": 3,
                    },
                ],
            },
        )
        self.assertEqual(
            store.read("x"),
            [
                {
                    "entity": "x",
                    "first_id": "a",
                    "start": 0,
                    "end": 0,
                    "count": 1,
                    "sum": 2,
                },
                {
                    "entity": "x",
                    "first_id": "c",
                    "start": 10,
                    "end": 10,
                    "count": 1,
                    "sum": 3,
                },
            ],
        )

        reinsert = store.put("b", "x", 5, -2)
        self.assertEqual(len(reinsert["removed"]), 2)
        self.assertEqual(len(reinsert["added"]), 1)
        self.assertEqual(store.read("x")[0]["count"], 3)
        self.assertEqual(store.read("x")[0]["sum"], 3)

    def test_replacement_unknown_entity_and_noops(self):
        store = Store(2)
        self.assertEqual(
            store.put("1", "x", 0, 10),
            {
                "removed": [],
                "added": [
                    {
                        "entity": "x",
                        "first_id": "1",
                        "start": 0,
                        "end": 0,
                        "count": 1,
                        "sum": 10,
                    }
                ],
            },
        )
        self.assertEqual(
            store.put("1", "x", 0, 10), {"removed": [], "added": []}
        )
        self.assertEqual(store.delete("missing"), {"removed": [], "added": []})
        self.assertEqual(store.read("unknown"), [])

        replacement = store.put("1", "y", 5, -3)
        self.assertEqual(
            replacement["removed"],
            [
                {
                    "entity": "x",
                    "first_id": "1",
                    "start": 0,
                    "end": 0,
                    "count": 1,
                    "sum": 10,
                }
            ],
        )
        self.assertEqual(
            replacement["added"],
            [
                {
                    "entity": "y",
                    "first_id": "1",
                    "start": 5,
                    "end": 5,
                    "count": 1,
                    "sum": -3,
                }
            ],
        )
        self.assertEqual(store.read("x"), [])
        self.assertEqual(store.read("y")[0]["first_id"], "1")

    def test_zero_g_keeps_equal_times_together(self):
        store = Store(0)
        store.put("a", "x", 7, 2)
        store.put("b", "x", 7, 3)
        store.put("c", "x", 7, 4)
        store.put("d", "x", 8, 5)
        self.assertEqual(len(store.read("x")), 2)

        diff = store.delete("b")
        self.assertEqual(len(diff["removed"]), 1)
        self.assertEqual(len(diff["added"]), 1)
        self.assertEqual(store.read("x")[0]["first_id"], "a")
        self.assertEqual(store.read("x")[0]["count"], 2)
        self.assertEqual(store.read("x")[0]["sum"], 6)

        diff = store.delete("a")
        self.assertEqual(store.read("x")[0]["first_id"], "c")
        self.assertEqual(store.read("x")[0]["count"], 1)
        self.assertEqual(store.read("x")[0]["sum"], 4)

    def test_random_mutations_against_independent_recomputation(self):
        rng = random.Random(20261114)
        entities = ["e0", "e1", "e2", "e3"]

        for G in (0, 1, 2, 5):
            store = Store(G)
            events = {}
            next_id = 0

            for _ in range(3000):
                before = naive_sessions(G, events)
                choose_delete = events and rng.random() < 0.32

                if choose_delete:
                    event_id = rng.choice(list(events))
                    actual = store.delete(event_id)
                    del events[event_id]
                else:
                    if events and rng.random() < 0.45:
                        event_id = rng.choice(list(events))
                    else:
                        event_id = f"id-{next_id}"
                        next_id += 1

                    entity = rng.choice(entities)
                    # Negative times and amounts are legal.
                    time = rng.randint(-20, 20)
                    amount = rng.randint(-50, 50)
                    actual = store.put(event_id, entity, time, amount)
                    events[event_id] = (entity, time, amount)

                after = naive_sessions(G, events)
                expected = expected_diff(before, after)
                self.assertTrue(
                    same_diff(actual, expected),
                    msg=(G, actual, expected),
                )

                model = naive_sessions(G, events)
                for entity in entities:
                    self.assertEqual(
                        store.read(entity), model.get(entity, [])
                    )
                self.assertEqual(store.read("absent-entity"), [])

    def test_invalid_arguments_leave_state_unchanged(self):
        with self.assertRaises(ValueError):
            Store(True)
        for bad_G in (-1, 1.0, "5", None):
            with self.assertRaises(ValueError):
                Store(bad_G)

        store = Store(3)
        store.put("a", "x", 0, 5)
        store.put("b", "x", 4, 7)
        expected = store.read("x")

        invalid_puts = [
            ("", "x", 1, 1),
            ("c", "", 1, 1),
            (None, "x", 1, 1),
            ("c", "x", True, 1),
            ("c", "x", 1, False),
            ("c", "x", 1.0, 1),
            ("c", "x", "1", 1),
            ("c", "x", 1, 1.5),
        ]
        for args in invalid_puts:
            with self.assertRaises(ValueError):
                store.put(*args)
            self.assertEqual(store.read("x"), expected)

        for bad_id in ("", None, 3):
            with self.assertRaises(ValueError):
                store.delete(bad_id)
            self.assertEqual(store.read("x"), expected)

        with self.assertRaises(ValueError):
            store.read("")
        self.assertEqual(store.read("x"), expected)

    def test_returned_snapshots_are_copies(self):
        store = Store(2)
        store.put("a", "x", 0, 5)
        sessions = store.read("x")
        sessions[0]["sum"] = 999
        sessions.append({"bad": True})
        self.assertEqual(store.read("x")[0]["sum"], 5)

        diff = store.put("b", "x", 1, 2)
        diff["added"][0]["sum"] = 999
        self.assertEqual(store.read("x")[0]["sum"], 7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
