"""Persistent, explicitly registered, identity-scoped memory for MILO.

``MemoryStore(path)`` is a context manager. IDs are opaque strings. Calling
``create_person(name)`` always registers a NEW identity, even for a repeated
name; text and face matches never register, merge, or attach identities.
``new_session()`` creates a separate anonymous scope. A session's identity is
immutable. Registered sessions can retrieve the same person's relevant memory.

Only call ``put_fact`` for facts/preferences the user explicitly asked to save.
``remember`` and ``summarize`` never promote conversation to facts. The default
summary is a labeled extract of user utterances, with no interpretation. A
caller-supplied summary must follow the same no-inference policy.

``context`` returns bounded JSON lines containing untrusted data, not prompt
instructions. It selects relevant facts/events/summaries/turns and a small
current-session tail; it does not inject entire histories. Token limits use a
conservative one-token-per-UTF-8-byte bound for byte-based tokenizers, not an
approximate characters/4 estimate. Use the byte limit for other tokenizers.

Face storage accepts vectors, never photos. Matching is advisory only: an
unknown result never exposes a candidate identity. Defaults are conservative
but must be calibrated for the chosen embedding model and environment.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import sqlite3
import struct
import threading
import time
from dataclasses import dataclass
from typing import Iterable
from uuid import uuid4


_SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS people (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    person_id TEXT REFERENCES people(id) ON DELETE CASCADE,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_person ON sessions(person_id);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('turn', 'fact', 'event', 'summary')),
    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    person_id TEXT REFERENCES people(id) ON DELETE CASCADE,
    key TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    user_text TEXT,
    assistant_text TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    CHECK ((kind = 'fact' AND person_id IS NOT NULL AND session_id IS NULL
            AND length(key) > 0 AND user_text IS NULL AND assistant_text IS NULL)
        OR (kind != 'fact' AND person_id IS NULL AND session_id IS NOT NULL)),
    CHECK ((kind = 'turn' AND user_text IS NOT NULL AND assistant_text IS NOT NULL)
        OR (kind != 'turn' AND user_text IS NULL AND assistant_text IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS fact_key
    ON documents(person_id, key) WHERE kind = 'fact';
CREATE INDEX IF NOT EXISTS documents_session
    ON documents(session_id, kind, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS documents_person ON documents(person_id);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    key, text, content='documents', content_rowid='id', tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS documents_insert AFTER INSERT ON documents BEGIN
    INSERT INTO memory_fts(rowid, key, text) VALUES (new.id, new.key, new.text);
END;
CREATE TRIGGER IF NOT EXISTS documents_delete AFTER DELETE ON documents BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, key, text)
        VALUES ('delete', old.id, old.key, old.text);
END;
CREATE TRIGGER IF NOT EXISTS documents_update AFTER UPDATE ON documents BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, key, text)
        VALUES ('delete', old.id, old.key, old.text);
    INSERT INTO memory_fts(rowid, key, text) VALUES (new.id, new.key, new.text);
END;
CREATE TABLE IF NOT EXISTS embedding_models (
    model_id TEXT PRIMARY KEY,
    dimensions INTEGER NOT NULL CHECK(dimensions BETWEEN 1 AND 4096),
    UNIQUE(model_id, dimensions)
);
CREATE TABLE IF NOT EXISTS face_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    model_id TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector BLOB NOT NULL CHECK(length(vector) = dimensions * 8),
    created_at REAL NOT NULL,
    FOREIGN KEY(model_id, dimensions)
        REFERENCES embedding_models(model_id, dimensions)
);
CREATE INDEX IF NOT EXISTS face_person_model ON face_embeddings(person_id, model_id);
PRAGMA user_version = 1;
COMMIT;
"""


@dataclass(frozen=True)
class FaceMatch:
    """An advisory result; ``person_id`` and ``name`` are absent if unknown."""

    person_id: str | None
    name: str | None
    score: float | None
    runner_up_score: float | None
    reason: str

    @property
    def known(self) -> bool:
        return self.person_id is not None


