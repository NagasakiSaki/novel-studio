"""Chinese desktop author center. All novel writes delegate to workspace_admin."""
from __future__ import annotations
import argparse
import ctypes
import json
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).parent))
from service import Controller, RoleSession, SafeTree, admin, friendly_error, ConfigStartupCancelled

BG = "#f5f3ee"
INK = "#263b42"
ACCENT = "#176f68"


def choose_config(issue, parent):
    """Same LocalStore protocol as Qt; no parser text or stored values displayed."""
    dialog = tk.Toplevel(parent)
    dialog.title('配置文件无法正常读取')
    dialog.geometry('820x460')
    choice = ['exit']
    text = ('本机界面配置异常。小说正文和作品资料不会被清空。\n\n'
            '文件：center.json\n类型：' + issue.LABELS[issue.kind]
            + '\n原始配置已安全备份：' + ('是' if issue.backup_verified else '否')
            + '\n备份位置：' + (issue.backup or '尚无已验证备份')
            + '\n原始 SHA256：' + (issue.sha256 or '无法读取，未计算')
            + '\n\n' + ('将仅恢复默认界面设置，不自动打开任何作品。' if issue.can_reset
                         else '目前不能用默认设置覆盖原文件，请重试或退出。'))
    if issue.backup_failed:
        text += '\n备份失败，已停止自动重置。'
    ttk.Label(dialog, text=text, wraplength=780).pack(fill='x', padx=16, pady=16)
    def decide(value):
        choice[0] = value
        dialog.destroy()
    buttons = ttk.Frame(dialog)
    buttons.pack(fill='x', padx=16, pady=8)
    ttk.Button(buttons, text='使用默认界面设置启动', state='normal' if issue.can_reset else 'disabled', command=lambda:decide('defaults')).pack(side='left')
    ttk.Button(buttons, text='查看诊断信息', command=lambda:messagebox.showinfo('配置诊断', json.dumps(issue.diagnostic(), ensure_ascii=False, indent=2), parent=dialog)).pack(side='left')
    ttk.Button(buttons, text='重新尝试读取', command=lambda:decide('retry')).pack(side='left')
    ttk.Button(buttons, text='暂不恢复并退出', command=lambda:decide('exit')).pack(side='left')
    dialog.transient(parent)
    dialog.grab_set()
    parent.wait_window(dialog)
    return choice[0]


def text_box(parent, height=12, editable=False):
    frame = ttk.Frame(parent)
    widget = tk.Text(frame, wrap="word", height=height, font=("Microsoft YaHei UI", 11), padx=14, pady=12,
                     background="#ffffff", foreground=INK, relief="flat", spacing3=6, undo=editable)
    scroll = ttk.Scrollbar(frame, command=widget.yview)
    widget.configure(yscrollcommand=scroll.set)
    widget.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    if not editable:
        widget.configure(state="disabled")
    return frame, widget


def set_text(widget, value):
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("end", value)
    widget.configure(state="disabled")


class DiffWindow(tk.Toplevel):
    def __init__(self, app, plan, action, title="预览差异并批准", done=None):
        super().__init__(app.root)
        self.app, self.plan, self.action, self.done = app, plan, action, done
        self.title("Novel Studio — " + title)
        self.geometry("1000x740")
        self.minsize(760, 540)
        self.configure(background=BG)
        ttk.Label(self, text=title, style="Title.TLabel").pack(anchor="w", padx=24, pady=(20, 8))
        ttk.Label(self, text="正式文件："+Path(plan["target"]).name+"   ｜   旧版本将自动备份", wraplength=940).pack(anchor="w", padx=24, pady=6)
        frame, self.diff = text_box(self, 20)
        diff_frame = frame
        set_text(self.diff, plan["diff"] or "两个版本没有文字差异。")
        self.confirm = tk.BooleanVar(value=False)
        controls = ttk.Frame(self)
        controls.pack(side="bottom", fill="x")
        ttk.Checkbutton(controls, text="我已检查完整差异，确认这就是我要保存的版本", variable=self.confirm, command=self.toggle).pack(anchor="w", padx=24, pady=10)
        row = ttk.Frame(controls)
        row.pack(fill="x", padx=24, pady=(8, 22))
        ttk.Button(row, text="返回，不修改", command=self.destroy).pack(side="left")
        self.save = ttk.Button(row, text="批准并保存", style="Accent.TButton", command=self.commit, state="disabled")
        self.save.pack(side="right")
        diff_frame.pack(fill="both", expand=True, padx=24, pady=12)
        self.transient(app.root)

    def toggle(self):
        self.save.configure(state="normal" if self.confirm.get() else "disabled")

    def commit(self):
        if not self.confirm.get():
            return
        try:
            self.app.controller.require_idle()
            result = self.action(self.plan)
            self.app.status.set("已保存，旧版本已备份。可在“版本历史”恢复。")
            if self.done:
                self.done(result)
            self.app.refresh()
            self.destroy()
        except Exception as error:
            self.save.configure(state="disabled")
            self.confirm.set(False)
            self.app.error(error, self)


