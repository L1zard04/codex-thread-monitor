import json
from datetime import datetime,timezone
import time
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

    def history_entries(self):
        return [json.loads(line) for line in
                (Path(self.tmp.name)/'status-history.jsonl').read_text(encoding='utf-8').splitlines()]

    def test_restore_sends_saved_selection_and_reports_count(self):
        self.app.bind_slot(2,self.thread)
        self.app.clear_slot(0)
        extra=tk.Toplevel(self.root)
        try:
            app=MonitorApp(extra,config_store=self.store,start_worker=False)
            self.assertEqual(app.commands.get_nowait(),['a'])
            self.assertIn('恢复 1 个',app.footer.get())
            self.assertIsNone(app.config['slots'][0])
            self.assertEqual(app.config['slots'][2]['id'],'a')
            self.assertEqual(app.tiles[2]['background'],COLORS['unknown'])
            self.assertEqual(self.history_entries()[-1]['event'],'restore')
        finally:
            extra.destroy()

    def test_status_history_deduplicates_polling_but_tracks_new_turn(self):
        self.app.bind_slot(0,self.thread)
        first={'status':'active','turn_id':'one','event_time':'now','answer':'secret'}
        self.app.apply_snapshot({'a':first})
        self.app.apply_snapshot({'a':dict(first,detail='changed detail')})
        self.app.apply_snapshot({'a':dict(first,turn_id='two')})
        self.app.apply_snapshot({'a':dict(first,status='completed',turn_id='two')})
        entries=[e for e in self.history_entries() if e['event']=='status']
        self.assertEqual(len(entries),3)
        self.assertNotIn('secret',json.dumps(entries))

    def test_close_saves_current_slots_and_records_close(self):
        extra=tk.Toplevel(self.root)
        app=MonitorApp(extra,config_store=self.store,start_worker=False)
        app.config['slots'][3]={'id':'b','title':'Saved','project':'B'}
        app.close()
        self.assertEqual(self.store.load()['slots'][3]['id'],'b')
        self.assertEqual(self.history_entries()[-1]['event'],'close')
        self.assertEqual(self.history_entries()[-1]['slots'][0]['slot'],4)

    def test_history_failure_does_not_stop_monitor_or_disappear_on_poll(self):
        path=Path(self.tmp.name)/'status-history.jsonl'
        path.unlink()
        path.mkdir()
        with self.assertLogs(level='WARNING'):
            self.app.bind_slot(0,self.thread)
            self.app.output.put(('snapshot',({'a':{'status':'completed'}},'')))
            self.app.consume()
        self.assertEqual(self.app.tiles[0]['background'],COLORS['completed'])
        self.assertIn('日志',self.app.footer.get())
        self.root.after_cancel(self.app.poll_job)
        self.app.poll_job=None

    def test_close_reopen_keeps_positions_and_cleared_slots(self):
        extra=tk.Toplevel(self.root)
        app=MonitorApp(extra,config_store=self.store,start_worker=False)
        app.bind_slot(0,self.thread)
        app.bind_slot(2,Thread('b','Other','Project B','D:/b','b.jsonl'))
        app.bind_slot(3,self.thread)
        app.clear_slot(3)
        app.apply_snapshot({'a':{'status':'completed','answer':'Old result'}})
        app.close()
        reopened=tk.Toplevel(self.root)
        try:
            restored=MonitorApp(reopened,config_store=self.store,start_worker=False)
            self.assertEqual(restored.commands.get_nowait(),['a','b'])
            self.assertEqual([s['id'] if s else None for s in restored.config['slots']],
                             ['a',None,'b',None])
            self.assertIn('恢复 2 个',restored.footer.get())
            self.assertEqual(restored.tiles[0]['background'],COLORS['unknown'])
            restored.apply_snapshot({'a':{'status':'active','turn_id':'new'}})
            self.assertEqual(restored.tiles[0]['background'],COLORS['active'])
        finally:
            reopened.destroy()

    def test_configuration_failure_does_not_block_selection_or_close(self):
        extra=tk.Toplevel(self.root)
        app=MonitorApp(extra,config_store=self.store,start_worker=False)
        with patch.object(self.store,'save',side_effect=PermissionError('read only')):
            with self.assertLogs(level='ERROR'):
                app.bind_slot(0,self.thread)
                self.assertIn('设置保存失败',app.footer.get())
                app.set_footer('normal poll')
                self.assertIn('设置保存失败',app.footer.get())
                app.close()
        self.assertTrue(app.closed)

    def test_unread_survives_reopen_and_only_successful_open_marks_read(self):
        self.app.bind_slot(0,self.thread)
        self.app.apply_snapshot({'a':{'status':'active','turn_id':'one'}})
        completed={'a':{'status':'completed','turn_id':'one','answer_turn_id':'one','answer':'Done'}}
        self.app.apply_snapshot(completed)
        self.assertIn('未读',self.app.status_widgets[0]['text'])
        self.assertTrue(self.store.load()['attention']['a']['latest'])
        extra=tk.Toplevel(self.root)
        try:
            app=MonitorApp(extra,config_store=self.store,start_worker=False)
            app.apply_snapshot(completed)
            self.assertIn('未读',app.status_widgets[0]['text'])
            with patch('app.open_thread',side_effect=OSError('failed')), patch('app.messagebox.showerror'):
                app.go_to_thread(0)
            self.assertIn('未读',app.status_widgets[0]['text'])
            with patch('app.open_thread'):
                app.go_to_thread(0)
            self.assertNotIn('未读',app.status_widgets[0]['text'])
            app.apply_snapshot(completed)
            self.assertNotIn('未读',app.status_widgets[0]['text'])
            app.mark_slot(0,unread=True)
            self.assertIn('未读',app.status_widgets[0]['text'])
        finally:
            extra.destroy()

    def test_bound_title_opens_chat_and_visible_menu_has_management_actions(self):
        self.app.bind_slot(0,self.thread)
        self.root.deiconify()
        self.root.update()
        title=self.app.title_widgets[0]
        with patch('app.open_thread') as open_chat, patch.object(self.app,'open_picker') as picker:
            title.event_generate('<Button-1>')
            self.root.update()
        open_chat.assert_called_once_with('a')
        picker.assert_not_called()
        self.assertTrue(self.app.menu_buttons[0].winfo_ismapped())
        menu=self.app.build_menu(0)
        try:
            labels=[menu.entrycget(i,'label') for i in range(menu.index('end')+1)
                    if menu.type(i)=='command']
            self.assertIn('选择 / 更换对话',labels)
            self.assertIn('标记为未读',labels)
        finally:
            menu.destroy()

    def test_clear_can_be_undone_without_affecting_other_slots(self):
        self.app.bind_slot(0,self.thread)
        self.app.bind_slot(1,Thread('b','Other','Project B','D:/b','b.jsonl'))
        self.app.clear_slot(0)
        self.app.undo_clear()
        self.assertEqual([s['id'] if s else None for s in self.store.load()['slots']],
                         ['a','b',None,None])
        self.assertIsNone(self.app.undo_selection)

    def test_management_popup_is_not_destroyed_before_user_chooses(self):
        self.app.bind_slot(0,self.thread)
        self.root.deiconify()
        self.root.update()
        with patch.object(tk.Menu,'tk_popup') as popup:
            self.app.button_menu(0)
        popup.assert_called_once()
        self.assertTrue(self.app.context_menu.winfo_exists())

    def test_rebinding_cleared_slot_prevents_stale_undo(self):
        self.app.bind_slot(0,self.thread)
        self.app.clear_slot(0)
        self.app.bind_slot(0,Thread('b','Other','Project B','D:/b','b.jsonl'))
        self.app.undo_clear()
        self.assertEqual(self.store.load()['slots'][0]['id'],'b')

    def test_rebinding_same_idle_chat_does_not_miss_next_completion(self):
        self.app.bind_slot(0,self.thread)
        first={'a':{'status':'completed','turn_id':'one','answer':'First'}}
        self.app.apply_snapshot(first)
        self.app.clear_slot(0)
        self.app.bind_slot(0,self.thread)
        self.app.apply_snapshot(first)
        self.app.apply_snapshot({'a':{'status':'completed','turn_id':'two','answer':'Next'}})
        self.assertIn('未读',self.app.status_widgets[0]['text'])

    def test_undo_keeps_newer_read_marker_from_duplicate_slot(self):
        self.app.bind_slot(0,self.thread)
        self.app.bind_slot(1,self.thread)
        self.app.apply_snapshot({'a':{'status':'active'}})
        self.app.apply_snapshot({'a':{'status':'completed','turn_id':'one','answer':'Done'}})
        self.app.clear_slot(0)
        self.app.mark_slot(1)
        self.app.undo_clear()
        self.assertNotIn('未读',self.app.status_widgets[0]['text'])
        self.assertNotIn('未读',self.app.status_widgets[1]['text'])

    def test_marking_read_preserves_answer_widget_and_selection(self):
        self.app.bind_slot(0,self.thread)
        self.app.apply_snapshot({'a':{'status':'active'}})
        self.app.apply_snapshot({'a':{'status':'completed','turn_id':'one','answer':'Answer'}})
        text=self.app.answer_widgets[0]
        text.tag_add('sel','1.0','1.3')
        self.app.mark_slot(0)
        self.assertIs(self.app.answer_widgets[0],text)
        self.assertEqual(tuple(str(i) for i in text.tag_ranges('sel')),('1.0','1.3'))

    def test_normal_geometry_saved_and_restored(self):
        self.root.deiconify()
        self.root.geometry('520x640+80+90')
        self.root.update()
        self.app.remember_geometry()
        saved=self.store.load()['geometry']
        self.assertEqual((saved['width'],saved['height']),(520,640))
        extra=tk.Toplevel(self.root)
        try:
            app=MonitorApp(extra,config_store=self.store,start_worker=False)
            extra.update()
            self.assertEqual((extra.winfo_width(),extra.winfo_height()),(520,640))
        finally:
            extra.destroy()

    def test_quota_header_consumes_passive_snapshot_without_changing_selection(self):
        self.app.bind_slot(0,self.thread)
        now=time.time()
        self.app.output.put(('quota',{'timestamp':datetime.fromtimestamp(now,timezone.utc).isoformat(),
                                      'limits':{'primary':{'used_percent':20,'window_minutes':300,
                                                          'resets_at':now+3600}}}))
        self.app.consume()
        self.assertIn('5小时剩余 80%',self.app.quota_summary.get())
        self.assertIn('7天 --',self.app.quota_summary.get())
        self.assertIn('不发起模型请求',self.app.quota_detail)
        self.assertEqual(self.app.config['slots'][0]['id'],'a')


if __name__=='__main__':
    unittest.main()
