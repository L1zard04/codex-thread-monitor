"""Read-only adapters for Codex's local catalogue and rollout events."""
from __future__ import annotations

import json
import ntpath
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


def normal_path(value: str) -> str:
    if value.startswith('\\\\?\\'):
        value = value[4:]
    return ntpath.normcase(ntpath.normpath(value))


@dataclass(frozen=True)
class Thread:
    id: str
    title: str
    project: str
    cwd: str
    rollout_path: str


class Catalog:
    def __init__(self, home: Path):
        self.home = Path(home)

    def list_threads(self) -> list[Thread]:
        path = self.home/'state_5.sqlite'
        uri = path.resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=0.5)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            columns = {r[1] for r in conn.execute('PRAGMA table_info(threads)')}
            required = {'id', 'title', 'cwd', 'rollout_path', 'archived', 'source', 'updated_at'}
            if not required <= columns:
                raise RuntimeError('Codex 本地数据格式已变化，无法读取对话目录')
            pid = 'project_id' if 'project_id' in columns else 'NULL AS project_id'
            rows = conn.execute(
                f'SELECT id,title,cwd,rollout_path,source,{pid} '
                'FROM threads WHERE archived=0 ORDER BY updated_at DESC').fetchall()
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            projects = dict(conn.execute('SELECT id,name FROM projects')) if 'projects' in tables else {}
        rows = [r for r in rows if 'subagent' not in (r['source'] or '').lower()]
        labels = {normal_path(r['cwd']): projects[r['project_id']]
                  for r in rows if r['project_id'] in projects}
        # Desktop project assignments may not yet be migrated into SQLite.
        try:
            desktop = json.loads((self.home/'.codex-global-state.json').read_text(encoding='utf-8'))
            if not isinstance(desktop, dict):
                desktop = {}
        except (OSError, ValueError):
            desktop = {}
        local = desktop.get('local-projects', {})
        assignments = desktop.get('thread-project-assignments', {})
        local = local if isinstance(local,dict) else {}
        assignments = assignments if isinstance(assignments,dict) else {}
        for project in local.values():
            if isinstance(project,dict) and isinstance(project.get('name'),str):
                roots = project.get('rootPaths',[])
                if isinstance(roots,list):
                    for root in roots:
                        if isinstance(root,str):
                            labels[normal_path(root)] = project['name']

        def project_name(row):
            assignment = assignments.get(row['id'],{})
            if isinstance(assignment,dict) and assignment.get('projectKind')=='local':
                project = local.get(assignment.get('projectId'),{})
                if isinstance(project,dict) and isinstance(project.get('name'),str):
                    return project['name']
            return (labels.get(normal_path(row['cwd'])) or projects.get(row['project_id'])
                    or ntpath.basename(row['cwd'].removeprefix('\\\\?\\').rstrip('\\/')) or '未分组')

        return [Thread(r['id'], r['title'] or '未命名对话',
                       project_name(r),
                       r['cwd'], r['rollout_path']) for r in rows]


@dataclass
class TurnState:
    status: str = 'unknown'
    turn_id: str | None = None
    timestamp: str = ''
    answer: str = ''
    answer_turn_id: str | None = None

    def apply(self, event: dict) -> None:
        if event.get('type') != 'event_msg':
            return
        p = event.get('payload')
        if not isinstance(p, dict):
            return
        kind = p.get('type')
        if kind not in ('task_started', 'task_complete', 'turn_aborted'):
            return
        turn = p.get('turn_id')
        root = p.get('root_turn_id')
        if turn and root and turn != root:
            return  # A child task completing doesn't complete the root turn.
        if kind == 'task_started':
            self.turn_id, self.status = turn, 'active'
        elif self.turn_id is None or turn == self.turn_id or (kind == 'turn_aborted' and not turn):
            self.turn_id = turn or self.turn_id
            self.status = 'completed' if kind == 'task_complete' else 'interrupted'
            if kind == 'task_complete':
                message = p.get('last_agent_message')
                self.answer = message if isinstance(message,str) else ''
                self.answer_turn_id = self.turn_id
        else:
            return
        self.timestamp = event.get('timestamp') or ''


class LogReader:
    """Poll append-only JSONL; hold incomplete bytes until their newline arrives."""
    def __init__(self, path: Path):
        self.path = Path(path)
        self.offset = 0
        self.state = TurnState()
        self.error = ''
        self._identity = None
        self._prefix = b''
        self._buffer = b''
        self._checkpoint = b''
        self.catching_up = False

    def _reset(self):
        self.offset = 0
        self.state = TurnState()
        self._buffer = b''
        self._prefix = b''
        self._checkpoint = b''

    def poll(self) -> TurnState:
        try:
            with self.path.open('rb') as f:
                st = os.fstat(f.fileno())
                identity = (st.st_dev, st.st_ino)
                head = f.read(128)
                f.seek(max(0,self.offset-len(self._checkpoint)))
                changed_checkpoint = f.read(len(self._checkpoint)) != self._checkpoint
                if (self._identity != identity or st.st_size < self.offset
                        or changed_checkpoint
                        or (self._prefix and not head.startswith(self._prefix))):
                    self._reset()
                self._identity = identity
                if not self._prefix:
                    self._prefix = head
                f.seek(self.offset)
                data = f.read(8 * 1024 * 1024)
                self.offset = f.tell()
                self.catching_up = self.offset < st.st_size
                f.seek(max(0,self.offset-256))
                self._checkpoint = f.read(self.offset-f.tell())
            lines = (self._buffer + data).split(b'\n')
            self._buffer = lines.pop()
            for line in lines:
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(record, dict):
                    self.state.apply(record)
            self.error = ''
        except OSError as e:
            self._reset()
            self._identity = None
            self.catching_up = False
            self.error = f'日志不可读：{e.strerror or type(e).__name__}'
        return self.state


class ConfigStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.error = ''

    def load(self) -> dict:
        default = {'slots': [None]*4, 'topmost': True, 'attention':{}, 'geometry':None,
                   'view_mode':'full', 'notifications':False}
        if not self.path.exists():
            return default
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('配置应为对象')
            slots = data.get('slots')
            if not isinstance(slots, list) or len(slots) != 4:
                raise ValueError('配置必须包含四个格子')
            if any(x is not None and (not isinstance(x, dict) or not isinstance(x.get('id'), str))
                   for x in slots):
                raise ValueError('格子配置无效')
            self.error = ''
            raw = data.get('attention',{})
            ids = {slot['id'] for slot in slots if slot}
            attention = {tid:{'latest':entry['latest'], 'read':entry['read'],
                              'unread':bool(entry.get('unread',False))}
                         for tid,entry in (raw.items() if isinstance(raw,dict) else [])
                         if tid in ids and isinstance(entry,dict) and
                         isinstance(entry.get('latest'),str) and isinstance(entry.get('read'),str)}
            return {'slots': slots, 'topmost': bool(data.get('topmost', True)),
                    'attention':attention, 'geometry':data.get('geometry'),
                    'view_mode':'compact' if data.get('view_mode')=='compact' else 'full',
                    'notifications':data.get('notifications') is True}
        except (OSError, ValueError) as e:
            self.error = f'配置读取失败，已使用空面板：{e}'
            return default

    def save(self, config: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        temporary.replace(self.path)