class PickWindow(tk.Toplevel):
    def __init__(self, app, title, items, callback):
        super().__init__(app.root)
        self.app, self.items, self.callback = app, items, callback
        self.title("Novel Studio — " + title)
        self.geometry("760x430")
        ttk.Label(self, text=title, style="Title.TLabel").pack(anchor="w", padx=20, pady=18)
        self.list = tk.Listbox(self, font=("Microsoft YaHei UI", 12), selectmode="single", height=10, relief="flat")
        for label, value in items:
            self.list.insert("end", label)
        if items:
            self.list.selection_set(0)
        ttk.Button(self, text="选择这一项", style="Accent.TButton", command=self.choose).pack(side="bottom", anchor="e", padx=20, pady=18)
        self.list.pack(fill="both", expand=True, padx=20)
        self.list.bind("<Double-Button-1>", lambda event: self.choose())
        self.transient(app.root)

    def choose(self):
        selected = self.list.curselection()
        if selected:
            value = self.items[selected[0]][1]
            self.destroy()
            self.app.guard(lambda: self.callback(value))


class RoleWindow(tk.Toplevel):
    def __init__(self, app, role):
        super().__init__(app.root)
        self.app, self.role = app, role
        self.events = queue.Queue()
        self.session = RoleSession(app.controller, role, self.events)
        label = "主笔" if role == "writer" else "责编"
        self.title(f"Novel Studio {label} — {app.controller.project.name}")
        self.geometry("1030x790")
        self.minsize(800, 600)
        self.configure(background=BG)
        top = ttk.Frame(self)
        top.pack(fill="x", padx=24, pady=(20, 8))
        ttk.Label(top, text="Novel Studio · "+label, style="Title.TLabel").pack(side="left")
        ttk.Label(top, text="官方 Antigravity 连接 · 独立权限", style="Note.TLabel").pack(side="right")
        instructions = "可以写草稿和提案；正式作品、设定副本和备份不可改。" if role == "writer" else "只读审稿副本，报告单独保存；不改原稿、正典或主笔草稿。"
        ttk.Label(self, text=instructions).pack(anchor="w", padx=24, pady=6)
        frame, self.output = text_box(self, 18)
        output_frame = frame
        set_text(self.output, "在下方用自然语言提出任务。普通写作无需配置权限或输入命令。\n\n模型会通过官方 Antigravity 连接处理您指定的内容，遵循其数据政策。")
        self.progress = tk.StringVar(value="就绪")
        bottom = ttk.Frame(self)
        bottom.pack(side="bottom", fill="x")
        ttk.Label(bottom, textvariable=self.progress, wraplength=960, style="Note.TLabel").pack(anchor="w", padx=24, pady=6)
        entry_frame, self.input = text_box(bottom, 4, True)
        entry_frame.pack(fill="x", padx=24, pady=4)
        row = ttk.Frame(bottom)
        row.pack(fill="x", padx=24, pady=(8, 18))
        ttk.Button(row, text="返回控制中心", command=app.show_center).pack(side="left")
        self.send_button = ttk.Button(row, text="发送给"+label, style="Accent.TButton", command=self.send)
        self.send_button.pack(side="right")
        output_frame.pack(fill="both", expand=True, padx=24, pady=8)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, self.poll)

    def prefill(self, prompt):
        if not self.session.busy:
            self.input.delete("1.0", "end")
            self.input.insert("1.0", prompt)
            self.input.focus_set()

    def send(self):
        prompt = self.input.get("1.0", "end-1c")
        try:
            self.session.send(prompt)
            self.output.configure(state="normal")
            self.output.insert("end", "\n\n您：\n"+prompt+"\n\n")
            self.output.mark_set("answer", "end-1c")
            self.output.mark_gravity("answer", "left")
            self.output.configure(state="disabled")
            self.input.delete("1.0", "end")
            self.send_button.configure(state="disabled")
            self.progress.set("正在连接。您可以最小化窗口；请等待本回合结束后再关闭。")
        except Exception as error:
            self.app.error(error, self)

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "text":
                    self.output.configure(state="normal")
                    self.output.insert("end", value)
                    self.output.see("end")
                    self.output.configure(state="disabled")
                elif kind == "tool":
                    name = value.get("tool_name", "操作")
                    state = value.get("state")
                    if state == "ERROR":
                        message = value.get("tool_info", {}).get("error", {}).get("message", "操作未通过")
                        self.progress.set("这项操作未完成。请在执行详情查看宿主给出的具体原因。")
                        self.app.record({"action": "宿主工具错误（需按原始原因判断）", "role": self.role, "event": value, "message": message})
                    elif state == "DONE":
                        self.progress.set({"view_file": "已读取指定资料副本", "write_to_file": "已保存工作文件", "replace_file_content": "已修改草稿"}.get(name, "已完成："+name))
                        self.app.record({"action": "宿主操作完成", "role": self.role, "event": value})
                elif kind == "init":
                    self.app.record({"action": "图形会话已连接", "role": self.role, "event": value})
                elif kind == "result":
                    response = value.get("response", "")
                    self.output.configure(state="normal")
                    self.output.delete("answer", "end-1c")
                    self.output.insert("end", response or "本回合已结束。若目录外访问被拒绝，请继续使用已准备的资料副本。")
                    self.output.configure(state="disabled")
                    self.progress.set("本回合已结束。正式作品没有自动晋升。")
                    self.app.record({"action": "图形会话回合结束", "role": self.role, "status": value.get("status"), "denied_actions": value.get("denied_actions", [])})
                elif kind == "error":
                    self.progress.set(friendly_error(value))
                    self.app.record({"action": "连接错误", "error": str(value)})
                elif kind == "done":
                    self.send_button.configure(state="normal")
                    self.app.refresh()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self.poll)

    def close(self):
        if self.session.busy:
            messagebox.showinfo("当前回合尚未完成", "请等待本回合完成后关闭。您可以先最小化窗口。\n强行终止官方连接不保证撤销宿主中已经开始的操作。", parent=self)
            return
        self.app.roles.pop(self.role, None)
        self.destroy()
        if self.app.standalone and not self.app.roles:
            self.app.root.destroy()


