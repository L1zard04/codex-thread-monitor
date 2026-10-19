"""Official Codex local thread deep links, opened via the OS URI handler."""
import os
from urllib.parse import quote


def thread_url(thread_id):
    return 'codex://threads/'+quote(thread_id,safe='')


def open_thread(thread_id):
    os.startfile(thread_url(thread_id))
