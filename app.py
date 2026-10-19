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

    def run(self):
        threads, readers, ids = {}, {}, []
        next_catalog, catalog_error = 0, ''
        while not self.stop_event.is_set():
            try:
                while True:
                    ids = self.commands.get_nowait()
            except queue.Empty:
                pass
            try:
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


class MonitorApp:
    def __init__(self, root, home=None, config_store=None, start_worker=True):
        self.root = root
        self.home = Path(home or os.environ.get('CODEX_HOME') or Path.home()/'.codex')
        self.store = config_store or ConfigStore(data_directory()/'config.json')
        self.config = self.store.load()
        self.threads, self.snapshot = [], {}
        self.worker = None
        self.output, self.commands = queue.Queue(), queue.Queue()
        self.tooltip = None
        self.tooltip_job = None
        self.poll_job = None
        self.closed = False
        root.title('Codex · 四格监视器')
        root.geometry('360x420')
        root.resizable(True, True)
        root.minsize(300,360)
        root.configure(bg='#F4F6F5')
        self.topmost = tk.BooleanVar(root, self.config['topmost'])
        root.attributes('-topmost', self.topmost.get())
        header = tk.Frame(root, bg='#F4F6F5', height=40)
        header.pack(fill='x', padx=14, pady=(8,4))
        tk.Label(header, text='CODEX  /  对话状态', bg='#F4F6F5', fg=INK,
                 font=('Microsoft YaHei UI',10,'bold')).pack(side='left')
        tk.Checkbutton(header, text='置顶', variable=self.topmost, command=self.toggle_topmost,
                       bg='#F4F6F5', activebackground='#F4F6F5', fg=INK).pack(side='right')
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
        self.render_keys = [None]*4
        for i in range(4):
            tile = tk.Frame(grid, bg=COLORS['empty'], highlightthickness=1,
                            highlightbackground='#D5DDD8', cursor='hand2')
            tile.grid(row=i//2, column=i%2, padx=3, pady=3, sticky='nsew')
            tile.grid_propagate(False)
            self.tiles.append(tile)
        self.footer = tk.StringVar(root, self.store.error or '点击 ＋ 选择项目和对话')
        tk.Label(root, textvariable=self.footer, bg='#F4F6F5', fg='#63756B',
                 font=('Microsoft YaHei UI',8), wraplength=330).pack(padx=12,pady=(3,4))
        self.render()
        root.protocol('WM_DELETE_WINDOW', self.close)
        if start_worker:
            self.worker = Poller(self.home, self.output, self.commands)
            self.send_selection()
            self.worker.start()
            self.poll_job = root.after(100, self.consume)

    def save(self):
        try:
            self.store.save(self.config)
        except OSError as e:
            self.footer.set(f'设置保存失败：{e}')
            logging.exception('Cannot save monitor configuration')

    def toggle_topmost(self):
        self.root.attributes('-topmost', self.topmost.get())
        self.config['topmost'] = self.topmost.get()
        self.save()

    def send_selection(self):
        self.commands.put([x['id'] for x in self.config['slots'] if x])

    def bind_slot(self, index, thread: Thread):
        self.config['slots'][index] = {'id':thread.id,'title':thread.title,'project':thread.project}
        self.save()
        self.send_selection()
        self.render()

    def clear_slot(self, index):
        self.config['slots'][index] = None
        self.save()
        self.send_selection()
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
            try:
                open_thread(slot['id'])
            except OSError as e:
                messagebox.showerror('无法打开对话',f'请确认 Codex 已安装并注册链接协议。\n{e}',parent=self.root)

    def menu(self, event, index):
        self.hide_tip()
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label='选择 / 更换对话', command=lambda:self.open_picker(index))
        menu.add_command(label='清除这个格子', command=lambda:self.clear_slot(index))
        try:
            menu.tk_popup(event.x_root,event.y_root)
        finally:
            menu.grab_release()

    def render(self):
        self.hide_tip()
        for i,tile in enumerate(self.tiles):
            slot = self.config['slots'][i]
            status = self.snapshot.get(slot['id'],{}).get('status','unknown') if slot else 'empty'
            answer = self.snapshot.get(slot['id'],{}).get('answer','') if slot else ''
            key = (slot['id'],slot.get('project'),slot.get('title'),status,answer) if slot else ('empty',)
            if self.render_keys[i] == key:
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
                                   font=('Microsoft YaHei UI',11,'bold'))
                project.place(x=8,y=5,relwidth=1,width=-65,height=24)
                button=tk.Button(tile,text='打开',command=lambda idx=i:self.go_to_thread(idx),
                                 font=('Microsoft YaHei UI',8),bg=color,fg=INK,
                                 relief='flat',borderwidth=0,cursor='hand2')
                button.place(relx=1,x=-51,y=5,width=44,height=24)
                self.open_buttons[i]=button
                label = tk.Label(tile,text=title,bg=color,anchor='w',
                                 fg='#46594E',font=('Microsoft YaHei UI',8))
                label.place(x=8,y=29,relwidth=1,width=-16,height=18)
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
                caption = LABELS.get(status,'状态未知')
                if answer and status in ('active','interrupted'):
                    caption += ' · 上轮结果'
                state = tk.Label(tile,text=caption,bg=color,fg=INK,
                                 font=('Microsoft YaHei UI',9,'bold'))
                state.place(relx=.5,rely=1,y=-15,anchor='center')
                click_targets = (tile,project,label,state)
                text.bind('<Button-3>',lambda e,idx=i:self.menu(e,idx))
            else:
                self.answer_widgets[i] = None
                self.open_buttons[i] = None
                plus = tk.Label(tile,text='＋',bg=color,fg='#788E81',font=('Microsoft YaHei UI',30))
                plus.place(relx=.5,rely=.43,anchor='center')
                tk.Label(tile,text='选择对话',bg=color,fg='#788E81',font=('Microsoft YaHei UI',9)
                         ).place(relx=.5,rely=.73,anchor='center')
                click_targets = (tile,*tile.winfo_children())
            for w in click_targets:
                w.bind('<Button-1>',lambda e,idx=i:self.open_picker(idx))
                w.bind('<Button-3>',lambda e,idx=i:self.menu(e,idx))
                w.bind('<Enter>',lambda e,idx=i:self.schedule_tip(idx))
                w.bind('<Leave>',lambda e:self.hide_tip())

    def schedule_tip(self, index):
        self.hide_tip()
        self.tooltip_job = self.root.after(650,lambda:self.show_tip(index))

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
        text = f'{slot.get("project", "")}\n{slot.get("title", "")}\n{detail}\n右键：更换或清除'
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
            self.snapshot = snapshot
            self.render()

    def consume(self):
        try:
            while True:
                kind, value = self.output.get_nowait()
                if kind == 'catalog':
                    self.threads = value
                else:
                    snapshot, error = value
                    self.apply_snapshot(snapshot)
                    self.footer.set(error or f'本机 · 每秒检查 · {time.strftime("%H:%M:%S")}')
        except queue.Empty:
            pass
        if not self.closed:
            self.poll_job = self.root.after(150,self.consume)

    def close(self):
        self.closed = True
        self.hide_tip()
        if self.poll_job:
            self.root.after_cancel(self.poll_job)
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
        return
    try:
        root = tk.Tk()
        root.report_callback_exception = lambda *exc: (
            logging.error('UI callback failed',exc_info=exc),
            messagebox.showerror('界面操作失败',f'请查看日志：{directory / "monitor.log"}',parent=root))
        MonitorApp(root,home=home)
        root.mainloop()
    finally:
        lock.close()


if __name__=='__main__':
    main()
