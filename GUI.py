#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/GUI.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

import sys
import subprocess
import threading
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from tkinter import scrolledtext
from tkinter import messagebox, filedialog
import os
import json
from pathlib import Path
import time
import shutil
import requests


class ConfigEditor(ttk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.config_path = "data.json"
        self.backup_dir = Path("backups")
        self.backup_dir.mkdir(exist_ok=True)
        self.init_ui()
        self.load_config()

    def init_ui(self):
        self.title("配置文件编辑器 (data.json)")
        self.geometry("1000x800")

        # 顶部工具栏
        toolbar = ttk.Frame(self, padding="5")
        toolbar.pack(fill=X)

        ttk.Button(toolbar, text="保存修改", command=self.save_config, bootstyle="success").pack(side=LEFT, padx=5)
        ttk.Button(toolbar, text="重新加载", command=self.load_config, bootstyle="info").pack(side=LEFT, padx=5)
        ttk.Separator(toolbar, orient=VERTICAL).pack(side=LEFT, fill=Y, padx=10)
        ttk.Button(toolbar, text="创建备份", command=self.create_backup, bootstyle="warning").pack(side=LEFT, padx=5)
        ttk.Button(toolbar, text="管理备份", command=self.show_backups, bootstyle="secondary").pack(side=LEFT, padx=5)

        # 模式切换
        self.mode_var = ttk.StringVar(value="visual")
        mode_frame = ttk.Frame(toolbar)
        mode_frame.pack(side=RIGHT)
        ttk.Radiobutton(mode_frame, text="可视化编辑", variable=self.mode_var, value="visual", command=self.switch_mode).pack(side=LEFT, padx=5)
        ttk.Radiobutton(mode_frame, text="源码编辑", variable=self.mode_var, value="json", command=self.switch_mode).pack(side=LEFT, padx=5)

        # 主内容区域
        self.content_frame = ttk.Frame(self, padding="10")
        self.content_frame.pack(fill=BOTH, expand=True)

        # 源码编辑器 (Text)
        self.text_editor = scrolledtext.ScrolledText(self.content_frame, wrap=WORD, font=("Consolas", 11))

        # 可视化编辑器 (Treeview + Form)
        self.visual_frame = ttk.Frame(self.content_frame)
        self.create_visual_editor()

        # 默认显示可视化
        self.switch_mode()

    def create_visual_editor(self):
        # 左侧项目列表
        left_pane = ttk.Frame(self.visual_frame)
        left_pane.pack(side=LEFT, fill=Y, padx=(0, 10))

        ttk.Label(left_pane, text="项目列表", font=("bold", 10)).pack(anchor=W)

        self.project_list = ttk.Treeview(left_pane, columns=("project",), show="headings", height=20)
        self.project_list.heading("project", text="项目名称")
        self.project_list.column("project", width=150)
        self.project_list.pack(fill=Y, expand=True)
        self.project_list.bind("<<TreeviewSelect>>", self.on_project_select)

        btn_frame = ttk.Frame(left_pane)
        btn_frame.pack(fill=X, pady=5)
        ttk.Button(btn_frame, text="添加项目", command=self.add_project, width=10).pack(side=LEFT)
        ttk.Button(btn_frame, text="删除项目", command=self.delete_project, width=10).pack(side=RIGHT)

        # 右侧详细配置表单
        self.form_frame = ttk.LabelFrame(self.visual_frame, text="项目详情", padding="10")
        self.form_frame.pack(side=RIGHT, fill=BOTH, expand=True)

        # 表单字段
        self.form_vars = {}
        fields = [
            ("project", "项目名称"),
            ("username", "用户名(上传用)"),
            ("评论不少于", "评论不少于(条)"),
            ("作品天数", "作品天数(天)"),
            ("搜索关键词", "搜索关键词(逗号分隔)"),
            ("屏蔽关键词", "屏蔽关键词(逗号分隔)"),
            ("有关视频判定", "有关视频判定"),
            ("黑名单视频判定", "黑名单视频判定")
        ]

        for i, (key, label) in enumerate(fields):
            ttk.Label(self.form_frame, text=label + ":").grid(row=i, column=0, sticky=W, pady=5)
            var = ttk.StringVar()
            self.form_vars[key] = var
            # 绑定变量修改事件，实时同步到内存数据
            var.trace_add("write", lambda *args, k=key: self.update_current_project_data(k))

            if key in ["搜索关键词", "屏蔽关键词", "有关视频判定", "黑名单视频判定"]:
                entry = ttk.Entry(self.form_frame, textvariable=var, width=50)
            else:
                entry = ttk.Entry(self.form_frame, textvariable=var, width=30)
            entry.grid(row=i, column=1, sticky=W, pady=5, padx=5)

        # 全局配置区域 (放在底部)
        self.global_frame = ttk.LabelFrame(self.visual_frame, text="全局默认配置", padding="10")
        self.global_frame.pack(side=BOTTOM, fill=X, pady=(10, 0))

        # 简单的全局配置展示 (只读或简单编辑)
        self.global_text = ttk.Text(self.global_frame, height=5, font=("Consolas", 9))
        self.global_text.pack(fill=X)
        ttk.Label(self.global_frame, text="注：全局配置建议在源码模式下修改").pack(anchor=W)

    def load_config(self):
        if not os.path.exists(self.config_path):
            self.config_data = [{"items": []}]
        else:
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.config_data = json.load(f)
                    if not isinstance(self.config_data, list):
                        self.config_data = [self.config_data]
            except Exception as e:
                messagebox.showerror("错误", f"读取配置失败: {e}")
                self.config_data = [{"items": []}]

        # 刷新编辑器内容
        self.refresh_editors()

    def refresh_editors(self):
        # 1. 刷新源码编辑器
        self.text_editor.delete("1.0", END)
        self.text_editor.insert("1.0", json.dumps(self.config_data, indent=4, ensure_ascii=False))

        # 2. 刷新可视化编辑器
        self.project_list.delete(*self.project_list.get_children())
        items = self.config_data[0].get("items", [])
        for i, item in enumerate(items):
            self.project_list.insert("", END, iid=str(i), values=(item.get("project", "未命名"),))

        # 刷新全局配置显示
        global_conf = {k:v for k,v in self.config_data[0].items() if k != "items"}
        self.global_text.delete("1.0", END)
        self.global_text.insert("1.0", json.dumps(global_conf, indent=2, ensure_ascii=False))

    def switch_mode(self):
        mode = self.mode_var.get()
        if mode == "json":
            self.visual_frame.pack_forget()
            self.text_editor.pack(fill=BOTH, expand=True)
            # 同步可视化数据到源码
            # (在update_current_project_data已经实时更新了self.config_data)
            self.text_editor.delete("1.0", END)
            self.text_editor.insert("1.0", json.dumps(self.config_data, indent=4, ensure_ascii=False))

        else:
            self.text_editor.pack_forget()
            self.visual_frame.pack(fill=BOTH, expand=True)
            # 同步源码数据到可视化
            try:
                text_content = self.text_editor.get("1.0", END).strip()
                if text_content:
                    self.config_data = json.loads(text_content)
                    self.refresh_editors()
            except json.JSONDecodeError as e:
                messagebox.showerror("格式错误", f"JSON格式错误，无法切换到可视化模式: {e}")
                self.mode_var.set("json") # 切回json模式

    def on_project_select(self, event):
        selection = self.project_list.selection()
        if not selection:
            return

        index = int(selection[0])
        items = self.config_data[0].get("items", [])
        if 0 <= index < len(items):
            item = items[index]
            # 填充表单
            for key, var in self.form_vars.items():
                if key == "有关视频判定":
                    var.set(str(item.get(key, item.get("搜索关键词", ""))))
                elif key == "黑名单视频判定":
                    var.set(str(item.get(key, item.get("屏蔽关键词", ""))))
                else:
                    var.set(str(item.get(key, "")))

            self.current_item_index = index

    def update_current_project_data(self, key):
        if not hasattr(self, 'current_item_index'):
            return

        index = self.current_item_index
        items = self.config_data[0].get("items", [])

        if 0 <= index < len(items):
            # 更新数据
            val = self.form_vars[key].get()

            # 类型转换
            if key in ["评论不少于", "作品天数"]:
                try:
                    val = int(val)
                except:
                    pass

            items[index][key] = val

            # 如果是修改项目名称，同步更新列表显示
            if key == "project":
                self.project_list.item(str(index), values=(val,))

    def add_project(self):
        new_item = {
            "project": "新项目",
            "username": "外部导入",
            "评论不少于": 3,
            "作品天数": 30,
            "搜索关键词": "",
            "屏蔽关键词": "",
            "有关视频判定": "",
            "黑名单视频判定": ""
        }
        if "items" not in self.config_data[0]:
            self.config_data[0]["items"] = []

        self.config_data[0]["items"].append(new_item)
        self.refresh_editors()
        # 选中新项目
        new_idx = len(self.config_data[0]["items"]) - 1
        self.project_list.selection_set(str(new_idx))

    def delete_project(self):
        selection = self.project_list.selection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要删除的项目")
            return

        if messagebox.askyesno("确认", "确定要删除选中的项目吗？"):
            index = int(selection[0])
            del self.config_data[0]["items"][index]
            self.refresh_editors()

    def save_config(self):
        # 如果当前在源码模式，先解析源码
        if self.mode_var.get() == "json":
            try:
                content = self.text_editor.get("1.0", END).strip()
                self.config_data = json.loads(content)
            except json.JSONDecodeError as e:
                messagebox.showerror("保存失败", f"JSON格式错误: {e}")
                return

        try:
            # 备份当前配置
            self.create_backup(silent=True)

            # 写入文件
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config_data, f, indent=4, ensure_ascii=False)

            messagebox.showinfo("成功", "配置已保存")
        except Exception as e:
            messagebox.showerror("错误", f"保存失败: {e}")

    def create_backup(self, silent=False):
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup_name = f"data_backup_{timestamp}.json"
        backup_path = self.backup_dir / backup_name

        try:
            shutil.copy2(self.config_path, backup_path)
            if not silent:
                messagebox.showinfo("成功", f"备份已创建: {backup_name}")
        except Exception as e:
            if not silent:
                messagebox.showerror("错误", f"备份失败: {e}")


    def show_backups(self):
        BackupManager(self)

class BackupManager(ttk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent # ConfigEditor实例
        self.title("备份管理")
        self.geometry("600x400")

        self.listbox = ttk.Treeview(self, columns=("name", "time", "size"), show="headings")
        self.listbox.heading("name", text="文件名")
        self.listbox.heading("time", text="创建时间")
        self.listbox.heading("size", text="大小")
        self.listbox.column("name", width=250)
        self.listbox.pack(fill=BOTH, expand=True, padx=10, pady=10)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=X, pady=10, padx=10)
        ttk.Button(btn_frame, text="恢复选中备份", command=self.restore_backup).pack(side=LEFT)
        ttk.Button(btn_frame, text="删除选中备份", command=self.delete_backup, bootstyle="danger").pack(side=RIGHT)

        self.refresh_list()

    def refresh_list(self):
        self.listbox.delete(*self.listbox.get_children())
        backup_dir = Path("backups")
        if not backup_dir.exists(): return

        backups = []
        for f in backup_dir.glob("data_backup_*.json"):
            stat = f.stat()
            backups.append((f.name, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_ctime)), f"{stat.st_size} B"))

        backups.sort(key=lambda x: x[1], reverse=True)
        for b in backups:
            self.listbox.insert("", END, values=b)

    def restore_backup(self):
        selection = self.listbox.selection()
        if not selection: return

        filename = self.listbox.item(selection[0])['values'][0]
        backup_path = Path("backups") / filename

        if messagebox.askyesno("确认", f"确定要恢复备份 {filename} 吗？\n当前未保存的修改将丢失！"):
            try:
                shutil.copy2(backup_path, "data.json")
                self.parent.load_config() # 刷新父窗口配置
                messagebox.showinfo("成功", "配置已恢复")
                self.destroy()
            except Exception as e:
                messagebox.showerror("错误", f"恢复失败: {e}")

    def delete_backup(self):
        selection = self.listbox.selection()
        if not selection: return

        filename = self.listbox.item(selection[0])['values'][0]
        backup_path = Path("backups") / filename

        if messagebox.askyesno("确认", f"确定要删除备份 {filename} 吗？"):
            try:
                os.remove(backup_path)
                self.refresh_list()
            except Exception as e:
                messagebox.showerror("错误", f"删除失败: {e}")

