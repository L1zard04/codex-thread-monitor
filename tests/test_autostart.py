import tempfile
import unittest
from pathlib import Path

from autostart import InstanceLock, LaunchPolicy, is_codex_desktop


class AutoStartTests(unittest.TestCase):
    def test_identifies_codex_desktop_not_vscode_or_regular_chatgpt(self):
        self.assertTrue(is_codex_desktop(r'C:\Program Files\WindowsApps\OpenAI.Codex_26.1007.9446.0_x64__abc\app\ChatGPT.exe'))
        self.assertFalse(is_codex_desktop(r'C:\Program Files\WindowsApps\OpenAI.ChatGPT_1_x64__abc\app\ChatGPT.exe'))
        self.assertFalse(is_codex_desktop(r'C:\Users\u\.vscode\extensions\openai.chatgpt\codex.exe'))
        self.assertFalse(is_codex_desktop(r'C:\Users\u\AppData\Local\OpenAI\Codex\bin\hash\codex.exe'))

    def test_launches_on_open_not_repeatedly_after_manual_close(self):
        launched=[]
        p=LaunchPolicy(lambda:launched.append('open'))
        p.tick(False)
        p.tick(True)
        p.tick(True)
        self.assertEqual(launched,['open'])
        p.tick(False)
        p.tick(True)
        self.assertEqual(launched,['open','open'])

    def test_detection_failure_does_not_create_fake_close_open_edge(self):
        launched=[]
        p=LaunchPolicy(lambda:launched.append('open'))
        p.tick(True)
        p.tick(None)
        p.tick(True)
        self.assertEqual(launched,['open'])

    def test_instances_are_exclusive_and_released(self):
        with tempfile.TemporaryDirectory() as d:
            key=str(Path(d)/'app')
            a,b=InstanceLock(key),InstanceLock(key)
            try:
                self.assertTrue(a.acquire())
                self.assertFalse(b.acquire())
                a.close()
                self.assertTrue(b.acquire())
            finally:
                a.close()
                b.close()


if __name__=='__main__':
    unittest.main()
