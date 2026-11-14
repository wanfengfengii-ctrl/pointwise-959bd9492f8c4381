# sessions.Store

`sessions.Store(G)` incrementally maintains event sessions in memory using
only the Python standard library.

## Rules

For one entity, events are ordered by `(time, id)`. Two adjacent events
belong to the same session when their time difference is at most `G`. A
session is represented as:

```python
{
    "entity": entity,
    "first_id": first_event_id,
    "start": first_time,
    "end": last_time,
    "count": event_count,
    "sum": amount_sum,
}
```

`put(id, entity, time, amount)` inserts an event or completely replaces the
event with the same globally unique id. `delete(id)` removes an event and is
a no-op for an unknown id. Both return:

```python
{"removed": [...old complete session dictionaries...],
 "added":   [...new complete session dictionaries...]}
```

The diff is the set difference between the complete session collections
before and after the mutation. `read(entity)` returns that entity's sessions
ordered by start time and returns `[]` for an unknown entity.

## Indexes and invariants

The store maintains two randomized treaps for each entity plus a global hash
table for event ids.

1. **Global id index (`_ids`)**: maps each unique id to its current entity,
   time and amount. This makes replacement and deletion lookup expected
   `O(1)`.
2. **Event treap**: ordered by the unique key `(time, id)`. Each subtree
   stores its event count and amount sum. These subtree aggregates calculate
   the two halves of a split without scanning either half.
3. **Session treap**: ordered by integer session start time and storing the
   mutable session record. The predecessor/successor events identify the at
   most two affected sessions; `floor(start)` locates a session without
   enumerating sessions.
4. **Partition invariant**: for an entity, session treap records partition
   all events in its event treap exactly once.
5. **Adjacency invariant**: adjacent events in one session have time gap
   `<= G`; adjacent events belonging to different sessions have time gap
   `> G`.
6. **Summary invariant**: every session's `count` and `sum` equal the
   subtree aggregates of exactly its event interval, and `start`/`end`
   match its first/last event times.
7. **Ordering invariant**: in-order event traversal is sorted by
   `(time, id)`; in-order session traversal is sorted by start time.

A mutation touches only the predecessor, successor, and their containing
sessions. Session boundary search, event insertion/deletion, session
insertion/deletion, and split aggregate lookup are expected `O(log N)`.
Expected amortized modification cost is therefore `O(log(N+2))`. Storage is
`O(N)`: each current event has one global record, one event node, and at
most one session node per session. `read` additionally costs output size.

## Correctness verification

Run:

```bash
python3 -I verify.py
```

`verify.py` independently keeps a flat event table and recomputes every
session from scratch after 3,000 random puts/deletes for each of
`G = 0, 1, 2, 5`. It checks:

- all maintained sessions against the independent recomputation;
- every returned modification diff against full before/after set differences;
- replacement, unknown id, unknown entity, and no-op behavior;
- invalid arguments and unchanged state after `ValueError`;
- returned dictionaries are copies.

The specified bridge example is also checked explicitly: with `G=5`, events
`a=(0,2)`, `b=(5,-2)`, and `c=(10,3)` form `[0,10]`, count `3`, sum `3`;
deleting `b` splits them into the two singleton sessions, and reinserting
`b` merges them again.

## Bridge demo

```bash
python3 -I demo.py
```

This prints the complete before/split/restored JSON for the required `G=5`
example.

## Long-session update measurement

```bash
python3 -I benchmark.py
```

The benchmark constructs one long session, deletes its middle bridge event,
then reinserts it. A representative local run gave:

| N | Split median (µs) | Merge median (µs) | Current B/event |
|---:|---:|---:|---:|
| 1,024 | 12.333 | 9.333 | 408.4 |
| 2,048 | 15.625 | 12.125 | 413.3 |
| 4,096 | 13.250 | 11.084 | 416.0 |
| 8,192 | 16.792 | 12.791 | 417.4 |
| 16,384 | 15.833 | 11.709 | 418.0 |
| 32,768 | 14.583 | 11.042 | 422.3 |
| 65,536 | 14.958 | 11.667 | 422.4 |
| 131,072 | 19.792 | 14.416 | 422.5 |

The corresponding log-log slopes of median update time against N were
`0.057` for split and `0.047` for merge in this run. Bytes per event remain
nearly constant as N grows. Median wall-clock values include short-run
scheduling noise; the structural guarantee remains expected logarithmic
because no code path scans all entities, events, or sessions to find
boundaries or summaries.
