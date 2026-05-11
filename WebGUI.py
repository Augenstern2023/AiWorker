
import sys
import os
import time
import json
import threading
import subprocess
import webbrowser
import queue
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Any
from contextlib import asynccontextmanager

import uvicorn
import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Try to import optional dependencies
try:
    import dict_to_小鸡 as excel_converter
except ImportError:
    excel_converter = None

# Global State
class AppState:
    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        self.log_queue = queue.Queue()
        self.is_running = False
        self.current_project = ""
        self.batch_queue = []  # List of (project_name, ids)
        self.batch_total = 0
        self.batch_current = 0
        self.stop_requested = False
        self.config_file = "data.json"
        
    def log(self, message: str, level: str = "info"):
        timestamp = time.strftime("%H:%M:%S")
        # 尝试解码乱码
        try:
             # 如果是bytes，尝试解码
            if isinstance(message, bytes):
                # 尝试用 GBK 解码（Windows控制台常见编码）
                try:
                    message = message.decode('gbk')
                except:
                    # 如果 GBK 失败，尝试 UTF-8，最后用 replace
                    message = message.decode('utf-8', errors='replace')
        except:
            pass
            
        self.log_queue.put(f"[{timestamp}] [{level.upper()}] {message}")
        
        # 限制队列大小，只保留最近500条，防止前端卡顿
        while self.log_queue.qsize() > 500:
            try:
                self.log_queue.get_nowait()
            except queue.Empty:
                break
        
    def clear_logs(self):
        self.log_queue = queue.Queue()

state = AppState()

# FastAPI App
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    yield
    # Shutdown
    if state.process:
        state.process.terminate()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Models
class CrawlerConfig(BaseModel):
    platform: str
    login_type: str
    crawler_type: str
    keywords: str
    start_page: int
    headless: bool
    save_option: str
    get_comment: bool
    get_sub_comment: bool
    specified_ids: str = ""
    creator_ids: str = ""
    cookie: str = ""
    
    # Douyin specific
    dy_channel: str = "video"
    dy_sort: str = "latest"
    dy_publish_time: str = "1_week"
    
    # Project specific
    projects: Dict[str, bool] = {}
    
    # Auto Run
    autoRun: bool = False

class BatchTask(BaseModel):
    project_names: List[str]

class ConfigContent(BaseModel):
    content: str

class BackupAction(BaseModel):
    filename: str

class MediaDownloadRequest(BaseModel):
    file_path: str
    media_type: str  # "video" or "music"

class MediaItem(BaseModel):
    aweme_id: str
    title: str
    url: str
    filename: str

class MediaListResponse(BaseModel):
    videos: List[MediaItem]
    musics: List[MediaItem]

# --- Helper Functions ---

def read_output(process):
    for line in process.stdout:
        state.log(line.strip(), "info")
    state.is_running = False
    state.process = None
    state.log("任务已结束", "success")

def run_crawler_thread(command: List[str], config: CrawlerConfig = None):
    try:
        state.is_running = True
        state.log(f"执行命令: {' '.join(command)}", "info")
        
        # 在 Windows 上，设置环境变量 PYTHONIOENCODING=utf-8 可以强制 Python 输出 UTF-8
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        
        state.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            encoding='utf-8', # 明确指定子进程输出编码为 utf-8
            errors='replace',
            env=env # 传递环境变量
        )
        
        # Read output in a separate thread to not block
        # Wait for completion in a separate thread to handle autoRun
        # Capture the process object locally to avoid race conditions when state.process is cleared
        process = state.process
        
        def monitor_process():
            # Use local process reference
            read_output(process)
            
            # process is already finished here because read_output returns when stdout closes
            exit_code = process.wait()
            
            if exit_code == 0 and config and config.autoRun:
                state.log("检测到开启了[自动托管]，准备执行后续任务...", "info")
                # 触发自动任务
                threading.Thread(target=auto_run_sequence, args=(config,), daemon=True).start()
        
        threading.Thread(target=monitor_process, daemon=True).start()
        
    except Exception as e:
        state.is_running = False
        state.log(f"启动失败: {str(e)}", "error")

def close_edge_browser():
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], 
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            state.log("[自动托管] 已尝试关闭Edge浏览器", "info")
    except Exception as e:
        state.log(f"[自动托管] 关闭Edge浏览器失败: {e}", "warning")

