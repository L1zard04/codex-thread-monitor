import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from core import Catalog, ConfigStore, LogReader, TurnState


def event(kind, turn='a', **extra):
    return {'timestamp': '2026-10-19T12:00:00Z', 'type': 'event_msg',
            'payload': {'type': kind, 'turn_id': turn, **extra}}


class StateTests(unittest.TestCase):
    def test_only_current_turn_completion_finishes(self):
        s = TurnState()
        self.assertEqual(s.status, 'unknown')
        s.apply(event('task_started'))
        self.assertEqual(s.status, 'active')
        s.apply(event('task_started', 'b'))
        s.apply(event('task_complete', 'a'))
        self.assertEqual(s.status, 'active')
        s.apply(event('task_complete', 'b'))
        self.assertEqual(s.status, 'completed')

    def test_child_turn_and_messages_cannot_finish_root(self):
        s = TurnState()
        s.apply(event('task_started'))
        s.apply(event('task_started', 'child', root_turn_id='a'))
        s.apply(event('task_complete', 'child', root_turn_id='a'))
        s.apply(event('agent_message'))
        self.assertEqual(s.turn_id, 'a')
        self.assertEqual(s.status, 'active')

    def test_abort_is_not_completed(self):
        s = TurnState()
        s.apply(event('task_started'))
        s.apply(event('turn_aborted'))
        self.assertEqual(s.status, 'interrupted')

    def test_completed_only_log_is_supported(self):
        s = TurnState()
        s.apply(event('task_complete'))
        self.assertEqual(s.status, 'completed')

    def test_latest_result_is_retained_while_next_turn_runs(self):
        s = TurnState()
        s.apply(event('task_started'))
        s.apply(event('task_complete',last_agent_message='First answer'))
        self.assertEqual(s.answer,'First answer')
        self.assertEqual(s.answer_turn_id,'a')
        s.apply(event('task_started','b'))
        self.assertEqual(s.answer,'First answer')
        s.apply(event('task_complete','a',last_agent_message='Old delayed answer'))
        self.assertEqual(s.answer,'First answer')
        s.apply(event('task_complete','b',last_agent_message='Latest answer'))
        self.assertEqual(s.answer,'Latest answer')
        self.assertEqual(s.answer_turn_id,'b')

    def test_result_missing_in_new_completion_does_not_show_previous_as_current(self):
        s=TurnState()
        s.apply(event('task_complete',last_agent_message='Old'))
        s.apply(event('task_started','b'))
        s.apply(event('task_complete','b'))
        self.assertEqual(s.answer,'')


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'log.jsonl'

    def append(self, e, newline=True):
        with self.path.open('ab') as f:
            f.write(json.dumps(e).encode() + (b'\n' if newline else b''))

    def test_partial_line_is_deferred_and_poll_is_incremental(self):
        self.append(event('task_started'))
        r = LogReader(self.path)
        self.assertEqual(r.poll().status, 'active')
        self.append(event('task_complete'), newline=False)
        self.assertEqual(r.poll().status, 'active')
        with self.path.open('ab') as f:
            f.write(b'\n')
        self.assertEqual(r.poll().status, 'completed')
        self.assertEqual(r.poll().status, 'completed')
        self.assertEqual(r.offset, self.path.stat().st_size)

    def test_truncation_and_replacement_reset_old_state(self):
        self.append(event('task_started'))
        r = LogReader(self.path)
        r.poll()
        self.path.write_bytes(b'')
        self.assertEqual(r.poll().status, 'unknown')
        other = self.path.with_suffix('.new')
        other.write_text(json.dumps(event('task_complete', 'b'))+'\n', encoding='utf-8')
        other.replace(self.path)
        self.assertEqual(r.poll().status, 'completed')
        self.assertEqual(r.state.turn_id, 'b')

    def test_missing_log_does_not_leave_green_and_recovers(self):
        self.append(event('task_complete'))
        r = LogReader(self.path)
        self.assertEqual(r.poll().status, 'completed')
        self.path.unlink()
        self.assertEqual(r.poll().status, 'unknown')
        self.assertTrue(r.error)
        self.append(event('task_started', 'b'))
        self.assertEqual(r.poll().status, 'active')
        self.assertFalse(r.error)

    def test_rewrite_can_regrow_past_old_offset_with_same_session_header(self):
        header = json.dumps({'type':'session_meta','payload':{'padding':'x'*200}})+'\n'
        self.path.write_text(header,encoding='utf-8')
        self.append(event('task_started'))
        self.append(event('task_complete'))
        r = LogReader(self.path)
        self.assertEqual(r.poll().status,'completed')
        self.path.write_text(header+json.dumps(event('task_started','b'))+'\n'
                             +json.dumps({'padding':'y'*600})+'\n',encoding='utf-8')
        self.assertGreater(self.path.stat().st_size,r.offset)
        self.assertEqual(r.poll().status,'active')
        self.assertEqual(r.state.turn_id,'b')

    def test_malformed_line_does_not_hide_valid_events(self):
        self.path.write_bytes(b'bad json\n')
        self.append(event('task_started'))
        self.assertEqual(LogReader(self.path).poll().status, 'active')


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_catalog_groups_null_project_by_matching_cwd_and_filters_children(self):
        db = self.root/'state_5.sqlite'
        with closing(sqlite3.connect(db)) as c:
            c.executescript('''
                CREATE TABLE projects(id TEXT, name TEXT);
                CREATE TABLE threads(id TEXT, title TEXT, cwd TEXT, rollout_path TEXT,
                    source TEXT, archived INTEGER, updated_at INTEGER, project_id TEXT);
                INSERT INTO projects VALUES('p','My Project');
            ''')
            rows = [('a','Chat A',r'\\?\D:\work', 'a.jsonl','vscode',0,2,'p'),
                    ('b','Chat B',r'D:\work', 'b.jsonl','vscode',0,1,None),
                    ('c','Child',r'D:\work', 'c.jsonl','{"subagent":{}}',0,3,'p'),
                    ('d','Archived',r'D:\work','d.jsonl','vscode',1,4,'p')]
            c.executemany('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)',rows)
            c.commit()
        before = db.read_bytes()
        result = Catalog(self.root).list_threads()
        self.assertEqual([r.id for r in result], ['a','b'])
        self.assertEqual([r.project for r in result], ['My Project','My Project'])
        self.assertEqual(before, db.read_bytes())
        (self.root/'.codex-global-state.json').write_text(json.dumps({
            'local-projects':{'desktop-p':{'name':'Renamed Project','rootPaths':[r'D:\work']}},
            'thread-project-assignments':{'a':{'projectKind':'local','projectId':'desktop-p'}}
        }),encoding='utf-8')
        result = Catalog(self.root).list_threads()
        self.assertEqual([r.project for r in result],['Renamed Project','Renamed Project'])

    def test_missing_database_is_not_created(self):
        with self.assertRaises((OSError, sqlite3.Error)):
            Catalog(self.root).list_threads()
        self.assertFalse((self.root/'state_5.sqlite').exists())

    def test_configuration_roundtrip_and_corrupt_file(self):
        store = ConfigStore(self.root/'config.json')
        cfg = store.load()
        self.assertEqual(cfg['slots'], [None]*4)
        cfg['slots'][0] = {'id':'thread-1','project':'Example','title':'My chat'}
        cfg['topmost'] = False
        store.save(cfg)
        self.assertEqual(store.load(), cfg)
        store.path.write_text('{broken',encoding='utf-8')
        self.assertEqual(store.load()['slots'], [None]*4)
        self.assertTrue(store.error)


if __name__ == '__main__':
    unittest.main()