def _integer(value: int, name: str, minimum: int = 0, maximum: int = 1_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer in [{minimum}, {maximum}]')
    return value


def _text(value: str, name: str, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f'{name} must be a string')
    if '\x00' in value or (not empty and not value.strip()):
        raise ValueError(f'{name} must be nonblank and contain no NUL characters')
    if len(value.encode('utf-8')) > maximum:
        raise ValueError(f'{name} exceeds {maximum} UTF-8 bytes')
    return value


def _literal_query(query: str) -> str:
    # Only quoted literal tokens reach MATCH. User FTS operators are never syntax.
    tokens = list(dict.fromkeys(re.findall(r'[^\W_]+', query[:4096], re.UNICODE)))[:32]
    return ' OR '.join('"' + token[:128] + '"' for token in tokens)


def _normalize(vector: Iterable[float]) -> tuple[float, ...]:
    if isinstance(vector, (str, bytes, bytearray)):
        raise ValueError('embedding must be a numeric vector, not text or image bytes')
    values = []
    try:
        for value in vector:
            if isinstance(value, (bool, str, bytes)):
                raise ValueError('embedding values must be numbers')
            number = float(value)
            if not math.isfinite(number):
                raise ValueError('embedding values must be finite')
            values.append(number)
            if len(values) > 4096:
                raise ValueError('embedding dimensions must be in [1, 4096]')
    except (TypeError, OverflowError) as exc:
        raise ValueError('embedding must be a finite numeric vector') from exc
    if not values or not (scale := max(abs(value) for value in values)):
        raise ValueError('embedding must be nonempty and have nonzero norm')
    # Scaling first avoids overflow/underflow for finite but extreme inputs.
    scaled = [value / scale for value in values]
    norm = math.sqrt(math.fsum(value * value for value in scaled))
    return tuple(value / norm for value in scaled)


class MemoryStore:
    """SQLite/WAL storage. Public mutations are atomic and safe across instances.

    ``max_turns``, ``max_events`` and ``max_summaries`` are per-session retention
    caps enforced on writes. ``context_turns`` caps retrieved turns, including
    the recent tail. Set a retention cap to zero to disable that storage.
    ``prune(older_than=...)`` additionally expires session records by Unix time;
    explicit facts remain until overwritten, forgotten, or their person deleted.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_turns: int = 12,
        context_turns: int = 6,
        max_events: int = 100,
        max_summaries: int = 8,
        max_context_bytes: int = 4096,
        max_context_tokens: int | None = None,
        max_record_bytes: int = 65536,
        max_embeddings_per_person: int = 5,
    ) -> None:
        self.max_turns = _integer(max_turns, 'max_turns')
        self.context_turns = _integer(context_turns, 'context_turns')
        self.max_events = _integer(max_events, 'max_events')
        self.max_summaries = _integer(max_summaries, 'max_summaries')
        self.max_context_bytes = _integer(max_context_bytes, 'max_context_bytes')
        self.max_context_tokens = (
            None if max_context_tokens is None else _integer(max_context_tokens, 'max_context_tokens')
        )
        self.max_record_bytes = _integer(max_record_bytes, 'max_record_bytes', 1)
        self.max_embeddings_per_person = _integer(
            max_embeddings_per_person, 'max_embeddings_per_person', 1, 100
        )
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute('PRAGMA foreign_keys = ON')
            self._db.execute('PRAGMA busy_timeout = 10000')
            self._db.execute('PRAGMA journal_mode = WAL')
            self._db.execute('PRAGMA synchronous = NORMAL')
            version = self._db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f'unsupported memory schema version: {version}')
            self._db.executescript(_SCHEMA)
        except BaseException:
            self._db.close()
            raise

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _person(self, person_id: str) -> sqlite3.Row:
        row = self._db.execute('SELECT * FROM people WHERE id = ?', (person_id,)).fetchone()
        if row is None:
            raise KeyError(f'unknown person id: {person_id!r}')
        return row

    def _session(self, session_id: str) -> sqlite3.Row:
        row = self._db.execute('SELECT * FROM sessions WHERE id = ?', (session_id,)).fetchone()
        if row is None:
            raise KeyError(f'unknown session id: {session_id!r}')
        return row

    def create_person(self, name: str) -> str:
        """Explicitly register a new person. Equal names do not merge identities."""
        name = _text(name, 'name', 512).strip()
        person_id = uuid4().hex
        with self._lock, self._db:
            self._db.execute('INSERT INTO people VALUES (?, ?, ?)', (person_id, name, time.time()))
        return person_id

    def people(self):
        """Operator view without face vectors or raw conversation transcripts."""
        with self._lock:
            return [dict(row) for row in self._db.execute(
                '''SELECT p.id, p.name, p.created_at,
                   (SELECT max(s.created_at) FROM sessions s WHERE s.person_id=p.id) AS last_interaction,
                   (SELECT count(*) FROM face_embeddings f WHERE f.person_id=p.id) AS observations
                   FROM people p ORDER BY p.name LIMIT 200''')]

    def facts(self, person_id):
        with self._lock:
            self._person(person_id)
            return [dict(row) for row in self._db.execute(
                "SELECT id, key, text, created_at, updated_at FROM documents "
                "WHERE kind='fact' AND person_id=? ORDER BY updated_at DESC LIMIT 200", (person_id,))]

    def edit_fact(self, person_id, key, value, expected_updated_at):
        """Prevent a stale phone editor from overwriting a newer memory."""
        value = _text(value, 'value', min(4000, self.max_record_bytes))
        with self._lock, self._db:
            changed = self._db.execute(
                "UPDATE documents SET text=?, updated_at=? WHERE kind='fact' AND person_id=? "
                "AND key=? AND updated_at=?", (value, time.time(), person_id, key, expected_updated_at)).rowcount
            if not changed:
                raise ValueError('memory changed; reload before editing')

    def delete_fact_version(self, person_id, key, expected_updated_at):
        with self._lock, self._db:
            changed = self._db.execute(
                "DELETE FROM documents WHERE kind='fact' AND person_id=? AND key=? AND updated_at=?",
                (person_id, key, expected_updated_at)).rowcount
            if not changed:
                raise ValueError('memory changed; reload before deleting')

    def new_session(self, person_id: str | None = None) -> str:
        session_id = uuid4().hex
        with self._lock, self._db:
            if person_id is not None:
                self._person(person_id)
            self._db.execute('INSERT INTO sessions VALUES (?, ?, ?)', (session_id, person_id, time.time()))
        return session_id

    def _trim(self, session_id: str, kind: str, keep: int) -> int:
        return self._db.execute(
            '''DELETE FROM documents WHERE id IN (
                SELECT id FROM documents WHERE session_id = ? AND kind = ?
                ORDER BY updated_at DESC, id DESC LIMIT -1 OFFSET ?
            )''', (session_id, kind, keep)
        ).rowcount

    def remember(self, session_id: str, user: str, assistant: str) -> int:
        """Record one interaction and trim its session. Never extract facts."""
        user = _text(user, 'user', self.max_record_bytes, empty=True)
        assistant = _text(assistant, 'assistant', self.max_record_bytes, empty=True)
        body = json.dumps({'user': user, 'assistant': assistant}, ensure_ascii=False)
        with self._lock, self._db:
            self._session(session_id)
            now = time.time()
            cursor = self._db.execute(
                '''INSERT INTO documents
                   (kind, session_id, text, user_text, assistant_text, created_at, updated_at)
                   VALUES ('turn', ?, ?, ?, ?, ?, ?)''',
                (session_id, body, user, assistant, now, now)
            )
            self._trim(session_id, 'turn', self.max_turns)
            return cursor.lastrowid

    def put_fact(self, person_id: str, key: str, value: str) -> int:
        """Save/replace an explicitly requested fact or preference for a person.

        The caller must establish explicit user intent. This method is never
        invoked by conversation recording, summarization, or face recognition.
        """
        key = _text(key, 'key', 512).strip()
        value = _text(value, 'value', self.max_record_bytes)
        with self._lock, self._db:
            self._person(person_id)
            now = time.time()
            self._db.execute(
                '''INSERT INTO documents (kind, person_id, key, text, created_at, updated_at)
                   VALUES ('fact', ?, ?, ?, ?, ?)
                   ON CONFLICT(person_id, key) WHERE kind = 'fact'
                   DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at''',
                (person_id, key, value, now, now)
            )
            return self._db.execute(
                "SELECT id FROM documents WHERE kind = 'fact' AND person_id = ? AND key = ?",
                (person_id, key)
            ).fetchone()[0]

    def forget_fact(self, person_id: str, key: str) -> bool:
        with self._lock, self._db:
            self._person(person_id)
            return bool(self._db.execute(
                "DELETE FROM documents WHERE kind = 'fact' AND person_id = ? AND key = ?",
                (person_id, key)
            ).rowcount)

    def _record(self, session_id: str, kind: str, text: str, key: str, keep: int) -> int:
        with self._lock, self._db:
            self._session(session_id)
            now = time.time()
            cursor = self._db.execute(
                '''INSERT INTO documents (kind, session_id, key, text, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)''', (kind, session_id, key, text, now, now)
            )
            self._trim(session_id, kind, keep)
            return cursor.lastrowid

    def record_event(self, session_id: str, text: str, *, kind: str = 'interaction') -> int:
        """Store an observed/explicit event, without inferring emotion or intent."""
        text = _text(text, 'text', self.max_record_bytes)
        kind = _text(kind, 'kind', 128)
        return self._record(session_id, 'event', text, kind, self.max_events)

    def summarize(self, session_id: str, summary: str | None = None, *, max_bytes: int = 2048) -> int:
        """Store a bounded summary, or literal user excerpts if none is supplied.

        No turns are deleted here. Call before pruning to preserve excerpts.
        Summaries remain session-derived records, never explicit facts.
        """
        maximum = min(_integer(max_bytes, 'max_bytes', 1), self.max_record_bytes)
        with self._lock:
            self._session(session_id)
            if summary is None:
                rows = self._db.execute(
                    "SELECT user_text FROM documents WHERE session_id = ? AND kind = 'turn' ORDER BY id",
                    (session_id,)
                ).fetchall()
                if not rows:
                    raise ValueError('cannot summarize a session with no turns')
                summary = 'User excerpts (unverified):\n' + '\n'.join(
                    json.dumps(row['user_text'], ensure_ascii=False) for row in rows
                )
            else:
                summary = _text(summary, 'summary', self.max_record_bytes)
            summary = summary.encode('utf-8')[:maximum].decode('utf-8', errors='ignore')
            if not summary:
                raise ValueError('summary budget is too small for its text')
            return self._record(session_id, 'summary', summary, '', self.max_summaries)

    @staticmethod
    def _render_record(record: dict, remaining: int) -> str:
        def render() -> str:
            return json.dumps(record, ensure_ascii=False, separators=(',', ':'))

        output = render()
        if len(output.encode('utf-8')) <= remaining:
            return output
        text = record.get('text')
        if text is None:
            return ''
        record = dict(record, truncated=True)
        # Preserve a parseable record and whole UTF-8 code points under the cap.
        low, high = 0, len(text)
        record['text'] = ''
        if len(render().encode('utf-8')) > remaining:
            return ''
        while low < high:
            middle = (low + high + 1) // 2
            record['text'] = text[:middle]
            if len(render().encode('utf-8')) <= remaining:
                low = middle
            else:
                high = middle - 1
        record['text'] = text[:low]
        return render() if low else ''

    def context(
        self, session_id: str, query: str, limit: int = 12, *,
        max_bytes: int | None = None, max_tokens: int | None = None,
    ) -> str:
        """Return relevant, scoped JSON lines with strict output budgets.

        ``limit`` caps memory records (0..100); an optional identity record is
        additional. Per-call budgets may only tighten the configured budgets.
        Empty/punctuation-only queries return just the current session tail.
        Missing IDs raise KeyError; they never fall back to a shared scope.
        """
        limit = _integer(limit, 'limit', maximum=100)
        if not isinstance(query, str):
            raise TypeError('query must be a string')
        budgets = [self.max_context_bytes]
        for name, value in (('max_bytes', max_bytes), ('max_tokens', max_tokens),
                            ('max_context_tokens', self.max_context_tokens)):
            if value is not None:
                budgets.append(_integer(value, name))
        budget = min(budgets)
        with self._lock:
            session = self._session(session_id)
            if not budget or not limit:
                return ''
            person_id = session['person_id']
            records = []
            if person_id is not None:
                person = self._person(person_id)
                records.append({'kind': 'person', 'id': person_id, 'name': person['name']})
            expression = _literal_query(query)
            matches = []
            if expression:
                matches = self._db.execute(
                    '''SELECT d.* FROM memory_fts
                       JOIN documents d ON d.id = memory_fts.rowid
                       LEFT JOIN sessions s ON s.id = d.session_id
                       WHERE memory_fts MATCH ? AND
                           (d.session_id = ? OR (? IS NOT NULL AND
                            (d.person_id = ? OR s.person_id = ?)))
                       ORDER BY bm25(memory_fts), d.updated_at DESC, d.id DESC LIMIT ?''',
                    (expression, session_id, person_id, person_id, person_id,
                     limit + min(self.context_turns, 100))
                ).fetchall()
            recent = self._db.execute(
                """SELECT * FROM documents WHERE session_id = ? AND kind = 'turn'
                   ORDER BY id DESC LIMIT ?""", (session_id, min(self.context_turns, limit))
            ).fetchall()
            if (not matches and person_id is not None
                    and re.search(r'\b(remember|know|recall)\b.*\b(me|my|about)\b', query, re.I)):
                matches = self._db.execute(
                    "SELECT * FROM documents WHERE kind = 'fact' AND person_id = ? "
                    "ORDER BY updated_at DESC, id DESC LIMIT ?", (person_id, min(4, limit))
                ).fetchall()
            seen, turns = set(), 0
            for row in [*matches, *recent]:
                if row['id'] in seen or len(seen) >= limit:
                    continue
                if row['kind'] == 'turn':
                    if turns >= self.context_turns:
                        continue
                    turns += 1
                seen.add(row['id'])
                record = {'kind': row['kind'], 'text': row['text']}
                if row['key']:
                    record['key'] = row['key']
                records.append(record)
        lines, used = [], 0
        for record in records:
            line = self._render_record(record, budget - used - bool(lines))
            if line:
                used += len(line.encode('utf-8')) + bool(lines)
                lines.append(line)
        return '\n'.join(lines)

    def prune(
        self, *, max_turns: int | None = None, max_events: int | None = None,
        max_summaries: int | None = None, older_than: float | None = None,
    ) -> dict[str, int]:
        """Apply retention to all sessions; return deleted counts by record kind.

        Overrides apply to this call only. ``older_than`` is an absolute Unix
        timestamp. Facts, identity registrations, and face vectors are unaffected.
        """
        limits = {
            'turn': self.max_turns if max_turns is None else _integer(max_turns, 'max_turns'),
            'event': self.max_events if max_events is None else _integer(max_events, 'max_events'),
            'summary': self.max_summaries if max_summaries is None else _integer(max_summaries, 'max_summaries'),
        }
        if older_than is not None:
            if isinstance(older_than, bool) or not isinstance(older_than, (int, float)) or not math.isfinite(older_than):
                raise ValueError('older_than must be a finite Unix timestamp')
        counts = {}
        with self._lock, self._db:
            for kind, keep in limits.items():
                expired = 0
                if older_than is not None:
                    expired = self._db.execute(
                        'DELETE FROM documents WHERE kind = ? AND updated_at < ?',
                        (kind, older_than)
                    ).rowcount
                trimmed = self._db.execute(
                    '''DELETE FROM documents WHERE id IN (
                           SELECT id FROM (
                               SELECT id, row_number() OVER (
                                   PARTITION BY session_id ORDER BY updated_at DESC, id DESC
                               ) AS position FROM documents WHERE kind = ?
                           ) WHERE position > ?
                       )''', (kind, keep)
                ).rowcount
                counts[kind] = expired + trimmed
        return counts

    def delete_session(self, session_id: str) -> None:
        """Delete a session and all its turns/events/summaries, including FTS."""
        with self._lock, self._db:
            self._session(session_id)
            self._db.execute('DELETE FROM sessions WHERE id = ?', (session_id,))

    def delete_person(self, person_id: str) -> None:
        """Delete a person, their sessions, facts, and face vectors atomically."""
        with self._lock, self._db:
            self._person(person_id)
            self._db.execute('DELETE FROM people WHERE id = ?', (person_id,))

    def rebuild_index(self) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO memory_fts(memory_fts) VALUES ('rebuild')")

    def add_face_embedding(self, person_id: str, vector: Iterable[float], model_id: str) -> int:
        """Store a normalized vector for an existing explicitly registered ID.

        Each model identifier has a fixed dimension. Include model version and
        preprocessing in that identifier; vectors from different models never mix.
        """
        model_id = _text(model_id, 'model_id', 256).strip()
        values = _normalize(vector)
        dimensions = len(values)
        with self._lock, self._db:
            self._person(person_id)
            self._db.execute(
                'INSERT OR IGNORE INTO embedding_models VALUES (?, ?)', (model_id, dimensions)
            )
            expected = self._db.execute(
                'SELECT dimensions FROM embedding_models WHERE model_id = ?', (model_id,)
            ).fetchone()[0]
            if dimensions != expected:
                raise ValueError(f'model {model_id!r} requires {expected} dimensions, got {dimensions}')
            cursor = self._db.execute(
                '''INSERT INTO face_embeddings(person_id, model_id, dimensions, vector, created_at)
                   VALUES (?, ?, ?, ?, ?)''',
                (person_id, model_id, dimensions, struct.pack(f'<{dimensions}d', *values), time.time())
            )
            self._db.execute(
                '''DELETE FROM face_embeddings WHERE id IN (
                    SELECT id FROM face_embeddings WHERE person_id = ? AND model_id = ?
                    ORDER BY id DESC LIMIT -1 OFFSET ?
                )''', (person_id, model_id, self.max_embeddings_per_person)
            )
            return cursor.lastrowid

    def match_face(
        self, vector: Iterable[float], model_id: str, *,
        threshold: float = 0.92, ambiguity_margin: float = 0.05,
    ) -> FaceMatch:
        """Match cosine similarity with a margin over the next distinct person.

        Ties always remain unknown, including when margin is zero. Invalid or
        mismatched vectors raise ValueError. Matching never changes identity state.
        """
        model_id = _text(model_id, 'model_id', 256).strip()
        for label, value in (('threshold', threshold), ('ambiguity_margin', ambiguity_margin)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f'{label} must be finite and in [0, 1]')
        values = _normalize(vector)
        with self._lock:
            model = self._db.execute(
                'SELECT dimensions FROM embedding_models WHERE model_id = ?', (model_id,)
            ).fetchone()
            if model is None:
                return FaceMatch(None, None, None, None, 'no_candidates')
            if model['dimensions'] != len(values):
                raise ValueError(f'model {model_id!r} requires {model["dimensions"]} dimensions')
            rows = self._db.execute(
                '''SELECT f.person_id, p.name, f.vector FROM face_embeddings f
                   JOIN people p ON p.id = f.person_id WHERE f.model_id = ?''', (model_id,)
            ).fetchall()
        scores: dict[str, tuple[float, str]] = {}
        for row in rows:
            candidate = struct.unpack(f'<{len(values)}d', row['vector'])
            score = max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(values, candidate))))
            previous = scores.get(row['person_id'])
            if previous is None or score > previous[0]:
                scores[row['person_id']] = (score, row['name'])
        ranked = sorted(scores.items(), key=lambda item: item[1][0], reverse=True)
        if not ranked:
            return FaceMatch(None, None, None, None, 'no_candidates')
        person_id, (best, name) = ranked[0]
        runner_up = ranked[1][1][0] if len(ranked) > 1 else None
        if best < threshold:
            return FaceMatch(None, None, best, runner_up, 'below_threshold')
        if runner_up is not None and (best <= runner_up or best - runner_up < ambiguity_margin):
            return FaceMatch(None, None, best, runner_up, 'ambiguous')
        return FaceMatch(person_id, name, best, runner_up, 'matched')

    def forget_face_embeddings(self, person_id: str, model_id: str | None = None) -> int:
        with self._lock, self._db:
            self._person(person_id)
            if model_id is None:
                return self._db.execute(
                    'DELETE FROM face_embeddings WHERE person_id = ?', (person_id,)
                ).rowcount
            return self._db.execute(
                'DELETE FROM face_embeddings WHERE person_id = ? AND model_id = ?',
                (person_id, model_id)
            ).rowcount