def auto_run_sequence(config: CrawlerConfig):
    """自动执行后续任务：Contents -> Comments -> Excel -> Upload"""
    try:
        # 1. 查找最新的 Contents 文件
        data_dir = Path("./data")
        contents_files = sorted(
            [f for f in data_dir.rglob("*.json") if "contents" in f.name.lower()],
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        
        if not contents_files:
            state.log("[自动托管] 未找到生成的Contents文件，无法继续", "error")
            return
            
        latest_file = contents_files[0]
        state.log(f"[自动托管] 找到最新文件: {latest_file.name}", "info")
        
        # 2. 分析文件中的项目
        # 简单起见，我们直接读取文件内容，然后根据 config.projects 过滤
        # 或者如果 config.projects 为空，则处理所有项目
        
        projects_to_process = []
        if config.projects:
            projects_to_process = [p for p, v in config.projects.items() if v]
        
        # 如果没有明确指定，尝试从文件中读取所有项目
        if not projects_to_process:
             try:
                with open(latest_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    projects_to_process = list(data.keys())
                elif isinstance(data, list):
                    projects_to_process = ["Default"]
             except:
                 pass
        
        if not projects_to_process:
            state.log("[自动托管] 未识别到需要处理的项目", "error")
            return
            
        state.log(f"[自动托管] 即将处理项目: {', '.join(projects_to_process)}", "info")
        
        # 3. 构造 BatchRequest 并复用 batch_worker 逻辑
        # 我们需要先提取 IDs
        try:
            with open(latest_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            queue_items = []
            for proj in projects_to_process:
                ids = []
                if isinstance(data, dict):
                    items = data.get(proj, [])
                else:
                    items = data # Default
                    
                for item in items:
                    oid = item.get("aweme_id") or item.get("note_id") or item.get("id")
                    if oid:
                        ids.append(oid)
                
                if ids:
                    queue_items.append((proj, ids))
            
            if not queue_items:
                 state.log("[自动托管] 未提取到有效ID", "error")
                 return
                 
            state.batch_queue = queue_items
            state.stop_requested = False
            
            # 启动批量爬取评论
            # 修改：每爬取一个项目，就立即导出和上传
            
            # batch_worker 是同步阻塞的，所以直接调用
            # 我们不再使用 batch_worker 的批量队列模式，而是手动循环
            # state.log("[自动托管] 开始批量爬取评论...", "info")
            # batch_worker() # 这会阻塞直到所有评论爬取完成
            
            # 手动循环
            total = len(queue_items)
            for i, (project_name, ids) in enumerate(queue_items):
                current_idx = i + 1
                state.current_project = project_name
                state.log(f"--- [自动托管] 开始处理项目 [{current_idx}/{total}]: {project_name} ---", "info")
                
                # 1. 爬取评论
                command = build_comment_command(ids, project_name)
                
                try:
                    env = os.environ.copy()
                    env["PYTHONIOENCODING"] = "utf-8"

                    state.process = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        universal_newlines=True,
                        encoding='utf-8',
                        errors='replace',
                        env=env
                    )
                    state.is_running = True
                    
                    for line in state.process.stdout:
                        state.log(line.strip(), "info")
                    
                    exit_code = state.process.wait()
                    state.is_running = False
                    state.process = None
                    
                    if exit_code != 0:
                        state.log(f"项目 {project_name} 爬取失败 (Code: {exit_code})", "error")
                        continue # 失败则跳过导出上传
                        
                    state.log(f"项目 {project_name} 爬取完成", "success")
                    
                    # 2. 导出并上传 (立即执行)
                    if excel_converter:
                        # 重新扫描最新的 Comments 文件
                        # 等待一下文件系统
                        time.sleep(2)
                        current_comments_files = sorted(
                            [f for f in data_dir.rglob("*.json") if "comments" in f.name.lower()],
                            key=lambda x: x.stat().st_mtime,
                            reverse=True
                        )
                        
                        if current_comments_files:
                            latest_c_file = current_comments_files[0]
                            # 简单检查时间 (2分钟内)
                            if time.time() - latest_c_file.stat().st_mtime < 120:
                                state.log(f"正在导出项目 {project_name} (使用文件: {latest_c_file.name})", "info")
                                try:
                                    with open(latest_c_file, "r", encoding="utf-8") as f:
                                        data = json.load(f)
                                    
                                    items = data.get(project_name) if isinstance(data, dict) else data
                                    if items:
                                        temp_file = f"temp_{project_name}.json"
                                        with open(temp_file, "w", encoding="utf-8") as tf:
                                            json.dump({project_name: items}, tf, ensure_ascii=False, indent=4)
                                            
                                        excel_path = excel_converter.convert_json_to_excel(temp_file)
                                        state.log(f"导出成功: {excel_path}", "success")
                                        
                                        # 上传
                                        upload_res = None
                                        max_retries = 10
                                        for attempt in range(max_retries):
                                            upload_res = upload_excel_to_interface(excel_path, project_override=project_name)
                                            if upload_res and "error" not in upload_res:
                                                state.log(f"项目 {project_name} 上传成功", "success")
                                                break
                                            else:
                                                err = upload_res.get("error") if upload_res else "未知错误"
                                                state.log(f"项目 {project_name} 上传失败 (第 {attempt+1}/{max_retries} 次): {err}", "error")
                                                if attempt < max_retries - 1:
                                                    state.log(f"等待 5 秒后重试...", "info")
                                                    time.sleep(5)
                                        
                                        if not upload_res or "error" in upload_res:
                                             state.log(f"项目 {project_name} 上传最终失败，已达到最大重试次数", "error")
                                        
                                        if os.path.exists(temp_file):
                                            os.remove(temp_file)
                                    else:
                                        state.log(f"文件中未找到项目 {project_name} 的数据", "warning")
                                except Exception as e:
                                    state.log(f"导出/上传出错: {str(e)}", "error")
                            else:
                                state.log("未找到刚刚生成的评论文件，跳过导出", "warning")
                        else:
                            state.log("未找到评论文件，跳过导出", "warning")
                    else:
                        state.log("缺少导出模块，无法导出Excel", "error")
                        
                except Exception as e:
                    state.is_running = False
                    state.log(f"项目 {project_name} 执行出错: {str(e)}", "error")
                
                # 项目间稍微停顿
                if current_idx < total and not state.stop_requested:
                    state.log("等待 3 秒后开始下一个项目...", "info")
                    time.sleep(3)
                
                if state.stop_requested:
                    state.log("用户请求停止", "warning")
                    break
            
            state.log("[自动托管] 全流程结束", "success")
            close_edge_browser()
            state.current_project = ""
            state.batch_total = 0
            state.batch_current = 0
            
            # 删除旧的 Step 4 代码
            return
                
        except Exception as e:
            state.log(f"[自动托管] 流程出错: {str(e)}", "error")
            close_edge_browser()
            
    except Exception as e:
        state.log(f"[自动托管] 全局错误: {str(e)}", "error")
        close_edge_browser()

def upload_excel_to_interface(file_path, project_override=""):
    """WebGUI 版本的上传逻辑"""
    try:
        import requests
        # 读取配置
        if os.path.exists(state.config_file):
            with open(state.config_file, "r", encoding="utf-8") as f:
                data_config = json.load(f)
                if isinstance(data_config, list): data_config = data_config[0]
                
                host = data_config.get("host")
                if not host: return {"error": "未配置host"}
                
                # 获取上传配置
                upload_config = data_config.get("upload_config", {})
                project = project_override if project_override else upload_config.get("project")
                
                # 查找 username
                username = upload_config.get("username")
                items = data_config.get("items", [])
                if project:
                    for item in items:
                        if item.get("project") == project:
                            item_username = item.get("username")
                            if item_username: username = item_username
                            break
                
                if not username or not project:
                    return {"error": "缺少username或project配置"}
                    
                url = f"{host.rstrip('/')}/upload/douyin"
                payload = {
                    "username": username,
                    "project": project,
                    "task": upload_config.get("task", 1)
                }
                
                with open(file_path, "rb") as f:
                    response = requests.post(url, data=payload, files={"file": f}, timeout=300)
                    
                if response.status_code == 200:
                    return response.json().get("data", {})
                else:
                    return {"error": f"HTTP {response.status_code}"}
    except Exception as e:
        return {"error": str(e)}
    return {"error": "配置读取失败"}

def batch_worker():
    """Worker thread for batch processing"""
    state.batch_total = len(state.batch_queue)
    state.batch_current = 0
    
    while state.batch_queue and not state.stop_requested:
        project_name, ids = state.batch_queue.pop(0)
        state.batch_current += 1
        state.current_project = project_name
        
        state.log(f"--- 开始处理项目 [{state.batch_current}/{state.batch_total}]: {project_name} ---", "info")
        
        command = build_comment_command(ids, project_name)
        
        try:
            # 同样为批量任务设置环境变量
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"

            state.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                encoding='utf-8',
                 errors='replace',
                 env=env
            )
            state.is_running = True
            
            for line in state.process.stdout:
                state.log(line.strip(), "info")
            
            exit_code = state.process.wait()
            state.is_running = False
            state.process = None
            
            if exit_code == 0:
                state.log(f"项目 {project_name} 完成", "success")
            else:
                state.log(f"项目 {project_name} 失败 (Code: {exit_code})", "error")
                
        except Exception as e:
            state.is_running = False
            state.log(f"项目 {project_name} 执行出错: {str(e)}", "error")
            
        if state.batch_queue and not state.stop_requested:
            state.log("等待 3 秒后继续...", "info")
            time.sleep(3)
            
    state.log("批量任务全部结束", "success")
    state.current_project = ""
    state.batch_total = 0
    state.batch_current = 0
    state.stop_requested = False


def build_command(config: CrawlerConfig, project_map_json: str = "{}"):
    command = ["uv", "run", "main.py"]
    command.extend(["--platform", config.platform.split()[0]])
    command.extend(["--lt", config.login_type.split()[0]])
    command.extend(["--type", config.crawler_type.split()[0]])
    command.extend(["--start", str(config.start_page)])
    
    if config.keywords:
        command.extend(["--keywords", config.keywords])
    
    if project_map_json != "{}":
        command.extend(["--project_map", project_map_json])
        
    command.extend(["--get_comment", "yes" if config.get_comment else "no"])
    
    # Logic from original GUI
    if config.crawler_type.startswith("detail"):
        command.extend(["--get_comment", "yes"])
        command.extend(["--get_sub_comment", "yes"])
    else:
        command.extend(["--get_sub_comment", "yes" if config.get_sub_comment else "no"])
        
    command.extend(["--headless", "yes" if config.headless else "no"])
    command.extend(["--save_data_option", config.save_option.split()[0]])
    
    # 处理Cookie - 如果是二维码登录方式，尝试加载已保存的登录状态
    cookie = config.cookie or ""
    state.log(f"[调试] 用户输入的cookie: {cookie[:50] if cookie else '无'}...", "info")
    
    if config.login_type == "qrcode":
        state.log("[调试] 登录方式为qrcode，尝试加载保存的登录状态", "info")
        try:
            state_file = Path(QRCODE_LOGIN_STATE_FILE)
            state.log(f"[调试] 查找登录状态文件: {state_file.absolute()}", "info")
            if state_file.exists():
                with open(state_file, "r", encoding="utf-8") as f:
                    state_data = json.load(f)
                timestamp = state_data.get("timestamp", 0)
                state.log(f"[调试] 找到登录状态文件，时间戳: {timestamp}", "info")
                # 检查是否过期（7天）
                if time.time() - timestamp <= 7*30 * 24 * 3600:
                    saved_cookies = state_data.get("cookies", [])
                    state.log(f"[调试] 保存的cookies数量: {len(saved_cookies)}", "info")
                    if saved_cookies:
                        # 将cookies转换为字符串格式
                        saved_cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in saved_cookies])
                        state.log(f"[调试] 转换后的cookie字符串长度: {len(saved_cookie_str)}", "info")
                        if saved_cookie_str:
                            if cookie:
                                cookie = cookie + "; " + saved_cookie_str
                            else:
                                cookie = saved_cookie_str
                            state.log(f"[信息] 已加载保存的二维码登录状态，cookie长度: {len(cookie)}", "info")
                else:
                    state.log("[警告] 二维码登录状态已过期，请重新扫码", "warning")
            else:
                state.log("[警告] 未找到二维码登录状态，请先扫码登录", "warning")
        except Exception as e:
            state.log(f"[警告] 加载二维码登录状态失败: {e}", "warning")
    
    state.log(f"[调试] 最终cookie长度: {len(cookie) if cookie else 0}", "info")
    
    if cookie:
        command.extend(["--cookies", cookie])
        
    if config.specified_ids:
        # 检查ID列表长度，如果太长则写入临时文件
        if len(config.specified_ids) > 3000:
            temp_ids_file = f"temp_specified_ids_{int(time.time())}.txt"
            with open(temp_ids_file, "w", encoding="utf-8") as f:
                f.write(config.specified_ids)
            command.extend(["--specified_id_file", temp_ids_file])
            state.log(f"[信息] 指定ID列表较长，已写入临时文件: {temp_ids_file}", "info")
        else:
            command.extend(["--specified_id", config.specified_ids])
        
    if config.creator_ids:
        # 检查创作者ID列表长度，如果太长则写入临时文件
        if len(config.creator_ids) > 3000:
            temp_ids_file = f"temp_creator_ids_{int(time.time())}.txt"
            with open(temp_ids_file, "w", encoding="utf-8") as f:
                f.write(config.creator_ids)
            command.extend(["--creator_id_file", temp_ids_file])
            state.log(f"[信息] 创作者ID列表较长，已写入临时文件: {temp_ids_file}", "info")
        else:
            command.extend(["--creator_id", config.creator_ids])
        
    # Douyin specifics
    if config.platform.startswith("dy"):
        channel_map = {
            "general": "aweme_general", "video": "aweme_video_web",
            "user": "aweme_user_web", "live": "aweme_live"
        }
        val = config.dy_channel.split()[0]
        if val in channel_map:
            command.extend(["--dy_channel", channel_map[val]])
            
        sort_map = {"general": "0", "most_like": "1", "latest": "2"}
        val = config.dy_sort.split()[0]
        if val in sort_map:
            command.extend(["--dy_sort", sort_map[val]])
            
        time_map = {"unlimited": "0", "1_day": "1", "1_week": "7", "6_months": "180"}
        val = config.dy_publish_time.split()[0]
        if val in time_map:
            command.extend(["--dy_publish_time", time_map[val]])
            
    # Auto close
    command.extend(["--auto_close", "yes"])
            
    return command

