"""Incremental event-to-session aggregation.

The module provides :class:`Store`, an in-memory warehouse that keeps events
ordered per entity and incrementally maintains their sessions.  Python's
standard library has no balanced ordered map, so randomized treaps are used
here.  All ordered operations therefore take expected logarithmic time.
"""

from collections import Counter
import random

__all__ = ["Store"]


class _Session:
    """Mutable internal representation of one session."""

    __slots__ = ("entity", "first_id", "start", "end", "count", "sum")

    def __init__(self, entity, first_id, start, end, count, total):
        self.entity = entity
        self.first_id = first_id
        self.start = start
        self.end = end
        self.count = count
        self.sum = total

    def snapshot(self):
        return {
            "entity": self.entity,
            "first_id": self.first_id,
            "start": self.start,
            "end": self.end,
            "count": self.count,
            "sum": self.sum,
        }


class _Event:
    """Global event record indexed by event id."""

    __slots__ = ("entity", "time", "amount")

    def __init__(self, entity, time, amount):
        self.entity = entity
        self.time = time
        self.amount = amount


class _EntityState:
    """Both ordered indexes belonging to one entity."""

    __slots__ = ("events", "sessions")

    def __init__(self):
        self.events = None
        self.sessions = None


class _EventNode:
    """Treap node keyed by ``(time, event_id)``."""

    __slots__ = ("key", "amount", "priority", "left", "right", "size", "total")

    def __init__(self, key, amount, priority):
        self.key = key
        self.amount = amount
        self.priority = priority
        self.left = None
        self.right = None
        self.size = 1
        self.total = amount


class _SessionNode:
    """Treap node keyed by a session's integer start time."""

    __slots__ = ("key", "session", "priority", "left", "right", "size")

    def __init__(self, key, session, priority):
        self.key = key
        self.session = session
        self.priority = priority
        self.left = None
        self.right = None
        self.size = 1


# ---------------------------------------------------------------------------
# Event treap
# ---------------------------------------------------------------------------


def _event_size(node):
    return node.size if node is not None else 0


def _event_total(node):
    return node.total if node is not None else 0


def _event_update(node):
    node.size = 1 + _event_size(node.left) + _event_size(node.right)
    node.total = (
        node.amount
        + _event_total(node.left)
        + _event_total(node.right)
    )


def _event_rotate_right(y):
    x = y.left
    y.left = x.right
    x.right = y
    _event_update(y)
    _event_update(x)
    return x


def _event_rotate_left(x):
    y = x.right
    x.right = y.left
    y.left = x
    _event_update(x)
    _event_update(y)
    return y


def _event_insert(root, key, amount, rng):
    if root is None:
        return _EventNode(key, amount, rng.getrandbits(64))

    if key < root.key:
        root.left = _event_insert(root.left, key, amount, rng)
        if root.left.priority > root.priority:
            root = _event_rotate_right(root)
    else:
        # Public methods ensure the key is not already present.
        root.right = _event_insert(root.right, key, amount, rng)
        if root.right.priority > root.priority:
            root = _event_rotate_left(root)

    _event_update(root)
    return root


def _event_delete(root, key):
    if root is None:
        return None

    if key < root.key:
        root.left = _event_delete(root.left, key)
    elif key > root.key:
        root.right = _event_delete(root.right, key)
    else:
        if root.left is None:
            return root.right
        if root.right is None:
            return root.left

        if root.left.priority > root.right.priority:
            root = _event_rotate_right(root)
            root.right = _event_delete(root.right, key)
        else:
            root = _event_rotate_left(root)
            root.left = _event_delete(root.left, key)

    _event_update(root)
    return root


def _event_find(root, key):
    while root is not None:
        if key == root.key:
            return root
        root = root.left if key < root.key else root.right
    return None


def _event_predecessor(root, key):
    """Largest event key strictly less than *key*."""
    best = None
    while root is not None:
        if root.key < key:
            best = root
            root = root.right
        else:
            root = root.left
    return best


