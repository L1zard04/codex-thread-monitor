import unittest
from unittest.mock import patch

from navigation import open_thread, thread_url


class NavigationTests(unittest.TestCase):
    def test_thread_identifier_cannot_inject_uri_path_or_query(self):
        self.assertEqual(thread_url('abc?x=1/2'),'codex://threads/abc%3Fx%3D1%2F2')

    def test_open_uses_os_protocol_handler_without_sending_a_message(self):
        with patch('navigation.os.startfile') as start:
            open_thread('thread-123')
        start.assert_called_once_with('codex://threads/thread-123')


if __name__=='__main__':
    unittest.main()