def build_comment_command(ids: List[str], project_name: str):
    command = ["uv", "run", "main.py"]
    command.extend(["--platform", "dy"])
    command.extend(["--lt", "qrcode"])
    command.extend(["--type", "detail"])
    
    # 检查ID列表长度，如果太长则写入临时文件
    ids_str = ",".join(ids)
    if len(ids_str) > 3000:  # Windows命令行长度限制约为32767，预留足够余量
        # 将ID写入临时文件
        temp_ids_file = f"temp_ids_{project_name}_{int(time.time())}.txt"
        with open(temp_ids_file, "w", encoding="utf-8") as f:
            f.write(ids_str)
        command.extend(["--specified_id_file", temp_ids_file])
        state.log(f"[信息] ID列表较长({len(ids)}个)，已写入临时文件: {temp_ids_file}", "info")
    else:
        command.extend(["--specified_id", ids_str])
    
    if project_name:
        command.extend(["--project", project_name])
    command.extend(["--get_comment", "yes"])
    command.extend(["--get_sub_comment", "yes"])
    command.extend(["--headless", "no"])
    command.extend(["--auto_close", "yes"])
    
    # 加载保存的二维码登录状态
    try:
        state_file = Path(QRCODE_LOGIN_STATE_FILE)
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
                        state.log("[信息] 评论爬取已加载保存的二维码登录状态", "info")
            else:
                state.log("[警告] 二维码登录状态已过期，评论爬取可能需要重新登录", "warning")
        else:
            state.log("[警告] 未找到二维码登录状态，评论爬取可能需要扫码登录", "warning")
    except Exception as e:
        state.log(f"[警告] 加载二维码登录状态失败: {e}", "warning")
    
    return command