def _event_successor(root, key):
    """Smallest event key strictly greater than *key*."""
    best = None
    while root is not None:
        if key < root.key:
            best = root
            root = root.left
        else:
            root = root.right
    return best


def _event_rank(root, key):
    """One-based rank of an existing key, i.e. the number of keys <= key."""
    rank = 0
    while root is not None:
        left_size = _event_size(root.left)
        if key < root.key:
            root = root.left
        elif key > root.key:
            rank += left_size + 1
            root = root.right
        else:
            return rank + left_size + 1
    return rank


def _event_prefix(root, k):
    """Return ``(count, amount_sum)`` for the first *k* events."""
    if k <= 0 or root is None:
        return 0, 0

    left_size = _event_size(root.left)
    if k <= left_size:
        return _event_prefix(root.left, k)

    count = left_size + 1
    total = _event_total(root.left) + root.amount
    if k == left_size + 1:
        return count, total

    right_count, right_total = _event_prefix(
        root.right, k - left_size - 1
    )
    return count + right_count, total + right_total


# ---------------------------------------------------------------------------
# Session treap
# ---------------------------------------------------------------------------


def _session_size(node):
    return node.size if node is not None else 0


def _session_update(node):
    node.size = 1 + _session_size(node.left) + _session_size(node.right)


def _session_rotate_right(y):
    x = y.left
    y.left = x.right
    x.right = y
    _session_update(y)
    _session_update(x)
    return x


def _session_rotate_left(x):
    y = x.right
    x.right = y.left
    y.left = x
    _session_update(x)
    _session_update(y)
    return y


def _session_insert(root, key, session, rng):
    if root is None:
        return _SessionNode(key, session, rng.getrandbits(64))

    if key < root.key:
        root.left = _session_insert(root.left, key, session, rng)
        if root.left.priority > root.priority:
            root = _session_rotate_right(root)
    else:
        root.right = _session_insert(root.right, key, session, rng)
        if root.right.priority > root.priority:
            root = _session_rotate_left(root)

    _session_update(root)
    return root


def _session_delete(root, key):
    if root is None:
        return None

    if key < root.key:
        root.left = _session_delete(root.left, key)
    elif key > root.key:
        root.right = _session_delete(root.right, key)
    else:
        if root.left is None:
            return root.right
        if root.right is None:
            return root.left

        if root.left.priority > root.right.priority:
            root = _session_rotate_right(root)
            root.right = _session_delete(root.right, key)
        else:
            root = _session_rotate_left(root)
            root.left = _session_delete(root.left, key)

    _session_update(root)
    return root


def _session_floor(root, time):
    """Session node with the greatest start time <= *time*."""
    best = None
    while root is not None:
        if time < root.key:
            root = root.left
        elif time > root.key:
            best = root
            root = root.right
        else:
            return root
    return best


def _session_snapshots(root):
    result = []
    stack = []
    while root is not None or stack:
        while root is not None:
            stack.append(root)
            root = root.left
        root = stack.pop()
        result.append(root.session.snapshot())
        root = root.right
    return result


