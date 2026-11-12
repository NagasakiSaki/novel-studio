"""Bounded process-local derived data. Callers supply freshly verified hashes.

No file I/O, credentials, persistence or authority lives here. Factories must be
pure computations: cache locks never acquire project/Core locks.
"""
from collections import OrderedDict
from copy import deepcopy
from threading import RLock


class DerivedCache:
    def __init__(self, max_entries=4096, max_bytes=8 * 1024 * 1024):
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self._entries, self._bytes = OrderedDict(), 0
        self._lock = RLock()
        self._counters = dict(hits=0, misses=0, evictions=0, bypasses=0)

    def get(self, key, factory, *, weight):
        with self._lock:
            if key in self._entries:
                self._counters['hits'] += 1
                value, unused = self._entries[key]
                self._entries.move_to_end(key)
                return deepcopy(value)
            self._counters['misses'] += 1
            value = factory()
            if weight > self.max_bytes or self.max_entries < 1:
                self._counters['bypasses'] += 1
                return value
            while self._entries and (len(self._entries) >= self.max_entries or self._bytes + weight > self.max_bytes):
                unused_key, (unused_value, old_weight) = self._entries.popitem(last=False)
                self._bytes -= old_weight
                self._counters['evictions'] += 1
            self._entries[key] = (deepcopy(value), weight)
            self._bytes += weight
            return value

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def stats(self):
        with self._lock:
            return {**self._counters, 'entries': len(self._entries), 'accounted_bytes': self._bytes,
                    'max_entries': self.max_entries, 'max_bytes': self.max_bytes,
                    'byte_scope': 'conservative admission weights; not total process RSS'}
