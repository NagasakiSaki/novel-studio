"""SQLite FTS5 in-memory adapter; indexes point back into unchanged source text."""
import re
import sqlite3
import hashlib
import json
from collections import OrderedDict
from threading import RLock


def terms(text):
    values = re.findall(r"[a-zA-Z0-9_]{2,}|[\u3400-\u9fff]+", text.casefold())
    result = []
    for value in values:
        if re.fullmatch(r"[\u3400-\u9fff]+", value):
            result.extend(value[i:i + 2] for i in range(max(1, len(value) - 1)))
        else:
            result.append(value)
    return list(dict.fromkeys(result))


def chunks(record, max_chars=2400):
    for section in record["sections"]:
        start = section["start"]
        while start < section["end"]:
            end = min(section["end"], start + max_chars)
            if end < section["end"]:
                boundary = record["text"].rfind("\n", start + max_chars // 2, end)
                if boundary >= 0:
                    end = boundary + 1
            yield {"section_id": section["id"], "title": section["title"], "start": start, "end": end,
                   "text": record["text"][start:end]}
            start = end


def _index(record):
    pieces = list(chunks(record))
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    try:
        connection.execute("CREATE VIRTUAL TABLE source_index USING fts5(title, body)")
        connection.executemany("INSERT INTO source_index(rowid,title,body) VALUES (?,?,?)",
            [(i + 1, " ".join(terms(p["title"])), " ".join(terms(p["text"]))) for i, p in enumerate(pieces)])
        connection.commit()
        size = connection.execute('PRAGMA page_count').fetchone()[0] * connection.execute('PRAGMA page_size').fetchone()[0]
        size += sum(len(p['text'].encode('utf-8')) * 4 + len(p['title'].encode('utf-8')) * 4 + 1024 for p in pieces)
        return connection, pieces, size
    except BaseException:
        connection.close()
        raise


def _query(connection, pieces, query_terms, limit):
    expression = " OR ".join('"' + word.replace('"', '""') + '"' for word in query_terms)
    rows = connection.execute("SELECT rowid FROM source_index WHERE source_index MATCH ? ORDER BY bm25(source_index, 3.0, 1.0),rowid LIMIT ?", (expression, min(30, limit))).fetchall()
    return [dict(pieces[row[0] - 1]) for row in rows]


class SearchCache:
    """Per-project, bounded FTS indexes; source text is always freshly checked.

    SQLite connections are used only under this lock (including cross-thread
    reuse). No project/Core calls occur while holding it. Nothing is persisted.
    """
    def __init__(self, max_entries=4, max_bytes=8 * 1024 * 1024):
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self._entries, self._bytes = OrderedDict(), 0
        self._lock = RLock()
        self._counters = dict(hits=0, misses=0, evictions=0, bypasses=0, rebuilds=0)

    def clear(self):
        with self._lock:
            for connection, unused, size in self._entries.values():
                connection.close()
            self._entries.clear()
            self._bytes = 0

    def stats(self):
        with self._lock:
            return {**self._counters, 'entries': len(self._entries), 'accounted_bytes': self._bytes,
                    'max_entries': self.max_entries, 'max_bytes': self.max_bytes,
                    'byte_scope': 'SQLite pages plus conservative Python piece weights; not RSS'}

    def search(self, record, query_terms, limit):
        # Only text and section identity/bounds/title affect the existing index;
        # include material identity so two records never borrow provenance.
        source_sha = hashlib.sha256(record['text'].encode('utf-8')).hexdigest()
        sections = [{k: section[k] for k in ('id', 'title', 'start', 'end')} for section in record['sections']]
        raw = json.dumps([record.get('material_id'), source_sha, sections],
                         ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        key = hashlib.sha256(raw).hexdigest()
        with self._lock:
            if key in self._entries:
                connection, pieces, size = self._entries[key]
                try:
                    result = _query(connection, pieces, query_terms, limit)
                except sqlite3.DatabaseError:
                    connection.close()
                    del self._entries[key]
                    self._bytes -= size
                    self._counters['rebuilds'] += 1
                else:
                    self._entries.move_to_end(key)
                    self._counters['hits'] += 1
                    return result
            self._counters['misses'] += 1
            connection, pieces, size = _index(record)
            try:
                result = _query(connection, pieces, query_terms, limit)
                if size > self.max_bytes or len(raw) > self.max_bytes or self.max_entries < 1:
                    self._counters['bypasses'] += 1
                    return result
                while self._entries and (len(self._entries) >= self.max_entries or self._bytes + size > self.max_bytes):
                    unused_key, (old, unused_pieces, old_size) = self._entries.popitem(last=False)
                    old.close()
                    self._bytes -= old_size
                    self._counters['evictions'] += 1
                self._entries[key] = (connection, pieces, size)
                self._bytes += size
                connection = None
                return result
            finally:
                if connection is not None:
                    connection.close()


def search(record, query, limit=8, *, cache=None):
    query_terms = terms(query)[:80]
    if not query_terms:
        return []
    if cache is not None:
        return cache.search(record, query_terms, limit)
    connection, pieces, unused_size = _index(record)
    try:
        return _query(connection, pieces, query_terms, limit)
    finally:
        connection.close()
