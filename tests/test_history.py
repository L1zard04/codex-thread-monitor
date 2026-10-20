import json
import tempfile
import unittest
from pathlib import Path

from history import StatusHistory


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'status-history.jsonl'

    def test_records_only_status_metadata_with_timezone(self):
        history = StatusHistory(self.path)
        self.assertTrue(history.record('status', [None, {'id':'a','title':'Private title'}],
                                      {'a':{'status':'active','turn_id':'t',
                                            'event_time':'2026-10-19T12:00:00Z',
                                            'answer':'Private answer','detail':'private path'}}))
        entry = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(entry['event'], 'status')
        self.assertEqual(entry['slots'], [{'slot':2,'id':'a','status':'active',
                                          'turn_id':'t','event_time':'2026-10-19T12:00:00Z'}])
        self.assertRegex(entry['time'], r'[+-]\d\d:\d\d$')
        self.assertNotIn('Private', self.path.read_text(encoding='utf-8'))

    def test_rotation_keeps_bounded_history_and_valid_json_lines(self):
        history = StatusHistory(self.path, max_bytes=250, backups=2)
        for _ in range(20):
            self.assertTrue(history.record('restore', [{'id':'a'}]))
        files = list(self.path.parent.glob('status-history.jsonl*'))
        self.assertEqual(len(files), 3)
        for path in files:
            self.assertLessEqual(path.stat().st_size, 250)
            for line in path.read_text(encoding='utf-8').splitlines():
                self.assertEqual(json.loads(line)['event'], 'restore')

    def test_write_failure_is_reported_and_recovers(self):
        self.path.mkdir()
        history = StatusHistory(self.path)
        self.assertFalse(history.record('restore', []))
        self.assertTrue(history.error)
        self.path.rmdir()
        self.assertTrue(history.record('restore', []))
        self.assertEqual(history.error, '')


if __name__ == '__main__':
    unittest.main()
