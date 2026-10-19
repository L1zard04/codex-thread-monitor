"""Exercise the actual worker/catalog/log pipeline, without a live Codex turn."""
from contextlib import closing
import json
from pathlib import Path
import queue
import sqlite3
import tempfile
import time
import unittest

from app import Poller


class WorkerTests(unittest.TestCase):
    def test_active_to_completed_flows_through_worker(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d)
            log=home/'rollout.jsonl'

            def append(kind):
                e={'type':'event_msg','payload':{'type':kind,'turn_id':'turn-1'}}
                with log.open('a',encoding='utf-8') as f:
                    f.write(json.dumps(e)+'\n')

            append('task_started')
            with closing(sqlite3.connect(home/'state_5.sqlite')) as db:
                db.execute('CREATE TABLE threads(id,title,cwd,rollout_path,source,archived,updated_at)')
                db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?)',
                           ('t','Chat','D:/project',str(log),'vscode',0,1))
                db.commit()
            output,commands=queue.Queue(),queue.Queue()
            commands.put(['t'])
            worker=Poller(home,output,commands)
            worker.start()

            def wait_status(expected):
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    kind,value=output.get(timeout=5)
                    if kind=='snapshot' and value[0].get('t',{}).get('status')==expected:
                        return
                self.fail('No worker snapshot for '+expected)

            try:
                wait_status('active')
                append('task_complete')
                wait_status('completed')
                log.unlink()
                wait_status('unknown')
            finally:
                worker.stop_event.set()
                worker.join(timeout=3)
                self.assertFalse(worker.is_alive())


if __name__=='__main__':
    unittest.main()
