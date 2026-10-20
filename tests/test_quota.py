from contextlib import closing
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from quota import QuotaReader, quota_display


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)
        self.now=1800000000

    def event(self,stamp,used=55,window=300,limit_id='codex'):
        return {'timestamp':datetime.fromtimestamp(stamp,timezone.utc).isoformat(),
                'type':'event_msg','payload':{'type':'token_count','rate_limits':{
                    'limit_id':limit_id,'primary':{'used_percent':used,'window_minutes':window,
                                                'resets_at':self.now+3600},
                    'secondary':{'used_percent':9,'window_minutes':10080,'resets_at':self.now+86400}}}}

    def catalog(self,paths):
        with closing(sqlite3.connect(self.home/'state_5.sqlite')) as db:
            db.execute('CREATE TABLE threads(rollout_path TEXT,updated_at INTEGER)')
            db.executemany('INSERT INTO threads VALUES(?,?)',[(str(p),i) for i,p in enumerate(paths)])
            db.commit()

    def test_passive_reader_selects_latest_codex_event_and_keeps_sources_unchanged(self):
        first,second=self.home/'one.jsonl',self.home/'two.jsonl'
        first.write_text(json.dumps(self.event(self.now-60))+'\n',encoding='utf-8')
        second.write_text(json.dumps(self.event(self.now-120,used=75))+'\n'+
                          json.dumps(self.event(self.now-10,used=99,limit_id='other'))+'\n',encoding='utf-8')
        self.catalog([first,second])
        before={p:p.read_bytes() for p in (first,second,self.home/'state_5.sqlite')}
        result=QuotaReader(self.home).poll()
        text,detail=quota_display(result,now=self.now)
        self.assertIn('5小时剩余 45%',text)
        self.assertIn('7天剩余 91%',text)
        self.assertIn('本地',text)
        self.assertIn('重置',detail)
        for path,data in before.items():
            self.assertEqual(path.read_bytes(),data)

    def test_expired_reset_is_not_assumed_to_be_full(self):
        event=self.event(self.now-1)
        event['payload']['rate_limits']['primary']['resets_at']=self.now
        text,_=quota_display({'timestamp':event['timestamp'],'limits':event['payload']['rate_limits']},now=self.now)
        self.assertIn('5小时待更新',text)
        self.assertNotIn('100%',text)

    def test_missing_invalid_percentage_or_wrong_window_is_unknown(self):
        for used,window in [(None,300),(True,300),(float('nan'),300),(55,60),(-1,300),(101,300)]:
            event=self.event(self.now,used=used,window=window)
            text,_=quota_display({'timestamp':event['timestamp'],'limits':event['payload']['rate_limits']},now=self.now)
            self.assertIn('5小时 --',text)

    def test_partial_new_line_is_deferred_and_append_refreshes_cache(self):
        path=self.home/'one.jsonl'
        first=json.dumps(self.event(self.now-20))+'\n'
        second=json.dumps(self.event(self.now-10,used=60))
        path.write_text(first+second,encoding='utf-8')
        self.catalog([path])
        reader=QuotaReader(self.home)
        self.assertIn('45%',quota_display(reader.poll(),now=self.now)[0])
        with path.open('a',encoding='utf-8') as stream:
            stream.write('\n')
        self.assertIn('40%',quota_display(reader.poll(),now=self.now)[0])

    def test_missing_database_does_not_create_it_and_does_not_show_zero(self):
        result=QuotaReader(self.home).poll()
        text,detail=quota_display(result,now=self.now)
        self.assertIn('5小时 --',text)
        self.assertIn('无法',detail)
        self.assertFalse((self.home/'state_5.sqlite').exists())

    def test_old_snapshot_is_labelled_and_sparse_latest_does_not_inherit_week(self):
        event=self.event(self.now-3600)
        event['payload']['rate_limits']['secondary']=None
        text,_=quota_display({'timestamp':event['timestamp'],'limits':event['payload']['rate_limits']},now=self.now)
        self.assertIn('旧快照',text)
        self.assertIn('7天 --',text)