class Center:
    def __init__(self, root, bundle: Path, state: Path, initial_role=None):
        self.root, self.bundle = root, bundle
        self.events = queue.Queue()
        self.controller = Controller(bundle, state, self.events.put, config_recovery=lambda issue:choose_config(issue, root))
        self.roles = {}
        self.standalone = initial_role is not None
        self.installing = False
        self.audit = []
        self.root.title("Novel Studio 作者控制中心")
        self.root.geometry(f"{min(1360, root.winfo_screenwidth()-60)}x{min(960, root.winfo_screenheight()-80)}")
        self.root.minsize(930, 650)
        self.root.configure(background=BG)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10), background=BG, foreground=INK)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("Note.TLabel", foreground="#617278", font=("Microsoft YaHei UI", 9))
        style.configure("TButton", padding=(14, 6))
        style.configure("Accent.TButton", background=ACCENT, foreground="white")
        style.map("Accent.TButton", background=[("active", "#115a54"), ("disabled", "#a8b9b5")])
        header = ttk.Frame(root)
        header.pack(fill="x", padx=28, pady=(24, 8))
        ttk.Label(header, text="Novel Studio", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="作者控制中心", font=("Microsoft YaHei UI", 13)).pack(side="left", padx=16)
        ttk.Button(header, text="安装与连接", command=lambda: self.guard(self.install)).pack(side="right")
        ttk.Button(header, text="保存执行详情", command=lambda: self.guard(self.export_details)).pack(side="right", padx=10)
        ttk.Button(header, text='界面配置恢复记录', command=lambda:self.guard(lambda:messagebox.showinfo(
            '界面配置恢复记录', json.dumps(self.controller.store.recovery_records(), ensure_ascii=False, indent=2), parent=root))).pack(side='right')
        self.book = tk.StringVar()
        self.path = tk.StringVar()
        ttk.Label(root, textvariable=self.book, font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w", padx=28, pady=(12, 4))
        ttk.Label(root, textvariable=self.path, style="Note.TLabel", wraplength=1030).pack(anchor="w", padx=28)
        body = ttk.Frame(root)
        body.pack(fill="both", expand=True, padx=28, pady=18)
        nav_holder = ttk.Frame(body)
        nav_holder.pack(side="left", fill="y", padx=(0, 20))
        canvas = tk.Canvas(nav_holder, width=260, background=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(nav_holder, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        nav = ttk.Frame(canvas)
        nav_item = canvas.create_window((0, 0), window=nav, anchor="nw")
        nav.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(nav_item, width=event.width))
        for title, label, fn in [
            ("01  准备作品", "新建作品", self.new_book), (None, "打开已有作品", self.open_book), (None, "导入设定资料", self.import_file),
            ("02  主笔创作", "准备主笔资料", self.context), (None, "打开主笔", lambda: self.open_role("writer")),
            ("03  独立审稿", "将草稿交给责编", self.handoff), (None, "打开责编", lambda: self.open_role("editor")), (None, "查看报告 / 采纳意见", self.report),
            ("04  作者裁决", "预览差异 / 批准定稿", self.promote), (None, "版本历史 / 恢复", self.history),
        ]:
            if title:
                ttk.Label(nav, text=title, style="Note.TLabel").pack(anchor="w", pady=(10, 3))
            ttk.Button(nav, text=label, command=lambda f=fn: self.guard(f), width=23).pack(fill="x", pady=3)
        content = ttk.Frame(body)
        content.pack(side="left", fill="both", expand=True)
        ttk.Label(content, text="让创作流畅，让定稿由您决定", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w", pady=(8, 12))
        ttk.Label(content, text="主笔写草稿，责编只审副本。正式作品需要您查看完整差异并明确批准。\n所有作品保存在本机；模型交互遵循 Antigravity 和供应商的数据政策。", wraplength=710, justify="left").pack(anchor="w", pady=8)
        self.summary = tk.StringVar()
        ttk.Label(content, textvariable=self.summary, justify="left", wraplength=710).pack(anchor="w", pady=12)
        ttk.Label(content, text="执行详情（可选查看，无需填写参数）", style="Note.TLabel").pack(anchor="w", pady=(14, 5))
        frame, self.details = text_box(content, 14)
        frame.pack(fill="both", expand=True)
        set_text(self.details, "尚未执行操作。\n\n本图形界面通过官方本地 CLI 连接模型，不是 Antigravity 原生 App 的项目权限导入器。不会修改其全局设置。")
        self.status = tk.StringVar(value="先点击“安装与连接”，然后新建作品。无需打开命令行。")
        body.pack_forget()
        ttk.Label(root, textvariable=self.status, wraplength=1050, style="Note.TLabel").pack(side="bottom", anchor="w", padx=28, pady=(4, 18))
        body.pack(fill="both", expand=True, padx=28, pady=18)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        if self.controller.store.safe_start:
            self.status.set('已安全启动界面；未自动打开作品，原小说没有清空，请手动打开原工程。配置备份可在“界面配置恢复记录”查看。')
        self.root.after(100, self.poll)
        if initial_role and self.controller.project:
            self.root.after(300, lambda: self.guard(lambda: self.open_role(initial_role)))

    def show_center(self):
        self.root.deiconify()
        self.root.lift()

    def guard(self, fn):
        try:
            if self.installing:
                raise ValueError("安装正在进行，请稍等。")
            return fn()
        except Exception as error:
            self.error(error)

    def error(self, error, parent=None):
        self.record({"action": "操作未完成", "error": str(error)})
        messagebox.showerror("没有完成这项操作", friendly_error(error), parent=parent or self.root)

    def record(self, event):
        # Details are visible in this window, not automatically uploaded or persisted.
        self.audit.append(event)
        self.details.configure(state="normal")
        self.details.insert("end", "\n"+json.dumps(event, ensure_ascii=False, indent=2)+"\n")
        self.details.see("end")
        self.details.configure(state="disabled")

    def export_details(self):
        if not messagebox.askokcancel("保存本机执行详情", "执行详情可能包含文件路径和工具处理的正文片段。仅保存到本机，不会上传。\n\n是否保存本次窗口的执行详情？", parent=self.root):
            return
        destination = self.controller.export_details(self.audit)
        self.status.set("执行详情已保存到本机："+str(destination))

    def poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event.get("action") == "安装完成":
                    self.installing = False
                    self.status.set("安装完成。三个桌面入口已准备好，可以新建作品。")
                elif event.get("action") == "安装失败":
                    self.installing = False
                    self.status.set("安装没有完成，请查看执行详情。")
                    self.error(ValueError(event["error"]))
                self.record(event)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def refresh(self):
        c = self.controller
        self.book.set(c.project.name if c.project else "尚未选择作品")
        self.path.set(str(c.project) if c.project else "请创建新作品，或打开由控制中心建立的工程。")
        if c.project:
            state = c.state()
            self.summary.set("主笔资料："+("已准备" if state.get("context") else "尚未准备")+"\n审稿副本："+("已准备" if state.get("review") else "尚未交接")+f"\n草稿文件：{len(c.files('drafts'))}    审稿报告：{len(c.files('reports'))}\n\n正式作品区：AI 不可直接修改\n作者操作：晋升前检查差异，自动备份旧版本")
        else:
            self.summary.set("推荐顺序\n\n安装连接组件 → 新建作品 → 导入设定\n准备主笔资料 → 打开主笔写作\n交给责编 → 查看报告 → 您选择意见\n主笔修订 → 预览差异 → 批准正式定稿")

    def install(self):
        if self.installing:
            return
        self.controller.require_idle()
        if not self.controller.cli:
            chosen = filedialog.askopenfilename(title="选择官方 Antigravity 连接程序 agy.exe", filetypes=[("Antigravity CLI", "agy.exe")])
            if not chosen:
                return
            self.controller.cli = Path(chosen)
        if not messagebox.askokcancel("安装 Novel Studio", "将校验并通过官方 Antigravity CLI 安装本地写作组件，复制本程序到当前用户应用目录，并创建三个桌面快捷方式。\n\n不会修改全局安全设置，不会访问或迁移私人小说。执行内容和结果会显示在窗口中。", parent=self.root):
            return
        self.installing = True
        self.status.set("正在安装。请保留此窗口，执行内容会显示在右侧。")
        def task():
            try:
                from deployment import install_app
                installed = install_app(self.bundle, self.controller.store.root.parent, self.controller.emit)
                self.controller.install_component()
                self.controller.store.save({"installed_bundle": str(installed)})
                self.events.put({"action": "安装完成", "application": str(installed)})
            except Exception as error:
                self.events.put({"action": "安装失败", "error": str(error)})
        threading.Thread(target=task, daemon=False).start()

    def new_book(self):
        self.controller.require_idle()
        dialog = tk.Toplevel(self.root)
        dialog.title("Novel Studio — 新建作品")
        dialog.geometry("900x560")
        ttk.Label(dialog, text="创建一部新作品", style="Title.TLabel").pack(anchor="w", padx=24, pady=20)
        title = tk.StringVar()
        ttk.Label(dialog, text="作品名称").pack(anchor="w", padx=24)
        entry = ttk.Entry(dialog, textvariable=title, font=("Microsoft YaHei UI", 13))
        entry.pack(fill="x", padx=24, pady=8)
        parent = self.controller.store.root.parent/"Books"
        with SafeTree(parent.parent) as tree:
            tree.mkdir(parent.name)
        location = tk.StringVar(value=str(parent))
        row = ttk.Frame(dialog)
        row.pack(fill="x", padx=24, pady=10)
        ttk.Label(row, textvariable=location, wraplength=490).pack(side="left")
        ttk.Button(row, text="更改保存位置…", command=lambda: location.set(filedialog.askdirectory(parent=dialog, title="选择新作品的保存位置") or location.get())).pack(side="right")
        ttk.Label(dialog, text="创建全新文件夹，不会覆盖已有作品。", style="Note.TLabel").pack(anchor="w", padx=24, pady=8)
        def create():
            try:
                self.controller.new_project(Path(location.get()), title.get().strip())
                self.status.set("作品已创建。请导入设定资料，或直接准备主笔资料。")
                self.refresh()
                dialog.destroy()
            except Exception as error:
                self.error(error, dialog)
        ttk.Button(dialog, text="创建作品", style="Accent.TButton", command=create).pack(anchor="e", padx=24, pady=16)
        entry.focus_set()

    def open_book(self):
        self.controller.require_idle()
        chosen = filedialog.askdirectory(title="选择 Novel Studio 作品文件夹")
        if chosen:
            self.controller.open_project(Path(chosen))
            self.refresh()

    def import_file(self):
        self.controller.require_project()
        chosen = filedialog.askopenfilename(title="选择作者准备的设定文本", filetypes=[("文本资料", "*.md *.txt *.json")])
        if not chosen:
            return
        source = Path(chosen)
        choices = [("人物设定", "Canon-Characters"), ("世界观 / 文风 / 大纲", "Canon-Worldbuilding"), ("已批准时间线", "Approved-Timeline"), ("已有正式正文", "Final-Manuscript")]
        def select(category):
            plan = self.controller.import_preview(source, category)
            DiffWindow(self, plan, lambda reviewed: self.controller.import_approve(source, reviewed), "检查并导入作者资料")
        PickWindow(self, "这份资料属于哪一类？", choices, select)

    def context(self):
        self.controller.context()
        self.status.set("已准备新的设定副本。主笔不会直接改动正式设定。")
        self.refresh()

    def open_role(self, role):
        self.controller.require_project()
        if role in self.roles and self.roles[role].winfo_exists():
            self.roles[role].lift()
            return self.roles[role]
        state = self.controller.state()
        if role == "writer" and not state.get("context"):
            self.context()
        if role == "editor" and not state.get("review"):
            raise ValueError("请先点击“将草稿交给责编”，选择要审阅的草稿。")
        window = RoleWindow(self, role)
        self.roles[role] = window
        if role == "editor":
            window.prefill("请审阅本次交接的稿件副本，引用原句与位置，区分事实问题、主观建议和证据不足，并保存一份新的审稿报告。不要改动原稿。")
        if self.standalone:
            self.root.withdraw()
        return window

    def pick(self, area, title, callback):
        items = self.controller.files(area)
        if not items:
            raise ValueError("尚未找到草稿。请先让主笔保存草稿。" if area == "drafts" else "尚未找到审稿报告。请先让责编完成并保存报告。")
        if len(items) == 1:
            return callback(items[0])
        PickWindow(self, title, [(Path(name).name, name) for name in items], callback)

    def handoff(self):
        def go(draft):
            self.controller.handoff(draft)
            self.status.set("审稿副本已准备好。现在可以打开责编；主笔原稿没有被移动或覆盖。")
            self.refresh()
        self.pick("drafts", "选择交给责编的草稿", go)

    def report(self):
        def show(report):
            dialog = tk.Toplevel(self.root)
            dialog.title("Novel Studio — 审稿报告与作者选择")
            dialog.geometry("1010x780")
            ttk.Label(dialog, text=Path(report).name, style="Title.TLabel").pack(anchor="w", padx=20, pady=16)
            frame, output = text_box(dialog, 17)
            report_frame = frame
            set_text(output, self.controller.read(report))
            controls = ttk.Frame(dialog)
            controls.pack(side="bottom", fill="x")
            ttk.Label(controls, text="您决定采纳什么？在下方写下选定意见，不会自动采纳整份报告。", wraplength=960).pack(anchor="w", padx=20, pady=10)
            frame, selected = text_box(controls, 4, True)
            frame.pack(fill="x", padx=20)
            def transfer():
                try:
                    self.controller.feedback(report, selected.get("1.0", "end-1c"))
                    writer = self.open_role("writer")
                    writer.prefill("请读取作者选定意见副本和指定草稿，仅按作者圈定范围修订；保留修改前版本，不合并其他建议，不修改正式作品。")
                    writer.lift()
                    self.status.set("已将您选择的意见交给主笔。检查输入框后，点击发送。")
                    dialog.destroy()
                except Exception as error:
                    self.error(error, dialog)
            ttk.Button(controls, text="将我选定的意见交给主笔", style="Accent.TButton", command=transfer).pack(anchor="e", padx=20, pady=16)
            report_frame.pack(fill="both", expand=True, padx=20)
        self.pick("reports", "选择审稿报告", show)

    def promote(self):
        def choose(draft):
            dialog = tk.Toplevel(self.root)
            dialog.title("Novel Studio — 正式章节名称")
            dialog.geometry("800x340")
            ttk.Label(dialog, text="保存为哪一个正式章节？", style="Title.TLabel").pack(anchor="w", padx=20, pady=18)
            name = tk.StringVar(value=Path(draft).name)
            ttk.Entry(dialog, textvariable=name, font=("Microsoft YaHei UI", 12)).pack(fill="x", padx=20, pady=8)
            def preview():
                try:
                    plan = self.controller.preview(draft, name.get().strip())
                    dialog.destroy()
                    DiffWindow(self, plan, self.controller.approve, "检查修订差异，再批准定稿")
                except Exception as error:
                    self.error(error, dialog)
            ttk.Button(dialog, text="预览完整差异", style="Accent.TButton", command=preview).pack(anchor="e", padx=20, pady=12)
        self.pick("drafts", "选择要晋升的草稿版本", choose)

    def history(self):
        history = self.controller.history()
        if not history:
            raise ValueError("还没有正式保存记录。批准定稿或导入资料后，会自动记录历史。")
        def choose(identifier):
            plan = self.controller.restore_preview(identifier)
            DiffWindow(self, plan, lambda reviewed: self.controller.restore_approve(identifier, reviewed), "检查恢复差异，再确认恢复")
        PickWindow(self, "选择要撤回的正式保存记录", [(Path(target).name+(" · 可恢复旧版" if has_old else " · 首次创建，无更早版本"), identifier) for identifier, target, has_old in history], choose)

    def close(self):
        if self.installing or self.controller.active:
            messagebox.showinfo("请等待操作完成", "当前仍有安装、写作或审稿操作。请先等待完成，可以暂时最小化窗口。", parent=self.root)
            return
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--role", choices=("writer", "editor"))
    args = parser.parse_args()
    bundle = args.bundle or Path(__file__).resolve().parents[1]
    # Use an ordinary user folder: packaged parent processes can virtualize AppData.
    # Do not weaken opened-handle path verification to accommodate that alias.
    if os.name == "nt":
        ctypes.windll.user32.SetProcessDPIAware()
    root = tk.Tk()
    try:
        local = Path.home()
        with SafeTree(local) as tree:
            tree.mkdir("NovelStudio")
        state = args.state or local/"NovelStudio/State"
        Center(root, bundle, state, args.role)
        root.mainloop()
    except ConfigStartupCancelled:
        root.destroy()
    except Exception as error:
        messagebox.showerror("Novel Studio 未能启动", friendly_error(error), parent=root)
        root.destroy()


if __name__ == "__main__":
    main()