# --- API Routes ---

@app.get("/")
async def index():
    return FileResponse("templates/index.html")

@app.get("/api/config")
async def get_config():
    if os.path.exists(state.config_file):
        try:
            with open(state.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and data:
                    return data[0]
                return data
        except:
            return {}
    return {}

@app.get("/api/config/raw")
async def get_config_raw():
    if os.path.exists(state.config_file):
        try:
            with open(state.config_file, "r", encoding="utf-8") as f:
                return {"content": f.read()}
        except Exception as e:
             return {"content": "", "error": str(e)}
    return {"content": ""}

@app.post("/api/config/raw")
async def save_config_raw(config: ConfigContent):
    try:
        # Validate JSON
        json.loads(config.content)
        # Write
        with open(state.config_file, "w", encoding="utf-8") as f:
            f.write(config.content)
        return {"status": "success", "message": "配置已保存"}
    except json.JSONDecodeError as e:
        return JSONResponse({"status": "error", "message": f"JSON格式错误: {e}"}, status_code=400)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.get("/api/config/backups")
async def get_backups():
    backup_dir = Path("backups")
    if not backup_dir.exists():
        return []
    
    backups = []
    for f in backup_dir.glob("data_backup_*.json"):
        stats = f.stat()
        backups.append({
            "name": f.name,
            "path": str(f),
            "size": stats.st_size,
            "created": stats.st_ctime
        })
    backups.sort(key=lambda x: x["created"], reverse=True)
    return backups

@app.post("/api/config/backup/create")
async def create_backup():
    if not os.path.exists(state.config_file):
        return JSONResponse({"status": "error", "message": "配置文件不存在"}, status_code=404)
        
    backup_dir = Path("backups")
    backup_dir.mkdir(exist_ok=True)
    
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_name = f"data_backup_{timestamp}.json"
    backup_path = backup_dir / backup_name
    
    try:
        shutil.copy2(state.config_file, backup_path)
        return {"status": "success", "message": "备份创建成功", "filename": backup_name}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/config/backup/restore")
async def restore_backup(action: BackupAction):
    backup_path = Path("backups") / action.filename
    if not backup_path.exists():
        return JSONResponse({"status": "error", "message": "备份文件不存在"}, status_code=404)
        
    try:
        shutil.copy2(backup_path, state.config_file)
        return {"status": "success", "message": "配置已恢复"}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/config/backup/delete")
async def delete_backup(action: BackupAction):
    backup_path = Path("backups") / action.filename
    if not backup_path.exists():
        return JSONResponse({"status": "error", "message": "备份文件不存在"}, status_code=404)
        
    try:
        os.remove(backup_path)
        return {"status": "success", "message": "备份已删除"}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/run")
async def run_crawler(config: CrawlerConfig):
    if state.is_running:
        return JSONResponse({"status": "error", "message": "爬虫正在运行中"}, status_code=400)
    
    # Process projects and keywords
    merged_keywords = ""
    project_map_json = "{}"
    
    # Read project items from config file to get their keywords
    project_items = []
    try:
        if os.path.exists(state.config_file):
            with open(state.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and data:
                    project_items = data[0].get("items", [])
                elif isinstance(data, dict):
                    project_items = data.get("items", [])
    except:
        pass

    if project_items and config.projects:
        selected_keywords = set()
        keyword_to_projects = {}
        
        for item in project_items:
            p_name = item.get("project")
            if config.projects.get(p_name):
                k_str = item.get("搜索关键词", "")
                if k_str:
                    for k in [x.strip() for x in k_str.split(",") if x.strip()]:
                        selected_keywords.add(k)
                        if k not in keyword_to_projects:
                            keyword_to_projects[k] = []
                        keyword_to_projects[k].append(p_name)
        
        if selected_keywords:
            merged_keywords = ",".join(selected_keywords)
            project_map_json = json.dumps(keyword_to_projects, ensure_ascii=False)
            config.keywords = merged_keywords # Override keywords
    
    if not config.keywords and not config.specified_ids and not config.creator_ids:
         return JSONResponse({"status": "error", "message": "未提供关键词或ID，且未选择有效项目"}, status_code=400)

    state.clear_logs()
    command = build_command(config, project_map_json)
    threading.Thread(target=run_crawler_thread, args=(command, config), daemon=True).start()
    return {"status": "success", "message": "爬虫已启动"}

@app.post("/api/stop")
async def stop_crawler():
    state.stop_requested = True
    if state.process:
        state.log("正在停止...", "info")
        
        # 使用 taskkill 强制结束进程树
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(state.process.pid)], 
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            state.process.terminate()
            
        return {"status": "success", "message": "停止信号已发送"}
    return {"status": "error", "message": "没有运行中的爬虫"}

@app.get("/api/logs")
async def get_logs():
    logs = []
    while not state.log_queue.empty():
        logs.append(state.log_queue.get())
    return {"logs": logs, "is_running": state.is_running, "batch_progress": f"{state.batch_current}/{state.batch_total}" if state.batch_total > 0 else ""}

@app.get("/api/files")
async def list_files(filter: str = "all"):
    data_dir = Path("./data")
    if not data_dir.exists():
        return []
    
    files = []
    # 递归查找所有文件
    for f in data_dir.rglob("*"):
        if f.is_file() and f.suffix in [".json", ".csv", ".xlsx"]:
            cat = "unknown"
            if "comments" in f.name.lower(): cat = "Comments"
            elif "contents" in f.name.lower(): cat = "Contents"
            
            if filter != "all" and cat != filter:
                continue
                
            stats = f.stat()
            files.append({
                "name": f.name,
                "path": str(f),
                "size": stats.st_size,
                "modified": stats.st_mtime,
                "category": cat,
                "type": f.suffix[1:]
            })
    
    # Sort by modified desc
    files.sort(key=lambda x: x["modified"], reverse=True)
    return files

@app.get("/api/file/content")
async def get_file_content(path: str):
    if not os.path.exists(path):
        raise HTTPException(404, "File not found")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
            # Try to parse JSON to pretty print
            if path.endswith(".json"):
                try:
                    parsed = json.loads(content)
                    return {"content": json.dumps(parsed, indent=2, ensure_ascii=False)}
                except:
                    pass
            return {"content": content}
    except Exception as e:
        return {"content": str(e)}

@app.get("/api/file/download")
async def download_file(path: str):
    if not os.path.exists(path):
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=os.path.basename(path))