class MediaCrawlerGUI:
    def __init__(self, root):
        self.root = root
        self.process = None

        # 初始化所有需要的变量，确保build_command可以正常工作
        # 基础配置变量
        self.platform_var = None
        self.crawler_type_var = None
        self.start_var = None
        self.keywords_var = None

        # 抖音专用配置变量
        self.dy_channel_var = None
        self.dy_sort_var = None
        self.dy_publish_time_var = None

        # 账号配置变量
        self.login_type_var = None
        self.cookie_var = None

        # 评论配置变量
        self.get_comment_var = None
        self.get_sub_comment_var = None

        # 运行配置变量
        self.headless_var = None

        # 存储配置变量
        self.save_option_var = None

        # 高级配置变量
        self.specified_id_var = None
        self.creator_id_var = None

        # 先初始化所有变量的默认值，再创建UI
        self.init_default_values()
        self.init_ui()

    def init_ui(self):
        # 初始化数据浏览器引用
        self.data_browser_window = None

        # 设置窗口标题和大小
        self.root.title("AI数字员工（本软件仅供学习交流使用，请勿用于非法用途，一切后果由使用者承担）")
        self.root.geometry("1400x1000")
        self.root.minsize(1400, 1000)

        # 创建主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=BOTH, expand=YES)

        # 添加主题切换下拉菜单
        self.create_theme_switcher(main_frame)

        # 创建分割器，用于左右布局
        splitter = ttk.PanedWindow(main_frame, orient=HORIZONTAL)
        splitter.pack(fill=BOTH, expand=YES, pady=5)

        # 左侧参数配置面板
        left_panel = ttk.Frame(splitter, padding="10")
        splitter.add(left_panel, weight=1)

        # 创建滚动框架
        canvas = ttk.Canvas(left_panel)
        scrollbar = ttk.Scrollbar(left_panel, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # 添加各个配置组
        self.create_basic_config(scrollable_frame)
        self.create_account_config(scrollable_frame)


        # 根据需求开放，隐藏的话就直接注释掉
        # self.create_comment_config(scrollable_frame)
        self.create_run_config(scrollable_frame)
        # self.create_storage_config(scrollable_frame)
        # self.create_advanced_config(scrollable_frame)

        self.create_run_buttons(scrollable_frame)

        # 布局滚动组件
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 右侧日志输出面板
        right_panel = ttk.Frame(splitter, padding="10")
        splitter.add(right_panel, weight=1)

        # 日志标题
        ttk.Label(right_panel, text="运行日志", font=("Arial", 12, "bold")).pack(anchor="w", pady=(0, 5))

        # 日志输出区域
        self.log_output = scrolledtext.ScrolledText(right_panel, wrap=WORD, height=30)
        self.log_output.pack(fill=BOTH, expand=YES)
        self.log_output.config(state=DISABLED)


    def create_theme_switcher(self, parent):
        """创建主题切换下拉菜单和配置编辑按钮"""
        theme_frame = ttk.Frame(parent)
        theme_frame.pack(fill=X, pady=(0, 10))

        # 配置编辑按钮
        ttk.Button(theme_frame, text="编辑配置(data.json)", command=self.open_config_editor, bootstyle="outline-primary").pack(side=LEFT)

        # 主题切换
        right_frame = ttk.Frame(theme_frame)
        right_frame.pack(side=RIGHT)

        ttk.Label(right_frame, text="主题:").pack(side=LEFT, padx=(0, 5))

        self.theme_var = ttk.StringVar(value=self.root.style.theme_use())
        theme_combo = ttk.Combobox(
            right_frame,
            textvariable=self.theme_var,
            values=sorted(self.root.style.theme_names()),
            state="readonly",
            width=20
        )
        theme_combo.pack(side=LEFT)
        theme_combo.bind("<<ComboboxSelected>>", self.change_theme)

    def open_config_editor(self):
        """打开配置编辑器"""
        ConfigEditor(self.root)

    def change_theme(self, event):
        """切换主题"""
        new_theme = self.theme_var.get()
        self.root.style.theme_use(new_theme)

        # 重置日志输出样式
        self.log_output.config(state=NORMAL)
        self.log_output.delete(1.0, END)
        self.log_output.insert(END, f"已切换到主题: {new_theme}\n")
        self.log_output.config(state=DISABLED)

    def create_basic_config(self, parent):
        # 基础配置组
        frame = ttk.LabelFrame(parent, text="基础配置", padding="10")
        frame.pack(fill=X, pady=5)

        # 媒体平台
        ttk.Label(frame, text="媒体平台:").grid(row=0, column=0, sticky=W, pady=2)
        self.platform_var = ttk.StringVar(value="dy (抖音)")
        platform_combo = ttk.Combobox(frame, textvariable=self.platform_var,
                                      values=[
                                        # "xhs (小红书)",
                                        "dy (抖音)",
                                        # "ks (快手)",
                                        # "bili (哔哩哔哩)",
                                        # "wb (微博)",
                                        # "tieba (百度贴吧)",
                                        # "zhihu (知乎)"
                                        ])
        platform_combo.grid(row=0, column=1, columnspan=2, sticky=EW, pady=2)

        # 爬取类型
        ttk.Label(frame, text="爬取类型:").grid(row=1, column=0, sticky=W, pady=2)
        self.crawler_type_var = ttk.StringVar(value="search (搜索)")
        crawler_type_combo = ttk.Combobox(frame, textvariable=self.crawler_type_var,
                                         values=["search (搜索)", "detail (详情)", "creator (创作者)"])
        crawler_type_combo.grid(row=1, column=1, columnspan=2, sticky=EW, pady=2)

        # 起始页码
        ttk.Label(frame, text="起始页码:").grid(row=2, column=0, sticky=W, pady=2)
        self.start_var = ttk.IntVar(value=1)
        start_spin = ttk.Spinbox(frame, from_=1, to=1000, textvariable=self.start_var)
        start_spin.grid(row=2, column=1, sticky=EW, pady=2)

        # 项目选择配置组
        project_frame = ttk.LabelFrame(parent, text="项目选择", padding="10")
        project_frame.pack(fill=X, pady=5)

        ttk.Label(project_frame, text="请选择要让AI数字人托管的项目:", font=("Arial", 10, "bold")).pack(anchor=W, pady=5)

        self.project_vars = {}
        if self.project_items:
            for item in self.project_items:
                project_name = item.get("project", "未命名项目")
                var = ttk.BooleanVar(value=True)
                self.project_vars[project_name] = var
                ttk.Checkbutton(project_frame, text=project_name, variable=var).pack(anchor=W, padx=20, pady=2)
        else:
            # 如果没有项目配置，显示关键词输入（兼容旧模式或空配置）
            ttk.Label(project_frame, text="关键词:").pack(anchor=W, pady=2)
            self.keywords_var = ttk.StringVar(value=self.config_data.get("搜索关键词", ""))
            ttk.Entry(project_frame, textvariable=self.keywords_var).pack(fill=X, padx=20, pady=2)

        # 抖音配置 (仅抖音生效)
        dy_frame = ttk.LabelFrame(parent, text="抖音配置 (仅抖音生效)", padding="5")
        dy_frame.pack(fill=X, pady=5)

        # 搜索频道
        ttk.Label(dy_frame, text="搜索频道:").grid(row=0, column=0, sticky=W, pady=2)
        self.dy_channel_var = ttk.StringVar(value="video (视频)")
        dy_channel_combo = ttk.Combobox(dy_frame, textvariable=self.dy_channel_var,
                                       values=["general (综合)", "video (视频)", "user (用户)", "live (直播)"])
        dy_channel_combo.grid(row=0, column=1, sticky=EW, pady=2)

        # 排序方式
        ttk.Label(dy_frame, text="排序方式:").grid(row=0, column=2, sticky=W, pady=2, padx=(10, 0))
        self.dy_sort_var = ttk.StringVar(value="latest (最新发布)")
        dy_sort_combo = ttk.Combobox(dy_frame, textvariable=self.dy_sort_var,
                                    values=["general (综合排序)", "most_like (最多点赞)", "latest (最新发布)"])
        dy_sort_combo.grid(row=0, column=3, sticky=EW, pady=2)

        # 发布时间
        ttk.Label(dy_frame, text="发布时间:").grid(row=1, column=0, sticky=W, pady=2)
        self.dy_publish_time_var = ttk.StringVar(value="1_week (一周内)")
        dy_time_combo = ttk.Combobox(dy_frame, textvariable=self.dy_publish_time_var,
                                    values=["unlimited (不限)", "1_day (一天内)", "1_week (一周内)", "6_months (半年内)"])
        dy_time_combo.grid(row=1, column=1, sticky=EW, pady=2)

        # 设置dy_frame的权重
        dy_frame.columnconfigure(1, weight=1)
        dy_frame.columnconfigure(3, weight=1)

    def create_account_config(self, parent):
        # 账号配置组
        frame = ttk.LabelFrame(parent, text="账号配置", padding="10")
        frame.pack(fill=X, pady=5)

        # 登录方式
        ttk.Label(frame, text="登录方式:").grid(row=0, column=0, sticky=W, pady=2)
        self.login_type_var = ttk.StringVar(value="qrcode (二维码)")
        login_type_combo = ttk.Combobox(frame, textvariable=self.login_type_var,
                                       values=["qrcode (二维码)", "phone (手机号)", "cookie (Cookie)"])
        login_type_combo.grid(row=0, column=1, columnspan=2, sticky=EW, pady=2)

        # 二维码登录相关控件
        self.qrcode_frame = ttk.Frame(frame)
        self.qrcode_frame.grid(row=1, column=0, columnspan=3, sticky=EW, pady=5)

        # 前往扫码按钮
        self.qrcode_login_btn = ttk.Button(
            self.qrcode_frame,
            text="前往扫码",
            command=self.start_qrcode_login,
            bootstyle="primary"
        )
        self.qrcode_login_btn.pack(side=LEFT, padx=(0, 10))

        # 登录状态标签
        self.qrcode_status_var = ttk.StringVar(value="未登录")
        self.qrcode_status_label = ttk.Label(
            self.qrcode_frame,
            textvariable=self.qrcode_status_var,
            foreground="gray"
        )
        self.qrcode_status_label.pack(side=LEFT, padx=(0, 10))

        # 清除登录状态按钮
        self.qrcode_clear_btn = ttk.Button(
            self.qrcode_frame,
            text="清除登录",
            command=self.clear_qrcode_login,
            bootstyle="outline-secondary",
            state=DISABLED
        )
        self.qrcode_clear_btn.pack(side=LEFT)

        # Cookie输入
        ttk.Label(frame, text="Cookie:").grid(row=2, column=0, sticky=W, pady=2)
        self.cookie_var = ttk.StringVar()
        cookie_entry = ttk.Entry(frame, textvariable=self.cookie_var)
        cookie_entry.grid(row=2, column=1, columnspan=2, sticky=EW, pady=2)

        # 设置网格权重
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, weight=2)

        # 绑定登录方式变化事件
        self.login_type_var.trace_add("write", self.on_login_type_changed)

        # 初始化二维码登录状态
        self.update_qrcode_status()

    def on_login_type_changed(self, *args):
        """登录方式改变时的回调"""
        login_type = self.login_type_var.get()
        if "qrcode" in login_type:
            self.qrcode_frame.grid()
            self.update_qrcode_status()
        else:
            self.qrcode_frame.grid_remove()

    def update_qrcode_status(self):
        """更新二维码登录状态显示"""
        try:
            state_file = Path("qrcode_login_state.json")
            if state_file.exists():
                with open(state_file, "r", encoding="utf-8") as f:
                    state_data = json.load(f)
                timestamp = state_data.get("timestamp", 0)
                # 检查是否过期（7天）
                if time.time() - timestamp > 7 * 24 * 3600:
                    self.qrcode_status_var.set("登录状态已过期")
                    self.qrcode_status_label.config(foreground="red")
                    self.qrcode_clear_btn.config(state=DISABLED)
                else:
                    save_time = time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
                    self.qrcode_status_var.set(f"已登录 ({save_time})")
                    self.qrcode_status_label.config(foreground="green")
                    self.qrcode_clear_btn.config(state=NORMAL)
            else:
                self.qrcode_status_var.set("未登录")
                self.qrcode_status_label.config(foreground="gray")
                self.qrcode_clear_btn.config(state=DISABLED)
        except Exception as e:
            self.qrcode_status_var.set("状态未知")
            self.qrcode_status_label.config(foreground="red")
            self.qrcode_clear_btn.config(state=DISABLED)

    def start_qrcode_login(self):
        """启动二维码登录流程"""
        if self.process is not None and self.process.poll() is None:
            messagebox.showwarning("提示", "爬虫正在运行中，请先停止")
            return

        self.log("[信息] 正在启动二维码登录...", "info")
        self.qrcode_login_btn.config(state=DISABLED)

        # 在后台线程中执行登录
        def login_worker():
            try:
                import asyncio
                result = asyncio.run(self.perform_qrcode_login())
                # 在主线程中更新UI
                self.root.after(0, self.on_qrcode_login_finished, result)
            except Exception as e:
                self.root.after(0, self.on_qrcode_login_finished, {"success": False, "error": str(e)})

        threading.Thread(target=login_worker, daemon=True).start()

    def on_qrcode_login_finished(self, result):
        """二维码登录完成后的回调"""
        self.qrcode_login_btn.config(state=NORMAL)
        self.update_qrcode_status()

        if result.get("success"):
            messagebox.showinfo("成功", "扫码登录成功，登录状态已保存")
        else:
            error_msg = result.get("error", "未知错误")
            messagebox.showerror("失败", f"扫码登录失败: {error_msg}")

    def clear_qrcode_login(self):
        """清除二维码登录状态"""
        try:
            state_file = Path("qrcode_login_state.json")
            if state_file.exists():
                os.remove(state_file)
                self.log("[信息] 二维码登录状态已清除", "info")
                self.update_qrcode_status()
                messagebox.showinfo("成功", "登录状态已清除")
            else:
                messagebox.showinfo("提示", "没有保存的登录状态")
        except Exception as e:
            messagebox.showerror("错误", f"清除登录状态失败: {e}")

    async def perform_qrcode_login(self):
        """执行二维码登录流程"""
        from playwright.async_api import async_playwright

        browser = None
        browser_context = None
        login_success = False
        error_msg = ""

        try:
            self.log("[扫码登录] 正在启动浏览器...", "info")
            async with async_playwright() as p:
                # 启动浏览器
                browser = await p.chromium.launch(
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled"]
                )
                self.log("[扫码登录] 浏览器已启动", "info")

                browser_context = await browser.new_context(
                    viewport={"width": 1920, "height": 1080}
                )
                self.log("[扫码登录] 浏览器上下文已创建", "info")

                # 打开页面
                page = await browser_context.new_page()
                self.log("[扫码登录] 新页面已创建", "info")

                self.log("[扫码登录] 正在打开抖音网站...", "info")
                try:
                    # 先尝试快速加载
                    await page.goto("https://www.douyin.com", wait_until="domcontentloaded", timeout=30000)
                    self.log("[扫码登录] 抖音网站已加载 (domcontentloaded)", "info")
                except:
                    # 如果超时，继续等待网络空闲
                    self.log("[扫码登录] 页面基础内容已加载，继续等待...", "info")

                # 再等待一段时间让页面完全加载
                await page.wait_for_timeout(5000)
                self.log("[扫码登录] 页面加载完成", "info")
                await page.wait_for_timeout(3000)

                # 尝试点击登录按钮
                try:
                    login_btn = page.locator("xpath=//p[text() = '登录']")
                    count = await login_btn.count()
                    self.log(f"[扫码登录] 找到 {count} 个登录按钮", "info")
                    if count > 0:
                        await login_btn.click()
                        await page.wait_for_timeout(1000)
                        self.log("[扫码登录] 已点击登录按钮", "info")
                except Exception as e:
                    self.log(f"[扫码登录] 点击登录按钮失败: {e}", "warning")

                # 等待二维码出现
                try:
                    qrcode_selector = "xpath=//div[@id='animate_qrcode_container']//img"
                    self.log("[扫码登录] 等待二维码出现...", "info")
                    await page.wait_for_selector(qrcode_selector, timeout=10000)
                    self.log("[扫码登录] 请使用手机扫描二维码", "info")
                except Exception as e:
                    self.log(f"[扫码登录] 未找到二维码: {e}", "warning")
                    # 截图查看当前页面状态
                    try:
                        await page.screenshot(path="qrcode_debug.png")
                        self.log("[扫码登录] 已保存调试截图: qrcode_debug.png", "info")
                    except:
                        pass

                # 等待登录完成（最多等待5分钟）
                max_wait = 300  # 5分钟
                waited = 0
                check_interval = 2

                self.log("[扫码登录] 开始等待扫码，请在5分钟内完成扫码...", "info")

                while waited < max_wait:
                    try:
                        # 检查登录状态
                        if await self.check_login_state_douyin(browser_context):
                            login_success = True
                            break

                        # 检查页面是否仍然打开
                        if page.is_closed():
                            self.log("[扫码登录] 页面已被关闭", "warning")
                            error_msg = "页面被关闭，请重试"
                            break

                        await page.wait_for_timeout(check_interval * 1000)
                        waited += check_interval

                        if waited % 10 == 0:
                            self.log(f"[扫码登录] 等待扫码中... {waited}秒", "info")
                    except Exception as e:
                        self.log(f"[扫码登录] 等待过程中出错: {e}", "warning")
                        # 如果是页面关闭的错误，直接退出循环
                        if "closed" in str(e).lower():
                            error_msg = "浏览器被关闭，请重试"
                            break
                        # 其他错误继续等待
                        await asyncio.sleep(check_interval)
                        waited += check_interval

                if login_success:
                    # 保存登录状态
                    await self.save_qrcode_login_state(browser_context)
                    self.log("[扫码登录] 登录成功，状态已保存", "success")
                elif not error_msg:
                    error_msg = "登录超时，请重试"
                    self.log("[扫码登录] 登录超时", "error")

        except Exception as e:
            error_msg = str(e)
            self.log(f"[扫码登录] 发生错误: {e}", "error")
            import traceback
            self.log(f"[扫码登录] 错误详情: {traceback.format_exc()}", "error")
        finally:
            if browser_context:
                try:
                    await browser_context.close()
                except:
                    pass
            if browser:
                try:
                    await browser.close()
                except:
                    pass
            self.log("[扫码登录] 浏览器已关闭", "info")

        return {"success": login_success, "error": error_msg}

    async def check_login_state_douyin(self, browser_context):
        """检查抖音登录状态"""
        try:
            current_cookie = await browser_context.cookies()
            for cookie in current_cookie:
                if cookie.get("name") == "LOGIN_STATUS" and cookie.get("value") == "1":
                    return True

            # 检查localStorage
            pages = browser_context.pages
            for page in pages:
                try:
                    local_storage = await page.evaluate("() => window.localStorage")
                    if local_storage.get("HasUserLogin", "") == "1":
                        return True
                except:
                    pass
            return False
        except Exception as e:
            return False

    async def save_qrcode_login_state(self, browser_context):
        """保存二维码登录状态"""
        try:
            cookies = await browser_context.cookies()
            state_data = {
                "platform": "douyin",
                "timestamp": time.time(),
                "cookies": cookies,
            }

            # 尝试获取localStorage
            pages = browser_context.pages
            for page in pages:
                try:
                    local_storage = await page.evaluate("() => window.localStorage")
                    state_data["local_storage"] = local_storage
                    break
                except:
                    pass

            with open("qrcode_login_state.json", "w", encoding="utf-8") as f:
                json.dump(state_data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            self.log(f"[扫码登录] 保存登录状态失败: {e}", "error")
            return False

    def create_comment_config(self, parent):
        # 评论配置组
        frame = ttk.LabelFrame(parent, text="评论配置", padding="10")
        frame.pack(fill=X, pady=5)

        # 爬取一级评论
        self.get_comment_var = ttk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="爬取一级评论", variable=self.get_comment_var).grid(row=0, column=0, columnspan=3, sticky=W, pady=2)

        # 爬取二级评论
        self.get_sub_comment_var = ttk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="爬取二级评论", variable=self.get_sub_comment_var).grid(row=1, column=0, columnspan=3, sticky=W, pady=2)

    def create_run_config(self, parent):
        # 运行配置组
        frame = ttk.LabelFrame(parent, text="运行配置", padding="10")
        frame.pack(fill=X, pady=5)

        # 自动托管开关
        if not hasattr(self, 'auto_run_var') or self.auto_run_var is None:
            self.auto_run_var = ttk.BooleanVar(value=False)
        auto_run_check = ttk.Checkbutton(frame, text="启用【自动托管】模式", variable=self.auto_run_var, bootstyle="round-toggle")
        auto_run_check.grid(row=0, column=0, columnspan=3, sticky=W, pady=2)

        ttk.Label(frame, text="说明: 开启后，Contents爬取完成后将自动爬取评论、导出Excel并上传。", font=("Arial", 9, "italic"), foreground="gray").grid(row=1, column=0, columnspan=3, sticky=W, pady=(2, 5))

        # 无头模式
        if self.headless_var is None:
            self.headless_var = ttk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="启用无头模式", variable=self.headless_var).grid(row=2, column=0, columnspan=3, sticky=W, pady=2)

    # [REMOVED] Duplicate init_default_values was here

    def process_finished(self, exit_code):
        # 进程结束处理
        if exit_code == 0:
            self.log(f"[信息] 爬虫已结束，退出代码: {exit_code}", "success")

            # 自动托管逻辑
            if self.auto_run_var.get():
                self.log("[信息] 检测到【自动托管】开启，开始执行后续任务...", "info")
                threading.Thread(target=self.auto_run_sequence, daemon=True).start()

        else:
            self.log(f"[错误] 爬虫已结束，退出代码: {exit_code}", "error")

        # 只有在非自动托管或自动托管全部完成后才恢复按钮状态
        # 这里为了简单，如果开启自动托管，我们暂时不恢复按钮，直到自动托管流程结束
        if not self.auto_run_var.get():
            self.run_btn.config(state=NORMAL)
            self.stop_btn.config(state=DISABLED)

    def auto_run_sequence(self):
        """自动执行后续任务：Comments -> Excel -> Upload"""
        try:
            # 1. 查找最新的 Contents 文件
            data_dir = Path("./data")
            contents_files = sorted(
                [f for f in data_dir.rglob("*.json") if "contents" in f.name.lower()],
                key=lambda x: x.stat().st_mtime,
                reverse=True
            )

            if not contents_files:
                self.log("[自动托管] 未找到生成的Contents文件，流程终止", "error")
                self.restore_ui_state()
                return

            latest_file = contents_files[0]
            self.log(f"[自动托管] 锁定最新Contents文件: {latest_file.name}", "info")

            # 2. 从文件提取项目和ID
            # 我们需要复用 DataBrowser 的 extract_and_filter_aweme_ids 方法
            # 为此需要实例化一个临时的 DataBrowser 或者把该方法提取为静态/工具方法
            # 这里简单起见，我们临时实例化一个DataBrowser，但这不太优雅。
            # 更好的是直接在GUI类里实现，或者调用 DataBrowser 的逻辑。
            # 由于 DataBrowser 是 Toplevel，实例化开销不大但会弹窗。
            # 我们直接复制 extract_and_filter_aweme_ids 的核心逻辑，或者调用它。

            # 临时创建 DataBrowser 实例（不显示）
            # 为了避免弹窗，我们可以重构代码，把逻辑抽离。
            # 但为了快速实现，我们手动解析一下。

            # 复用 self.data_browser_window 如果存在，否则创建不可见的
            db = DataBrowser(self.root)
            db.withdraw() # 隐藏窗口

            file_info = {
                "path": str(latest_file),
                "name": latest_file.name
            }

            # 提取ID
            project_ids = db.extract_and_filter_aweme_ids([file_info])
            db.destroy() # 用完销毁

            if not project_ids:
                self.log("[自动托管] 未提取到有效ID，流程终止", "warning")
                self.restore_ui_state()
                return

            # 过滤项目：只处理用户在主界面勾选的项目
            selected_projects = []
            for p_name in project_ids.keys():
                if self.project_vars.get(p_name) and self.project_vars.get(p_name).get():
                    selected_projects.append(p_name)

            # 如果没有勾选任何项目（比如是关键词搜索模式），则处理所有提取到的项目
            if not selected_projects:
                selected_projects = list(project_ids.keys())

            if not selected_projects:
                self.log("[自动托管] 没有需要处理的项目，流程终止", "warning")
                self.restore_ui_state()
                return

            self.log(f"[自动托管] 即将处理项目: {', '.join(selected_projects)}", "info")

            # 3. 批量爬取评论
            # 我们需要调用 run_batch_comment_crawler，但它会弹窗。
            # 我们可以改造它，或者模拟它的逻辑。
            # 为了保持一致性，我们直接调用它，让用户看到进度窗口也是合理的。
            # 但 run_batch_comment_crawler 是 DataBrowser 的方法。
            # 我们需要在 GUI 类中也能调用。

            # 实例化一个临时的 DataBrowser 用于调用方法
            db_runner = DataBrowser(self.root)
            db_runner.withdraw()

            # 重写 build_comment_crawler_command 以确保 auto_close 生效
            # (DataBrowser里已经有了)

            # 改造 run_batch_comment_crawler 使其支持回调通知结束
            # 由于它是异步线程，我们需要一种机制等待它结束。
            # 这里比较复杂。
            # 简化方案：我们手动在这里实现批量爬取逻辑，不弹窗，直接输出到主日志。

            total = len(selected_projects)
            for i, project_name in enumerate(selected_projects):
                self.log(f"[自动托管] ({i+1}/{total}) 正在爬取评论: {project_name} ...", "info")

                aweme_ids = project_ids.get(project_name, [])
                if not aweme_ids: continue

                command = db_runner.build_comment_crawler_command(aweme_ids, project_name)

                # 同步执行
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )

                for line in process.stdout:
                    # 可以在这里过滤一些日志，避免主界面太乱
                    pass

                process.wait()
                self.log(f"[自动托管] 项目 {project_name} 评论爬取完成", "success")

                # ==========================================
                # 修改：爬取完一个项目，立即执行导出和上传
                # ==========================================

                # 重新扫描最新的 Comments 文件
                # 注意：因为刚刚爬取完，应该会有最新的文件生成
                # 我们假设最新的那个就是当前项目的（或者包含了当前项目）
                try:
                    import dict_to_小鸡 as excel_converter

                    time.sleep(2) # 等待文件写入
                    current_comments_files = sorted(
                        [f for f in data_dir.rglob("*.json") if "comments" in f.name.lower()],
                        key=lambda x: x.stat().st_mtime,
                        reverse=True
                    )

                    if current_comments_files:
                        latest_c_file = current_comments_files[0]
                        # 检查时间是否是最近的 (例如 2 分钟内)
                        if time.time() - latest_c_file.stat().st_mtime < 120:
                            self.log(f"[自动托管] 正在导出项目 {project_name} (使用文件: {latest_c_file.name})", "info")

                            with open(latest_c_file, "r", encoding="utf-8") as f:
                                data = json.load(f)

                            # 提取当前项目数据
                            # 评论文件通常结构: {project_name: [...]} 或 [...]
                            items = data.get(project_name) if isinstance(data, dict) else data

                            if items:
                                temp_file = f"temp_{project_name}.json"
                                with open(temp_file, "w", encoding="utf-8") as tf:
                                    json.dump({project_name: items}, tf, ensure_ascii=False, indent=4)

                                excel_path = excel_converter.convert_json_to_excel(temp_file)
                                self.log(f"[自动托管] 导出成功: {excel_path}", "success")

                                # 上传
                                db_uploader = DataBrowser(self.root)
                                db_uploader.withdraw()
                                upload_res = None
                                max_retries = 10
                                for attempt in range(max_retries):
                                    upload_res = db_uploader.upload_excel_to_interface(excel_path, project_override=project_name)
                                    if upload_res and "error" not in upload_res:
                                        self.log(f"[自动托管] 项目 {project_name} 上传成功", "success")
                                        break
                                    else:
                                        err = upload_res.get("error") if upload_res else "未知错误"
                                        self.log(f"[自动托管] 项目 {project_name} 上传失败 (第 {attempt+1}/{max_retries} 次): {err}", "error")
                                        if attempt < max_retries - 1:
                                            self.log(f"[自动托管] 等待 5 秒后重试...", "info")
                                            time.sleep(5)
                                db_uploader.destroy()

                                if not upload_res or "error" in upload_res:
                                     self.log(f"[自动托管] 项目 {project_name} 上传最终失败，已达到最大重试次数", "error")

                                if os.path.exists(temp_file):
                                    os.remove(temp_file)
                            else:
                                self.log(f"[自动托管] 文件中未找到项目 {project_name} 的数据，跳过导出", "warning")
                        else:
                            self.log(f"[自动托管] 未找到刚刚生成的评论文件，跳过导出", "warning")
                    else:
                        self.log(f"[自动托管] 未找到评论文件，跳过导出", "warning")
                except ImportError:
                    self.log("[自动托管] 缺少 dict_to_小鸡 模块，无法导出", "error")
                except Exception as e:
                    self.log(f"[自动托管] 导出/上传出错: {e}", "error")

                # ==========================================
                # 结束导出上传
                # ==========================================

                if i < total - 1:
                    time.sleep(3)

            db_runner.destroy()

            self.log("[自动托管] 全流程结束", "success")
            self.close_edge_browser()
            self.restore_ui_state()

        except Exception as e:
            self.log(f"[自动托管] 发生严重错误: {e}", "error")
            self.close_edge_browser()
            self.restore_ui_state()

    def restore_ui_state(self):
        self.run_btn.config(state=NORMAL)
        self.stop_btn.config(state=DISABLED)

    def create_storage_config(self, parent):
        # 存储配置组
        frame = ttk.LabelFrame(parent, text="存储配置", padding="10")
        frame.pack(fill=X, pady=5)

        # 数据保存方式
        ttk.Label(frame, text="保存方式:").grid(row=0, column=0, sticky=W, pady=2)
        self.save_option_var = ttk.StringVar(value="json (JSON文件)")
        save_option_combo = ttk.Combobox(frame, textvariable=self.save_option_var,
                                        values=["json (JSON文件)", "csv (CSV文件)", "db (MySQL)", "sqlite (SQLite)", "mongodb (MongoDB)", "excel (Excel)"])
        save_option_combo.grid(row=0, column=1, columnspan=2, sticky=EW, pady=2)

        # 初始化数据库按钮
        ttk.Button(frame, text="初始化数据库", command=self.init_database).grid(row=1, column=1, sticky=EW, pady=2)

        # 设置网格权重
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, weight=2)

    def create_advanced_config(self, parent):
        # 高级配置组
        frame = ttk.LabelFrame(parent, text="高级配置", padding="10")
        frame.pack(fill=X, pady=5)

        # 详情模式ID列表
        ttk.Label(frame, text="帖子ID列表:").grid(row=0, column=0, sticky=W, pady=2)
        self.specified_id_var = ttk.StringVar()
        specified_id_entry = ttk.Entry(frame, textvariable=self.specified_id_var)
        specified_id_entry.grid(row=0, column=1, columnspan=2, sticky=EW, pady=2)

        # 创作者模式ID列表
        ttk.Label(frame, text="创作者ID列表:").grid(row=1, column=0, sticky=W, pady=2)
        self.creator_id_var = ttk.StringVar()
        creator_id_entry = ttk.Entry(frame, textvariable=self.creator_id_var)
        creator_id_entry.grid(row=1, column=1, columnspan=2, sticky=EW, pady=2)

        # 设置网格权重
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, weight=2)

    def create_run_buttons(self, parent):
        # 运行按钮组
        frame = ttk.Frame(parent, padding="10")
        frame.pack(fill=X, pady=10)

        self.button_frame = ttk.Frame(frame)
        self.button_frame.pack(fill=X, expand=True)

        # 使用grid布局让按钮均匀分布
        self.button_frame.columnconfigure(0, weight=1)
        self.button_frame.columnconfigure(1, weight=1)
        self.button_frame.columnconfigure(2, weight=1)
        self.button_frame.columnconfigure(3, weight=1)

        self.run_btn = ttk.Button(self.button_frame, text="运行爬虫", command=self.run_crawler, style="Primary.TButton")
        self.run_btn.grid(row=0, column=0, padx=5, pady=5, sticky=EW)

        self.stop_btn = ttk.Button(self.button_frame, text="停止爬虫", command=self.stop_crawler, state=DISABLED)
        self.stop_btn.grid(row=0, column=1, padx=5, pady=5, sticky=EW)

        self.clear_log_btn = ttk.Button(self.button_frame, text="清空日志", command=self.clear_log)
        self.clear_log_btn.grid(row=0, column=2, padx=5, pady=5, sticky=EW)

        # 添加数据浏览器按钮
        self.data_browser_btn = ttk.Button(self.button_frame, text="数据浏览器", command=self.open_data_browser)
        self.data_browser_btn.grid(row=0, column=3, padx=5, pady=5, sticky=EW)

    def init_default_values(self):
        """初始化所有配置变量的默认值"""

        # 读取data.json配置
        self.config_data = {}
        self.project_items = []
        try:
            if os.path.exists("data.json"):
                with open("data.json", "r", encoding="utf-8") as f:
                    self.config_data = json.load(f)
                    if isinstance(self.config_data, list) and len(self.config_data) > 0:
                        self.config_data = self.config_data[0]

                    self.project_items = self.config_data.get("items", [])
        except Exception:
            pass

        config_data = self.config_data

        # 基础配置变量
        if self.platform_var is None:
            default_platform = "dy (抖音)"
            if "默认平台" in config_data:
                 default_platform = config_data["默认平台"]
            self.platform_var = ttk.StringVar(value=default_platform)

        if self.crawler_type_var is None:
            default_crawler_type = "search (搜索)"
            if "默认爬取类型" in config_data:
                 default_crawler_type = config_data["默认爬取类型"]
            self.crawler_type_var = ttk.StringVar(value=default_crawler_type)

        if self.start_var is None:
            default_start = 1
            if "默认起始页码" in config_data:
                 default_start = config_data["默认起始页码"]
            self.start_var = ttk.IntVar(value=default_start)

        if self.keywords_var is None:
            default_keywords = "关节,膝盖,股骨头,半月板,滑膜炎,膝关节炎,软骨损伤,骨质增,关节积液,上下楼疼痛,PRP治疗,骨关节炎,运动损伤,中老年关节病"
            default_keywords = config_data.get("搜索关键词", default_keywords)
            self.keywords_var = ttk.StringVar(value=default_keywords)

        # 抖音专用配置变量
        if self.dy_channel_var is None:
            default_dy_channel = "video (视频)"
            if "默认抖音频道" in config_data:
                 default_dy_channel = config_data["默认抖音频道"]
            self.dy_channel_var = ttk.StringVar(value=default_dy_channel)

        if self.dy_sort_var is None:
            default_dy_sort = "latest (最新发布)"
            if "默认抖音排序" in config_data:
                 default_dy_sort = config_data["默认抖音排序"]
            self.dy_sort_var = ttk.StringVar(value=default_dy_sort)

        if self.dy_publish_time_var is None:
            default_dy_time = "6_months (半年内)"
            if "默认抖音发布时间" in config_data:
                 default_dy_time = config_data["默认抖音发布时间"]
            self.dy_publish_time_var = ttk.StringVar(value=default_dy_time)

        # 账号配置变量
        if self.login_type_var is None:
            default_login_type = "qrcode (二维码)"
            if "默认登录方式" in config_data:
                 default_login_type = config_data["默认登录方式"]
            self.login_type_var = ttk.StringVar(value=default_login_type)

        if self.cookie_var is None:
            default_cookie = ""
            if "默认Cookie" in config_data:
                 default_cookie = config_data["默认Cookie"]
            self.cookie_var = ttk.StringVar(value=default_cookie)

        # 评论配置变量
        if self.get_comment_var is None:
            default_get_comment = False
            if "默认爬取一级评论" in config_data:
                 default_get_comment = config_data["默认爬取一级评论"]
            self.get_comment_var = ttk.BooleanVar(value=default_get_comment)

        if self.get_sub_comment_var is None:
            default_get_sub_comment = False
            if "默认爬取二级评论" in config_data:
                 default_get_sub_comment = config_data["默认爬取二级评论"]
            self.get_sub_comment_var = ttk.BooleanVar(value=default_get_sub_comment)

        # 运行配置变量
        if self.headless_var is None:
            default_headless = False
            if "默认无头模式" in config_data:
                 default_headless = config_data["默认无头模式"]
            self.headless_var = ttk.BooleanVar(value=default_headless)

        # 自动托管变量
        if not hasattr(self, 'auto_run_var') or self.auto_run_var is None:
            default_auto_run = False
            if "autoRun" in config_data:
                default_auto_run = config_data["autoRun"]
            self.auto_run_var = ttk.BooleanVar(value=default_auto_run)

        # 存储配置变量
        if self.save_option_var is None:
            default_save_option = "json (JSON文件)"
            if "默认保存方式" in config_data:
                 default_save_option = config_data["默认保存方式"]
            self.save_option_var = ttk.StringVar(value=default_save_option)

        # 高级配置变量
        if self.specified_id_var is None:
            self.specified_id_var = ttk.StringVar(value="")
        if self.creator_id_var is None:
            self.creator_id_var = ttk.StringVar(value="")

    def apply_styles(self):
        # ttkbootstrap已经提供了内置样式，只需要配置一些特定样式
        style = ttk.Style()

        # 配置日志输出样式
        self.log_output.config(font=("Consolas", 10))
        self.log_output.tag_config("error", foreground="red")
        self.log_output.tag_config("info", foreground="blue")
        self.log_output.tag_config("success", foreground="green")

    def build_command(self, keywords=None, project_map=None):
        # 构建命令行参数
        command = ["uv", "run", "main.py"]

        # 提取平台值
        platform = self.platform_var.get().split()[0]
        command.extend(["--platform", platform])

        # 提取登录方式
        login_type = self.login_type_var.get().split()[0]
        command.extend(["--lt", login_type])

        # 提取爬取类型
        crawler_type = self.crawler_type_var.get().split()[0]
        command.extend(["--type", crawler_type])

        # 添加起始页码
        command.extend(["--start", str(self.start_var.get())])

        # 添加关键词
        final_keywords = keywords if keywords is not None else self.keywords_var.get().strip()
        if final_keywords:
            command.extend(["--keywords", final_keywords])

        # 添加项目映射
        if project_map:
            command.extend(["--project_map", project_map])

        # 添加评论配置
        get_comment = "yes" if self.get_comment_var.get() else "no"
        command.extend(["--get_comment", get_comment])

        # 根据爬取评论变量设置
        # 如果爬取类型是 detail，通常就是要爬取评论的
        if crawler_type == "detail":
             command.extend(["--get_comment", "yes"])
             command.extend(["--get_sub_comment", "yes"])
        else:
            get_sub_comment = "yes" if self.get_sub_comment_var.get() else "no"
            command.extend(["--get_sub_comment", get_sub_comment])

        # 添加无头模式
        headless = "yes" if self.headless_var.get() else "no"
        command.extend(["--headless", headless])

        # 提取保存方式
        save_option = self.save_option_var.get().split()[0]
        command.extend(["--save_data_option", save_option])

        # 添加Cookie
        cookie = self.cookie_var.get().strip()
        self.log(f"[调试] 用户输入的cookie: {cookie[:50] if cookie else '无'}...", "info")

        # 如果是二维码登录方式，尝试加载已保存的登录状态
        if login_type == "qrcode":
            self.log("[调试] 登录方式为qrcode，尝试加载保存的登录状态", "info")
            try:
                state_file = Path("qrcode_login_state.json")
                self.log(f"[调试] 查找登录状态文件: {state_file.absolute()}", "info")
                if state_file.exists():
                    with open(state_file, "r", encoding="utf-8") as f:
                        state_data = json.load(f)
                    timestamp = state_data.get("timestamp", 0)
                    self.log(f"[调试] 找到登录状态文件，时间戳: {timestamp}", "info")
                    # 检查是否过期（7天）
                    if time.time() - timestamp <= 7*30 * 24 * 3600:
                        cookies = state_data.get("cookies", [])
                        self.log(f"[调试] 保存的cookies数量: {len(cookies)}", "info")
                        if cookies:
                            # 将cookies转换为字符串格式
                            cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                            self.log(f"[调试] 转换后的cookie字符串长度: {len(cookie_str)}", "info")
                            if cookie_str:
                                if cookie:
                                    # 合并用户输入的cookie和保存的cookie
                                    cookie = cookie + "; " + cookie_str
                                else:
                                    cookie = cookie_str
                                self.log(f"[信息] 已加载保存的二维码登录状态，cookie长度: {len(cookie)}", "info")
                    else:
                        self.log("[警告] 二维码登录状态已过期，请重新扫码", "warning")
                else:
                    self.log("[警告] 未找到二维码登录状态，请先扫码登录", "warning")
            except Exception as e:
                self.log(f"[警告] 加载二维码登录状态失败: {e}", "warning")

        self.log(f"[调试] 最终cookie长度: {len(cookie) if cookie else 0}", "info")

        if cookie:
            command.extend(["--cookies", cookie])

        # 添加帖子ID列表
        specified_id = self.specified_id_var.get().strip()
        if specified_id:
            command.extend(["--specified_id", specified_id])

        # 添加创作者ID列表
        creator_id = self.creator_id_var.get().strip()
        if creator_id:
            command.extend(["--creator_id", creator_id])

        # 添加抖音专用配置
        if platform == "dy":
            # 搜索频道映射
            channel_map = {
                "general": "aweme_general",
                "video": "aweme_video_web",
                "user": "aweme_user_web",
                "live": "aweme_live"
            }
            channel_val = self.dy_channel_var.get().split()[0]
            if channel_val in channel_map:
                command.extend(["--dy_channel", channel_map[channel_val]])

            # 排序方式映射
            sort_map = {
                "general": "0",
                "most_like": "1",
                "latest": "2"
            }
            sort_val = self.dy_sort_var.get().split()[0]
            if sort_val in sort_map:
                command.extend(["--dy_sort", sort_map[sort_val]])

            # 发布时间映射
            time_map = {
                "unlimited": "0",
                "1_day": "1",
                "1_week": "7",
                "6_months": "180"
            }
            time_val = self.dy_publish_time_var.get().split()[0]
            if time_val in time_map:
                command.extend(["--dy_publish_time", time_map[time_val]])

        return command

    def run_crawler(self):
        # 运行爬虫
        if self.process is not None and self.process.poll() is None:
            self.log("[错误] 爬虫已在运行中", "error")
            return

        # 获取合并后的关键词和项目映射
        merged_keywords = ""
        project_map_json = "{}"

        if self.project_items:
            selected_keywords = set()
            keyword_to_projects = {}

            for item in self.project_items:
                project_name = item.get("project")
                if self.project_vars.get(project_name).get():
                    # 合并关键词
                    keywords_str = item.get("搜索关键词", "")
                    if keywords_str:
                        k_list = [k.strip() for k in keywords_str.split(",") if k.strip()]
                        for k in k_list:
                            selected_keywords.add(k)
                            if k not in keyword_to_projects:
                                keyword_to_projects[k] = []
                            keyword_to_projects[k].append(project_name)

            merged_keywords = ",".join(selected_keywords)
            project_map_json = json.dumps(keyword_to_projects, ensure_ascii=False)

            if not merged_keywords:
                self.log("[信息] 未选择任何项目或项目无关键词，取消运行", "info")
                return
        else:
            merged_keywords = self.keywords_var.get().strip()

        self.log("[信息] 正在启动爬虫...", "info")
        self.run_btn.config(state=DISABLED)
        self.stop_btn.config(state=NORMAL)

        # 构建命令
        command = self.build_command(merged_keywords, project_map_json)
        self.log(f"[命令] {' '.join(command)}", "info")

        # 启动进程
        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )

            # 启动线程读取输出
            threading.Thread(target=self.read_output, daemon=True).start()

            # 启动线程等待进程结束
            threading.Thread(target=self.wait_process, daemon=True).start()

        except Exception as e:
            self.log(f"[错误] 启动爬虫失败: {e}", "error")
            self.run_btn.config(state=NORMAL)
            self.stop_btn.config(state=DISABLED)

    def stop_crawler(self):
        # 停止爬虫
        if self.process is not None and self.process.poll() is None:
            self.log("[信息] 正在停止爬虫...", "info")

            # 使用 taskkill 强制结束进程树
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()

            # 恢复按钮状态
            self.run_btn.config(state=NORMAL)
            self.stop_btn.config(state=DISABLED)

    def close_edge_browser(self):
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.log("[自动托管] 已尝试关闭Edge浏览器", "info")
        except Exception as e:
            self.log(f"[自动托管] 关闭Edge浏览器失败: {e}", "warning")

    def read_output(self):
        # 读取进程输出
        if self.process is not None:
            for line in self.process.stdout:
                self.log(line.strip())

    def wait_process(self):
        # 等待进程结束
        if self.process is not None:
            exit_code = self.process.wait()
            self.root.after(0, self.process_finished, exit_code)

    # [REMOVED] Duplicate process_finished was here

    def init_database(self):
        # 初始化数据库
        self.log("[信息] 正在初始化数据库...", "info")

        # 构建命令
        command = ["uv", "run", "main.py", "--init_db"]
        self.log(f"[命令] {' '.join(command)}", "info")

        # 执行命令
        try:
            result = subprocess.run(command, capture_output=True, text=True)

            if result.returncode == 0:
                self.log("[成功] 数据库初始化完成", "success")
                messagebox.showinfo("成功", "数据库初始化完成")
            else:
                self.log(f"[错误] 数据库初始化失败: {result.stderr}", "error")
                messagebox.showerror("错误", f"数据库初始化失败: {result.stderr}")
        except Exception as e:
            self.log(f"[错误] 数据库初始化失败: {e}", "error")
            messagebox.showerror("错误", f"数据库初始化失败: {e}")

    def log(self, message, tag=""):
        # 记录日志
        self.log_output.config(state=NORMAL)
        if tag:
            self.log_output.insert(END, message + "\n", tag)
        else:
            self.log_output.insert(END, message + "\n")

        # 只保留最近的500行，防止页面卡顿
        try:
            # 获取当前总行数
            # end-1c 指向最后一个字符（刚才插入的换行符）
            current_lines = int(self.log_output.index('end-1c').split('.')[0])
            if current_lines > 500:
                # 计算需要删除的行数
                lines_to_delete = current_lines - 500
                # 删除开头的多余行
                # delete的第二个参数是不包含的，所以要+1指向下一行的开头
                self.log_output.delete("1.0", f"{lines_to_delete + 1}.0")
        except Exception:
            pass

        self.log_output.see(END)
        self.log_output.config(state=DISABLED)

    def clear_log(self):
        # 清空日志
        self.log_output.config(state=NORMAL)
        self.log_output.delete(1.0, END)
        self.log_output.config(state=DISABLED)
        self.log("日志已清空", "info")

    def on_closing(self):
        # 关闭窗口时的处理
        if self.process is not None and self.process.poll() is None:
            if messagebox.askokcancel("退出", "爬虫正在运行，确定要退出吗？"):
                self.stop_crawler()
                self.root.destroy()
        else:
            self.root.destroy()


