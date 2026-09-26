"""Storage, privacy, retention, retrieval, and face matching contracts."""

import concurrent.futures
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import time
import unittest

from milo_next.memory import MemoryStore


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'memory.sqlite3'
        self.memory = MemoryStore(self.path)
        self.addCleanup(self.memory.close)

    @staticmethod
    def records(context):
        return [json.loads(line) for line in context.splitlines()]

    def sql(self, statement, params=()):
        with sqlite3.connect(self.path) as db:
            return db.execute(statement, params).fetchall()

    def test_persistence_wal_and_cross_instance_visibility(self):
        person = self.memory.create_person('Alex')
        session = self.memory.new_session(person)
        self.memory.remember(session, 'I am testing persistence', 'Recorded conversation')
        self.memory.put_fact(person, 'drink', 'oolong')
        self.memory.record_event(session, 'Visited the observatory')
        self.memory.summarize(session)
        self.memory.add_face_embedding(person, [3, 4], 'face-v1')
        self.memory.close()
        with MemoryStore(self.path) as reopened:
            text = reopened.context(session, 'oolong observatory persistence')
            for expected in ('oolong', 'observatory', 'persistence', 'Alex'):
                self.assertIn(expected, text)
            match = reopened.match_face([30, 40], 'face-v1')
            self.assertTrue(match.known)
            self.assertEqual(match.person_id, person)
            self.assertEqual(match.name, 'Alex')
            self.assertAlmostEqual(match.score, 1)
            with MemoryStore(self.path) as second:
                second.put_fact(person, 'drink', 'hibiscus')
                self.assertIn('hibiscus', reopened.context(session, 'hibiscus'))
        self.assertEqual(self.sql('PRAGMA journal_mode'), [('wal',)])

    def test_anonymous_sessions_never_share_or_register_names(self):
        first, second = self.memory.new_session(), self.memory.new_session()
        self.memory.remember(first, 'My name is Alex. Secret comet.', 'Hello Alex')
        self.memory.record_event(first, 'comet event')
        self.memory.summarize(first)
        self.assertIn('comet', self.memory.context(first, 'comet'))
        self.assertEqual(self.memory.context(second, 'comet Alex'), '')
        person = self.memory.create_person('Alex')
        named = self.memory.new_session(person)
        self.assertNotIn('comet', self.memory.context(named, 'comet'))
        self.memory.put_fact(person, 'secret', 'named-satellite')
        self.assertNotIn('named-satellite', self.memory.context(first, 'satellite'))
        self.assertEqual(self.sql('SELECT count(*) FROM people'), [(1,)])
        self.assertEqual(self.sql('SELECT person_id FROM sessions WHERE id = ?', (first,)), [(None,)])

    def test_named_people_and_duplicate_names_are_isolated(self):
        alice = self.memory.create_person('Same Name')
        bob = self.memory.create_person('Same Name')
        self.assertNotEqual(alice, bob)
        a1, a2 = self.memory.new_session(alice), self.memory.new_session(alice)
        b1 = self.memory.new_session(bob)
        self.memory.put_fact(alice, 'color', 'cobalt')
        self.memory.put_fact(bob, 'color', 'vermilion')
        self.memory.remember(a1, 'cobalt telescope', 'cobalt reply')
        self.memory.record_event(a1, 'cobalt meeting')
        self.memory.summarize(a1, 'cobalt summary')
        self.memory.remember(b1, 'vermilion private', 'vermilion response')
        relevant = self.records(self.memory.context(a2, 'cobalt', limit=20))
        self.assertEqual({r['kind'] for r in relevant}, {'person', 'fact', 'event', 'turn', 'summary'})
        self.assertNotIn('vermilion', self.memory.context(a1, 'vermilion cobalt'))
        self.assertNotIn('cobalt', self.memory.context(b1, 'cobalt vermilion'))
        # With no relevant query, another session's history is not injected.
        self.assertEqual([r['kind'] for r in self.records(self.memory.context(a2, ''))], ['person'])

    def test_no_automatic_facts_or_emotion_claims(self):
        person = self.memory.create_person('Alex')
        session = self.memory.new_session(person)
        self.memory.remember(session, 'I drink tea.', 'You must be feeling sad.')
        self.memory.summarize(session)
        self.assertEqual(self.sql("SELECT count(*) FROM documents WHERE kind = 'fact'"), [(0,)])
        summary = self.sql("SELECT text FROM documents WHERE kind = 'summary'")[0][0]
        self.assertIn('I drink tea.', summary)
        self.assertIn('unverified', summary)
        self.assertNotIn('sad', summary)

    def test_missing_ids_do_not_fall_back_or_create_people(self):
        invalid_operations = [
            lambda: self.memory.new_session('Alex'),
            lambda: self.memory.remember('missing', 'x', 'y'),
            lambda: self.memory.put_fact('Alex', 'key', 'value'),
            lambda: self.memory.record_event('missing', 'event'),
            lambda: self.memory.summarize('missing', 'summary'),
            lambda: self.memory.context('missing', 'query'),
            lambda: self.memory.context('missing', '', limit=0),
            lambda: self.memory.add_face_embedding('Alex', [1, 0], 'model'),
            lambda: self.memory.delete_session('missing'),
            lambda: self.memory.delete_person('missing'),
        ]
        for operation in invalid_operations:
            with self.subTest(operation=operation), self.assertRaises(KeyError):
                operation()
        self.assertEqual(self.sql('SELECT count(*) FROM people'), [(0,)])
        self.assertEqual(self.sql('SELECT count(*) FROM documents'), [(0,)])
        self.assertEqual(self.sql('SELECT count(*) FROM embedding_models'), [(0,)])

    def test_retention_defaults_and_context_turn_cap(self):
        session = self.memory.new_session()
        for index in range(25):
            self.memory.remember(session, f'turn {index}', f'reply {index}')
        rows = self.sql("SELECT user_text FROM documents WHERE kind = 'turn' ORDER BY id")
        self.assertEqual([row[0] for row in rows], [f'turn {i}' for i in range(13, 25)])
        recent = self.records(self.memory.context(session, '', limit=100))
        self.assertEqual(len(recent), 6)
        self.assertEqual([json.loads(row['text'])['user'] for row in recent],
                         [f'turn {i}' for i in range(24, 18, -1)])
        matching = self.records(self.memory.context(session, 'turn', limit=100))
        self.assertEqual(len([row for row in matching if row['kind'] == 'turn']), 6)

    def test_configurable_retention_summaries_pruning_and_index(self):
        with MemoryStore(self.path, max_turns=3, max_events=2, max_summaries=1) as memory:
            person = memory.create_person('Alex')
            first, second = memory.new_session(person), memory.new_session()
            memory.put_fact(person, 'durable', 'evergreen')
            for session in (first, second):
                for i in range(5):
                    memory.remember(session, f'turn{i}', 'reply')
                    memory.record_event(session, f'event{i}')
                    memory.summarize(session, f'summary{i}')
            for kind, expected in (('turn', 6), ('event', 4), ('summary', 2)):
                self.assertEqual(self.sql('SELECT count(*) FROM documents WHERE kind = ?', (kind,)), [(expected,)])
            self.assertEqual(memory.prune(max_turns=1, max_events=0, max_summaries=0),
                             {'turn': 4, 'event': 4, 'summary': 2})
            self.assertNotIn('event', memory.context(first, 'event4 summary4'))
            self.assertEqual(memory.prune(older_than=time.time() + 1),
                             {'turn': 2, 'event': 0, 'summary': 0})
            self.assertIn('evergreen', memory.context(first, 'evergreen'))
            memory.rebuild_index()
            self.assertIn('evergreen', memory.context(first, 'evergreen'))
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO memory_fts(memory_fts, rank) VALUES ('integrity-check', 1)")

    def test_zero_retention_and_empty_summary(self):
        with MemoryStore(self.path, max_turns=0, max_events=0, max_summaries=0) as memory:
            session = memory.new_session()
            memory.remember(session, 'private', 'reply')
            memory.record_event(session, 'private')
            memory.summarize(session, 'private')
            self.assertEqual(memory.context(session, 'private'), '')
            with self.assertRaises(ValueError):
                memory.summarize(session)
        self.assertEqual(self.sql('SELECT count(*) FROM documents'), [(0,)])

    def test_fact_upsert_forget_and_fts_synchronization(self):
        person = self.memory.create_person('Alex')
        session = self.memory.new_session(person)
        first = self.memory.put_fact(person, 'drink', 'oolong')
        second = self.memory.put_fact(person, 'drink', 'hibiscus')
        self.assertEqual(first, second)
        self.assertNotIn('fact', self.memory.context(session, 'oolong'))
        self.assertIn('hibiscus', self.memory.context(session, 'drink'))
        self.assertTrue(self.memory.forget_fact(person, 'drink'))
        self.assertFalse(self.memory.forget_fact(person, 'drink'))
        self.assertNotIn('hibiscus', self.memory.context(session, 'hibiscus'))

    def test_fts_hostile_input_is_literal_and_scoped(self):
        alice, bob = self.memory.create_person('A'), self.memory.create_person('B')
        session = self.memory.new_session(alice)
        self.memory.put_fact(alice, 'topic', 'astronomy')
        self.memory.put_fact(bob, 'private', 'CLASSIFIED astronomy')
        hostile = [
            '"', "'; DROP TABLE people; --", 'astronomy OR private',
            'NEAR(astronomy, private)', 'text:astronomy NOT *', '"unterminated',
            '(){}[]:^*+-', '\x00', 'OR AND NOT', '" OR 1=1 --',
            'astronomy ' * 10000, 'astronomy " * NEAR(x, y) -key:private',
        ]
        for query in hostile:
            with self.subTest(query=query[:60]):
                context = self.memory.context(session, query)
                self.assertNotIn('CLASSIFIED', context)
                self.records(context)
        self.assertIn('astronomy', self.memory.context(session, 'astronomy'))
        self.assertEqual(self.sql('SELECT count(*) FROM people'), [(2,)])

    def test_relevance_does_not_dump_unrelated_facts(self):
        person = self.memory.create_person('Alex')
        session = self.memory.new_session(person)
        self.memory.put_fact(person, 'favorite drink', 'oolong')
        self.memory.put_fact(person, 'address', 'secret street')
        self.memory.put_fact(person, 'instrument', 'cello')
        context = self.memory.context(session, 'drink')
        self.assertIn('oolong', context)
        self.assertNotIn('secret street', context)
        self.assertNotIn('cello', context)
        self.assertNotIn('oolong', self.memory.context(session, ''))

    def test_utf8_json_and_tight_context_budgets(self):
        session = self.memory.new_session()
        # Unicode, quotes, backslashes, and embedded line breaks all affect bytes.
        text = ('\u732b\U0001f642\\"\n' * 500)
        self.memory.remember(session, text, 'reply')
        for budget in (0, 1, 20, 60, 100, 257, 1024):
            with self.subTest(budget=budget):
                context = self.memory.context(session, '', max_bytes=budget)
                self.assertLessEqual(len(context.encode('utf-8')), budget)
                self.records(context)
        truncated = self.records(self.memory.context(session, '', max_bytes=257))
        self.assertTrue(truncated[0]['truncated'])
        context = self.memory.context(session, '', max_tokens=180, max_bytes=500)
        self.assertLessEqual(len(context.encode('utf-8')), 180)
        with MemoryStore(self.path, max_context_bytes=170, max_context_tokens=150) as memory:
            context = memory.context(session, '', max_bytes=900, max_tokens=900)
            self.assertLessEqual(len(context.encode('utf-8')), 150)
        self.assertEqual(self.memory.context(session, '', limit=0), '')

    def test_summary_budget_and_limit_validation(self):
        session = self.memory.new_session()
        self.memory.remember(session, '\u732b' * 50, 'reply')
        self.memory.summarize(session, '\u732b' * 50, max_bytes=10)
        value = self.sql("SELECT text FROM documents WHERE kind = 'summary'")[0][0]
        self.assertLessEqual(len(value.encode('utf-8')), 10)
        for kwargs in ({'limit': -1}, {'limit': 101}, {'limit': True},
                       {'max_bytes': -1}, {'max_tokens': 1.5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.memory.context(session, '', **kwargs)
        with self.assertRaises(ValueError):
            self.memory.prune(older_than=float('nan'))
        with self.assertRaises(ValueError):
            MemoryStore(self.path, max_turns=-1)
        with self.assertRaises(ValueError):
            self.memory.create_person('  ')
        with self.assertRaises(ValueError):
            self.memory.remember(session, 'a\x00b', 'reply')

    def test_face_vector_validation_and_model_dimensions(self):
        person = self.memory.create_person('Alex')
        self.memory.add_face_embedding(person, [3, 4], 'model-a')
        invalid = ([], [0, 0], [float('nan'), 1], [float('inf'), 1],
                   [float('-inf'), 1], [True, 1], ['1', 0], b'photo', 'photo',
                   None, [object()], [1] * 4097)
        for vector in invalid:
            with self.subTest(vector=str(vector)[:60]):
                with self.assertRaises(ValueError):
                    self.memory.add_face_embedding(person, vector, 'model-a')
                with self.assertRaises(ValueError):
                    self.memory.match_face(vector, 'model-a')
        with self.assertRaises(ValueError):
            self.memory.add_face_embedding(person, [1, 2, 3], 'model-a')
        with self.assertRaises(ValueError):
            self.memory.match_face([1, 2, 3], 'model-a')
        self.memory.add_face_embedding(person, [1, 2, 3], 'model-b')
        self.assertEqual(self.memory.match_face([3, 4], 'missing').reason, 'no_candidates')
        self.assertEqual(self.sql('SELECT count(*) FROM face_embeddings'), [(2,)])
        vector = self.sql("SELECT vector FROM face_embeddings WHERE model_id = 'model-a'")[0][0]
        self.assertEqual(struct.unpack('<2d', vector), (0.6, 0.8))
        for extreme in (1e308, 1e-308):
            match = self.memory.match_face([extreme * 0.6, extreme * 0.8], 'model-a')
            self.assertEqual(match.person_id, person)

    def test_conservative_matching_and_ambiguity(self):
        alice, bob = self.memory.create_person('Alice'), self.memory.create_person('Bob')
        unknown_session = self.memory.new_session()
        self.memory.add_face_embedding(alice, [1, 0], 'v1')
        self.memory.add_face_embedding(alice, [1, 0.01], 'v1')
        self.assertEqual(self.memory.match_face([1, 0], 'v1').person_id, alice)
        weak = self.memory.match_face([0, 1], 'v1')
        self.assertFalse(weak.known)
        self.assertIsNone(weak.name)
        self.assertEqual(weak.reason, 'below_threshold')
        self.memory.add_face_embedding(bob, [1, 0.02], 'v1')
        ambiguous = self.memory.match_face([1, 0], 'v1')
        self.assertFalse(ambiguous.known)
        self.assertIsNone(ambiguous.name)
        self.assertEqual(ambiguous.reason, 'ambiguous')
        self.assertIsNotNone(ambiguous.runner_up_score)
        self.memory.add_face_embedding(bob, [1, 0], 'v1')
        self.assertEqual(self.memory.match_face([1, 0], 'v1', ambiguity_margin=0).reason, 'ambiguous')
        self.assertEqual(self.sql('SELECT person_id FROM sessions WHERE id = ?', (unknown_session,)), [(None,)])
        self.assertEqual(self.memory.forget_face_embeddings(bob, 'v1'), 2)
        self.assertEqual(self.memory.match_face([1, 0], 'v1').person_id, alice)
        self.assertEqual(self.memory.forget_face_embeddings(alice), 2)
        self.assertEqual(self.memory.match_face([1, 0], 'v1').reason, 'no_candidates')
        for kwargs in ({'threshold': -0.1}, {'threshold': float('nan')},
                       {'threshold': True}, {'ambiguity_margin': 2},
                       {'ambiguity_margin': float('inf')}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.memory.match_face([1, 0], 'v1', **kwargs)

    def test_face_models_and_embedding_retention(self):
        alice, bob = self.memory.create_person('Alice'), self.memory.create_person('Bob')
        with MemoryStore(self.path, max_embeddings_per_person=2) as memory:
            for _ in range(5):
                memory.add_face_embedding(alice, [1, 0], 'v1')
            memory.add_face_embedding(bob, [1, 0], 'v2')
            self.assertEqual(memory.match_face([1, 0], 'v1').person_id, alice)
            self.assertEqual(memory.match_face([1, 0], 'v2').person_id, bob)
        self.assertEqual(self.sql('SELECT count(*) FROM face_embeddings WHERE person_id = ?', (alice,)), [(2,)])

    def test_foreign_keys_cascades_and_search_cleanup(self):
        person = self.memory.create_person('Alice')
        session = self.memory.new_session(person)
        survivor = self.memory.new_session()
        self.memory.remember(session, 'privateword', 'reply')
        self.memory.record_event(session, 'privateword event')
        self.memory.summarize(session, 'privateword summary')
        self.memory.put_fact(person, 'fact', 'privateword')
        self.memory.add_face_embedding(person, [1, 0], 'v1')
        self.memory.remember(survivor, 'survivorword', 'reply')
        self.assertEqual(self.memory._db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        with self.assertRaises(sqlite3.IntegrityError), self.memory._db:
            self.memory._db.execute('INSERT INTO sessions VALUES (?, ?, ?)', ('bad', 'missing', 0))
        with self.assertRaises(sqlite3.IntegrityError), self.memory._db:
            self.memory._db.execute(
                'INSERT INTO face_embeddings(person_id, model_id, dimensions, vector, created_at) VALUES (?, ?, ?, ?, ?)',
                (person, 'v1', 3, struct.pack('<3d', 1, 0, 0), 0)
            )
        self.memory.delete_person(person)
        for table in ('people', 'face_embeddings'):
            self.assertEqual(self.sql(f'SELECT count(*) FROM {table}'), [(0,)])
        self.assertEqual(self.sql('SELECT id FROM sessions'), [(survivor,)])
        self.assertEqual(self.sql('SELECT count(*) FROM documents'), [(1,)])
        self.assertEqual(self.sql("SELECT rowid FROM memory_fts WHERE memory_fts MATCH 'privateword'"), [])
        self.assertEqual(self.sql('PRAGMA foreign_key_check'), [])
        self.memory.delete_session(survivor)
        self.assertEqual(self.sql('SELECT count(*) FROM documents'), [(0,)])
        self.assertEqual(self.sql("SELECT rowid FROM memory_fts WHERE memory_fts MATCH 'survivorword'"), [])

    def test_delete_session_preserves_person_facts(self):
        person = self.memory.create_person('Alex')
        first, second = self.memory.new_session(person), self.memory.new_session(person)
        self.memory.put_fact(person, 'drink', 'oolong')
        self.memory.remember(first, 'lostword', 'reply')
        self.memory.delete_session(first)
        self.assertIn('oolong', self.memory.context(second, 'oolong'))
        self.assertNotIn('lostword', self.memory.context(second, 'lostword'))

    def test_parallel_instances_preserve_retention_and_index(self):
        session = self.memory.new_session()

        def worker(worker_id):
            with MemoryStore(self.path, max_turns=12) as memory:
                for index in range(10):
                    memory.remember(session, f'worker {worker_id} turn {index}', 'reply')

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(worker, range(4)))
        self.assertEqual(self.sql("SELECT count(*) FROM documents WHERE kind = 'turn'"), [(12,)])
        self.assertEqual(len(self.records(self.memory.context(session, 'worker'))), 6)
        self.assertEqual(self.sql('PRAGMA foreign_key_check'), [])
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO memory_fts(memory_fts, rank) VALUES ('integrity-check', 1)")


if __name__ == '__main__':
    unittest.main()