@app.post("/api/batch/crawl_comments")
async def batch_crawl_comments(task: BatchTask):
    # This logic mimics the `run_batch_comment_crawler`
    # We need to scan files to find IDs for the selected projects?
    # Actually, the UI should provide the projects and we assume the logic to find IDs is done?
    # No, the UI just selects projects. We need to parse the files to find IDs.
    
    # Wait, the previous GUI `crawl_comments_from_file` did the parsing.
    # In the new API, we should split this:
    # 1. UI requests "Analysis" of a file -> returns list of projects and counts.
    # 2. UI lets user select projects.
    # 3. UI calls this endpoint with selected projects AND the file path? 
    # Or we just pass the file path and selected projects.
    pass

# We need an endpoint to analyze a file first
class AnalyzeRequest(BaseModel):
    file_path: str

@app.post("/api/file/analyze")
async def analyze_file(req: AnalyzeRequest):
    if not os.path.exists(req.file_path):
        raise HTTPException(404, "File not found")
    
    # Use the logic from `extract_and_filter_aweme_ids`
    # ... (Simplified for brevity, assuming we just need to return project list)
    project_ids = {}
    try:
        with open(req.file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, list):
                    project_ids[k] = len(v)
        elif isinstance(data, list):
            project_ids["Default"] = len(data)
            
    except Exception as e:
        return {"error": str(e)}
        
    return project_ids

