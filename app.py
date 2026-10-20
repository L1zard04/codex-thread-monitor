"""A four-slot local Codex turn monitor. No network or model calls."""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from core import Catalog, ConfigStore, LogReader, Thread
from autostart import InstanceLock, user_data
from navigation import open_thread
from history import StatusHistory
from attention import update_attention, is_unread, mark_viewed, completion_token
from placement import restore_bounds, work_areas, frame_size
from quota import QuotaReader, quota_display


COLORS = {'empty':'#FFFFFF', 'unknown':'#F1F3F5', 'active':'#FFE39A',
          'completed':'#BCEACD', 'interrupted':'#F1F3F5'}
LABELS = {'unknown':'状态未知', 'active':'回答中', 'completed':'回答完毕',
          'interrupted':'已中断'}
INK = '#24362F'


def data_directory():
    return user_data()


class Poller(threading.Thread):
    def __init__(self, home, output, commands):
        super().__init__(name='codex-status-reader', daemon=True)
        self.catalog = Catalog(home)
        self.output, self.commands = output, commands
        self.stop_event = threading.Event()
        self.quota=QuotaReader(home)

    def run(self):
        threads, readers, ids = {}, {}, []
        next_catalog, next_quota, catalog_error = 0, 0, ''
        while not self.stop_event.is_set():
            try:
                while True:
                    ids = self.commands.get_nowait()
            except queue.Empty:
                pass
            try:
                if time.monotonic()>=next_quota:
                    self.output.put(('quota',self.quota.poll()))
                    next_quota=time.monotonic()+15
                if time.monotonic() >= next_catalog:
                    try:
                        items = self.catalog.list_threads()
                        threads = {t.id:t for t in items}
                        catalog_error = ''
                        self.output.put(('catalog', items))
                    except Exception as e:
                        catalog_error = f'目录不可读：{e}'
                    next_catalog = time.monotonic() + 5
                snapshot = {}
                readers = {i:r for i,r in readers.items() if i in ids}
                for tid in ids:
                    t = threads.get(tid)
                    if catalog_error or not t:
                        snapshot[tid] = {'status':'unknown', 'detail':catalog_error or '对话已归档、删除或无法读取'}
                        continue
                    if tid not in readers or readers[tid].path != Path(t.rollout_path):
                        readers[tid] = LogReader(Path(t.rollout_path))
                    r = readers[tid]
                    state = r.poll()
                    status = 'unknown' if r.error or r.catching_up else state.status
                    detail = r.error or ('正在读取历史日志…' if r.catching_up else
                                        f'末次状态事件：{state.timestamp or "未发现"}')
                    snapshot[tid] = {'status':status, 'detail':detail,
                                     'turn_id':state.turn_id,'event_time':state.timestamp,
                                     'answer':state.answer,'answer_turn_id':state.answer_turn_id}
                self.output.put(('snapshot', (snapshot, catalog_error)))
            except Exception:
                logging.exception('Status reader failed')
                self.output.put(('snapshot', ({tid:{'status':'unknown', 'detail':'状态读取异常，请查看日志'}
                                               for tid in ids}, '状态读取异常')))
            self.stop_event.wait(1)