class Store:
    """Maintain sessions for a dynamically changing event warehouse."""

    def __init__(self, G):
        if type(G) is not int or G < 0:
            raise ValueError("G must be a non-boolean, non-negative integer")

        self._G = G
        self._ids = {}
        self._entities = {}
        self._rng = random.Random()

    # -- validation ---------------------------------------------------------

    @staticmethod
    def _require_nonempty_string(value, name):
        if type(value) is not str or not value:
            raise ValueError(f"{name} must be a non-empty string")

    @staticmethod
    def _require_integer(value, name):
        if type(value) is not int:
            raise ValueError(f"{name} must be a non-boolean integer")

    def _validate_event(self, event_id, entity, time, amount):
        self._require_nonempty_string(event_id, "id")
        self._require_nonempty_string(entity, "entity")
        self._require_integer(time, "time")
        self._require_integer(amount, "amount")

    # -- small helpers ------------------------------------------------------

    @staticmethod
    def _session_key(session):
        return (
            session.entity,
            session.first_id,
            session.start,
            session.end,
            session.count,
            session.sum,
        )

    @staticmethod
    def _session_from_key(key):
        entity, first_id, start, end, count, total = key
        return {
            "entity": entity,
            "first_id": first_id,
            "start": start,
            "end": end,
            "count": count,
            "sum": total,
        }

    def _remove_session(self, state, session):
        state.sessions = _session_delete(state.sessions, session.start)

    def _add_session(self, state, session):
        state.sessions = _session_insert(
            state.sessions, session.start, session, self._rng
        )

    def _diff(self, removed_counter, added_counter):
        removed = []
        added = []
        for key in sorted(set(removed_counter) | set(added_counter)):
            removed_count = removed_counter.get(key, 0)
            added_count = added_counter.get(key, 0)
            if removed_count > added_count:
                removed.append(self._session_from_key(key))
            elif added_count > removed_count:
                added.append(self._session_from_key(key))
        return {"removed": removed, "added": added}

    # -- internal insertion/deletion ---------------------------------------

    def _insert_event(self, event_id, entity, time, amount):
        key = (time, event_id)
        state = self._entities.get(entity)

        if state is None:
            state = _EntityState()
            self._entities[entity] = state
            state.events = _event_insert(None, key, amount, self._rng)
            session = _Session(
                entity, event_id, time, time, 1, amount
            )
            self._add_session(state, session)
            self._ids[event_id] = _Event(entity, time, amount)
            return [], [session]

        predecessor = _event_predecessor(state.events, key)
        successor = _event_successor(state.events, key)
        state.events = _event_insert(
            state.events, key, amount, self._rng
        )
        self._ids[event_id] = _Event(entity, time, amount)

        def add_singleton():
            session = _Session(
                entity, event_id, time, time, 1, amount
            )
            self._add_session(state, session)
            return [], [session]

        if predecessor is None:
            if successor is None:
                return add_singleton()

            right_session = _session_floor(
                state.sessions, successor.key[0]
            ).session
            if successor.key[0] - time <= self._G:
                removed_key = self._session_key(right_session)
                self._remove_session(state, right_session)
                right_session.start = time
                right_session.first_id = event_id
                right_session.count += 1
                right_session.sum += amount
                self._add_session(state, right_session)
                return [removed_key], [right_session]
            return add_singleton()

        if successor is None:
            left_session = _session_floor(
                state.sessions, predecessor.key[0]
            ).session
            if time - predecessor.key[0] <= self._G:
                removed_key = self._session_key(left_session)
                left_session.end = time
                left_session.count += 1
                left_session.sum += amount
                return [removed_key], [left_session]
            return add_singleton()

        left_session = _session_floor(
            state.sessions, predecessor.key[0]
        ).session
        right_session = _session_floor(
            state.sessions, successor.key[0]
        ).session
        left_gap = time - predecessor.key[0]
        right_gap = successor.key[0] - time

        if left_gap <= self._G and right_gap <= self._G:
            if left_session is right_session:
                removed_key = self._session_key(left_session)
                left_session.count += 1
                left_session.sum += amount
                return [removed_key], [left_session]

            removed_left = self._session_key(left_session)
            removed_right = self._session_key(right_session)
            self._remove_session(state, left_session)
            self._remove_session(state, right_session)
            merged = _Session(
                entity,
                left_session.first_id,
                left_session.start,
                right_session.end,
                left_session.count + 1 + right_session.count,
                left_session.sum + amount + right_session.sum,
            )
            self._add_session(state, merged)
            return [removed_left, removed_right], [merged]

        if left_gap <= self._G:
            removed_key = self._session_key(left_session)
            left_session.end = time
            left_session.count += 1
            left_session.sum += amount
            return [removed_key], [left_session]

        if right_gap <= self._G:
            removed_key = self._session_key(right_session)
            self._remove_session(state, right_session)
            right_session.start = time
            right_session.first_id = event_id
            right_session.count += 1
            right_session.sum += amount
            self._add_session(state, right_session)
            return [removed_key], [right_session]

        return add_singleton()

    def _delete_event(self, event_id):
        record = self._ids[event_id]
        entity = record.entity
        time = record.time
        amount = record.amount
        key = (time, event_id)
        state = self._entities[entity]

        predecessor = _event_predecessor(state.events, key)
        successor = _event_successor(state.events, key)
        session = _session_floor(state.sessions, time).session
        removed_key = self._session_key(session)

        if session.count == 1:
            self._remove_session(state, session)
            state.events = _event_delete(state.events, key)
            del self._ids[event_id]
            if state.events is None:
                del self._entities[entity]
            return [removed_key], []

        if key == (session.start, session.first_id):
            # The event is the first event of a non-singleton session.  A
            # predecessor may exist, but it belongs to the previous session.
            self._remove_session(state, session)
            state.events = _event_delete(state.events, key)
            session.start = successor.key[0]
            session.first_id = successor.key[1]
            session.count -= 1
            session.sum -= amount
            self._add_session(state, session)
            del self._ids[event_id]
            return [removed_key], [session]

        if successor is None or successor.key[0] > session.end:
            # The event is a last-time event of a non-singleton session.
            state.events = _event_delete(state.events, key)
            session.end = predecessor.key[0]
            session.count -= 1
            session.sum -= amount
            del self._ids[event_id]
            return [removed_key], [session]

        if successor.key[0] - predecessor.key[0] <= self._G:
            # Removal does not break the session.
            state.events = _event_delete(state.events, key)
            session.count -= 1
            session.sum -= amount
            del self._ids[event_id]
            return [removed_key], [session]

        # The removed event is a bridge: split its session without scanning
        # either side.  The event treap's prefix sums give the left totals.
        start_rank = _event_rank(
            state.events, (session.start, session.first_id)
        )
        predecessor_rank = _event_rank(state.events, predecessor.key)
        _, before_sum = _event_prefix(state.events, start_rank - 1)
        _, through_predecessor_sum = _event_prefix(
            state.events, predecessor_rank
        )
        left_count = predecessor_rank - start_rank + 1
        left_sum = through_predecessor_sum - before_sum
        right_count = session.count - 1 - left_count
        right_sum = session.sum - amount - left_sum

        self._remove_session(state, session)
        state.events = _event_delete(state.events, key)

        left_session = _Session(
            entity,
            session.first_id,
            session.start,
            predecessor.key[0],
            left_count,
            left_sum,
        )
        right_session = _Session(
            entity,
            successor.key[1],
            successor.key[0],
            session.end,
            right_count,
            right_sum,
        )
        self._add_session(state, left_session)
        self._add_session(state, right_session)
        del self._ids[event_id]
        return [removed_key], [left_session, right_session]

    # -- public API ---------------------------------------------------------

    def put(self, event_id, entity, time, amount):
        """Insert an event or completely replace an existing event id."""
        self._validate_event(event_id, entity, time, amount)

        old = self._ids.get(event_id)
        if (
            old is not None
            and old.entity == entity
            and old.time == time
            and old.amount == amount
        ):
            return {"removed": [], "added": []}

        removed = Counter()
        added = Counter()

        if old is not None:
            old_removed, old_added = self._delete_event(event_id)
            removed.update(old_removed)
            added.update(self._session_key(s) for s in old_added)

        new_removed, new_added = self._insert_event(
            event_id, entity, time, amount
        )
        removed.update(new_removed)
        added.update(self._session_key(s) for s in new_added)
        return self._diff(removed, added)

    def delete(self, event_id):
        """Delete an event; deleting an unknown id is a no-op."""
        self._require_nonempty_string(event_id, "id")
        if event_id not in self._ids:
            return {"removed": [], "added": []}

        removed_keys, added_sessions = self._delete_event(event_id)
        removed = Counter(removed_keys)
        added = Counter(
            self._session_key(session) for session in added_sessions
        )
        return self._diff(removed, added)

    def read(self, entity):
        """Return an entity's sessions ordered by start time."""
        self._require_nonempty_string(entity, "entity")
        state = self._entities.get(entity)
        if state is None:
            return []
        return _session_snapshots(state.sessions)