class BatchRunRequest(BaseModel):
    file_path: str
    projects: List[str] # List of project names to process
    auto_upload: bool = True # Whether to auto upload after export

class FileReviewRequest(BaseModel):
    file_path: str

def review_worker(file_path: str):
    """Worker thread for file review"""
    state.is_running = True
    try:
        file_path_obj = Path(file_path)
        state.log(f"开始复查文件: {file_path_obj.name}", "info")
        
        # 1. 读取配置文件
        state.log("正在读取配置文件 data.json ...", "info")
        if not os.path.exists("data.json"):
            state.log("找不到 data.json", "error")
            return
            
        with open("data.json", "r", encoding="utf-8") as f:
            config_data = json.load(f)
            if isinstance(config_data, list) and len(config_data) > 0:
                config_data = config_data[0]
        
        host = config_data.get("host")
        if not host:
            state.log("data.json 中未配置 host", "error")
            return
            
        api_url = f"{host.rstrip('/')}/api/filter_video_titles"
        
        # 准备项目配置映射
        project_configs = {}
        for item in config_data.get("items", []):
            p_name = item.get("project")
            if p_name:
                project_configs[p_name] = {
                    "主题": item.get("project", ""),
                    "有关": item.get("有关视频判定", item.get("搜索关键词", "")),
                    "无关": item.get("黑名单视频判定", item.get("屏蔽关键词", ""))
                }
        
        # 2. 读取内容文件
        state.log(f"正在读取文件: {file_path_obj.name}", "info")
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # 3. 处理数据
        total_processed = 0
        has_changes = False
        import requests
        
        # 统一转为字典处理
        data_dict = {}
        if isinstance(data, list):
            data_dict["Default"] = data
        elif isinstance(data, dict):
            data_dict = data
        
        for project_name, items in data_dict.items():
            if not items: continue
            
            if state.stop_requested:
                state.log("用户请求停止复查", "warning")
                break

            # 获取筛选配置
            filter_config = project_configs.get(project_name)
            if not filter_config:
                state.log(f"[WARNING] 未找到项目 {project_name} 的筛选配置，跳过筛选", "warning")
                continue
            
            state.log(f"正在处理项目: {project_name}, 共 {len(items)} 条", "info")
            
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
                if state.stop_requested: break
                
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
                                    state.log(f"  - [{status_str}] {titles[j][:30]}...", "info")
                        else:
                            state.log(f"[ERROR] 接口返回失败: {res_json}", "error")
                    else:
                        state.log(f"[ERROR] 接口HTTP错误: {resp.status_code}", "error")
                except Exception as e:
                    state.log(f"[ERROR] 请求异常: {e}", "error")
                    
                # 稍微停顿避免请求过快
                time.sleep(0.5)
        
        # 4. 保存文件
        if has_changes:
            state.log("正在保存文件...", "info")
            # 恢复原始结构
            save_data = data_dict if isinstance(data, dict) else data_dict["Default"]
            
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(save_data, f, ensure_ascii=False, indent=4)
            state.log("[SUCCESS] 文件保存成功", "success")
            state.log(f"复查完成，共处理 {total_processed} 条数据", "success")
        else:
            state.log("没有数据发生变化", "info")
            
    except Exception as e:
        state.log(f"复查失败: {str(e)}", "error")
    finally:
        state.is_running = False
        state.stop_requested = False

@app.post("/api/file/review")
async def review_file(req: FileReviewRequest):
    if state.is_running:
         return JSONResponse({"status": "error", "message": "已有任务在运行"}, status_code=400)
    
    if not os.path.exists(req.file_path):
        return JSONResponse({"status": "error", "message": "文件不存在"}, status_code=404)

    state.clear_logs()
    state.stop_requested = False
    threading.Thread(target=review_worker, args=(req.file_path,), daemon=True).start()
    return {"status": "success", "message": "复查任务已启动，请在控制台查看进度"}

