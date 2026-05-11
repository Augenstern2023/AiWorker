import sys
import os
import time
import json
import threading
import queue
from pathlib import Path
from typing import List, Dict, Optional, Set
from datetime import datetime
import asyncio

try:
    import tkinter as tk
    from tkinter import ttk, scrolledtext, messagebox, filedialog
except ImportError:
    print("请先安装tkinter")
    sys.exit(1)

# 全局状态
class AppState:
    def __init__(self):
        self.is_running = False
        self.is_paused = False
        self.stop_requested = False
        self.log_queue = queue.Queue()
        self.success_count = 0
        self.fail_count = 0
        self.total_count = 0
        self.current_index = 0
        self.results = []  # 存储发送结果
        self.data_list = []  # 存储Excel导入的数据
        self.sent_secuids = set()  # 已发送的SECUID集合
        self.pending_list = []  # 待发送列表（过滤后的）
        
    def log(self, message: str, level: str = "info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_queue.put(f"[{timestamp}] [{level.upper()}] {message}")
        
        # 限制队列大小
        while self.log_queue.qsize() > 500:
            try:
                self.log_queue.get_nowait()
            except queue.Empty:
                break

state = AppState()

# 配置文件路径
QRCODE_LOGIN_STATE_FILE = "qrcode_login_state.json"
DATA_JSON_FILE = "data.json"
SEND_RECORD_FILE = "发送记录.json"
PROGRESS_FILE = "发送进度.json"

def load_cookies() -> List[Dict]:
    """从文件加载cookies"""
    try:
        if os.path.exists(QRCODE_LOGIN_STATE_FILE):
            with open(QRCODE_LOGIN_STATE_FILE, "r", encoding="utf-8") as f:
                state_data = json.load(f)
            return state_data.get("cookies", [])
    except Exception as e:
        print(f"加载cookies失败: {e}")
    return []

def check_login_state() -> bool:
    """检查登录状态是否有效"""
    try:
        if os.path.exists(QRCODE_LOGIN_STATE_FILE):
            with open(QRCODE_LOGIN_STATE_FILE, "r", encoding="utf-8") as f:
                state_data = json.load(f)
            timestamp = state_data.get("timestamp", 0)
            # 检查是否过期（7天）
            if time.time() - timestamp <= 7 * 24 * 3600:
                cookies = state_data.get("cookies", [])
                if cookies:
                    return True
    except Exception as e:
        print(f"检查登录状态失败: {e}")
    return False

def get_host_from_config() -> str:
    """从data.json读取host"""
    try:
        if os.path.exists(DATA_JSON_FILE):
            with open(DATA_JSON_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and len(data) > 0:
                return data[0].get("host", "").rstrip("/")
            elif isinstance(data, dict):
                return data.get("host", "").rstrip("/")
    except Exception as e:
        print(f"读取host失败: {e}")
    return ""

def get_sent_secuids_from_records() -> Set[str]:
    """从发送记录中获取所有已发送的SECUID"""
    sent_secuids = set()
    try:
        if os.path.exists(SEND_RECORD_FILE):
            with open(SEND_RECORD_FILE, "r", encoding="utf-8") as f:
                records = json.load(f)
            
            # 遍历所有日期的记录
            for date, date_records in records.items():
                for record in date_records:
                    sec_uid = record.get("sec_uid", "").strip()
                    if sec_uid and record.get("success", False):
                        sent_secuids.add(sec_uid)
    except Exception as e:
        print(f"读取发送记录失败: {e}")
    return sent_secuids

def generate_private_message(project: str, comment: str) -> str:
    """调用API生成私信内容"""
    try:
        import requests
        
        host = get_host_from_config()
        if not host:
            state.log("未找到host配置，使用默认内容", "warning")
            return "你好"
        
        url = f"{host}/api/private_message"
        data = {
            "project_name": project,
            "comment": comment
        }
        
        state.log(f"正在生成私信内容: project={project}", "info")
        resp = requests.post(url, json=data, timeout=60)
        result = resp.json()
        
        # API返回的内容在 result 字段
        if result.get("success"):
            message = result.get("result", "")
            if message:
                return message
        
        # 兼容其他可能的返回格式
        if "result" in result:
            return result["result"]
        if "message" in result:
            return result["message"]
        
        state.log(f"API返回异常: {result}", "warning")
        return "你好"
        
    except Exception as e:
        state.log(f"生成私信内容失败: {e}", "error")
        return "你好"

def save_send_record(record: Dict):
    """保存发送记录到JSON文件（按日期分组）"""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        
        # 读取现有记录
        records = {}
        if os.path.exists(SEND_RECORD_FILE):
            try:
                with open(SEND_RECORD_FILE, "r", encoding="utf-8") as f:
                    records = json.load(f)
            except:
                records = {}
        
        # 确保今天的记录列表存在
        if today not in records:
            records[today] = []
        
        # 添加新记录
        records[today].append(record)
        
        # 保存回文件
        with open(SEND_RECORD_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
            
    except Exception as e:
        state.log(f"保存发送记录失败: {e}", "error")

def save_progress():
    """保存当前发送进度"""
    try:
        progress = {
            "current_index": state.current_index,
            "total": state.total_count,
            "success": state.success_count,
            "fail": state.fail_count,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "is_paused": state.is_paused
        }
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(progress, f, ensure_ascii=False, indent=2)
    except Exception as e:
        state.log(f"保存进度失败: {e}", "error")

def load_progress() -> Optional[Dict]:
    """加载上次发送进度"""
    try:
        if os.path.exists(PROGRESS_FILE):
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"加载进度失败: {e}")
    return None

def clear_progress():
    """清除进度文件"""
    try:
        if os.path.exists(PROGRESS_FILE):
            os.remove(PROGRESS_FILE)
    except:
        pass

def load_send_records() -> Dict:
    """加载所有发送记录"""
    try:
        if os.path.exists(SEND_RECORD_FILE):
            with open(SEND_RECORD_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"加载发送记录失败: {e}")
    return {}

def parse_pasted_excel(text: str) -> List[Dict]:
    """解析粘贴的Excel内容（按\t分列）"""
    lines = text.strip().split('\n')
    if not lines:
        return []
    
    # 第一行是表头
    headers = lines[0].split('\t')
    headers = [h.strip() for h in headers]
    
    # 查找关键列的索引
    secuid_idx = -1
    project_idx = -1
    nickname_idx = -1
    content_idx = -1
    user_id_idx = -1
    location_idx = -1
    
    for i, h in enumerate(headers):
        h_lower = h.lower()
        if h_lower in ['sec_uid', 'secuid', 'sec_uid']:
            secuid_idx = i
        elif h_lower in ['project', '项目名称', '项目']:
            project_idx = i
        elif h_lower in ['nickname', '昵称']:
            nickname_idx = i
        elif h_lower in ['content', '评论内容', '评论']:
            content_idx = i
        elif h_lower in ['user_unique_id', '抖音号', '用户id']:
            user_id_idx = i
        elif h_lower in ['ip_location', '地区', '位置']:
            location_idx = i
    
    # 解析数据行
    data_list = []
    for line in lines[1:]:
        if not line.strip():
            continue
        
        values = line.split('\t')
        row = {}
        
        # 必须包含SECUID
        if secuid_idx >= 0 and secuid_idx < len(values):
            sec_uid = values[secuid_idx].strip()
            if sec_uid:
                row['sec_uid'] = sec_uid
                
                # 添加其他字段
                if project_idx >= 0 and project_idx < len(values):
                    row['project'] = values[project_idx].strip()
                if nickname_idx >= 0 and nickname_idx < len(values):
                    row['nickname'] = values[nickname_idx].strip()
                if content_idx >= 0 and content_idx < len(values):
                    row['content'] = values[content_idx].strip()
                if user_id_idx >= 0 and user_id_idx < len(values):
                    row['user_unique_id'] = values[user_id_idx].strip()
                if location_idx >= 0 and location_idx < len(values):
                    row['ip_location'] = values[location_idx].strip()
                
                data_list.append(row)
    
    return data_list

# 私信发送核心逻辑
async def send_private_message(sec_uid: str, message_content: str, max_retries: int = 3) -> Dict:
    """发送私信给指定用户"""
    from playwright.async_api import async_playwright
    
    browser = None
    browser_context = None
    
    try:
        async with async_playwright() as p:
            # 启动浏览器
            browser = await p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"]
            )
            
            # 创建浏览器上下文并加载cookies
            cookies = load_cookies()
            if cookies:
                browser_context = await browser.new_context(
                    viewport={"width": 1920, "height": 1080}
                )
                await browser_context.add_cookies(cookies)
                state.log(f"已加载 {len(cookies)} 个cookies", "info")
            else:
                browser_context = await browser.new_context(
                    viewport={"width": 1920, "height": 1080}
                )
            
            page = await browser_context.new_page()
            
            # 1. 打开用户主页
            user_url = f"https://www.douyin.com/user/{sec_uid}"
            state.log(f"正在打开用户页面: {sec_uid}", "info")
            
            try:
                await page.goto(user_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)
            except Exception as e:
                return {"success": False, "message": f"打开页面失败: {str(e)}"}
            
            # 2. 点击私信按钮
            try:
                msg_btn_xpath = '//*[@id="user_detail_element"]/div/div[2]/div[3]/div[3]/div/button[2]'
                msg_btn = page.locator(f"xpath={msg_btn_xpath}")
                
                await msg_btn.wait_for(state="visible", timeout=10000)
                
                btn_text = await msg_btn.inner_text()
                if "私信" not in btn_text:
                    return {"success": False, "message": f"按钮文本不匹配: {btn_text}"}
                
                await msg_btn.click()
                state.log("已点击私信按钮", "info")
                await page.wait_for_timeout(2000)
            except Exception as e:
                return {"success": False, "message": f"点击私信按钮失败: {str(e)}"}
            
            # 3. 点击输入框
            try:
                input_xpath = '//div[@contenteditable="true"]'
                input_box = page.locator(f"xpath={input_xpath}").first
                
                await input_box.wait_for(state="visible", timeout=10000)
                await input_box.click()
                state.log("已点击输入框", "info")
                await page.wait_for_timeout(1000)
            except Exception as e:
                return {"success": False, "message": f"点击输入框失败: {str(e)}"}
            
            # 4. 输入私信内容
            try:
                await input_box.fill("")
                await page.wait_for_timeout(500)
                
                await input_box.fill(message_content)
                state.log(f"已输入内容: {message_content[:50]}...", "info")
                await page.wait_for_timeout(1000)
            except Exception as e:
                return {"success": False, "message": f"输入内容失败: {str(e)}"}
            
            # 5. 发送消息
            try:
                await page.keyboard.press("Enter")
                state.log("已按回车发送", "info")
                await page.wait_for_timeout(2000)
                
                current_content = await input_box.inner_text()
                if not current_content.strip():
                    return {"success": True, "message": "发送成功"}
                
                send_btn_xpath = '//span[contains(text(), "发送") or contains(@class, "send")]'
                send_btn = page.locator(f"xpath={send_btn_xpath}").first
                
                if await send_btn.is_visible():
                    await send_btn.click()
                    state.log("已点击发送按钮", "info")
                    await page.wait_for_timeout(2000)
                    
                    current_content = await input_box.inner_text()
                    if not current_content.strip():
                        return {"success": True, "message": "发送成功"}
                
                return {"success": False, "message": "发送后输入框未清空，可能发送失败"}
                
            except Exception as e:
                return {"success": False, "message": f"发送消息失败: {str(e)}"}
                
    except Exception as e:
        return {"success": False, "message": f"发生错误: {str(e)}"}
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

async def send_message_with_retry(sec_uid: str, message_content: str, max_retries: int = 3) -> Dict:
    """带重试机制的发送"""
    for attempt in range(max_retries):
        if state.stop_requested:
            return {"success": False, "message": "用户停止"}
        
        # 检查是否暂停
        while state.is_paused and not state.stop_requested:
            await asyncio.sleep(0.5)
        
        if state.stop_requested:
            return {"success": False, "message": "用户停止"}
        
        state.log(f"第 {attempt + 1}/{max_retries} 次尝试...", "info")
        result = await send_private_message(sec_uid, message_content, max_retries)
        
        if result["success"]:
            return result
        
        if attempt < max_retries - 1:
            wait_time = 3
            state.log(f"发送失败，{wait_time}秒后重试...", "warning")
            await asyncio.sleep(wait_time)
    
    return result

# 发送记录查看窗口
class SendRecordWindow:
    def __init__(self, parent):
        self.window = tk.Toplevel(parent)
        self.window.title("发送记录查看")
        self.window.geometry("1200x700")
        self.window.minsize(1000, 600)
        
        self.create_widgets()
        self.load_data()
        
    def create_widgets(self):
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 顶部筛选区域
        filter_frame = ttk.LabelFrame(main_frame, text="筛选", padding="5")
        filter_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(filter_frame, text="日期:").pack(side=tk.LEFT, padx=(0, 5))
        self.date_var = tk.StringVar()
        self.date_combo = ttk.Combobox(filter_frame, textvariable=self.date_var, state="readonly", width=15)
        self.date_combo.pack(side=tk.LEFT, padx=(0, 10))
        self.date_combo.bind("<<ComboboxSelected>>", self.on_date_change)
        
        ttk.Label(filter_frame, text="项目:").pack(side=tk.LEFT, padx=(0, 5))
        self.project_var = tk.StringVar()
        self.project_entry = ttk.Entry(filter_frame, textvariable=self.project_var, width=15)
        self.project_entry.pack(side=tk.LEFT, padx=(0, 10))
        
        ttk.Label(filter_frame, text="状态:").pack(side=tk.LEFT, padx=(0, 5))
        self.status_var = tk.StringVar(value="全部")
        self.status_combo = ttk.Combobox(filter_frame, textvariable=self.status_var, 
                                          values=["全部", "成功", "失败"], state="readonly", width=10)
        self.status_combo.pack(side=tk.LEFT, padx=(0, 10))
        self.status_combo.bind("<<ComboboxSelected>>", self.apply_filter)
        
        ttk.Button(filter_frame, text="筛选", command=self.apply_filter).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(filter_frame, text="重置", command=self.reset_filter).pack(side=tk.LEFT)
        
        self.stats_label = ttk.Label(filter_frame, text="", font=("Microsoft YaHei", 10))
        self.stats_label.pack(side=tk.RIGHT)
        
        # 表格区域
        table_frame = ttk.Frame(main_frame)
        table_frame.pack(fill=tk.BOTH, expand=True)
        
        columns = ("时间", "SECUID", "项目", "昵称", "评论内容", "抖音号", "地区", "发送内容", "状态", "消息")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        
        col_widths = {"时间": 120, "SECUID": 150, "项目": 100, "昵称": 100, 
                      "评论内容": 150, "抖音号": 100, "地区": 80, "发送内容": 200, "状态": 60, "消息": 150}
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=col_widths.get(col, 100), anchor=tk.W)
        
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        
        # 底部按钮
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Button(btn_frame, text="导出当前筛选", command=self.export_filtered).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="刷新", command=self.load_data).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="关闭", command=self.window.destroy).pack(side=tk.RIGHT)
        
    def load_data(self):
        """加载发送记录数据"""
        self.records = load_send_records()
        
        dates = sorted(self.records.keys(), reverse=True)
        self.date_combo["values"] = ["全部"] + dates
        if dates:
            self.date_combo.set(dates[0])
        else:
            self.date_combo.set("全部")
        
        self.apply_filter()
        
    def on_date_change(self, event=None):
        self.apply_filter()
        
    def apply_filter(self, event=None):
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        selected_date = self.date_var.get()
        project_filter = self.project_var.get().strip()
        status_filter = self.status_var.get()
        
        filtered_records = []
        
        for date, records in self.records.items():
            if selected_date != "全部" and date != selected_date:
                continue
            
            for record in records:
                if project_filter and project_filter.lower() not in record.get("project", "").lower():
                    continue
                
                if status_filter != "全部":
                    is_success = record.get("success", False)
                    if status_filter == "成功" and not is_success:
                        continue
                    if status_filter == "失败" and is_success:
                        continue
                
                filtered_records.append(record)
        
        for record in filtered_records:
            status = "成功" if record.get("success", False) else "失败"
            values = (
                record.get("time", ""),
                record.get("sec_uid", ""),
                record.get("project", ""),
                record.get("nickname", ""),
                record.get("comment", "")[:30] + "..." if len(record.get("comment", "")) > 30 else record.get("comment", ""),
                record.get("user_unique_id", ""),
                record.get("ip_location", ""),
                record.get("send_content", "")[:40] + "..." if len(record.get("send_content", "")) > 40 else record.get("send_content", ""),
                status,
                record.get("message", "")
            )
            self.tree.insert("", tk.END, values=values)
        
        total = len(filtered_records)
        success = sum(1 for r in filtered_records if r.get("success", False))
        fail = total - success
        self.stats_label.config(text=f"总计: {total} | 成功: {success} | 失败: {fail}")
        
    def reset_filter(self):
        self.project_var.set("")
        self.status_var.set("全部")
        if self.date_combo["values"] and len(self.date_combo["values"]) > 1:
            self.date_combo.set(self.date_combo["values"][1])
        else:
            self.date_combo.set("全部")
        self.apply_filter()
        
    def export_filtered(self):
        file_path = filedialog.asksaveasfilename(
            title="导出记录",
            defaultextension=".xlsx",
            filetypes=[("Excel文件", "*.xlsx"), ("CSV文件", "*.csv"), ("JSON文件", "*.json")]
        )
        
        if not file_path:
            return
        
        try:
            selected_date = self.date_var.get()
            project_filter = self.project_var.get().strip()
            status_filter = self.status_var.get()
            
            filtered_records = []
            for date, records in self.records.items():
                if selected_date != "全部" and date != selected_date:
                    continue
                
                for record in records:
                    if project_filter and project_filter.lower() not in record.get("project", "").lower():
                        continue
                    
                    if status_filter != "全部":
                        is_success = record.get("success", False)
                        if status_filter == "成功" and not is_success:
                            continue
                        if status_filter == "失败" and is_success:
                            continue
                    
                    filtered_records.append(record)
            
            if file_path.endswith('.json'):
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(filtered_records, f, ensure_ascii=False, indent=2)
                    
            elif file_path.endswith('.csv'):
                import csv
                with open(file_path, 'w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.writer(f)
                    writer.writerow(['时间', 'SECUID', '项目', '昵称', '评论内容', '抖音号', '地区', '发送内容', '状态', '消息'])
                    for r in filtered_records:
                        writer.writerow([
                            r.get('time', ''), r.get('sec_uid', ''), r.get('project', ''),
                            r.get('nickname', ''), r.get('comment', ''), r.get('user_unique_id', ''),
                            r.get('ip_location', ''), r.get('send_content', ''),
                            '成功' if r.get('success', False) else '失败', r.get('message', '')
                        ])
                        
            elif file_path.endswith('.xlsx'):
                try:
                    import pandas as pd
                    df = pd.DataFrame(filtered_records)
                    if 'success' in df.columns:
                        df['状态'] = df['success'].apply(lambda x: '成功' if x else '失败')
                        df = df.drop('success', axis=1)
                    df.to_excel(file_path, index=False, engine='openpyxl')
                except ImportError:
                    messagebox.showerror("错误", "导出Excel需要安装pandas和openpyxl\n请运行: pip install pandas openpyxl")
                    return
            
            messagebox.showinfo("导出成功", f"成功导出 {len(filtered_records)} 条记录到:\n{file_path}")
            
        except Exception as e:
            messagebox.showerror("导出失败", f"导出时出错:\n{str(e)}")

# 粘贴Excel内容窗口
class PasteExcelWindow:
    def __init__(self, parent, callback):
        self.window = tk.Toplevel(parent)
        self.window.title("粘贴Excel内容")
        self.window.geometry("800x600")
        self.callback = callback
        
        self.create_widgets()
        
    def create_widgets(self):
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 说明文字
        ttk.Label(main_frame, text="请从Excel复制内容（包含表头），粘贴到下方文本框：", 
                  font=("Microsoft YaHei", 10)).pack(anchor=tk.W, pady=(0, 5))
        
        ttk.Label(main_frame, text="格式要求：第一行为表头（包含SECUID列），数据行用Tab分隔", 
                  foreground="gray").pack(anchor=tk.W, pady=(0, 10))
        
        # 文本框
        self.text = scrolledtext.ScrolledText(main_frame, wrap=tk.NONE, font=("Consolas", 10))
        self.text.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # 按钮
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X)
        
        ttk.Button(btn_frame, text="解析并导入", command=self.parse_and_import).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="清空", command=self.clear_text).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="取消", command=self.window.destroy).pack(side=tk.RIGHT)
        
    def clear_text(self):
        self.text.delete("1.0", tk.END)
        
    def parse_and_import(self):
        content = self.text.get("1.0", tk.END).strip()
        if not content:
            messagebox.showwarning("警告", "内容为空")
            return
        
        try:
            data_list = parse_pasted_excel(content)
            if not data_list:
                messagebox.showwarning("警告", "未解析到有效数据，请确保包含SECUID列")
                return
            
            self.callback(data_list)
            self.window.destroy()
            messagebox.showinfo("导入成功", f"成功导入 {len(data_list)} 条数据")
            
        except Exception as e:
            messagebox.showerror("解析失败", f"解析时出错:\n{str(e)}")

