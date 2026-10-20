"""Passive quota snapshots from local rollout tails. No network or inference."""
from contextlib import closing
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import time


def timestamp(value):
    if not isinstance(value,str):
        return None
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:
            return None
        return parsed.timestamp()
    except (ValueError,OverflowError,OSError):
        return None


class QuotaReader:
    def __init__(self,home):
        self.home=Path(home)
        self.cache={}

    def poll(self):
        try:
            uri=(self.home/'state_5.sqlite').resolve().as_uri()+'?mode=ro'
            with closing(sqlite3.connect(uri,uri=True,timeout=.5)) as db:
                db.execute('PRAGMA query_only=ON')
                rows=db.execute('SELECT rollout_path FROM threads ORDER BY updated_at DESC LIMIT 32').fetchall()
            paths={Path(row[0]) for row in rows if isinstance(row[0],str) and row[0]}
            self.cache={path:value for path,value in self.cache.items() if path in paths}
            samples=[]
            for path in paths:
                try:
                    stat=path.stat()
                    signature=(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns)
                    cached=self.cache.get(path)
                    if not cached or cached[0]!=signature:
                        with path.open('rb') as stream:
                            offset=max(0,stat.st_size-512*1024)
                            stream.seek(offset)
                            data=stream.read(512*1024)
                        lines=data.split(b'\n')[:-1]
                        if offset and lines:
                            lines=lines[1:]
                        latest=None
                        for line in lines:
                            try:
                                record=json.loads(line)
                            except (ValueError,UnicodeError):
                                continue
                            if not isinstance(record,dict) or record.get('type')!='event_msg':
                                continue
                            payload=record.get('payload')
                            if not isinstance(payload,dict) or payload.get('type')!='token_count':
                                continue
                            limits=payload.get('rate_limits')
                            stamp=timestamp(record.get('timestamp'))
                            if (isinstance(limits,dict) and limits.get('limit_id') in (None,'codex')
                                    and stamp is not None):
                                if latest is None or stamp>latest[0]:
                                    # Persist only quota metadata in memory, never the full event or text.
                                    latest=(stamp,{'timestamp':record['timestamp'],
                                                   'limits':{key:limits.get(key) for key in ('primary','secondary')}})
                        cached=(signature,latest)
                        self.cache[path]=cached
                    if cached[1] is not None:
                        samples.append(cached[1])
                except OSError:
                    continue
            if samples:
                return max(samples,key=lambda sample:sample[0])[1]
            return {'error':'无法读取额度快照：最近日志未记录或暂时不可读'}
        except (OSError,sqlite3.Error) as error:
            return {'error':f'无法读取本地额度目录：{error}'}


def quota_display(snapshot,now=None):
    now=time.time() if now is None else now
    stamp=timestamp(snapshot.get('timestamp'))
    limits=snapshot.get('limits')
    limits=limits if isinstance(limits,dict) else {}
    details=['只读本机额度快照，不联网、不发起模型请求。',
             'Codex 写入新的额度事件后才更新；不是实时账户查询。']
    texts=[]
    for label,minutes in [('5小时',300),('7天',10080)]:
        window=next((value for value in (limits.get('primary'),limits.get('secondary'))
                     if isinstance(value,dict) and value.get('window_minutes')==minutes),{})
        used,reset=window.get('used_percent'),window.get('resets_at')
        valid_used=(type(used) in (int,float) and math.isfinite(used) and 0<=used<=100)
        valid_reset=(type(reset) in (int,float) and math.isfinite(reset) and reset>0)
        if stamp is None or stamp>now+300 or not valid_used:
            texts.append(label+' --')
            details.append(label+'：暂无可靠额度数据')
            continue
        if valid_reset and now>=reset:
            texts.append(label+'待更新')
            details.append(label+'：记录的重置时间已过，等待新的官方快照，不能据此推定满额')
            continue
        texts.append(f'{label}剩余 {100-used:g}%')
        try:
            reset_text=datetime.fromtimestamp(reset,timezone.utc).astimezone().strftime('%m-%d %H:%M') if valid_reset else '未知'
        except (ValueError,OverflowError,OSError):
            reset_text='未知'
        details.append(f'{label}：已用 {used:g}%；重置时间 {reset_text}')
    if stamp is not None:
        age=max(0,int(now-stamp))
        local=datetime.fromtimestamp(stamp,timezone.utc).astimezone()
        texts.append(('旧快照 ' if age>900 else '本地快照 ')+local.strftime('%H:%M'))
        details.append('快照记录时间：'+local.strftime('%Y-%m-%d %H:%M:%S %z'))
    else:
        texts.append('本地快照')
    if snapshot.get('error'):
        details.append(snapshot['error'])
    return ' · '.join(texts),'\n'.join(details)