class Picker(tk.Toplevel):
    def __init__(self, parent, threads, callback):
        super().__init__(parent)
        self.title('选择要监视的对话')
        self.geometry('570x440')
        self.minsize(440, 320)
        self.transient(parent)
        self.threads, self.callback = threads, callback
        self.project = tk.StringVar(self, '全部项目')
        self.search = tk.StringVar(self)
        panel = ttk.Frame(self, padding=16)
        panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='项目').pack(anchor='w')
        combo = ttk.Combobox(panel, textvariable=self.project, state='readonly',
                             values=['全部项目']+sorted({t.project for t in threads}))
        combo.pack(fill='x', pady=(4,12))
        ttk.Label(panel, text='搜索对话标题').pack(anchor='w')
        entry = ttk.Entry(panel, textvariable=self.search)
        entry.pack(fill='x', pady=(4,12))
        frame = ttk.Frame(panel)
        frame.pack(fill='both', expand=True)
        self.listbox = tk.Listbox(frame, font=('Microsoft YaHei UI',10), activestyle='dotbox',
                                  exportselection=False, selectbackground='#C6E9D5')
        scroll = ttk.Scrollbar(frame, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.note = ttk.Label(panel, text='仅显示本机未归档的主对话；双击选择', wraplength=520)
        self.note.pack(anchor='w', pady=(10,8))
        ttk.Button(panel, text='监视此对话', command=self.choose).pack(side='right')
        self.listbox.bind('<Double-Button-1>', lambda e:self.choose())
        self.listbox.bind('<<ListboxSelect>>', self.show_selection)
        self.bind('<Return>', lambda e:self.choose())
        self.bind('<Escape>', lambda e:self.destroy())
        self.project.trace_add('write', lambda *_:self.refresh())
        self.search.trace_add('write', lambda *_:self.refresh())
        self.refresh()
        entry.focus_set()

    def refresh(self):
        text = self.search.get().casefold()
        project = self.project.get()
        self.visible = [t for t in self.threads if (project == '全部项目' or t.project == project)
                        and text in t.title.casefold()]
        self.listbox.delete(0, 'end')
        for t in self.visible:
            self.listbox.insert('end', f'{t.project}  ·  {t.title}')

    def show_selection(self, _event=None):
        selected = self.listbox.curselection()
        if selected:
            t = self.visible[selected[0]]
            self.note.configure(text=f'{t.title}\n{t.cwd}')

    def choose(self):
        selected = self.listbox.curselection()
        if selected:
            self.callback(self.visible[selected[0]])
            self.destroy()


class AnswerReader(tk.Toplevel):
    def __init__(self,app,slot):
        super().__init__(app.root)
        self.app,self.thread_id = app,slot['id']
        self.title('阅读回答 · '+slot.get('title','对话'))
        try:
            areas=work_areas()
        except OSError:
            areas=[(0,0,self.winfo_screenwidth(),self.winfo_screenheight())]
        bounds=restore_bounds({'width':680,'height':600,'x':app.root.winfo_x()+30,
                               'y':app.root.winfo_y()+30},areas,frame_size=frame_size())
        self.geometry(f"{bounds['width']}x{bounds['height']}+{bounds['x']}+{bounds['y']}")
        self.minsize(min(420,bounds['width']),min(320,bounds['height']))
        self.attributes('-topmost',app.topmost.get())
        self.token,self.answer = '', ''
        tk.Label(self,text=slot.get('title','对话'),font=('Microsoft YaHei UI',11,'bold'),
                 anchor='w',wraplength=600).pack(fill='x',padx=14,pady=(12,4))
        self.note=tk.StringVar(self)
        tk.Label(self,textvariable=self.note,anchor='w',fg='#63756B').pack(fill='x',padx=14,pady=4)
        body=ttk.Frame(self)
        body.pack(fill='both',expand=True,padx=14,pady=8)
        self.text=tk.Text(body,wrap='word',font=('Microsoft YaHei UI',11),state='disabled')
        scroll=ttk.Scrollbar(body,command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y')
        self.text.pack(side='left',fill='both',expand=True)
        actions=ttk.Frame(self)
        actions.pack(fill='x',padx=14,pady=(0,12))
        self.refresh_button=ttk.Button(actions,text='刷新回答',command=self.refresh)
        self.refresh_button.pack(side='left')
        ttk.Button(actions,text='复制全文',command=self.copy_answer).pack(side='left',padx=6)
        ttk.Button(actions,text='标记已读',command=self.mark_read).pack(side='left')
        ttk.Button(actions,text='返回对话',command=self.open_source).pack(side='right')
        self.bind('<Escape>',lambda _event:self.destroy())
        self.bind('<Destroy>',self.on_destroy,add='+')
        self.refresh()

    def on_destroy(self,event):
        if event.widget==self and self.app.readers.get(self.thread_id) is self:
            self.app.readers.pop(self.thread_id,None)

    def sync(self):
        state=self.app.snapshot.get(self.thread_id,{})
        token=completion_token(state)
        changed=bool(token and (token!=self.token or state.get('answer','')!=self.answer))
        self.note.set('有新回答，点击刷新查看（当前正文保持不变）' if changed else
                      ('回答中 · 当前显示上轮结果' if state.get('status')=='active' else
                       LABELS.get(state.get('status'),'状态未知')))
        self.refresh_button.configure(state='normal' if changed else 'disabled')

    def refresh(self):
        state=self.app.snapshot.get(self.thread_id,{})
        if state.get('status','unknown')=='unknown':
            self.note.set('暂时无法读取最新回答；当前正文保留')
            return
        self.token=completion_token(state) or ''
        self.answer=state.get('answer','')
        self.text.configure(state='normal')
        self.text.delete('1.0','end')
        self.text.insert('1.0',self.answer or '暂无已完成的回答正文')
        self.text.configure(state='disabled')
        self.mark_read()
        self.sync()

    def mark_read(self):
        entry=self.app.config['attention'].get(self.thread_id,{})
        if self.token and entry.get('latest')==self.token:
            if mark_viewed(self.app.config['attention'],self.thread_id):
                self.app.save(quiet=True)
                self.app.render()

    def copy_answer(self):
        self.clipboard_clear()
        self.clipboard_append(self.answer)

    def open_source(self):
        self.app.open_source(self.thread_id)


class MonitorApp:
    def __init__(self, root, home=None, config_store=None, start_worker=True):
        self.root = root
        self.home = Path(home or os.environ.get('CODEX_HOME') or Path.home()/'.codex')
        self.store = config_store or ConfigStore(data_directory()/'config.json')
        self.config = self.store.load()
        self.history = StatusHistory(self.store.path.with_name('status-history.jsonl'))
        self.save_error = self.store.error
        self.threads, self.snapshot = [], {}
        self.worker = None
        self.output, self.commands = queue.Queue(), queue.Queue()
        self.tooltip = None
        self.tooltip_job = None
        self.poll_job = None
        self.geometry_job = None
        self.undo_job = None
        self.undo_selection = None
        self.context_menu = None
        self.desktop = None
        self.desktop_error = ''
        self.readers = {}
        self.pending_notifications = {}
        self.notification_job = None
        self.quota_summary=tk.StringVar(root,'5小时 -- · 7天 -- · 本地快照')
        self.quota_detail='正在只读检查本机额度快照，不发起模型请求。'
        self.closed = False
        root.title('Codex · 四格监视器')
        try:
            areas = work_areas()
        except OSError:
            areas = [(0,0,root.winfo_screenwidth(),root.winfo_screenheight())]
        bounds = restore_bounds(self.config.get('geometry'),areas,frame_size=frame_size())
        root.geometry(f"{bounds['width']}x{bounds['height']}+{bounds['x']}+{bounds['y']}")
        root.resizable(True, True)
        root.minsize(300,360)
        root.configure(bg='#F4F6F5')
        self.topmost = tk.BooleanVar(root, self.config['topmost'])
        root.attributes('-topmost', self.topmost.get())
        header = tk.Frame(root, bg='#F4F6F5', height=40)
        header.pack(fill='x', padx=14, pady=(8,4))
        tk.Checkbutton(header, text='置顶', variable=self.topmost, command=self.toggle_topmost,
                       bg='#F4F6F5', activebackground='#F4F6F5', fg=INK).pack(side='right')
        quota_label=self.quota_label=tk.Label(header,textvariable=self.quota_summary,bg='#F4F6F5',fg=INK,
                             anchor='w',font=('Microsoft YaHei UI',8))
        quota_label.pack(side='left',fill='x',expand=True)
        quota_label.bind('<Enter>',lambda _event:self.schedule_quota_tip())
        quota_label.bind('<Leave>',lambda _event:self.hide_tip())
        toolbar=tk.Frame(root,bg='#F4F6F5')
        toolbar.pack(fill='x',padx=14,pady=(0,3))
        self.mode_button=ttk.Button(toolbar,text='切换紧凑' if self.config['view_mode']=='full' else '切换全文',
                                    command=lambda:self.set_view_mode(
                                        'compact' if self.config['view_mode']=='full' else 'full'))
        self.mode_button.pack(side='left')
        self.notifications=tk.BooleanVar(root,self.config['notifications'])
        ttk.Checkbutton(toolbar,text='完成提醒',variable=self.notifications,
                        command=self.toggle_notifications).pack(side='left',padx=8)
        self.hide_button=ttk.Button(toolbar,text='收起',command=self.hide_to_tray,state='disabled',width=5)
        self.hide_button.pack(side='right')
        holder = tk.Frame(root,bg='#F4F6F5')
        holder.pack(fill='both',expand=True,padx=10,pady=4)
        grid = tk.Frame(holder, bg='#F4F6F5', width=332, height=332)
        grid.place(relx=.5,rely=.5,anchor='center',width=332,height=332)
        holder.bind('<Configure>',lambda e:grid.place_configure(
            width=max(1,min(e.width,e.height)),height=max(1,min(e.width,e.height))))
        grid.pack_propagate(False)
        grid.grid_propagate(False)
        for i in range(2):
            grid.rowconfigure(i, weight=1, uniform='square')
            grid.columnconfigure(i, weight=1, uniform='square')
        self.tiles = []
        self.answer_widgets = [None]*4
        self.open_buttons = [None]*4
        self.menu_buttons = [None]*4
        self.title_widgets = [None]*4
        self.status_widgets = [None]*4
        self.render_keys = [None]*4
        for i in range(4):
            tile = tk.Frame(grid, bg=COLORS['empty'], highlightthickness=1,
                            highlightbackground='#D5DDD8', cursor='hand2')
            tile.grid(row=i//2, column=i%2, padx=3, pady=3, sticky='nsew')
            tile.grid_propagate(False)
            self.tiles.append(tile)
        self.footer = tk.StringVar(root, self.store.error or '点击 ＋ 选择项目和对话')
        footer = tk.Frame(root,bg='#F4F6F5')
        footer.pack(fill='x',padx=12,pady=(3,4))
        tk.Label(footer, textvariable=self.footer, bg='#F4F6F5', fg='#63756B',
                 font=('Microsoft YaHei UI',8), wraplength=260).pack(side='left')
        self.undo_button = ttk.Button(footer,text='撤销清除',command=self.undo_clear)
        self.render()
        self.record_history('restore')
        count = sum(slot is not None for slot in self.config['slots'])
        self.set_footer(f'已恢复 {count} 个监视对话 · 正在读取最新状态' if count else
                        '点击 ＋ 选择项目和对话')
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<Configure>',self.schedule_geometry,add='+')
        root.bind('<Destroy>',self.on_destroy,add='+')
        self.send_selection()
        if start_worker:
            self.worker = Poller(self.home, self.output, self.commands)
            self.worker.start()
            self.poll_job = root.after(100, self.consume)

    def save(self, quiet=False):
        try:
            self.store.save(self.config)
            self.save_error = ''
        except OSError as e:
            self.save_error = f'设置保存失败：{e}'
            logging.exception('Cannot save monitor configuration')
        if not quiet or self.save_error:
            self.set_footer('监视设置已保存')

    def schedule_geometry(self,event):
        if event.widget != self.root or self.closed:
            return
        if self.geometry_job:
            self.root.after_cancel(self.geometry_job)
        self.geometry_job = self.root.after(500,self.remember_geometry)

    def on_destroy(self,event):
        if event.widget != self.root:
            return
        self.closed = True
        for name in ('geometry_job','undo_job','poll_job','tooltip_job','notification_job'):
            job = getattr(self,name)
            if job:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
                setattr(self,name,None)
        if self.worker:
            self.worker.stop_event.set()
        if self.desktop:
            self.desktop.close()

    def remember_geometry(self):
        if self.geometry_job:
            self.root.after_cancel(self.geometry_job)
            self.geometry_job = None
        if self.root.state() != 'normal':
            return
        bounds = {'width':self.root.winfo_width(),'height':self.root.winfo_height(),
                  'x':self.root.winfo_x(),'y':self.root.winfo_y()}
        if bounds['width'] >= 300 and bounds['height'] >= 360 and bounds != self.config.get('geometry'):
            self.config['geometry'] = bounds
            self.save(quiet=True)

    def set_footer(self, message):
        self.footer.set(self.save_error or self.history.error or self.desktop_error or message)

    def attach_desktop(self,desktop):
        self.desktop=desktop
        self.hide_button.configure(state='normal' if desktop.available else 'disabled')
        self.desktop_error='' if desktop.available else ('托盘不可用：'+desktop.error)
        self.set_footer('托盘已就绪 · 收起后可从托盘打开')

    def hide_to_tray(self):
        if not self.desktop or not self.desktop.available:
            self.set_footer('托盘不可用，窗口保持显示')
            return
        self.remember_geometry()
        self.hide_tip()
        for reader in self.readers.values():
            if reader.winfo_exists():
                reader.withdraw()
        self.root.withdraw()

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes('-topmost',self.topmost.get())

    def set_view_mode(self,mode):
        self.config['view_mode']='compact' if mode=='compact' else 'full'
        self.mode_button.configure(text='切换全文' if mode=='compact' else '切换紧凑')
        self.save(quiet=True)
        self.render()

    def open_reader(self,index):
        slot=self.config['slots'][index]
        if not slot:
            return None
        reader=self.readers.get(slot['id'])
        if reader and reader.winfo_exists():
            reader.deiconify()
            reader.lift()
            reader.sync()
        else:
            reader=AnswerReader(self,dict(slot))
            self.readers[slot['id']]=reader
        return reader

    def toggle_notifications(self):
        self.config['notifications']=self.notifications.get()
        self.save()
        if not self.notifications.get():
            self.pending_notifications.clear()
            if self.notification_job:
                self.root.after_cancel(self.notification_job)
                self.notification_job=None

    def flush_notifications(self):
        if self.notification_job:
            self.root.after_cancel(self.notification_job)
            self.notification_job=None
        ids={slot['id'] for slot in self.config['slots'] if slot}
        pending={tid:token for tid,token in self.pending_notifications.items()
                 if tid in ids and is_unread(self.config['attention'],tid) and
                 self.config['attention'].get(tid,{}).get('latest')==token}
        self.pending_notifications.clear()
        if pending and self.notifications.get() and self.desktop and self.desktop.available:
            try:
                self.desktop.notify('Codex · 回答完成',f'{len(pending)} 个监视对话有新的完成回答。点击返回监视器。')
            except OSError as error:
                self.desktop_error=f'完成通知发送失败：{error}'
                self.set_footer('')

    def record_history(self, event):
        previous_error = self.history.error
        if not self.history.record(event, self.config['slots'], self.snapshot):
            if self.history.error != previous_error:
                logging.warning('%s', self.history.error)
            self.set_footer('')

    def toggle_topmost(self):
        self.root.attributes('-topmost', self.topmost.get())
        self.config['topmost'] = self.topmost.get()
        self.save()

    def send_selection(self):
        self.commands.put([x['id'] for x in self.config['slots'] if x])

    def bind_slot(self, index, thread: Thread):
        if self.undo_selection and self.undo_selection[0] == index:
            self.expire_undo()
        replacing = self.config['slots'][index] is not None
        self.config['slots'][index] = {'id':thread.id,'title':thread.title,'project':thread.project}
        self.prune_attention()
        update_attention(self.config['attention'],thread.id,self.snapshot.get(thread.id,{}))
        self.save()
        self.record_history('replace' if replacing else 'bind')
        self.send_selection()
        self.render()

    def clear_slot(self, index):
        slot = self.config['slots'][index]
        if not slot:
            return
        self.expire_undo()
        entry = self.config['attention'].get(slot['id'])
        self.undo_selection = (index,dict(slot),dict(entry) if entry else None)
        self.undo_button.pack(side='right')
        self.undo_job = self.root.after(8000,self.expire_undo)
        self.config['slots'][index] = None
        self.prune_attention()
        self.save()
        self.record_history('clear')
        self.send_selection()
        self.render()

    def prune_attention(self):
        ids = {slot['id'] for slot in self.config['slots'] if slot}
        self.config['attention'] = {tid:entry for tid,entry in self.config['attention'].items() if tid in ids}

    def expire_undo(self):
        if self.undo_job:
            self.root.after_cancel(self.undo_job)
            self.undo_job = None
        self.undo_selection = None
        self.undo_button.pack_forget()

    def undo_clear(self):
        if not self.undo_selection:
            return
        index,slot,entry = self.undo_selection
        self.expire_undo()
        if self.config['slots'][index] is not None:
            return
        self.config['slots'][index] = slot
        if entry and slot['id'] not in self.config['attention']:
            self.config['attention'][slot['id']] = entry
        self.save()
        self.record_history('undo_clear')
        self.send_selection()
        self.render()

    def mark_slot(self,index,unread=False):
        slot = self.config['slots'][index]
        if slot and mark_viewed(self.config['attention'],slot['id'],unread=unread):
            self.save(quiet=True)
            self.render()

    def open_picker(self, index):
        self.hide_tip()
        if not self.threads:
            messagebox.showinfo('对话目录', '对话目录正在读取或没有可用对话。\n请检查底部状态，稍后再试。', parent=self.root)
            return
        Picker(self.root, list(self.threads), lambda t:self.bind_slot(index,t))

    def go_to_thread(self,index):
        slot=self.config['slots'][index]
        if slot:
            self.open_source(slot['id'])

    def open_source(self,thread_id):
        try:
            open_thread(thread_id)
        except OSError as e:
            messagebox.showerror('无法打开对话',f'请确认 Codex 已安装并注册链接协议。\n{e}',parent=self.root)
        else:
            if mark_viewed(self.config['attention'],thread_id):
                self.save(quiet=True)
                self.render()

    def build_menu(self,index):
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label='选择 / 更换对话', command=lambda:self.open_picker(index))
        slot = self.config['slots'][index]
        if slot:
            menu.add_command(label='展开阅读',command=lambda:self.open_reader(index))
            menu.add_command(label='标记为已读',command=lambda:self.mark_slot(index))
            entry = self.config['attention'].get(slot['id'],{})
            menu.add_command(label='标记为未读',command=lambda:self.mark_slot(index,unread=True),
                             state='normal' if entry.get('latest') else 'disabled')
        menu.add_command(label='清除这个格子', command=lambda:self.clear_slot(index))
        return menu

    def menu(self, event, index):
        self.popup_menu(index,event.x_root,event.y_root)

    def button_menu(self,index):
        button = self.menu_buttons[index]
        self.popup_menu(index,button.winfo_rootx(),button.winfo_rooty()+button.winfo_height())

    def popup_menu(self,index,x,y):
        self.hide_tip()
        if self.context_menu:
            self.context_menu.destroy()
        menu = self.build_menu(index)
        self.context_menu = menu
        try:
            menu.tk_popup(x,y)
        finally:
            menu.grab_release()

    def status_caption(self,slot,status,answer):
        caption = LABELS.get(status,'状态未知')
        unread = is_unread(self.config['attention'],slot['id'])
        if answer and status in ('active','interrupted'):
            caption += ' · 上轮未读' if unread else ' · 上轮结果'
        elif unread:
            caption += ' · 未读'
        return caption

    def render(self):
        self.hide_tip()
        for i,tile in enumerate(self.tiles):
            slot = self.config['slots'][i]
            status = self.snapshot.get(slot['id'],{}).get('status','unknown') if slot else 'empty'
            answer = self.snapshot.get(slot['id'],{}).get('answer','') if slot else ''
            key = (slot['id'],slot.get('project'),slot.get('title'),status,answer,self.config['view_mode']) if slot else ('empty',)
            if self.render_keys[i] == key:
                if slot and self.status_widgets[i]:
                    self.status_widgets[i].configure(text=self.status_caption(slot,status,answer))
                continue
            self.render_keys[i] = key
            for widget in tile.winfo_children():
                widget.destroy()
            color = COLORS.get(status,COLORS['unknown'])
            tile.configure(bg=color)
            if slot:
                name = slot.get('project','未分组')
                title = slot.get('title','未命名对话')
                project = tk.Label(tile,text=name,bg=color,fg=INK,anchor='w',
                                   font=('Microsoft YaHei UI',9))
                project.place(x=8,y=5,relwidth=1,width=-94,height=24)
                button=tk.Button(tile,text='打开',command=lambda idx=i:self.go_to_thread(idx),
                                 font=('Microsoft YaHei UI',8),bg=color,fg=INK,
                                 relief='flat',borderwidth=0,cursor='hand2')
                button.place(relx=1,x=-78,y=5,width=44,height=24)
                self.open_buttons[i]=button
                more=tk.Button(tile,text='⋯',command=lambda idx=i:self.button_menu(idx),
                               bg=color,fg=INK,relief='flat',font=('Microsoft YaHei UI',11),cursor='hand2')
                more.place(relx=1,x=-31,y=5,width=25,height=24)
                self.menu_buttons[i]=more
                label = tk.Label(tile,text=title,bg=color,anchor='w',
                                 fg=INK,font=('Microsoft YaHei UI',9,'bold'))
                label.place(x=8,y=29,relwidth=1,width=-16,height=18)
                self.title_widgets[i]=label
                body = tk.Frame(tile,bg=color)
                body.place(x=7,y=50,relwidth=1,width=-14,relheight=1,height=-80)
                text = tk.Text(body,wrap='word',font=('Microsoft YaHei UI',9),
                               bg=color,fg=INK,relief='flat',borderwidth=0,
                               highlightthickness=0,padx=3,pady=2,cursor='xterm')
                scrollbar = ttk.Scrollbar(body,orient='vertical',command=text.yview)
                text.configure(yscrollcommand=scrollbar.set)
                scrollbar.pack(side='right',fill='y')
                text.pack(side='left',fill='both',expand=True)
                placeholder = '尚无已完成回答' if status=='active' else '暂无可读取的回答正文'
                text.insert('1.0',answer or placeholder)
                text.configure(state='disabled')
                self.answer_widgets[i] = text
                if self.config['view_mode']=='compact':
                    body.place_forget()
                    ttk.Button(tile,text='阅读全文',command=lambda idx=i:self.open_reader(idx)).place(
                        relx=.5,rely=.57,anchor='center')
                caption = self.status_caption(slot,status,answer)
                state = tk.Label(tile,text=caption,bg=color,fg=INK,
                                 font=('Microsoft YaHei UI',9,'bold'))
                state.place(relx=.5,rely=1,y=-15,anchor='center')
                self.status_widgets[i]=state
                click_targets = (tile,project,label,state)
                text.bind('<Button-3>',lambda e,idx=i:self.menu(e,idx))
            else:
                self.answer_widgets[i] = None
                self.open_buttons[i] = None
                self.menu_buttons[i] = None
                self.title_widgets[i] = None
                self.status_widgets[i] = None
                plus = tk.Label(tile,text='＋',bg=color,fg='#788E81',font=('Microsoft YaHei UI',30))
                plus.place(relx=.5,rely=.43,anchor='center')
                tk.Label(tile,text='选择对话',bg=color,fg='#788E81',font=('Microsoft YaHei UI',9)
                         ).place(relx=.5,rely=.73,anchor='center')
                click_targets = (tile,*tile.winfo_children())
            for w in click_targets:
                if slot:
                    w.bind('<Button-1>',lambda e,idx=i:self.go_to_thread(idx))
                else:
                    w.bind('<Button-1>',lambda e,idx=i:self.open_picker(idx))
                w.bind('<Button-3>',lambda e,idx=i:self.menu(e,idx))
                w.bind('<Enter>',lambda e,idx=i:self.schedule_tip(idx))
                w.bind('<Leave>',lambda e:self.hide_tip())

    def schedule_tip(self, index):
        self.hide_tip()
        self.tooltip_job = self.root.after(650,lambda:self.show_tip(index))

    def schedule_quota_tip(self):
        self.hide_tip()
        self.tooltip_job=self.root.after(500,self.show_quota_tip)

    def show_quota_tip(self):
        self.tooltip_job=None
        tip=self.tooltip=tk.Toplevel(self.root)
        tip.overrideredirect(True)
        tip.attributes('-topmost',True)
        tip.geometry(f'+{self.root.winfo_pointerx()+12}+{self.root.winfo_pointery()+12}')
        tk.Label(tip,text=self.quota_detail,justify='left',wraplength=400,padx=12,pady=10,
                 bg='#24362F',fg='white',font=('Microsoft YaHei UI',9)).pack()

    def show_tip(self,index):
        self.tooltip_job = None
        slot = self.config['slots'][index]
        if not slot:
            return
        detail = self.snapshot.get(slot['id'],{}).get('detail','等待读取状态')
        tip = self.tooltip = tk.Toplevel(self.root)
        tip.overrideredirect(True)
        tip.attributes('-topmost',True)
        tip.geometry(f'+{self.root.winfo_pointerx()+12}+{self.root.winfo_pointery()+12}')
        text = f'{slot.get("project", "")}\n{slot.get("title", "")}\n{detail}\n点击标题返回对话 · ⋯ 或右键管理'
        tk.Label(tip,text=text,justify='left',wraplength=400,padx=12,pady=10,
                 bg='#24362F',fg='white',font=('Microsoft YaHei UI',9)).pack()

    def hide_tip(self):
        if self.tooltip_job:
            self.root.after_cancel(self.tooltip_job)
            self.tooltip_job = None
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None

    def apply_snapshot(self,snapshot):
        if snapshot != self.snapshot:
            attention_changed = False
            for slot in self.config['slots']:
                if slot:
                    old=self.config['attention'].get(slot['id'])
                    old_token=old.get('latest') if old else None
                    state=snapshot.get(slot['id'],{})
                    attention_changed |= update_attention(self.config['attention'],slot['id'],
                                                          state)
                    token=completion_token(state)
                    if old is not None and state.get('status')=='completed' and token and token!=old_token:
                        if self.notifications.get():
                            self.pending_notifications[slot['id']]=token
                            if not self.notification_job:
                                self.notification_job=self.root.after(1200,self.flush_notifications)
            if attention_changed:
                self.save(quiet=True)
            def status_key(states):
                return [(slot['id'], *(states.get(slot['id'],{}).get(key,'unknown' if key=='status' else '')
                                      for key in ('status','turn_id','event_time')))
                        for slot in self.config['slots'] if slot]
            changed = status_key(snapshot) != status_key(self.snapshot)
            self.snapshot = snapshot
            if changed:
                self.record_history('status')
            self.render()
            for reader in self.readers.values():
                if reader.winfo_exists():
                    reader.sync()

    def consume(self):
        try:
            while True:
                kind, value = self.output.get_nowait()
                if kind == 'catalog':
                    self.threads = value
                elif kind == 'quota':
                    summary,self.quota_detail=quota_display(value)
                    self.quota_summary.set(summary)
                elif kind == 'desktop':
                    if value=='exit':
                        self.close()
                        return
                    if value=='hide':
                        self.hide_to_tray()
                    elif value=='notifications':
                        self.notifications.set(not self.notifications.get())
                        self.toggle_notifications()
                    else:
                        self.show_window()
                elif kind == 'desktop_error':
                    self.desktop_error='托盘不可用：'+str(value)
                    self.show_window()
                    self.hide_button.configure(state='disabled')
                    self.set_footer('')
                elif kind == 'desktop_ready':
                    self.attach_desktop(self.desktop)
                else:
                    snapshot, error = value
                    self.apply_snapshot(snapshot)
                    self.set_footer(error or f'本机 · 每秒检查 · {time.strftime("%H:%M:%S")}')
        except queue.Empty:
            pass
        if not self.closed:
            self.poll_job = self.root.after(150,self.consume)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.remember_geometry()
        self.save()
        self.record_history('close')
        self.hide_tip()
        if self.poll_job:
            self.root.after_cancel(self.poll_job)
        self.expire_undo()
        if self.worker:
            self.worker.stop_event.set()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description='Codex 本机对话状态小窗')
    parser.add_argument('--check',action='store_true',help='只读检查本机数据，不启动界面')
    args = parser.parse_args()
    home = Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')
    if args.check:
        threads = Catalog(home).list_threads()
        counts = {}
        for t in threads:
            r = LogReader(Path(t.rollout_path))
            r.poll()
            while r.catching_up:
                r.poll()
            status = 'unknown' if r.error or r.catching_up else r.state.status
            counts[status] = counts.get(status,0)+1
        print(json.dumps({'projects':len({t.project for t in threads}),
                          'threads':len(threads),'status_counts':counts},ensure_ascii=True))
        return
    directory = data_directory()
    directory.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(filename=directory/'monitor.log',level=logging.WARNING,
                        format='%(asctime)s %(levelname)s %(message)s',encoding='utf-8')
    lock=InstanceLock(directory/'ui')
    if not lock.acquire():
        from desktop import WindowsDesktop
        WindowsDesktop.wake_existing()
        return
    try:
        root = tk.Tk()
        root.report_callback_exception = lambda *exc: (
            logging.error('UI callback failed',exc_info=exc),
            messagebox.showerror('界面操作失败',f'请查看日志：{directory / "monitor.log"}',parent=root))
        app=MonitorApp(root,home=home)
        from desktop import WindowsDesktop
        app.attach_desktop(WindowsDesktop(app.output))
        root.mainloop()
    finally:
        lock.close()


if __name__=='__main__':
    main()