@app.post("/api/batch/run")
async def run_batch(req: BatchRunRequest):
    if state.is_running:
        return JSONResponse({"status": "error", "message": "已有任务在运行"}, status_code=400)
    
    # Extract IDs
    try:
        with open(req.file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        queue_items = []
        for proj in req.projects:
            ids = []
            if isinstance(data, dict):
                items = data.get(proj, [])
            else:
                items = data # Default
                
            for item in items:
                # Extract ID logic
                oid = item.get("aweme_id") or item.get("note_id") or item.get("id")
                if oid:
                    ids.append(oid)
            
            if ids:
                queue_items.append((proj, ids))
        
        if not queue_items:
             return JSONResponse({"status": "error", "message": "未找到有效ID"}, status_code=400)
             
        state.batch_queue = queue_items
        state.stop_requested = False
        state.clear_logs()
        
        threading.Thread(target=batch_worker, daemon=True).start()
        return {"status": "success", "message": "批量任务已启动"}
        
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/export/excel")
async def export_excel(req: BatchRunRequest):
    # Uses dict_to_小鸡
    if not excel_converter:
        return JSONResponse({"status": "error", "message": "缺少导出模块"}, status_code=500)
        
    # Logic to export selected projects
    # We can run this in a thread too as it might take time
    def export_worker():
        state.log("开始导出Excel...", "info")
        try:
            # Load data
            with open(req.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            for proj in req.projects:
                items = data.get(proj) if isinstance(data, dict) else data
                if not items: continue
                
                # Create temp file
                temp_file = f"temp_{proj}.json"
                with open(temp_file, "w", encoding="utf-8") as tf:
                    json.dump({proj: items}, tf, ensure_ascii=False, indent=4)
                    
                # Convert
                state.log(f"正在转换项目: {proj}", "info")
                excel_path = excel_converter.convert_json_to_excel(temp_file)
                state.log(f"导出成功: {excel_path}", "success")
                
                # Auto Upload
                if req.auto_upload:
                    upload_res = upload_excel_to_interface(excel_path, project_override=proj)
                    if upload_res and "error" not in upload_res:
                         state.log(f"项目 {proj} 上传成功", "success")
                    else:
                         err = upload_res.get("error", "未知错误")
                         state.log(f"项目 {proj} 上传失败: {err}", "error")
                else:
                    state.log(f"项目 {proj} 导出完成 (未开启自动上传)", "info")
                
                # Cleanup
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                    
        except Exception as e:
            state.log(f"导出失败: {str(e)}", "error")
            
    threading.Thread(target=export_worker, daemon=True).start()
    return {"status": "success", "message": "导出任务已后台启动，请查看日志"}


@app.get("/api/server/ip")
async def get_server_ip():
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 不真正连接，只是为了获取本机IP
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return {"ip": ip, "port": 8899}
    except:
        return {"ip": "127.0.0.1", "port": 8899}


# --- QR Code Login Functions ---

QRCODE_LOGIN_STATE_FILE = "qrcode_login_state.json"

async def check_login_state_douyin(browser_context) -> bool:
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


async def save_login_state(browser_context, platform: str = "douyin"):
    """保存登录状态到文件"""
    try:
        cookies = await browser_context.cookies()
        state_data = {
            "platform": platform,
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
        
        with open(QRCODE_LOGIN_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state_data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存登录状态失败: {e}")
        return False


def load_login_state() -> dict:
    """从文件加载登录状态"""
    try:
        if os.path.exists(QRCODE_LOGIN_STATE_FILE):
            with open(QRCODE_LOGIN_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"加载登录状态失败: {e}")
    return {}


async def perform_qrcode_login(platform: str = "douyin"):
    """执行二维码登录流程"""
    from playwright.async_api import async_playwright
    
    browser = None
    browser_context = None
    login_success = False
    error_msg = ""
    
    try:
        state.log("[扫码登录] 正在启动浏览器...", "info")
        async with async_playwright() as p:
            # 启动浏览器
            browser = await p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"]
            )
            state.log("[扫码登录] 浏览器已启动", "info")
            
            browser_context = await browser.new_context(
                viewport={"width": 1920, "height": 1080}
            )
            state.log("[扫码登录] 浏览器上下文已创建", "info")
            
            # 打开页面
            page = await browser_context.new_page()
            state.log("[扫码登录] 新页面已创建", "info")
            
            if platform == "douyin":
                state.log("[扫码登录] 正在打开抖音网站...", "info")
                try:
                    # 先尝试快速加载
                    await page.goto("https://www.douyin.com", wait_until="domcontentloaded", timeout=30000)
                    state.log("[扫码登录] 抖音网站已加载 (domcontentloaded)", "info")
                except:
                    # 如果超时，继续等待网络空闲
                    state.log("[扫码登录] 页面基础内容已加载，继续等待...", "info")
                
                # 再等待一段时间让页面完全加载
                await page.wait_for_timeout(5000)
                state.log("[扫码登录] 页面加载完成", "info")
                await page.wait_for_timeout(3000)
                
                # 尝试点击登录按钮
                try:
                    login_btn = page.locator("xpath=//p[text() = '登录']")
                    count = await login_btn.count()
                    state.log(f"[扫码登录] 找到 {count} 个登录按钮", "info")
                    if count > 0:
                        await login_btn.click()
                        await page.wait_for_timeout(1000)
                        state.log("[扫码登录] 已点击登录按钮", "info")
                except Exception as e:
                    state.log(f"[扫码登录] 点击登录按钮失败: {e}", "warning")
                
                # 等待二维码出现
                try:
                    qrcode_selector = "xpath=//div[@id='animate_qrcode_container']//img"
                    state.log("[扫码登录] 等待二维码出现...", "info")
                    await page.wait_for_selector(qrcode_selector, timeout=10000)
                    state.log("[扫码登录] 请使用手机扫描二维码", "info")
                except Exception as e:
                    state.log(f"[扫码登录] 未找到二维码: {e}", "warning")
                    # 截图查看当前页面状态
                    try:
                        await page.screenshot(path="qrcode_debug.png")
                        state.log("[扫码登录] 已保存调试截图: qrcode_debug.png", "info")
                    except:
                        pass
            
            # 等待登录完成（最多等待5分钟）
            max_wait = 300  # 5分钟
            waited = 0
            check_interval = 2
            
            state.log("[扫码登录] 开始等待扫码，请在5分钟内完成扫码...", "info")
            while waited < max_wait:
                try:
                    if await check_login_state_douyin(browser_context):
                        login_success = True
                        break
                    
                    # 检查页面是否仍然打开
                    if page.is_closed():
                        state.log("[扫码登录] 页面已被关闭", "warning")
                        error_msg = "页面被关闭，请重试"
                        break
                        
                    await page.wait_for_timeout(check_interval * 1000)
                    waited += check_interval
                    
                    if waited % 10 == 0:
                        state.log(f"[扫码登录] 等待扫码中... {waited}秒", "info")
                except Exception as e:
                    state.log(f"[扫码登录] 等待过程中出错: {e}", "warning")
                    # 如果是页面关闭的错误，直接退出循环
                    if "closed" in str(e).lower():
                        error_msg = "浏览器被关闭，请重试"
                        break
                    # 其他错误继续等待
                    await asyncio.sleep(check_interval)
                    waited += check_interval
            
            if login_success:
                # 保存登录状态
                await save_login_state(browser_context, platform)
                state.log("[扫码登录] 登录成功，状态已保存", "success")
            elif not error_msg:
                error_msg = "登录超时，请重试"
                state.log("[扫码登录] 登录超时", "error")
                
    except Exception as e:
        error_msg = str(e)
        state.log(f"[扫码登录] 发生错误: {e}", "error")
        import traceback
        state.log(f"[扫码登录] 错误详情: {traceback.format_exc()}", "error")
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
        state.log("[扫码登录] 浏览器已关闭", "info")
    
    return {"success": login_success, "error": error_msg}


# QR Code Login API Endpoints

@app.get("/api/qrcode/status")
async def get_qrcode_status():
    """获取二维码登录状态"""
    state_data = load_login_state()
    
    if not state_data:
        return {"has_state": False, "message": "未找到登录状态"}
    
    # 检查是否过期（7天）
    timestamp = state_data.get("timestamp", 0)
    if time.time() - timestamp > 7 * 24 * 3600:
        return {"has_state": False, "message": "登录状态已过期", "expired": True}
    
    platform = state_data.get("platform", "unknown")
    save_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
    
    return {
        "has_state": True,
        "platform": platform,
        "save_time": save_time,
        "message": f"已保存{platform}登录状态 ({save_time})"
    }


@app.post("/api/qrcode/login")
async def start_qrcode_login():
    """启动二维码登录流程"""
    if state.is_running:
        return JSONResponse({"status": "error", "message": "爬虫正在运行中，请先停止"}, status_code=400)
    
    state.clear_logs()
    state.log("[扫码登录] 启动扫码登录流程...", "info")
    
    # 在后台线程中执行登录
    def login_worker():
        import asyncio
        result = asyncio.run(perform_qrcode_login("douyin"))
        return result
    
    threading.Thread(target=login_worker, daemon=True).start()
    
    return {"status": "success", "message": "扫码登录已启动，请查看日志"}


@app.post("/api/qrcode/clear")
async def clear_qrcode_state():
    """清除保存的登录状态"""
    try:
        if os.path.exists(QRCODE_LOGIN_STATE_FILE):
            os.remove(QRCODE_LOGIN_STATE_FILE)
            return {"status": "success", "message": "登录状态已清除"}
        return {"status": "success", "message": "没有保存的登录状态"}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


# --- Media Download API Endpoints ---

def sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符"""
    import re
    # 替换非法字符为下划线
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    # 移除多余空格
    name = re.sub(r'\s+', ' ', name)
    name = name.strip()
    # 限制长度
    if len(name) > 100:
        name = name[:100]
    return name


def build_media_filename(title: str, aweme_id: str, media_type: str, extension: str) -> str:
    """构建媒体下载文件名: {标题}_{aweme_id}_{类型}.{扩展名}"""
    title = sanitize_filename(title)
    return f"{title}_{aweme_id}_{media_type}.{extension}"


@app.get("/api/media/list")
async def get_media_list(file_path: str):
    """获取文件中的所有视频/音频URL列表"""
    if not os.path.exists(file_path):
        raise HTTPException(404, "File not found")

    videos = []
    musics = []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 支持多种数据格式
        items = []
        if isinstance(data, dict):
            # 可能是 {project_name: [items]} 或直接是 {aweme_id: {...}}
            for key, value in data.items():
                if isinstance(value, list):
                    items.extend(value)
                elif isinstance(value, dict):
                    # 单个项目直接是数据
                    items.append(value)
        elif isinstance(data, list):
            items = data

        for item in items:
            aweme_id = item.get("aweme_id", "")
            title = item.get("title") or item.get("desc", "") or ""

            # 提取视频URL
            video_url = item.get("video_download_url", "")
            if video_url and isinstance(video_url, str) and video_url.startswith("http"):
                videos.append(MediaItem(
                    aweme_id=aweme_id,
                    title=title[:50],
                    url=video_url,
                    filename=build_media_filename(title, aweme_id, "video", "mp4")
                ))

            # 提取音频URL
            music_url = item.get("music_download_url", "")
            if music_url and isinstance(music_url, str) and music_url.startswith("http"):
                musics.append(MediaItem(
                    aweme_id=aweme_id,
                    title=title[:50],
                    url=music_url,
                    filename=build_media_filename(title, aweme_id, "music", "mp3")
                ))

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    return MediaListResponse(videos=videos, musics=musics)


@app.get("/api/media/download")
async def download_media(url: str, media_type: str = "video", filename: str = "media"):
    """通过URL转发下载视频或音频"""
    if not url or not url.startswith("http"):
        raise HTTPException(400, "Invalid URL")

    try:
        # 确定文件扩展名
        ext = "mp4" if media_type == "video" else "mp3"
        if not filename.endswith(f".{ext}"):
            filename = f"{filename}.{ext}"

        # 使用httpx异步获取内容并流式返回
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise HTTPException(response.status_code, f"Failed to fetch media: {response.status_code}")

                # 确定内容类型
                content_type = response.headers.get("content-type", "")
                if not content_type or "application/octet-stream" in content_type:
                    content_type = "video/mp4" if media_type == "video" else "audio/mpeg"

                async def stream_response():
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        if chunk:
                            yield chunk

                return StreamingResponse(
                    stream_response(),
                    media_type=content_type,
                    headers={
                        "Content-Disposition": f'attachment; filename*=UTF-8\'\'{filename}',
                        "Content-Length": response.headers.get("content-length", ""),
                    }
                )
    except Exception as e:
        raise HTTPException(500, f"Download failed: {str(e)}")


# Mount static files
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    # Ensure directories exist
    os.makedirs("data", exist_ok=True)
    os.makedirs("templates", exist_ok=True)
    
    print("Starting AiWorker GUI...")
    # Open browser
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8899")).start()
    uvicorn.run(app, host="0.0.0.0", port=8899, log_level="error")
