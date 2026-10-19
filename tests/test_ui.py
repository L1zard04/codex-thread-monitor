import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
from pathlib import Path

from app import MonitorApp, COLORS, Picker
from core import ConfigStore, Thread


class UiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.store = ConfigStore(Path(self.tmp.name)/'config.json')
        self.app = MonitorApp(self.root, config_store=self.store, start_worker=False)
        self.thread = Thread('a', 'Example conversation', 'Project A', 'D:/work', 'a.jsonl')

    def test_four_empty_white_slots(self):
        self.assertEqual(len(self.app.tiles), 4)
        for tile in self.app.tiles:
            self.assertEqual(tile['background'], COLORS['empty'])
        self.assertEqual(self.app.config['slots'], [None]*4)

    def test_tiles_are_visible_equal_squares(self):
        self.root.deiconify()
        self.root.update()
        sizes = [(t.winfo_width(),t.winfo_height()) for t in self.app.tiles]
        self.assertTrue(all(w >= 145 and h >= 145 for w,h in sizes),sizes)
        self.assertTrue(all(abs(w-h)<=1 for w,h in sizes),sizes)

    def test_resize_preserves_square_tiles_in_wide_and_tall_windows(self):
        self.assertTrue(all(self.root.resizable()))
        self.root.deiconify()
        for size in ['700x500','440x700','320x380']:
            self.root.geometry(size)
            self.root.update()
            sizes=[(t.winfo_width(),t.winfo_height()) for t in self.app.tiles]
            self.assertTrue(all(w>=100 and abs(w-h)<=1 for w,h in sizes),sizes)
        self.assertGreater(self.app.tiles[0].winfo_width(),100)

    def test_latest_answer_is_displayed_in_readonly_scrollable_text(self):
        self.app.bind_slot(0,self.thread)
        answer='The latest result\n'+'Detailed explanation.\n'*50
        self.app.apply_snapshot({'a':{'status':'completed','detail':'end','answer':answer}})
        text=self.app.answer_widgets[0]
        self.assertEqual(text.get('1.0','end-1c'),answer)
        self.assertEqual(text['state'],'disabled')
        self.assertEqual(self.app.tiles[0]['background'],COLORS['completed'])
        self.app.apply_snapshot({'a':{'status':'active','detail':'new turn','answer':answer}})
        self.assertEqual(self.app.answer_widgets[0].get('1.0','end-1c'),answer)
        self.assertEqual(self.app.tiles[0]['background'],COLORS['active'])

    def test_another_tile_update_does_not_recreate_current_reading_widget(self):
        self.app.bind_slot(0,self.thread)
        other=Thread('b','Other','Project B','D:/b','b.jsonl')
        self.app.bind_slot(1,other)
        first={'status':'completed','detail':'done','answer':'Long answer\n'*100}
        self.app.apply_snapshot({'a':first,'b':{'status':'active'}})
        text=self.app.answer_widgets[0]
        self.app.apply_snapshot({'a':first,'b':{'status':'completed','answer':'New result'}})
        self.assertIs(text,self.app.answer_widgets[0])

    def test_binding_status_clear_and_restart(self):
        self.app.bind_slot(0, self.thread)
        self.assertEqual(self.app.config['slots'][0]['id'], 'a')
        self.app.apply_snapshot({'a': {'status':'active','detail':'observed start'}})
        self.assertEqual(self.app.tiles[0]['background'], COLORS['active'])
        self.app.apply_snapshot({'a': {'status':'completed','detail':'observed end'}})
        self.assertEqual(self.app.tiles[0]['background'], COLORS['completed'])
        self.assertEqual(self.store.load()['slots'][0]['id'], 'a')
        self.app.clear_slot(0)
        self.assertIsNone(self.store.load()['slots'][0])
        self.assertEqual(self.app.tiles[0]['background'], COLORS['empty'])

    def test_topmost_preference_persists(self):
        self.app.topmost.set(False)
        self.app.toggle_topmost()
        self.assertFalse(self.store.load()['topmost'])

    def test_bound_slot_restores_without_pretending_completed(self):
        cfg = self.store.load()
        cfg['slots'][1] = {'id':'a','title':'Saved chat','project':'Project A'}
        self.store.save(cfg)
        extra = tk.Toplevel(self.root)
        try:
            app = MonitorApp(extra, config_store=self.store, start_worker=False)
            self.assertEqual(app.config['slots'][1]['id'], 'a')
            self.assertEqual(app.tiles[1]['background'], COLORS['unknown'])
        finally:
            extra.destroy()

    def test_picker_filters_project_and_title_then_selects(self):
        selected=[]
        threads=[self.thread,Thread('b','Other chat','Project B','D:/b','b.jsonl')]
        picker = Picker(self.root, threads, selected.append)
        picker.project.set('Project A')
        picker.search.set('Example')
        picker.refresh()
        self.assertEqual([t.id for t in picker.visible], ['a'])
        picker.listbox.selection_set(0)
        picker.choose()
        self.assertEqual(selected[0].id,'a')

    def test_open_button_opens_exact_bound_chat(self):
        self.app.bind_slot(0,self.thread)
        with patch('app.open_thread') as open_chat:
            self.app.open_buttons[0].invoke()
        open_chat.assert_called_once_with('a')

    def test_empty_slot_has_no_open_button(self):
        self.assertEqual(self.app.open_buttons,[None]*4)


if __name__=='__main__':
    unittest.main()
