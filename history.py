"""Bounded, local-only history of monitor selections and observed states."""
from datetime import datetime
import json
from pathlib import Path


class StatusHistory:
    def __init__(self, path, max_bytes=1024*1024, backups=3):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.backups = backups
        self.error = ''

    def record(self, event, slots, snapshot=None):
        snapshot = snapshot or {}
        entries = []
        for index, slot in enumerate(slots):
            if slot:
                state = snapshot.get(slot['id'], {})
                entries.append({'slot':index+1, 'id':slot['id'],
                                'status':state.get('status','unknown'),
                                'turn_id':state.get('turn_id',''),
                                'event_time':state.get('event_time','')})
        record = {'time':datetime.now().astimezone().isoformat(timespec='seconds'),
                  'event':event, 'slots':entries}
        line = (json.dumps(record, ensure_ascii=False)+'\n').encode('utf-8')
        try:
            if len(line) > self.max_bytes:
                raise OSError('单条状态记录超过日志大小上限')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size+len(line) > self.max_bytes:
                for index in range(self.backups, 0, -1):
                    source = self.path if index == 1 else Path(f'{self.path}.{index-1}')
                    if source.exists():
                        source.replace(Path(f'{self.path}.{index}'))
            with self.path.open('ab') as stream:
                stream.write(line)
            self.error = ''
            return True
        except OSError as error:
            self.error = f'状态日志保存失败：{error}'
            return False
