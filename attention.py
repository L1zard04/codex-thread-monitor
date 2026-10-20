"""Local read markers for completed turns; never persist answer text."""
import hashlib


def completion_token(state):
    if state.get('status') == 'unknown':
        return None
    answer_turn = state.get('answer_turn_id')
    if answer_turn and state.get('answer'):
        return 'turn:'+answer_turn
    if state.get('status') != 'completed':
        return None
    if state.get('turn_id'):
        return 'turn:'+state['turn_id']
    if state.get('event_time'):
        return 'event:'+state['event_time']
    if state.get('answer'):
        return 'answer:'+hashlib.sha256(state['answer'].encode('utf-8')).hexdigest()
    return None


def update_attention(saved, thread_id, state):
    if state.get('status','unknown') == 'unknown':
        return False
    token = completion_token(state)
    if thread_id not in saved:
        saved[thread_id] = {'latest':token or '', 'read':token or '', 'unread':False}
        return True
    entry = saved[thread_id]
    if token and token != entry['latest']:
        entry['latest'] = token
        return True
    return False


def is_unread(saved, thread_id):
    entry = saved.get(thread_id,{})
    return bool(entry.get('unread') or entry.get('latest','') != entry.get('read',''))


def mark_viewed(saved, thread_id, unread=False):
    entry = saved.get(thread_id)
    if entry is None:
        return False
    before = dict(entry)
    entry['read'] = entry['latest']
    entry['unread'] = unread
    return before != entry