class DataBrowser(ttk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.data_dir = Path("./data")
        self.files = []
        self.filtered_files = []
        self.displayed_files = [] # 当前显示的文件
        self.current_filter = "全部"
        self.show_all_files = False # 是否显示所有文件
        self.auto_upload_var = ttk.BooleanVar(value=True) # 自动上传选项，默认开启
        self.init_ui()
        self.scan_files()

    def init_ui(self):
        # 设置窗口标题和大小
        self.title("数据浏览器")
        self.geometry("900x600")
        self.minsize(800, 500)

        # 创建主框架
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=BOTH, expand=True)

        # 标题栏
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(fill=X, pady=(0, 10))

        ttk.Label(title_frame, text="数据浏览器", font=("Arial", 16, "bold")).pack(side=LEFT)

        # 数据文件管理标题和扫描按钮
        management_frame = ttk.Frame(main_frame)
        management_frame.pack(fill=X, pady=(0, 10))

        self.stats_label = ttk.Label(management_frame, text="数据文件管理 0条", font=("Arial", 12, "bold"))
        self.stats_label.pack(side=LEFT)

        refresh_btn = ttk.Button(management_frame, text="重新扫描", command=self.rescan_files)
        refresh_btn.pack(side=RIGHT)

        # 自动上传开关
        ttk.Checkbutton(management_frame, text="导出后自动上传", variable=self.auto_upload_var, bootstyle="round-toggle").pack(side=RIGHT, padx=10)

        # 过滤标签
        filter_frame = ttk.Frame(main_frame)
        filter_frame.pack(fill=X, pady=(0, 10))

        self.all_filter = ttk.Button(filter_frame, text="全部 (0)", command=lambda: self.set_filter("全部"))
        self.all_filter.pack(side=LEFT, padx=5)

        self.comments_filter = ttk.Button(filter_frame, text="Comments (0)", command=lambda: self.set_filter("Comments"))
        self.comments_filter.pack(side=LEFT, padx=5)

        self.contents_filter = ttk.Button(filter_frame, text="Contents (0)", command=lambda: self.set_filter("Contents"))
        self.contents_filter.pack(side=LEFT, padx=5)

        # 加载更多按钮（初始隐藏）
        self.load_more_btn = ttk.Button(filter_frame, text="加载更多历史数据", command=self.load_more_data, bootstyle="outline-primary")
        self.load_more_btn.pack(side=RIGHT, padx=5)

        # 文件列表框架
        self.files_frame = ttk.Frame(main_frame)
        self.files_frame.pack(fill=BOTH, expand=True)

        # 创建画布和滚动条
        self.canvas = ttk.Canvas(self.files_frame)
        self.scrollbar = ttk.Scrollbar(self.files_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        # 配置画布和滚动条
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        # 布局画布和滚动条
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")


    def scan_files(self):
        """扫描数据文件"""
        self.files = []
        supported_extensions = {".json", ".csv", ".xlsx", ".xls"}

        if not self.data_dir.exists():
            self.update_stats()
            self.update_file_list()
            return

        for root, dirs, filenames in os.walk(self.data_dir):
            root_path = Path(root)
            for filename in filenames:
                file_path = root_path / filename
                if file_path.suffix.lower() not in supported_extensions:
                    continue

                file_info = self.get_file_info(file_path)
                self.files.append(file_info)

        # 按修改时间排序（最新的在前）
        self.files.sort(key=lambda x: x["modified_at"], reverse=True)

        self.filter_files()

    def get_file_info(self, file_path):
        """获取文件信息"""
        stat = file_path.stat()
        record_count = 0
        project_counts = {}

        # 尝试获取记录数
        try:
            if file_path.suffix == ".json":
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        record_count = len(data)
                        project_counts = {"Default": record_count}
                    elif isinstance(data, dict):
                        # 如果是新版字典格式，统计各项目的记录数
                        for project_name, items in data.items():
                            if isinstance(items, list):
                                count = len(items)
                                project_counts[project_name] = count
                                record_count += count
            elif file_path.suffix == ".csv":
                with open(file_path, "r", encoding="utf-8") as f:
                    record_count = sum(1 for _ in f) - 1  # 减去标题行
                    project_counts = {"CSV数据": record_count}
        except Exception:
            record_count = 0
            project_counts = {}

        # 确定文件类型
        file_type = "unknown"
        if "comments" in file_path.name.lower():
            file_type = "Comments"
        elif "contents" in file_path.name.lower():
            file_type = "Contents"

        return {
            "name": file_path.name,
            "path": str(file_path),
            "size": stat.st_size,
            "modified_at": stat.st_mtime,
            "record_count": record_count,
            "project_counts": project_counts,
            "type": file_path.suffix[1:] if file_path.suffix else "unknown",
            "category": file_type
        }

    def filter_files(self):
        """根据当前过滤条件过滤文件"""
        # 1. 首先按类别过滤
        if self.current_filter == "全部":
            base_filtered = self.files.copy()
        else:
            base_filtered = [f for f in self.files if f["category"] == self.current_filter]

        # 2. 如果未启用"显示所有"，则仅保留3天内的数据
        if not self.show_all_files:
            three_days_ago = time.time() - 3 * 24 * 60 * 60
            self.displayed_files = [f for f in base_filtered if f["modified_at"] >= three_days_ago]

            # 如果过滤后数量少于总数，显示"加载更多"按钮
            if len(self.displayed_files) < len(base_filtered):
                self.load_more_btn.pack(side=RIGHT, padx=5)
            else:
                self.load_more_btn.pack_forget()
        else:
            self.displayed_files = base_filtered
            self.load_more_btn.pack_forget()

        self.filtered_files = base_filtered # 保存当前类别下的所有文件，用于统计
        self.update_stats()
        self.update_file_list()

    def load_more_data(self):
        """加载更多数据"""
        self.show_all_files = True
        self.filter_files()

    def update_stats(self):
        """更新统计信息"""
        total = len(self.files)
        comments = len([f for f in self.files if f["category"] == "Comments"])
        contents = len([f for f in self.files if f["category"] == "Contents"])

        # 显示当前展示数量/总数量
        self.stats_label.config(text=f"数据文件管理 {len(self.displayed_files)}/{len(self.filtered_files)}条")

        # 更新过滤按钮文本和样式
        self.all_filter.config(text=f"全部 ({total})")
        self.comments_filter.config(text=f"Comments ({comments})")
        self.contents_filter.config(text=f"Contents ({contents})")

        # 更新激活状态
        for btn in [self.all_filter, self.comments_filter, self.contents_filter]:
            btn.config(style="FilterButton.TButton")

        if self.current_filter == "全部":
            self.all_filter.config(style="ActiveFilter.TButton")
        elif self.current_filter == "Comments":
            self.comments_filter.config(style="ActiveFilter.TButton")
        elif self.current_filter == "Contents":
            self.contents_filter.config(style="ActiveFilter.TButton")

    def update_file_list(self):
        """更新文件列表"""
        # 清空现有文件卡片
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()

        # 创建文件卡片网格
        if not self.displayed_files:
            # 显示空状态
            empty_label = ttk.Label(self.scrollable_frame, text="没有找到文件", style="Empty.TLabel")
            empty_label.grid(row=0, column=0, padx=20, pady=20)
            return

        # 创建卡片容器框架
        cards_frame = ttk.Frame(self.scrollable_frame)
        cards_frame.grid(row=0, column=0, sticky=NSEW)

        # 配置网格权重
        self.scrollable_frame.columnconfigure(0, weight=1)
        cards_frame.columnconfigure(0, weight=1)
        cards_frame.columnconfigure(1, weight=1)
        cards_frame.columnconfigure(2, weight=1)

        # 创建文件卡片
        for i, file_info in enumerate(self.displayed_files):
            row = i // 3
            col = i % 3

            card = self.create_file_card(cards_frame, file_info)
            card.grid(row=row, column=col, padx=10, pady=10, sticky=NSEW)

        # 为最后一行添加占位符，确保卡片居中
        for col in range(len(self.displayed_files) % 3, 3):
            placeholder = ttk.Frame(cards_frame)
            placeholder.grid(row=(len(self.displayed_files) + 2) // 3, column=col, padx=10, pady=10)

    def create_file_card(self, parent, file_info):
        """创建文件卡片"""
        card = ttk.Frame(parent, padding="15", bootstyle="secondary")
        card.configure(relief="solid", borderwidth=1)

        # 文件图标和名称
        name_frame = ttk.Frame(card)
        name_frame.pack(fill=X, pady=(0, 10))

        ttk.Label(name_frame, text="📄", font=("Arial", 20)).pack(side=LEFT, padx=(0, 10))
        ttk.Label(name_frame, text=file_info["name"], font=("Arial", 11, "bold"), wraplength=200).pack(side=LEFT, fill=X, expand=True)

        # 文件大小和记录数
        info_frame = ttk.Frame(card)
        info_frame.pack(fill=X, pady=(0, 5))

        size_str = self.format_size(file_info["size"])
        ttk.Label(info_frame, text=f"大小: {size_str}", font=("Arial", 9)).pack(side=LEFT)
        ttk.Label(info_frame, text=f"总量: {file_info['record_count']} 条", font=("Arial", 9)).pack(side=RIGHT)

        # 分项目数量显示
        if file_info.get("project_counts"):
            counts_frame = ttk.Frame(card)
            counts_frame.pack(fill=X, pady=(0, 10))

            project_text = ""
            for p_name, p_count in file_info["project_counts"].items():
                project_text += f"{p_name}: {p_count} | "

            if project_text:
                project_text = project_text.rstrip(" | ")
                p_label = ttk.Label(counts_frame, text=project_text, font=("Arial", 8), foreground="gray", wraplength=250)
                p_label.pack(fill=X)

        # 修改时间
        time_str = time.strftime("%Y/%m/%d %H:%M", time.localtime(file_info["modified_at"]))
        ttk.Label(card, text=time_str).pack(fill=X, pady=(0, 10))

        # 底部区域：文件类型和操作按钮
        bottom_frame = ttk.Frame(card)
        bottom_frame.pack(fill=X, pady=(0, 5))

        # 文件类型标签
        type_label = ttk.Label(bottom_frame, text=f".{file_info['type']}", bootstyle="info")
        type_label.pack(side=LEFT)

        # 操作按钮
        btn_frame = ttk.Frame(bottom_frame)
        btn_frame.pack(side=RIGHT)

        # 导出Excel按钮（仅对Comments类型的JSON文件显示）
        if file_info['type'].lower() == 'json' and file_info['category'] == 'Comments':
            export_btn = ttk.Button(btn_frame, text="导出Excel", command=lambda: self.export_to_excel(file_info), bootstyle="success")
            export_btn.pack(side=RIGHT, padx=(0, 5))

        # 爬取评论按钮（仅对Contents类型的JSON文件显示）
        if file_info['type'].lower() == 'json' and file_info['category'] == 'Contents':
            crawl_btn = ttk.Button(btn_frame, text="爬取评论", command=lambda: self.crawl_comments_from_file(file_info), bootstyle="success")
            crawl_btn.pack(side=RIGHT, padx=(0, 5))

            # 复查按钮（清空pass值并重新过滤）
            review_btn = ttk.Button(btn_frame, text="复查", command=lambda: self.review_content_file(file_info), bootstyle="warning")
            review_btn.pack(side=RIGHT, padx=(0, 5))

        # 预览按钮（仅对JSON文件显示）
        if file_info['type'].lower() == 'json':
            preview_btn = ttk.Button(btn_frame, text="预览", command=lambda: self.preview_file(file_info), bootstyle="success")
            preview_btn.pack(side=RIGHT, padx=(0, 5))

        # 下载按钮
        download_btn = ttk.Button(btn_frame, text="下载", command=lambda: self.download_file(file_info), bootstyle="primary")
        download_btn.pack(side=RIGHT)


        return card

    def review_content_file(self, file_info):
        """复查Contents文件：清空pass值并重新过滤"""
        # 1. 确认
        if not messagebox.askyesno("确认", f"确定要复查文件 {file_info['name']} 吗？\n这将清空所有现有的pass状态并重新通过接口筛选。"):
            return

        # 2. 创建弹窗显示进度
        self.review_window = ttk.Toplevel(self)
        self.review_window.title("文件复查中")
        self.review_window.geometry("800x600")

        # 创建主框架
        main_frame = ttk.Frame(self.review_window, padding="10")
        main_frame.pack(fill=BOTH, expand=True)

        # 日志标题
        ttk.Label(main_frame, text="复查运行日志", font=("Arial", 12, "bold")).pack(anchor="w", pady=(0, 5))

        # 日志输出区域
        self.review_log = scrolledtext.ScrolledText(main_frame, wrap=WORD, height=30)
        self.review_log.pack(fill=BOTH, expand=YES)
        self.review_log.config(state=DISABLED)

        # 3. 启动线程
        threading.Thread(target=self.execute_review, args=(file_info,), daemon=True).start()

    def update_review_log(self, msg):
        """更新复查日志"""
        if not self.review_window.winfo_exists():
            return
        self.review_log.config(state=NORMAL)
        self.review_log.insert(END, msg + "\n")
        self.review_log.see(END)
        self.review_log.config(state=DISABLED)

    def execute_review(self, file_info):
        """执行复查逻辑"""
        file_path = Path(file_info["path"])
        try:
            # 1. 读取配置文件
            self.review_window.after(0, self.update_review_log, "[INFO] 正在读取配置文件 data.json ...")
            if not os.path.exists("data.json"):
                self.review_window.after(0, self.update_review_log, "[ERROR] 找不到 data.json")
                return

            with open("data.json", "r", encoding="utf-8") as f:
                config_data = json.load(f)
                if isinstance(config_data, list) and len(config_data) > 0:
                    config_data = config_data[0]

            host = config_data.get("host")
            if not host:
                self.review_window.after(0, self.update_review_log, "[ERROR] data.json 中未配置 host")
                return

            api_url = f"{host.rstrip('/')}/api/filter_video_titles"

            # 准备项目配置映射
            project_configs = {}
            for item in config_data.get("items", []):
                p_name = item.get("project")
                if p_name:
                    project_configs[p_name] = {
                        "主题": item.get("project", ""),
                        "有关": item.get("搜索关键词", ""),
                        "无关": item.get("屏蔽关键词", "")
                    }

            # 2. 读取内容文件
            self.review_window.after(0, self.update_review_log, f"[INFO] 正在读取文件: {file_info['name']}")
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 3. 处理数据
            total_processed = 0
            has_changes = False

            # 统一转为字典处理
            data_dict = {}
            if isinstance(data, list):
                data_dict["Default"] = data
            elif isinstance(data, dict):
                data_dict = data

            for project_name, items in data_dict.items():
                if not items: continue

                # 获取筛选配置
                filter_config = project_configs.get(project_name)
                if not filter_config:
                    self.review_window.after(0, self.update_review_log, f"[WARNING] 未找到项目 {project_name} 的筛选配置，跳过筛选")
                    continue

                self.review_window.after(0, self.update_review_log, f"[INFO] 正在处理项目: {project_name}, 共 {len(items)} 条")

                # 清除pass并准备检查列表
                items_to_check = []
                indices_to_check = []

                for i, item in enumerate(items):
                    # 清除旧的pass状态
                    if "pass" in item:
                        del item["pass"]
                        has_changes = True

                    items_to_check.append(item)
                    indices_to_check.append(i)

                # 分批调用接口
                batch_size = 10
                for i in range(0, len(items_to_check), batch_size):
                    batch_items = items_to_check[i:i+batch_size]
                    batch_indices = indices_to_check[i:i+batch_size]

                    titles = [item.get("title", "") or item.get("desc", "") for item in batch_items]

                    payload = {
                        "titles": titles,
                        "filter_config": filter_config
                    }

                    try:
                        resp = requests.post(api_url, json=payload, timeout=30)
                        if resp.status_code == 200:
                            res_json = resp.json()
                            if res_json.get("success"):
                                results = res_json.get("results", [])
                                for j, passed in enumerate(results):
                                    if j < len(batch_items):
                                        idx = batch_indices[j]
                                        items[idx]["pass"] = passed
                                        has_changes = True
                                        total_processed += 1
                                        status_str = "通过" if passed else "拒绝"
                                        self.review_window.after(0, self.update_review_log, f"  - [{status_str}] {titles[j][:30]}...")
                            else:
                                self.review_window.after(0, self.update_review_log, f"[ERROR] 接口返回失败: {res_json}")
                        else:
                            self.review_window.after(0, self.update_review_log, f"[ERROR] 接口HTTP错误: {resp.status_code}")
                    except Exception as e:
                        self.review_window.after(0, self.update_review_log, f"[ERROR] 请求异常: {e}")

                    # 稍微停顿避免请求过快
                    time.sleep(0.5)

            # 4. 保存文件
            if has_changes:
                self.review_window.after(0, self.update_review_log, "[INFO] 正在保存文件...")
                # 恢复原始结构
                save_data = data_dict if isinstance(data, dict) else data_dict["Default"]

                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(save_data, f, ensure_ascii=False, indent=4)
                self.review_window.after(0, self.update_review_log, "[SUCCESS] 文件保存成功")
                self.review_window.after(0, lambda: messagebox.showinfo("成功", f"复查完成，共处理 {total_processed} 条数据"))
            else:
                self.review_window.after(0, self.update_review_log, "[INFO] 没有数据发生变化")

        except Exception as e:
            self.review_window.after(0, self.update_review_log, f"[FATAL] 复查过程发生错误: {str(e)}")
            self.review_window.after(0, lambda: messagebox.showerror("错误", f"复查失败: {str(e)}"))

    def download_file(self, file_info):
        """下载文件"""
        try:
            # 获取源文件路径
            source_path = Path(file_info["path"])

            # 打开文件选择对话框，让用户选择保存位置
            save_path = filedialog.asksaveasfilename(
                defaultextension=f".{file_info['type']}",
                filetypes=[(f"{file_info['type'].upper()} Files", f"*.{file_info['type']}"), ("All Files", "*.*")],
                initialfile=file_info["name"]
            )

            if save_path:
                # 复制文件
                shutil.copy2(source_path, save_path)
                messagebox.showinfo("成功", f"文件已下载到: {save_path}")
        except Exception as e:
            messagebox.showerror("错误", f"下载失败: {str(e)}")

    def preview_file(self, file_info):
        """预览JSON文件"""
        try:
            # 创建预览窗口
            preview_window = ttk.Toplevel(self)
            preview_window.title(f"文件预览 - {file_info['name']}")
            preview_window.geometry("800x600")
            preview_window.minsize(600, 400)

            # 创建主框架
            main_frame = ttk.Frame(preview_window, padding="10")
            main_frame.pack(fill=BOTH, expand=True)

            # 标题栏
            title_frame = ttk.Frame(main_frame)
            title_frame.pack(fill=X, pady=(0, 10))

            ttk.Label(title_frame, text=f"预览: {file_info['name']}", font=("Arial", 14, "bold")).pack(side=LEFT)

            # 关闭按钮
            close_btn = ttk.Button(title_frame, text="关闭", command=preview_window.destroy, bootstyle="outline-danger")
            close_btn.pack(side=RIGHT)

            # 内容显示区域
            content_frame = ttk.Frame(main_frame)
            content_frame.pack(fill=BOTH, expand=True)

            # 创建文本框用于显示JSON内容
            text_widget = scrolledtext.ScrolledText(content_frame, wrap=WORD, font=("Consolas", 10))
            text_widget.pack(fill=BOTH, expand=True)
            text_widget.configure(bg="#2d2d2d", fg="#f0f0f0", insertbackground="white")

            # 读取并显示JSON文件内容
            file_path = Path(file_info["path"])
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

                # 尝试格式化JSON
                try:
                    import json as json_module
                    parsed = json_module.loads(content)
                    formatted = json_module.dumps(parsed, ensure_ascii=False, indent=2)
                    text_widget.insert(END, formatted)
                except json_module.JSONDecodeError:
                    # 如果JSON格式错误，直接显示原始内容
                    text_widget.insert(END, content)

            # 设置文本为只读
            text_widget.config(state=DISABLED)

        except Exception as e:
            messagebox.showerror("错误", f"预览失败: {str(e)}")

    def format_size(self, size_bytes):
        """格式化文件大小"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.2f} KB"
        else:
            return f"{size_bytes / (1024 * 1024):.2f} MB"

    def set_filter(self, filter_type):
        """设置过滤条件"""
        self.current_filter = filter_type
        self.filter_files()

    def rescan_files(self):
        """重新扫描文件"""
        self.scan_files()

    def crawl_comments_from_file(self, file_info):
        """从单个文件爬取评论"""
        # 提取并过滤aweme_id，返回项目到ID列表的映射
        project_ids = self.extract_and_filter_aweme_ids([file_info])
        if not project_ids:
            messagebox.showinfo("提示", f"文件 {file_info['name']} 中没有找到有效aweme_id")
            return

        # 显示项目选择对话框，让用户选择要爬取的项目
        selected_projects = self.show_project_selection_dialog(project_ids)
        if not selected_projects:
            return  # 用户取消或未选择任何项目

        # 为每个选中的项目依次运行评论爬虫
        # 使用批量运行方法，确保同一时间只有一个爬虫在运行
        self.run_batch_comment_crawler(selected_projects, project_ids)

    def show_project_selection_dialog(self, project_ids, title="选择要爬取的项目", prompt="请选择要爬取评论的项目：", item_suffix="个视频"):
        """显示项目选择对话框，返回用户选中的项目名称列表"""
        # 创建对话框窗口
        dialog = ttk.Toplevel(self)
        dialog.title(title)
        dialog.geometry("500x600")
        dialog.transient(self)  # 设置为父窗口的临时窗口
        dialog.grab_set()  # 模态对话框

        # 创建主框架
        main_frame = ttk.Frame(dialog, padding="20")
        main_frame.pack(fill=BOTH, expand=True)

        # 标题
        ttk.Label(main_frame, text=prompt, font=("Arial", 11, "bold")).pack(anchor=W, pady=(0, 10))

        # 说明
        ttk.Label(main_frame, text="提示：可以通过右侧按钮调整爬取顺序", font=("Arial", 9, "italic"), bootstyle="secondary").pack(anchor=W, pady=(0, 10))

        # 创建复选框框架
        checkbox_frame = ttk.Frame(main_frame)
        checkbox_frame.pack(fill=BOTH, expand=True, pady=(0, 10))

        # 添加滚动条
        canvas = ttk.Canvas(checkbox_frame)
        scrollbar = ttk.Scrollbar(checkbox_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 存储复选框变量和UI组件
        project_items = [] # list of dict: {name, var, frame}

        # 全选/取消全选变量
        select_all_var = ttk.BooleanVar(value=True)

        def toggle_all():
            """切换全选状态"""
            is_selected = select_all_var.get()
            for item in project_items:
                item['var'].set(is_selected)

        # 全选复选框
        select_all_cb = ttk.Checkbutton(
            scrollable_frame,
            text="全选",
            variable=select_all_var,
            command=toggle_all,
            bootstyle="primary"
        )
        select_all_cb.pack(anchor=W, pady=(0, 10))

        # 分隔线
        ttk.Separator(scrollable_frame, orient=HORIZONTAL).pack(fill=X, pady=(0, 10))

        def move_up(index):
            if index > 0:
                # 交换数据
                project_items[index], project_items[index-1] = project_items[index-1], project_items[index]
                # 重新渲染
                refresh_list()

        def move_down(index):
            if index < len(project_items) - 1:
                # 交换数据
                project_items[index], project_items[index+1] = project_items[index+1], project_items[index]
                # 重新渲染
                refresh_list()

        def refresh_list():
            # 清除当前显示
            for widget in scrollable_frame.winfo_children():
                if widget not in [select_all_cb]: # 保留全选按钮
                    # 检查是否是分隔线
                    if isinstance(widget, ttk.Separator):
                        continue
                    widget.pack_forget() # 暂时隐藏而不是销毁，或者销毁重建

            # 重新 pack 全选按钮和分隔线 (其实它们一直都在，只是位置问题)
            # 这里简单起见，我们假设 scrollable_frame 只包含项目 items
            # 因为全选按钮和分隔线是在最上面，不需要动

            # 先把所有 item frame 移除
            for item in project_items:
                item['frame'].pack_forget()

            # 按新顺序 pack
            for i, item in enumerate(project_items):
                item['frame'].pack(fill=X, pady=2)
                # 更新按钮命令
                item['up_btn'].configure(command=lambda idx=i: move_up(idx))
                item['down_btn'].configure(command=lambda idx=i: move_down(idx))

                # 更新按钮状态
                if i == 0:
                    item['up_btn'].configure(state=DISABLED)
                else:
                    item['up_btn'].configure(state=NORMAL)

                if i == len(project_items) - 1:
                    item['down_btn'].configure(state=DISABLED)
                else:
                    item['down_btn'].configure(state=NORMAL)

        # 初始化列表
        for project_name in sorted(project_ids.keys()):
            aweme_ids_count = len(project_ids[project_name])
            var = ttk.BooleanVar(value=True)  # 默认选中

            item_frame = ttk.Frame(scrollable_frame)

            # 复选框
            cb_text = f"{project_name} ({aweme_ids_count}{item_suffix})"
            cb = ttk.Checkbutton(item_frame, text=cb_text, variable=var)
            cb.pack(side=LEFT, fill=X, expand=True)

            # 排序按钮
            btn_frame = ttk.Frame(item_frame)
            btn_frame.pack(side=RIGHT)

            up_btn = ttk.Button(btn_frame, text="↑", width=3, bootstyle="outline-secondary")
            up_btn.pack(side=LEFT, padx=2)

            down_btn = ttk.Button(btn_frame, text="↓", width=3, bootstyle="outline-secondary")
            down_btn.pack(side=LEFT, padx=2)

            project_items.append({
                "name": project_name,
                "var": var,
                "frame": item_frame,
                "up_btn": up_btn,
                "down_btn": down_btn
            })

        # 初次渲染
        refresh_list()

        # 按钮框架
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=X, pady=(10, 0))

        # 存储结果
        result = []

        def on_confirm():
            """确认按钮回调"""
            nonlocal result
            # 按当前顺序收集选中的项目
            selected = [item['name'] for item in project_items if item['var'].get()]
            if not selected:
                messagebox.showwarning("提示", "请至少选择一个项目", parent=dialog)
                return
            result = selected
            dialog.destroy()

        def on_cancel():
            """取消按钮回调"""
            dialog.destroy()

        # 确认和取消按钮
        ttk.Button(button_frame, text="确认", command=on_confirm, bootstyle="primary").pack(side=RIGHT, padx=(10, 0))
        ttk.Button(button_frame, text="取消", command=on_cancel, bootstyle="secondary").pack(side=RIGHT)

        # 等待对话框关闭
        self.wait_window(dialog)

        return result

    def extract_and_filter_aweme_ids(self, contents_files):
        """从Contents文件中提取aweme_id并进行分项目过滤，返回项目到ID列表的映射"""
        project_ids = {} # {project_name: set(id1, id2, ...)}

        # 1. 预先加载所有项目的过滤规则映射
        project_configs = {}
        try:
            if os.path.exists("data.json"):
                with open("data.json", "r", encoding="utf-8") as f:
                    config_data = json.load(f)
                    # 兼容不同格式的 data.json
                    if isinstance(config_data, list) and len(config_data) > 0:
                        config_data = config_data[0]

                    items = config_data.get("items", [])
                    for item in items:
                        p_name = item.get("project")
                        if p_name:
                            # 提取并预处理该项目的规则
                            kw_str = item.get("屏蔽关键词", "")
                            project_configs[p_name] = {
                                "filter_keywords": [k.strip() for k in kw_str.split(",") if k.strip()],
                                "min_comment_count": item.get("评论不少于", 0),
                                "days_limit": item.get("作品天数", 60)
                            }
        except Exception as e:
            self.log_message(f"预加载项目配置失败: {str(e)}", "error")

        # 2. 遍历文件并按项目规则过滤
        for file_info in contents_files:
            file_path = Path(file_info["path"])
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if isinstance(data, list):
                    # 旧版列表格式，归类到 "Default"
                    if "Default" not in project_ids:
                        project_ids["Default"] = set()
                    self._process_items_list("Default", data, project_ids["Default"], project_configs)
                elif isinstance(data, dict):
                    for project_name, items in data.items():
                        if isinstance(items, list):
                            if project_name not in project_ids:
                                project_ids[project_name] = set()
                            self._process_items_list(project_name, items, project_ids[project_name], project_configs)

            except Exception as e:
                self.log_message(f"处理文件 {file_info['name']} 时出错: {str(e)}", "error")

        # 将 set 转换为 list
        return {p: list(ids) for p, ids in project_ids.items() if ids}

    def _process_items_list(self, project_name, items, ids_set, project_configs):
        """使用项目专属规则处理列表并提取ID"""
        # 获取该项目的专属配置，如果没有则使用默认值
        config = project_configs.get(project_name, {
            "filter_keywords": [],
            "min_comment_count": 0,
            "days_limit": 60
        })

        filter_keywords = config["filter_keywords"]
        min_comment_count = config["min_comment_count"]
        days_limit = config["days_limit"]
        time_limit_timestamp = time.time() - days_limit * 24 * 60 * 60

        for item in items:
            # 提取aweme_id
            content_id = None
            if "aweme_id" in item:
                content_id = item["aweme_id"]
            elif "note_id" in item:
                content_id = item["note_id"]
            elif "id" in item:
                content_id = item["id"]

            if not content_id:
                continue

            # 1. 专属屏蔽词过滤
            title = item.get("title", "") or item.get("desc", "")
            if title and filter_keywords:
                should_skip = False
                for keyword in filter_keywords:
                    if keyword in title:
                        should_skip = True
                        break
                if should_skip:
                    continue

            # 2. 专属评论数过滤
            comment_count = item.get("comment_count", 0)
            try:
                if isinstance(comment_count, str):
                    if comment_count.isdigit():
                        comment_count = int(comment_count)
                    else:
                        comment_count = 0
                elif not isinstance(comment_count, (int, float)):
                     comment_count = 0
            except:
                comment_count = 0

            if comment_count < min_comment_count:
                continue

            # 3. 专属作品天数过滤
            create_time = None
            if "create_time" in item:
                create_time = item["create_time"]
            elif "publish_time" in item:
                create_time = item["publish_time"]
            elif "timestamp" in item:
                create_time = item["timestamp"]

            if not create_time or create_time < time_limit_timestamp:
                continue

            ids_set.add(content_id)

    def build_comment_crawler_command(self, aweme_ids, project_name=""):
        """构建评论爬虫命令"""
        command = ["uv", "run", "main.py"]

        # 默认使用抖音平台
        command.extend(["--platform", "dy"])

        # 登录方式默认二维码
        command.extend(["--lt", "qrcode"])

        # 爬取类型为detail
        command.extend(["--type", "detail"])

        # 帖子ID列表
        specified_id = ",".join(aweme_ids)
        command.extend(["--specified_id", specified_id])

        # 项目名称
        if project_name:
            command.extend(["--project", project_name])

        # 爬取评论和子评论
        command.extend(["--get_comment", "yes"])
        command.extend(["--get_sub_comment", "yes"])

        # 无头模式默认关闭
        command.extend(["--headless", "no"])

        # 自动关闭浏览器
        command.extend(["--auto_close", "yes"])

        # 加载保存的二维码登录状态
        try:
            state_file = Path("qrcode_login_state.json")
            if state_file.exists():
                with open(state_file, "r", encoding="utf-8") as f:
                    state_data = json.load(f)
                timestamp = state_data.get("timestamp", 0)
                # 检查是否过期（7天）
                if time.time() - timestamp <= 7 * 24 * 3600:
                    saved_cookies = state_data.get("cookies", [])
                    if saved_cookies:
                        # 将cookies转换为字符串格式
                        saved_cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in saved_cookies])
                        if saved_cookie_str:
                            command.extend(["--cookies", saved_cookie_str])
                            self.log_message("[信息] 评论爬取已加载保存的二维码登录状态", "info")
                else:
                    self.log_message("[警告] 二维码登录状态已过期，评论爬取可能需要重新登录", "warning")
            else:
                self.log_message("[警告] 未找到二维码登录状态，评论爬取可能需要扫码登录", "warning")
        except Exception as e:
            self.log_message(f"[警告] 加载二维码登录状态失败: {e}", "warning")

        return command

    def run_batch_comment_crawler(self, selected_projects, project_ids):
        """批量运行评论爬虫，顺序执行"""
        # 创建临时窗口用于显示评论爬虫运行日志
        self.comment_crawler_window = ttk.Toplevel(self)
        self.comment_crawler_window.title("批量评论爬虫运行")
        self.comment_crawler_window.geometry("800x600")

        # 创建主框架
        main_frame = ttk.Frame(self.comment_crawler_window, padding="10")
        main_frame.pack(fill=BOTH, expand=True)

        # 日志标题
        ttk.Label(main_frame, text=f"批量评论爬虫运行日志 (共 {len(selected_projects)} 个项目)", font=("Arial", 12, "bold")).pack(anchor="w", pady=(0, 5))

        # 日志输出区域
        log_output = scrolledtext.ScrolledText(main_frame, wrap=WORD, height=30)
        log_output.pack(fill=BOTH, expand=YES)
        log_output.config(state=NORMAL)
        log_output.insert(END, "准备启动批量爬虫任务...\n")
        log_output.insert(END, f"任务队列: {', '.join(selected_projects)}\n")
        log_output.insert(END, "-" * 50 + "\n")
        log_output.config(state=DISABLED)

        # 更新日志的函数
        def update_log(line):
            if not self.comment_crawler_window.winfo_exists():
                return
            log_output.config(state=NORMAL)
            log_output.insert(END, line + "\n")

            # 限制日志行数
            try:
                current_lines = int(log_output.index('end-1c').split('.')[0])
                if current_lines > 500:
                    log_output.delete("1.0", f"{current_lines - 500 + 1}.0")
            except Exception:
                pass

            log_output.see(END)
            log_output.config(state=DISABLED)

        # 执行命令的函数
        def execute_batch():
            total = len(selected_projects)
            success_count = 0

            for i, project_name in enumerate(selected_projects):
                current_idx = i + 1
                self.comment_crawler_window.title(f"批量评论爬虫运行 - 进度: {current_idx}/{total}")
                self.comment_crawler_window.after(0, update_log, f"\n>>> [{current_idx}/{total}] 正在处理项目: {project_name}")

                aweme_ids = project_ids.get(project_name, [])
                if not aweme_ids:
                    self.comment_crawler_window.after(0, update_log, f"[警告] 项目 {project_name} 没有有效ID，跳过")
                    continue

                command = self.build_comment_crawler_command(aweme_ids, project_name)
                self.comment_crawler_window.after(0, update_log, f"执行命令: {' '.join(command)}")

                try:
                    process = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        universal_newlines=True
                    )

                    for line in process.stdout:
                        self.comment_crawler_window.after(0, update_log, line.strip())

                    exit_code = process.wait()
                    if exit_code == 0:
                        self.comment_crawler_window.after(0, update_log, f"[成功] 项目 {project_name} 爬取完成")
                        success_count += 1
                    else:
                        self.comment_crawler_window.after(0, update_log, f"[错误] 项目 {project_name} 爬取失败，退出代码: {exit_code}")

                except Exception as e:
                    self.comment_crawler_window.after(0, update_log, f"[错误] 项目 {project_name} 启动失败: {e}")

                # 项目间稍微停顿，模拟人工或者缓冲
                if current_idx < total:
                    self.comment_crawler_window.after(0, update_log, "等待 3 秒后开始下一个项目...")
                    time.sleep(3)

            self.comment_crawler_window.after(0, update_log, "\n" + "=" * 50)
            self.comment_crawler_window.after(0, update_log, f"批量任务结束。成功: {success_count}/{total}")

            if success_count == total:
                 self.comment_crawler_window.after(0, lambda: messagebox.showinfo("完成", "所有项目爬取完成"))
            else:
                 self.comment_crawler_window.after(0, lambda: messagebox.showwarning("完成", f"任务结束，{total - success_count} 个项目失败"))

        # 启动线程执行命令
        threading.Thread(target=execute_batch, daemon=True).start()

    def run_comment_crawler(self, command):
        """运行评论爬虫命令"""
        # 创建临时窗口用于显示评论爬虫运行日志
        self.comment_crawler_window = ttk.Toplevel(self)
        self.comment_crawler_window.title("评论爬虫运行")
        self.comment_crawler_window.geometry("800x600")

        # 创建主框架
        main_frame = ttk.Frame(self.comment_crawler_window, padding="10")
        main_frame.pack(fill=BOTH, expand=True)

        # 日志标题
        ttk.Label(main_frame, text="评论爬虫运行日志", font=("Arial", 12, "bold")).pack(anchor="w", pady=(0, 5))

        # 日志输出区域
        log_output = scrolledtext.ScrolledText(main_frame, wrap=WORD, height=30)
        log_output.pack(fill=BOTH, expand=YES)
        log_output.config(state=NORMAL)
        log_output.insert(END, f"正在执行命令: {' '.join(command)}\n")
        log_output.insert(END, "评论爬虫启动中...\n")
        log_output.config(state=DISABLED)

        # 更新日志的函数
        def update_log(line):
            log_output.config(state=NORMAL)
            log_output.insert(END, line + "\n")

            # 限制日志行数
            try:
                current_lines = int(log_output.index('end-1c').split('.')[0])
                if current_lines > 500:
                    log_output.delete("1.0", f"{current_lines - 500 + 1}.0")
            except Exception:
                pass

            log_output.see(END)
            log_output.config(state=DISABLED)

        # 执行命令的函数
        def execute_command():
            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )

                for line in process.stdout:
                    self.comment_crawler_window.after(0, update_log, line.strip())

                exit_code = process.wait()
                if exit_code == 0:
                    self.comment_crawler_window.after(0, update_log, f"[成功] 评论爬虫已结束，退出代码: {exit_code}")
                    self.comment_crawler_window.after(0, lambda: messagebox.showinfo("成功", "评论爬取完成"))
                else:
                    self.comment_crawler_window.after(0, update_log, f"[错误] 评论爬虫已结束，退出代码: {exit_code}")
                    self.comment_crawler_window.after(0, lambda: messagebox.showerror("错误", f"评论爬取失败，退出代码: {exit_code}"))

            except Exception as e:
                self.comment_crawler_window.after(0, update_log, f"[错误] 启动爬虫失败: {e}")
                self.comment_crawler_window.after(0, lambda: messagebox.showerror("错误", f"启动爬虫失败: {e}"))

        # 启动线程执行命令
        threading.Thread(target=execute_command, daemon=True).start()

    def log_message(self, message, tag=""):
        """记录消息到主窗口日志"""
        # 这里可以根据需要扩展，比如显示在主窗口日志或者弹出消息框
        print(f"[{tag.upper()}] {message}")

    def upload_excel_to_interface(self, file_path, project_override=""):
        """上传Excel文件到接口"""
        try:
            # 读取data.json获取host
            data_json_path = "data.json"
            if not os.path.exists(data_json_path):
                self.log_message("找不到data.json配置文件", "error")
                return None

            with open(data_json_path, "r", encoding="utf-8") as f:
                data_config = json.load(f)

            # 兼容列表格式
            if isinstance(data_config, list) and len(data_config) > 0:
                data_config = data_config[0]

            host = data_config.get("host")
            if not host:
                self.log_message("data.json中未配置host", "error")
                return None

            # 获取上传配置
            upload_config = data_config.get("upload_config", {})

            # 确定项目名称
            project = project_override if project_override else upload_config.get("project")

            # 从items中查找对应项目的username
            username = upload_config.get("username") # 默认回退值
            items = data_config.get("items", [])
            if project:
                for item in items:
                    if item.get("project") == project:
                        item_username = item.get("username")
                        if item_username:
                            username = item_username
                        break

            task = upload_config.get("task", 1)

            if not username or not project:
                self.log_message(f"data.json中未找到项目[{project}]的username配置", "error")
                # messagebox.showerror("配置错误", "data.json中缺少upload_config配置(username/project)")
                return None

            # 构建URL
            url = f"{host.rstrip('/')}/upload/douyin"

            # 准备请求数据
            payload = {
                "username": username,
                "project": project,
                "task": task
            }

            # 上传文件
            with open(file_path, "rb") as f:
                files = {"file": f}
                self.log_message(f"正在上传项目 [{project}] 文件到: {url}", "info")
                response = requests.post(url, data=payload, files=files, timeout=300)

            # 处理响应
            if response.status_code == 200:
                try:
                    res_json = response.json()
                    if res_json.get("success"):
                        return res_json.get("data", {})
                    else:
                        error_msg = res_json.get("message", "未知错误")
                        self.log_message(f"项目 [{project}] 上传失败: {error_msg}", "error")
                        return {"error": error_msg}
                except json.JSONDecodeError:
                    self.log_message(f"项目 [{project}] 接口返回非JSON格式数据", "error")
                    return {"error": "非JSON响应"}
            else:
                self.log_message(f"项目 [{project}] 上传失败 HTTP Code: {response.status_code}", "error")
                return {"error": f"HTTP {response.status_code}"}

        except Exception as e:
            self.log_message(f"自动上传过程出错: {str(e)}", "error")
            return {"error": str(e)}


    def export_to_excel(self, file_info):
        """将Comments JSON文件导出为Excel，支持按项目分文件导出和上传"""
        try:
            # 动态导入dict_to_小鸡模块
            import dict_to_小鸡 as excel_converter

            file_path = Path(file_info["path"])
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            results = []
            temp_files = []

            if isinstance(data, dict):
                # 多项目格式，先让用户选择项目
                # 准备项目数据
                project_ids = {}
                for project_name, items in data.items():
                    if items:
                        project_ids[project_name] = items

                # 显示项目选择对话框
                selected_projects = self.show_project_selection_dialog(
                    project_ids,
                    title="选择要导出的项目",
                    prompt="请选择要导出的项目：",
                    item_suffix="个评论"
                )
                if not selected_projects:
                    # 用户取消选择
                    return

                # 只导出用户选择的项目
                for project_name in selected_projects:
                    items = data.get(project_name)
                    if not items: continue

                    # 创建临时单项目JSON文件
                    temp_json = f"temp_{project_name}.json"
                    with open(temp_json, "w", encoding="utf-8") as tf:
                        json.dump({project_name: items}, tf, ensure_ascii=False, indent=4)
                    temp_files.append(temp_json)

                    # 转换
                    excel_path = excel_converter.convert_json_to_excel(temp_json)

                    # 上传
                    status = None
                    if self.auto_upload_var.get():
                        status = self.upload_excel_to_interface(excel_path, project_override=project_name)
                    else:
                        status = {"info": "自动上传已关闭"}

                    results.append({"project": project_name, "status": status})
            else:
                # 旧版列表格式
                excel_path = excel_converter.convert_json_to_excel(file_info["path"])

                status = None
                if self.auto_upload_var.get():
                    status = self.upload_excel_to_interface(excel_path)
                else:
                    status = {"info": "自动上传已关闭"}

                results.append({"project": "默认项目", "status": status})

            # 清理临时文件
            for f in temp_files:
                if os.path.exists(f): os.remove(f)

            # 汇总返回信息
            summary_msg = "导出与上传汇总报告：\n\n"
            for res in results:
                p_name = res["project"]
                status = res["status"]
                if status and "error" not in status:
                    if status.get("info") == "自动上传已关闭":
                        summary_msg += f"项目: {p_name} - [导出成功] (未自动上传)\n\n"
                    else:
                        summary_msg += (f"项目: {p_name} - [成功]\n"
                                       f"  批次: {status.get('batch_number')}\n"
                                       f"  有效行数: {status.get('valid_rows')}\n\n")
                else:
                    err = status.get("error", "未知错误") if status else "配置缺失"
                    summary_msg += f"项目: {p_name} - [失败] ({err})\n\n"

            messagebox.showinfo("处理完成", summary_msg)
            self.log_message("所有项目导出与上传处理完成", "success")

            # 打开导出文件夹
            if results and results[0]["status"] and "error" not in results[0]["status"]:
                 # 假设转换器把文件放到了某个固定位置，尝试打开第一个成功文件的目录
                 # convert_json_to_excel 应该返回了绝对路径
                 pass

        except Exception as e:
            messagebox.showerror("导出失败", f"导出Excel失败: {str(e)}")
            self.log_message(f"导出Excel失败: {str(e)}", "error")

    # Duplicate method removed



# 在MediaCrawlerGUI类中添加打开数据浏览器的方法

def open_data_browser(self):
    """打开数据浏览器"""
    # 检查窗口是否存在且未被销毁
    if self.data_browser_window is not None and self.data_browser_window.winfo_exists():
        self.data_browser_window.lift()  # 将窗口置顶
        self.data_browser_window.focus_force()  # 强制获取焦点
        return

    self.data_browser_window = DataBrowser(self.root)


# 动态添加方法到MediaCrawlerGUI类
MediaCrawlerGUI.open_data_browser = open_data_browser


if __name__ == "__main__":
    # 使用ttkbootstrap的Window类创建根窗口
    root = ttk.Window(themename="yeti")
    app = MediaCrawlerGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
