import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from app import MonitorApp
from core import ConfigStore, Thread


class FakeDesktop:
    available=True
    error=''
    def __init__(self):
        self.messages=[]
        self.closed=False
    def notify(self,title,body):
        self.messages.append((title,body))
    def close(self):
        self.closed=True


class DesktopFeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.store=ConfigStore(Path(self.tmp.name)/'config.json')
        self.app=MonitorApp(self.root,config_store=self.store,start_worker=False)
        self.desktop=FakeDesktop()
        self.app.attach_desktop(self.desktop)
        self.thread=Thread('a','Chat','Project','D:/work','a.jsonl')
        self.app.bind_slot(0,self.thread)

    def completed(self,turn):
        return {'status':'completed','turn_id':turn,'answer_turn_id':turn,'answer':'Private '+turn}

    def test_compact_mode_persists_and_reader_preserves_old_result_until_refresh(self):
        self.app.set_view_mode('compact')
        self.assertEqual(self.store.load()['view_mode'],'compact')
        self.app.apply_snapshot({'a':self.completed('one')})
        reader=self.app.open_reader(0)
        self.assertEqual(reader.text.get('1.0','end-1c'),'Private one')
        self.app.apply_snapshot({'a':self.completed('two')})
        self.assertEqual(reader.text.get('1.0','end-1c'),'Private one')
        self.assertIn('新回答',reader.note.get())
        reader.mark_read()
        self.assertIn('未读',self.app.status_widgets[0]['text'])
        reader.refresh()
        self.assertEqual(reader.text.get('1.0','end-1c'),'Private two')
        self.assertNotIn('未读',self.app.status_widgets[0]['text'])
        reader.copy_answer()
        self.assertEqual(self.root.clipboard_get(),'Private two')

    def test_notification_is_opt_in_deduplicated_and_contains_no_private_text(self):
        self.assertFalse(self.app.notifications.get())
        self.app.apply_snapshot({'a':self.completed('old')})
        self.app.notifications.set(True)
        self.app.toggle_notifications()
        self.app.bind_slot(1,self.thread)
        self.app.apply_snapshot({'a':self.completed('new')})
        self.app.apply_snapshot({'a':self.completed('new')})
        self.app.flush_notifications()
        self.assertEqual(len(self.desktop.messages),1)
        self.assertNotIn('Private',str(self.desktop.messages))
        self.assertIn('1',self.desktop.messages[0][1])
        self.app.notifications.set(False)
        self.app.toggle_notifications()
        self.app.apply_snapshot({'a':self.completed('later')})
        self.app.flush_notifications()
        self.assertEqual(len(self.desktop.messages),1)

    def test_already_viewed_completion_is_not_notified_after_merge_delay(self):
        self.app.notifications.set(True)
        self.app.toggle_notifications()
        self.app.apply_snapshot({'a':{'status':'active','turn_id':'one'}})
        self.app.apply_snapshot({'a':self.completed('one')})
        self.app.mark_slot(0)
        self.app.flush_notifications()
        self.assertEqual(self.desktop.messages,[])

    def test_simultaneous_completions_are_merged(self):
        self.app.bind_slot(1,Thread('b','Other','Project B','D:/b','b.jsonl'))
        self.app.notifications.set(True)
        self.app.toggle_notifications()
        self.app.apply_snapshot({'a':{'status':'active'},'b':{'status':'active'}})
        self.app.apply_snapshot({'a':self.completed('one'),'b':self.completed('two')})
        self.app.flush_notifications()
        self.assertEqual(len(self.desktop.messages),1)
        self.assertIn('2',self.desktop.messages[0][1])

    def test_reopening_existing_reader_does_not_replace_old_body_or_mark_new_read(self):
        self.app.apply_snapshot({'a':self.completed('one')})
        reader=self.app.open_reader(0)
        self.app.apply_snapshot({'a':self.completed('two')})
        reader.withdraw()
        self.assertIs(self.app.open_reader(0),reader)
        self.assertEqual(reader.text.get('1.0','end-1c'),'Private one')
        self.assertIn('未读',self.app.status_widgets[0]['text'])

    def test_tray_hide_restore_and_failure_does_not_strand_window(self):
        self.root.deiconify()
        self.root.update()
        self.app.hide_to_tray()
        self.assertEqual(self.root.state(),'withdrawn')
        self.app.output.put(('desktop','show'))
        self.app.consume()
        self.root.update()
        self.assertEqual(self.root.state(),'normal')
        self.desktop.available=False
        self.app.hide_to_tray()
        self.assertEqual(self.root.state(),'normal')

    def test_reader_keeps_original_thread_after_slot_replacement(self):
        self.app.apply_snapshot({'a':self.completed('one')})
        reader=self.app.open_reader(0)
        self.app.bind_slot(0,Thread('b','Other','B','D:/b','b.jsonl'))
        with patch('app.open_thread') as opened:
            reader.open_source()
        opened.assert_called_once_with('a')

    def test_closing_reader_releases_cached_answer_and_tray_failure_restores_window(self):
        self.app.apply_snapshot({'a':self.completed('one')})
        reader=self.app.open_reader(0)
        reader.destroy()
        self.assertNotIn('a',self.app.readers)
        self.root.deiconify()
        self.root.update()
        self.app.hide_to_tray()
        self.desktop.available=False
        self.app.output.put(('desktop_error','lost icon'))
        self.app.consume()
        self.root.update()
        self.assertEqual(self.root.state(),'normal')
        self.assertIn('lost icon',self.app.footer.get())
        self.desktop.available=True
        self.app.output.put(('desktop_ready',None))
        self.app.consume()
        self.assertEqual(str(self.app.hide_button['state']),'normal')
        self.assertEqual(self.app.desktop_error,'')

    def test_close_releases_desktop_integration(self):
        window=tk.Toplevel(self.root)
        app=MonitorApp(window,config_store=self.store,start_worker=False)
        desktop=FakeDesktop()
        app.attach_desktop(desktop)
        app.close()
        self.assertTrue(desktop.closed)