# GUI类
class PrivateMessageGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("抖音私信自动化工具")
        self.root.geometry("1000x800")
        self.root.minsize(900, 700)
        
        # 加载已发送的SECUID集合
        state.sent_secuids = get_sent_secuids_from_records()
        state.log(f"已加载发送记录，发现 {len(state.sent_secuids)} 个已发送用户", "info")
        
        # 检查登录状态
        self.check_login()
        
        # 创建UI
        self.create_widgets()
        
        # 启动日志更新线程
        self.update_logs()
        
    def check_login(self):
        if not check_login_state():
            messagebox.showwarning(
                "登录状态警告",
                "未找到有效的登录状态！\n\n请先使用WebGUI进行二维码登录，\n或确保 qrcode_login_state.json 文件存在且未过期。"
            )
    
    def create_widgets(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(4, weight=1)
        
        # === 第一行：数据导入 ===
        import_frame = ttk.LabelFrame(main_frame, text="数据导入", padding="5")
        import_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        import_frame.columnconfigure(0, weight=1)
        
        import_btn_frame = ttk.Frame(import_frame)
        import_btn_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(import_btn_frame, text="导入Excel文件", command=self.import_excel).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(import_btn_frame, text="粘贴Excel内容", command=self.paste_excel).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(import_btn_frame, text="查看发送记录", command=self.view_send_records).pack(side=tk.LEFT, padx=(0, 5))
        
        self.import_status_label = ttk.Label(import_btn_frame, text="未导入数据", font=("Microsoft YaHei", 9))
        self.import_status_label.pack(side=tk.LEFT, padx=(10, 0))
        
        # === 第二行：数据预览 ===
        preview_frame = ttk.LabelFrame(main_frame, text="数据预览 (前10条)", padding="5")
        preview_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        preview_frame.columnconfigure(0, weight=1)
        
        columns = ("SECUID", "项目", "昵称", "评论内容", "抖音号", "地区", "状态")
        self.preview_tree = ttk.Treeview(preview_frame, columns=columns, show="headings", height=5)
        
        col_widths = {"SECUID": 150, "项目": 100, "昵称": 100, "评论内容": 150, "抖音号": 100, "地区": 80, "状态": 60}
        for col in columns:
            self.preview_tree.heading(col, text=col)
            self.preview_tree.column(col, width=col_widths.get(col, 100), anchor=tk.W)
        
        vsb = ttk.Scrollbar(preview_frame, orient="vertical", command=self.preview_tree.yview)
        self.preview_tree.configure(yscrollcommand=vsb.set)
        
        self.preview_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        
        # === 第三行：控制按钮和统计 ===
        control_frame = ttk.Frame(main_frame)
        control_frame.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        
        btn_frame = ttk.Frame(control_frame)
        btn_frame.pack(side=tk.LEFT)
        
        self.start_btn = ttk.Button(btn_frame, text="开始发送", command=self.start_sending)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 5))
        
        self.pause_btn = ttk.Button(btn_frame, text="暂停", command=self.pause_sending, state="disabled")
        self.pause_btn.pack(side=tk.LEFT, padx=(0, 5))
        
        self.stop_btn = ttk.Button(btn_frame, text="停止", command=self.stop_sending, state="disabled")
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 5))
        
        self.stats_label = ttk.Label(control_frame, text="就绪 | 成功: 0 | 失败: 0 | 跳过: 0 | 总计: 0", font=("Microsoft YaHei", 10))
        self.stats_label.pack(side=tk.RIGHT)
        
        # === 第四行：发送间隔设置 ===
        interval_frame = ttk.Frame(main_frame)
        interval_frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        
        ttk.Label(interval_frame, text="发送间隔(秒):").pack(side=tk.LEFT, padx=(0, 5))
        self.interval_var = tk.IntVar(value=5)
        ttk.Spinbox(interval_frame, from_=3, to=60, textvariable=self.interval_var, width=5).pack(side=tk.LEFT, padx=(0, 20))
        
        ttk.Label(interval_frame, text="重试次数:").pack(side=tk.LEFT, padx=(0, 5))
        self.retry_var = tk.IntVar(value=3)
        ttk.Spinbox(interval_frame, from_=1, to=5, textvariable=self.retry_var, width=5).pack(side=tk.LEFT, padx=(0, 20))
        
        # 跳过已发送选项
        self.skip_sent_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(interval_frame, text="跳过已发送的用户", variable=self.skip_sent_var).pack(side=tk.LEFT)
        
        # === 第五行：日志区域 ===
        log_frame = ttk.LabelFrame(main_frame, text="运行日志", padding="5")
        log_frame.grid(row=4, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, font=("Consolas", 9), state="disabled")
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        self.log_text.tag_configure("INFO", foreground="black")
        self.log_text.tag_configure("SUCCESS", foreground="green")
        self.log_text.tag_configure("WARNING", foreground="orange")
        self.log_text.tag_configure("ERROR", foreground="red")
        
        # === 第六行：进度条 ===
        self.progress = ttk.Progressbar(main_frame, mode='determinate')
        self.progress.grid(row=5, column=0, sticky=(tk.W, tk.E), pady=(10, 0))
        
    def import_excel(self):
        """导入Excel文件"""
        file_path = filedialog.askopenfilename(
            title="选择Excel文件",
            filetypes=[("Excel文件", "*.xlsx *.xls"), ("CSV文件", "*.csv"), ("所有文件", "*.*")]
        )
        
        if not file_path:
            return
        
        try:
            try:
                import pandas as pd
                if file_path.endswith('.csv'):
                    df = pd.read_csv(file_path, encoding='utf-8')
                else:
                    df = pd.read_excel(file_path)
            except ImportError:
                messagebox.showerror("错误", "读取Excel需要安装pandas\n请运行: pip install pandas openpyxl")
                return
            
            data_list = df.to_dict('records')
            
            if not data_list:
                messagebox.showwarning("警告", "Excel文件为空")
                return
            
            # 查找SECUID列
            first_row = data_list[0]
            columns = list(first_row.keys())
            secuid_col = None
            for col in columns:
                if col.lower() in ['sec_uid', 'secuid', 'sec_uid']:
                    secuid_col = col
                    break
            
            if not secuid_col:
                messagebox.showerror("错误", "未找到SECUID列")
                return
            
            # 过滤并统一列名
            data_list = [row for row in data_list if row.get(secuid_col)]
            for row in data_list:
                if secuid_col in row:
                    row['sec_uid'] = str(row.pop(secuid_col)).strip()
            
            self.on_data_imported(data_list)
            
        except Exception as e:
            messagebox.showerror("导入失败", f"导入文件时出错:\n{str(e)}")
            state.log(f"导入失败: {e}", "error")
    
    def paste_excel(self):
        """打开粘贴Excel内容窗口"""
        PasteExcelWindow(self.root, self.on_data_imported)
    
    def on_data_imported(self, data_list: List[Dict]):
        """数据导入后的处理"""
        state.data_list = data_list
        
        # 过滤已发送的用户
        if self.skip_sent_var.get():
            state.pending_list = [row for row in data_list if row.get('sec_uid') not in state.sent_secuids]
            skipped = len(data_list) - len(state.pending_list)
            if skipped > 0:
                state.log(f"已跳过 {skipped} 个已发送的用户", "info")
        else:
            state.pending_list = data_list
        
        self.import_status_label.config(text=f"已导入 {len(data_list)} 条，待发送 {len(state.pending_list)} 条")
        state.log(f"成功导入 {len(data_list)} 条数据，待发送 {len(state.pending_list)} 条", "success")
        
        self.update_preview()
    
    def update_preview(self):
        """更新数据预览表格"""
        for item in self.preview_tree.get_children():
            self.preview_tree.delete(item)
        
        for i, row in enumerate(state.data_list[:10]):
            sec_uid = str(row.get('sec_uid', ''))
            is_sent = sec_uid in state.sent_secuids
            status = "已发送" if is_sent else "待发送"
            
            values = (
                sec_uid[:20] + "..." if len(sec_uid) > 20 else sec_uid,
                str(row.get('project', row.get('项目名称', ''))),
                str(row.get('nickname', row.get('昵称', ''))),
                str(row.get('content', row.get('评论内容', '')))[:25] + "..." if len(str(row.get('content', row.get('评论内容', '')))) > 25 else str(row.get('content', row.get('评论内容', ''))),
                str(row.get('user_unique_id', row.get('抖音号', ''))),
                str(row.get('ip_location', row.get('地区', ''))),
                status
            )
            self.preview_tree.insert("", tk.END, values=values)
    
    def view_send_records(self):
        SendRecordWindow(self.root)
    
    def start_sending(self):
        """开始发送私信"""
        if not state.pending_list:
            messagebox.showwarning("警告", "没有待发送的数据")
            return
        
        if not check_login_state():
            result = messagebox.askyesno("登录状态警告", "未找到有效的登录状态！\n\n是否继续？")
            if not result:
                return
        
        host = get_host_from_config()
        if not host:
            result = messagebox.askyesno("配置警告", f"未在 {DATA_JSON_FILE} 中找到host配置！\n\n是否继续？")
            if not result:
                return
        
        # 重置状态
        state.is_running = True
        state.is_paused = False
        state.stop_requested = False
        state.success_count = 0
        state.fail_count = 0
        state.total_count = len(state.pending_list)
        state.current_index = 0
        state.results = []
        
        # 更新UI
        self.start_btn.config(state="disabled")
        self.pause_btn.config(state="normal", text="暂停")
        self.stop_btn.config(state="normal")
        self.progress["maximum"] = len(state.pending_list)
        self.progress["value"] = 0
        
        # 清空日志
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state="disabled")
        
        state.log(f"开始发送私信，共 {len(state.pending_list)} 个用户（已跳过已发送）", "info")
        state.log(f"API地址: {host}/api/private_message", "info")
        
        threading.Thread(target=self.sending_worker, daemon=True).start()
    
    def pause_sending(self):
        """暂停/继续发送"""
        if state.is_paused:
            state.is_paused = False
            self.pause_btn.config(text="暂停")
            state.log("继续发送", "info")
        else:
            state.is_paused = True
            self.pause_btn.config(text="继续")
            state.log("已暂停", "warning")
            save_progress()
    
    def sending_worker(self):
        """发送工作线程"""
        async def process_all():
            interval = self.interval_var.get()
            max_retries = self.retry_var.get()
            
            for i, row in enumerate(state.pending_list):
                if state.stop_requested:
                    state.log("用户停止发送", "warning")
                    save_progress()
                    break
                
                # 检查暂停
                while state.is_paused and not state.stop_requested:
                    await asyncio.sleep(0.5)
                
                if state.stop_requested:
                    state.log("用户停止发送", "warning")
                    save_progress()
                    break
                
                state.current_index = i + 1
                sec_uid = str(row.get('sec_uid', '')).strip()
                
                if not sec_uid:
                    state.log(f"[{i+1}/{len(state.pending_list)}] SECUID为空，跳过", "warning")
                    continue
                
                # 再次检查是否已发送
                if self.skip_sent_var.get() and sec_uid in state.sent_secuids:
                    state.log(f"[{i+1}/{len(state.pending_list)}] {sec_uid[:30]}... 已发送过，跳过", "info")
                    continue
                
                state.log(f"[{i+1}/{len(state.pending_list)}] 正在处理: {sec_uid[:30]}...", "info")
                
                # 获取数据
                project = str(row.get('project', row.get('项目名称', ''))).strip()
                comment = str(row.get('content', row.get('评论内容', ''))).strip()
                nickname = str(row.get('nickname', row.get('昵称', ''))).strip()
                user_unique_id = str(row.get('user_unique_id', row.get('抖音号', ''))).strip()
                ip_location = str(row.get('ip_location', row.get('地区', ''))).strip()
                
                # 生成私信内容
                if project and comment:
                    send_content = generate_private_message(project, comment)
                else:
                    send_content = "你好"
                    state.log("项目或评论为空，使用默认内容", "warning")
                
                state.log(f"生成内容: {send_content[:50]}...", "info")
                
                # 发送
                result = await send_message_with_retry(sec_uid, send_content, max_retries)
                
                # 记录结果
                result_record = {
                    "sec_uid": sec_uid,
                    "project": project,
                    "nickname": nickname,
                    "comment": comment,
                    "user_unique_id": user_unique_id,
                    "ip_location": ip_location,
                    "send_content": send_content,
                    "success": result["success"],
                    "message": result["message"],
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                state.results.append(result_record)
                
                # 保存记录
                save_send_record(result_record)
                
                # 更新已发送集合
                if result["success"]:
                    state.success_count += 1
                    state.sent_secuids.add(sec_uid)
                    state.log(f"发送成功: {result['message']}", "success")
                else:
                    state.fail_count += 1
                    state.log(f"发送失败: {result['message']}", "error")
                
                self.update_stats()
                self.progress["value"] = i + 1
                
                # 保存进度
                save_progress()
                
                # 间隔等待
                if i < len(state.pending_list) - 1 and not state.stop_requested:
                    state.log(f"等待 {interval} 秒后发送下一条...", "info")
                    await asyncio.sleep(interval)
            
            state.log("发送任务完成", "success")
            state.is_running = False
            state.is_paused = False
            clear_progress()
            
            self.root.after(0, self.reset_ui)
        
        asyncio.run(process_all())
    
    def stop_sending(self):
        """停止发送"""
        state.stop_requested = True
        state.log("正在停止...", "warning")
        self.stop_btn.config(state="disabled")
        self.pause_btn.config(state="disabled")
    
    def reset_ui(self):
        """重置UI状态"""
        self.start_btn.config(state="normal")
        self.pause_btn.config(state="disabled", text="暂停")
        self.stop_btn.config(state="disabled")
        messagebox.showinfo("完成", f"发送完成！\n成功: {state.success_count}\n失败: {state.fail_count}")
    
    def update_stats(self):
        """更新统计标签"""
        status = "运行中" if state.is_running else ("已暂停" if state.is_paused else "就绪")
        skipped = len(state.data_list) - len(state.pending_list) if state.data_list else 0
        text = f"{status} | 成功: {state.success_count} | 失败: {state.fail_count} | 跳过: {skipped} | 总计: {state.total_count}"
        self.stats_label.config(text=text)
    
    def update_logs(self):
        """更新日志显示"""
        try:
            while True:
                log = state.log_queue.get_nowait()
                
                self.log_text.config(state="normal")
                
                level = "INFO"
                if "[SUCCESS]" in log:
                    level = "SUCCESS"
                elif "[WARNING]" in log:
                    level = "WARNING"
                elif "[ERROR]" in log:
                    level = "ERROR"
                
                self.log_text.insert(tk.END, log + '\n', level)
                self.log_text.see(tk.END)
                self.log_text.config(state="disabled")
                
                self.update_stats()
                
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self.update_logs)

def main():
    root = tk.Tk()
    app = PrivateMessageGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
