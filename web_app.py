from flask import Flask, request, jsonify, render_template, send_from_directory, session, redirect, url_for, Response, stream_with_context
from storage import BaiduStorage
from scheduler import TaskScheduler
from history_db import get_qms_logs as qms_get_logs
from qms_client import (
    QmsClient,
    QmsError,
    build_summary as qms_build_summary,
    get_links as qms_get_links,
    load_cfg as qms_load_cfg,
    merge_cfg as qms_merge_cfg,
    migrate_from_config as qms_migrate_from_config,
    new_link_id as qms_new_link_id,
    save_cfg as qms_save_cfg,
    trigger_link as qms_trigger_link,
    trigger_after_transfer as qms_trigger_after_transfer,
)
import json
from loguru import logger
import sys
import os
import atexit
import re
from functools import wraps
import signal
from utils import generate_transfer_notification
from notify import send as notify_send
from datetime import datetime
from flask_cors import CORS
import time
import socket
import threading
import queue

from gevent.pywsgi import WSGIServer

# GitHub 仓库信息
GITHUB_REPO = 'kokojacket/baidu-autosave'
# Docker Hub 信息
DOCKER_HUB_RSS = 'https://rsshub.rssforever.com/dockerhub/tag/kokojacket/baidu-autosave'
# 备用 Docker Hub RSS 源
DOCKER_HUB_RSS_ALT = 'https://rss.kuaisouxia.com/dockerhub/tag/kokojacket/baidu-autosave'
# 1ms.run API 源
MS_RUN_API = 'https://1ms.run/api/v1/registry/get_tags'

# 创建日志目录
os.makedirs('log', exist_ok=True)

# 配置日志
logger.remove()  # 移除默认的控制台输出

# 定义统一的日志格式和级别
log_format = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
log_level = "DEBUG"  # 使用DEBUG级别，可以看到所有日志

# 过滤敏感信息
def filter_sensitive_info(record):
    """过滤敏感信息，如BDUSS和cookies"""
    message = record["message"]
    # 替换BDUSS
    message = re.sub(r"BDUSS['\"]?\s*:\s*['\"]?([^'\"]+)['\"]?", "BDUSS: [已隐藏]", message)
    # 替换cookies
    message = re.sub(r"cookies['\"]?\s*:\s*['\"]?([^'\"]+)['\"]?", "cookies: [已隐藏]", message)
    record["message"] = message
    return True

# 过滤轮询请求日志
def filter_polling_requests(record):
    """过滤高频轮询请求的访问日志。"""
    message = record["message"]

    # 检查是否是HTTP请求日志（WSGI服务器的访问日志）
    if "GET /api/tasks/status HTTP" in message:
        return False  # 不显示这些日志

    return True  # 显示其他所有日志

# 应用过滤器到所有日志处理器
logger.configure(patcher=filter_sensitive_info)

# 添加彩色的控制台输出（带轮询过滤）
logger.add(sys.stdout, 
          level=log_level, 
          format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
          filter=filter_polling_requests)  # 添加轮询过滤器

# 添加文件输出 (内容与控制台输出相同，但没有颜色标记，且不过滤轮询请求)
logger.add("log/web_app_{time:YYYY-MM-DD}.log", 
          rotation="00:00",  # 每天零点创建新文件
          retention="7 days",  # 保留7天的日志
          level=log_level,
          encoding="utf-8",
          format=log_format)

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # 用于session加密
CORS(app)

# 全局变量声明
storage = None
scheduler = None


def _ensure_task_runtime_state():
    """初始化任务日志与 SSE 运行时状态。"""
    if not hasattr(app, 'task_logs'):
        app.task_logs = {}
    if not hasattr(app, 'task_stream_subscribers'):
        app.task_stream_subscribers = {}
    if not hasattr(app, 'task_order_to_uid'):
        app.task_order_to_uid = {}
    if not hasattr(app, '_log_cleanup_counter'):
        app._log_cleanup_counter = 0


def _remember_task_stream(task_uid, task_order):
    _ensure_task_runtime_state()
    if task_uid and task_order:
        app.task_order_to_uid[task_order] = task_uid


def _get_task_uid(task_order, task_uid=None):
    _ensure_task_runtime_state()
    return task_uid or app.task_order_to_uid.get(task_order)


def _enqueue_task_event(event_queue, event_name, payload):
    try:
        event_queue.put_nowait((event_name, payload))
    except queue.Full:
        try:
            event_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            event_queue.put_nowait((event_name, payload))
        except queue.Full:
            pass


def _publish_task_event(task_uid, event_name, payload):
    if not task_uid:
        return

    _ensure_task_runtime_state()
    subscribers = list(app.task_stream_subscribers.get(task_uid, []))
    for event_queue in subscribers:
        _enqueue_task_event(event_queue, event_name, payload)


def _register_task_stream(task_uid, event_queue):
    if not task_uid:
        return

    _ensure_task_runtime_state()
    app.task_stream_subscribers.setdefault(task_uid, []).append(event_queue)


def _unregister_task_stream(task_uid, event_queue):
    if not task_uid or not hasattr(app, 'task_stream_subscribers'):
        return

    subscribers = app.task_stream_subscribers.get(task_uid, [])
    if event_queue in subscribers:
        subscribers.remove(event_queue)
    if not subscribers and task_uid in app.task_stream_subscribers:
        del app.task_stream_subscribers[task_uid]


def _format_sse(event_name, payload):
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event_name}\ndata: {data}\n\n"


def _build_task_stream_snapshot(task):
    _ensure_task_runtime_state()
    task_order = task.get('order') if task else None
    logs = list(app.task_logs.get(task_order, [])) if task_order else []
    return {
        'task': task,
        'logs': logs,
    }


def _publish_task_status(task_order, task_uid=None):
    if not storage:
        return None

    stable_uid = _get_task_uid(task_order, task_uid)
    task = storage.resolve_task(stable_uid, order=task_order)
    if task and stable_uid:
        _publish_task_event(stable_uid, 'status', task)
    return task


def _append_task_log(task_order, message, level='INFO', task_uid=None):
    stable_uid = _get_task_uid(task_order, task_uid)
    _ensure_task_runtime_state()
    log_entry = {
        'timestamp': datetime.now().strftime('%H:%M:%S'),
        'level': level,
        'message': message,
        'task_order': task_order
    }
    app.task_logs.setdefault(task_order, []).append(log_entry)
    if stable_uid:
        _publish_task_event(stable_uid, 'log', log_entry)
    return log_entry


def _publish_task_completed(task_order, task_uid=None):
    task = _publish_task_status(task_order, task_uid)
    stable_uid = _get_task_uid(task_order, task_uid)
    if task and stable_uid:
        _publish_task_event(stable_uid, 'completed', {'task': task})
    return task


def get_server_port():
    """从环境变量读取服务端口，默认保持 5000 兼容。"""
    raw_port = os.environ.get('WEB_APP_PORT') or os.environ.get('PORT') or '5000'
    try:
        return int(raw_port)
    except ValueError:
        logger.warning(f"无效的服务端口配置: {raw_port}，回退到 5000")
        return 5000


# 登录装饰器
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login'))
            
        # 检查会话是否过期
        auth_config = storage.config.get('auth', {})
        session_timeout = auth_config.get('session_timeout', 3600)
        if time.time() - session.get('login_time', 0) > session_timeout:
            session.clear()
            return redirect(url_for('login'))
            
        # 更新最后活动时间
        session['login_time'] = time.time()
        return f(*args, **kwargs)
    return decorated_function


def init_app():
    """初始化应用"""
    global storage, scheduler
    try:
        logger.info("开始初始化应用...")
        # 初始化存储
        logger.info("正在初始化存储...")
        storage = BaiduStorage()

        # 修改: 早期 QMS 配置写在 config.json，会被转存进度回写冲掉，迁移到 SQLite
        try:
            if qms_migrate_from_config(storage):
                logger.info("QMS 配置已从 config.json 迁移到 SQLite")
        except Exception as e:
            logger.warning(f"QMS 配置迁移失败: {e}")
        
        # 使用已创建的 storage 实例初始化调度器
        try:
            logger.info("正在初始化调度器...")
            scheduler = TaskScheduler(storage)
            scheduler.start()
            logger.info("调度器初始化成功")
        except Exception as e:
            logger.error(f"初始化调度器失败: {str(e)}")
            scheduler = None
        
        if not storage.is_valid():
            logger.warning("存储初始化成功，但未登录或未配置用户")
            
        logger.info("应用初始化完成")
        return True, None
        
    except Exception as e:
        error_msg = f"应用初始化失败: {str(e)}"
        logger.error(error_msg)
        return False, error_msg

def cleanup():
    """清理资源"""
    global scheduler
    if scheduler:
        try:
            if hasattr(scheduler, 'is_running') and scheduler.is_running:
                scheduler.stop()
            scheduler = None
            logger.info("调度器已停止")
        except Exception as e:
            logger.error(f"停止调度器失败: {str(e)}")

def handle_api_error(f):
    """API错误处理装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            error_msg = f"{f.__name__} 失败: {str(e)}"
            logger.error(error_msg)
            return jsonify({'success': False, 'message': error_msg})
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    """登录处理"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not storage:
            # 对于POST请求，返回JSON响应（新前端使用API）
            return jsonify({'success': False, 'message': '系统未初始化'}), 400
            
        # 验证用户名和密码
        auth_config = storage.config.get('auth', {})
        if (username == auth_config.get('users') and 
            password == auth_config.get('password')):
            session['username'] = username
            session['login_time'] = time.time()
            
            # 返回JSON响应给新前端
            return jsonify({'success': True, 'message': '登录成功'})
        else:
            return jsonify({'success': False, 'message': '用户名或密码错误'}), 401
            
    # GET请求返回SPA的index.html，让Vue Router处理登录页面
    return send_from_directory('static', 'index.html')

@app.route('/logout')
def logout():
    """登出处理"""
    session.clear()
    # 返回JSON响应给新前端，而不是重定向
    return jsonify({'success': True, 'message': '登出成功'})

@app.route('/')
@login_required
def index():
    """首页 - 返回新前端SPA"""
    return send_from_directory('static', 'index.html')

@app.route('/api/tasks', methods=['GET'])
@login_required
@handle_api_error
def get_tasks():
    """获取所有任务"""
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
    tasks = storage.list_tasks()
    # 按 order 排序，没有 order 的排在最后
    tasks.sort(key=lambda x: x.get('order', float('inf')))
    return jsonify({'success': True, 'tasks': tasks})

@app.route('/api/tasks/<int:task_id>/status', methods=['GET'])
@login_required
@handle_api_error
def get_task_status(task_id):
    """获取单个任务状态"""
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
    tasks = storage.list_tasks()
    # 按 order 排序，确保 task_id 对应正确的任务
    tasks.sort(key=lambda x: x.get('order', float('inf')))
    if 0 <= task_id < len(tasks):
        return jsonify({'success': True, 'status': tasks[task_id]})
    return jsonify({'success': False, 'message': '任务不存在'})

@app.route('/api/tasks/running', methods=['GET'])
@login_required
@handle_api_error
def get_running_tasks():
    """获取正在运行的任务"""
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
    tasks = storage.list_tasks()
    # 按 order 排序后再筛选运行中的任务
    tasks.sort(key=lambda x: x.get('order', float('inf')))
    running_tasks = [task for task in tasks if task.get('status') == 'running']
    return jsonify({'success': True, 'tasks': running_tasks})

@app.route('/api/task/add', methods=['POST'])
@login_required
@handle_api_error
def add_task():
    """添加任务"""
    data = request.get_json()
    url = data.get('url', '').strip()
    save_dir = data.get('save_dir', '').strip()
    pwd = data.get('pwd', '').strip()
    name = data.get('name', '').strip()
    cron = data.get('cron', '').strip()
    category = data.get('category', '').strip()
    regex_pattern = data.get('regex_pattern', '').strip()
    regex_replace = data.get('regex_replace', '').strip()
    
    if not url or not save_dir:
        return jsonify({'success': False, 'message': '分享链接和保存目录不能为空'})
    
    # 移除URL中的hash部分
    if '#' in url:
        url = url.split('#')[0]
    
    # 处理第二种格式: https://pan.baidu.com/share/init?surl=xxx&pwd=xxx
    if '/share/init?' in url and 'surl=' in url:
        import urllib.parse
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        
        # 提取surl和pwd参数
        surl = params.get('surl', [''])[0]
        if not pwd and 'pwd' in params:
            pwd = params.get('pwd', [''])[0]
        
        # 转换为第一种格式
        if surl:
            url = f"https://pan.baidu.com/s/{surl}"
    
    # 处理第一种格式中的密码部分
    if '?pwd=' in url:
        url, pwd = url.split('?pwd=')
        pwd = pwd.strip()
        
    try:
        # 添加任务 - storage.py 内部会处理调度器更新
        if storage.add_task(url, save_dir, pwd, name, cron, category, regex_pattern, regex_replace):
            # 修改: 保存扩展字段（对比路径 / 转存文件夹 / 是否保存文件夹）
            try:
                tasks = storage.config.get('baidu', {}).get('tasks', [])
                target = None
                for t in tasks:
                    if t.get('url') == url:
                        target = t
                if target is not None:
                    target['compare_path'] = data.get('compare_path', '').strip()
                    target['transfer_folders'] = data.get('transfer_folders') or []
                    target['keep_folder'] = bool(data.get('keep_folder'))
                    target['include_subdirs'] = bool(data.get('include_subdirs', True))
                    storage._save_config()
            except Exception as e:
                logger.error(f"保存任务扩展字段失败: {str(e)}")
            
            return jsonify({'success': True, 'message': '添加任务成功'})
            
    except Exception as e:
        logger.error(f"添加任务失败: {str(e)}")
        return jsonify({'success': False, 'message': f'添加任务失败: {str(e)}'})

@app.route('/api/task/update', methods=['POST'])
@login_required
@handle_api_error
def update_task():
    """更新任务"""
    data = request.get_json()
    try:
        task_id = int(data.get('task_id', -1))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': '无效的任务ID'})
    
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
        
    tasks = storage.list_tasks()
    if not tasks:
        return jsonify({'success': False, 'message': '任务列表为空'})
        
    # 按order排序
    tasks.sort(key=lambda x: x.get('order', float('inf')))
    
    # 查找对应order的任务
    task_order = task_id + 1  # task_id 是从0开始的索引，而 order 是从1开始的
    task = None
    for t in tasks:
        if t.get('order') == task_order:
            task = t
            break
            
    if not task:
        return jsonify({'success': False, 'message': f'未找到任务(order={task_order})'})
    
    # 获取新的URL和密码
    new_url = data.get('url', '').strip()
    new_pwd = data.get('pwd', '').strip()
    
    # 如果URL中包含密码部分，提取出来
    if '?pwd=' in new_url:
        new_url, new_pwd = new_url.split('?pwd=')
        new_url = new_url.strip()
        new_pwd = new_pwd.strip()
    
    # 创建更新数据对象，保持原有状态
    update_data = {
        'url': new_url,
        'save_dir': data.get('save_dir', '').strip(),
        'pwd': new_pwd,  # 使用处理后的新密码
        'name': data.get('name', '').strip(),
        'cron': data.get('cron', '').strip(),
        'category': data.get('category', '').strip(),
        'compare_path': data.get('compare_path', '').strip(),
        'transfer_folders': data.get('transfer_folders') or [],
        'keep_folder': bool(data.get('keep_folder')),
        'regex_pattern': data.get('regex_pattern', '').strip(),
        'regex_replace': data.get('regex_replace', '').strip(),
        'order': task_order,  # 保持原有的order
        'status': task.get('status', 'normal'),  # 保持原有的状态
        'message': task.get('message', ''),  # 保持原有的消息
        'last_update': int(time.time())  # 添加更新时间戳
    }
    
    # 验证必填字段
    if not update_data['url']:
        return jsonify({'success': False, 'message': '分享链接不能为空'})
    if not update_data['save_dir']:
        return jsonify({'success': False, 'message': '保存目录不能为空'})
        
    try:
        # 更新任务
        success = storage.update_task_by_order(task_order, update_data)
        if not success:
            return jsonify({'success': False, 'message': '更新任务失败'})
        
        
        return jsonify({
            'success': True, 
            'message': '更新任务成功',
            'task': update_data
        })
    except Exception as e:
        logger.error(f"更新任务失败: {str(e)}")
        return jsonify({'success': False, 'message': f'更新任务失败: {str(e)}'})

@app.route('/api/share/info', methods=['POST'])
@login_required
@handle_api_error
def get_share_info():
    """获取分享链接信息"""
    data = request.get_json()
    url = data.get('url', '').strip()
    pwd = data.get('pwd', '').strip()
    
    if not url:
        return jsonify({'success': False, 'message': '分享链接不能为空'})
    
    try:
        # 移除URL中的hash部分
        url = url.split('#')[0]
        
        # 处理第二种格式: https://pan.baidu.com/share/init?surl=xxx&pwd=xxx
        if '/share/init?' in url and 'surl=' in url:
            import urllib.parse
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            
            # 提取surl和pwd参数
            surl = params.get('surl', [''])[0]
            if not pwd and 'pwd' in params:
                pwd = params.get('pwd', [''])[0]
            
            # 转换为第一种格式
            if surl:
                url = f"https://pan.baidu.com/s/{surl}"
        
        # 处理第一种格式中的密码部分
        if '?pwd=' in url:
            url, extracted_pwd = url.split('?pwd=')
            pwd = extracted_pwd.strip()
        
        # 获取分享文件信息
        result = storage.get_share_folder_name(url, pwd)
        
        if result['success']:
            return jsonify({
                'success': True,
                'folder_name': result['folder_name'],
                'message': '获取文件夹名称成功'
            })
        else:
            return jsonify({
                'success': False,
                'message': result.get('error', '获取分享信息失败')
            })
            
    except Exception as e:
        logger.error(f"获取分享信息失败: {str(e)}")
        return jsonify({'success': False, 'message': f'获取分享信息失败: {str(e)}'})

@app.route('/api/task/delete', methods=['POST'])
@login_required
@handle_api_error
def delete_task():
    """删除任务"""
    data = request.get_json()
    task_id = data.get('task_id')
    
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
    tasks = storage.list_tasks()
    # 按 order 排序，确保 task_id 对应正确的任务
    tasks.sort(key=lambda x: x.get('order', float('inf')))
    if 0 <= task_id < len(tasks):
        task = tasks[task_id]
        task_order = task.get('order', task_id + 1)
        if storage.remove_task_by_order(task_order):
            # 删除成功后重新整理剩余任务的顺序
            storage._update_task_orders()
            return jsonify({'success': True, 'message': '删除任务成功'})
    return jsonify({'success': False, 'message': '任务不存在'})


@app.route('/api/task/move', methods=['POST'])
@login_required
@handle_api_error
def move_task():
    """移动任务位置"""
    data = request.get_json()
    task_id = data.get('task_id')
    new_index = data.get('new_index')
    
    if task_id is None or new_index is None:
        return jsonify({'success': False, 'message': '缺少必要参数'})
    
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
    
    try:
        tasks = storage.list_tasks()
        # 按 order 排序
        tasks.sort(key=lambda x: x.get('order', float('inf')))
        
        if not (0 <= task_id < len(tasks)) or not (0 <= new_index < len(tasks)):
            return jsonify({'success': False, 'message': '任务ID或位置无效'})
        
        # 移动任务
        task = tasks.pop(task_id)
        tasks.insert(new_index, task)
        
        # 更新所有任务的order
        for i, task in enumerate(tasks):
            task['order'] = i + 1
            storage.update_task(i, task)
        
        return jsonify({'success': True, 'message': '任务位置已更新'})
        
    except Exception as e:
        logger.error(f"移动任务失败: {str(e)}")
        return jsonify({'success': False, 'message': f'移动任务失败: {str(e)}'})


def _resolve_request_task(task_ref=None, task_id=None, tasks=None):
    """按 task_uid/order/url 解析请求中的任务，兼容旧的 task_id。"""
    if not storage:
        return None

    task = storage.resolve_task(task_ref)
    if task is not None:
        return task

    if task_id is None:
        return None

    current_tasks = list(tasks if tasks is not None else storage.list_tasks())
    current_tasks.sort(key=lambda x: x.get('order', float('inf')))
    if 0 <= task_id < len(current_tasks):
        fallback_task = current_tasks[task_id]
        return storage.resolve_task(fallback_task) or fallback_task

    return None


@app.route('/api/task/execute', methods=['POST'])
@login_required
@handle_api_error
def execute_task():
    """执行指定的任务"""
    data = request.get_json() or {}
    task_uid = data.get('task_uid')

    # 获取并验证task_id（兼容旧前端只传索引）
    task_id = data.get('task_id')
    if task_uid is None:
        try:
            task_id = int(task_id)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'message': '无效的任务ID'})
    elif task_id is not None:
        try:
            task_id = int(task_id)
        except (TypeError, ValueError):
            task_id = None

    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})

    # 获取任务列表
    tasks = storage.list_tasks()
    if not tasks:
        return jsonify({'success': False, 'message': '任务列表为空'})

    # 按稳定标识优先解析，避免任务重排后串线
    task = _resolve_request_task(task_ref=task_uid, task_id=task_id, tasks=tasks)
    if not task:
        if task_uid:
            return jsonify({'success': False, 'message': f'未找到任务(task_uid={task_uid})'})
        return jsonify({'success': False, 'message': f'任务索引超出范围(task_id={task_id})'})

    task_order = task.get('order')
    task_uid = task.get('task_uid')
    task_url = task.get('url')

    if not task_order:
        return jsonify({'success': False, 'message': f'任务order不存在(task_uid={task_uid or task_id})'})

    task_name = task.get('name') or f'任务{task_order}'

    _ensure_task_runtime_state()
    _remember_task_stream(task_uid, task_order)

    # 更新为运行状态
    storage.update_task_status_by_order(task_order, 'running', '正在执行任务')
    _publish_task_status(task_order, task_uid)

    # 清理旧的任务日志，避免显示历史日志
    app.task_logs[task_order] = []
    _append_task_log(task_order, f'开始执行任务: {task_name}', task_uid=task_uid)
    
    # 立即返回响应，然后异步执行任务
    def execute_task_async():
        """异步执行任务"""
        try:
            _append_task_log(task_order, '任务线程已启动，正在准备执行...', task_uid=task_uid)
            
            # 重新获取最新的任务数据，优先使用稳定标识，避免任务重排后串任务
            latest_task = storage.resolve_task(task_uid, order=task_order, url=task_url)

            if not latest_task:
                logger.error(f'任务已不存在(order={task_order})')
                storage.update_task_status_by_order(task_order, 'error', '任务已不存在')
                _publish_task_status(task_order, task_uid)
                _append_task_log(task_order, '任务已不存在，执行失败', level='ERROR', task_uid=task_uid)
                _publish_task_completed(task_order, task_uid)
                return

            # 使用最新任务数据
            current_task = latest_task

            _append_task_log(
                task_order,
                f'开始处理任务: {current_task.get("name", "未命名任务")}',
                task_uid=task_uid
            )

            def progress_callback(status, message):
                """实时记录任务执行进度"""
                level = status.upper() if status in ['error', 'info', 'warning'] else 'INFO'
                _append_task_log(task_order, message, level=level, task_uid=task_uid)

                # 同时更新任务状态消息
                if status != 'error':
                    storage.update_task_status_by_order(task_order, 'running', message)
                    _publish_task_status(task_order, task_uid)

                # 记录到系统日志
                if status == 'error':
                    logger.error(f"[任务{task_order}] {message}")
                else:
                    logger.info(f"[任务{task_order}] {message}")

            result = storage.transfer_share(
                current_task['url'],
                current_task.get('pwd'),
                None,
                current_task.get('save_dir'),
                progress_callback,
                current_task  # 传入完整的任务配置
            )

            # 修改: 记录本次运行的真实结果（执行历史必须用本次数据，不能读任务上残留的旧值）
            try:
                _st = storage._transfer_stats.setdefault(task_order, {})
                _st['transferred_files'] = list(result.get('transferred_files') or [])
                _st['save_dir'] = current_task.get('save_dir') or ''
                _st['compare_dir'] = current_task.get('compare_path') or current_task.get('save_dir') or ''
            except Exception as _st_err:
                logger.debug(f"记录本次转存结果失败: {_st_err}")

            if result.get('success'):
                transferred_files = result.get('transferred_files', [])
                if transferred_files:
                    task_results = {
                        'success': [current_task],
                        'failed': [],
                        'transferred_files': {current_task['url']: transferred_files}
                    }
                    
                    try:
                        # 发送转存成功通知
                        notify_send('百度自动追更', generate_transfer_notification(task_results))
                    except Exception as e:
                        logger.error(f"发送转存成功通知失败: {str(e)}")
                    
                    storage.update_task_status_by_order(
                        task_order,
                        'normal',
                        '转存成功',
                        transferred_files=transferred_files
                    )
                    _publish_task_status(task_order, task_uid)
                    _append_task_log(task_order, '任务执行完成', task_uid=task_uid)
                    _publish_task_completed(task_order, task_uid)
                else:
                    storage.update_task_status_by_order(task_order, 'normal', '没有新文件需要转存')
                    _publish_task_status(task_order, task_uid)
                    _append_task_log(task_order, '没有新文件需要转存', task_uid=task_uid)
                    _publish_task_completed(task_order, task_uid)
            else:
                error_msg = result.get('error', '转存失败')
                storage.update_task_status_by_order(task_order, 'error', error_msg)
                _publish_task_status(task_order, task_uid)
                _append_task_log(task_order, f'任务执行失败: {error_msg}', level='ERROR', task_uid=task_uid)
                _publish_task_completed(task_order, task_uid)

        except Exception as e:
            error_msg = str(e)
            # 使用存储模块的错误解析功能
            parsed_error = storage._parse_share_error(error_msg) if storage else error_msg
            
            is_share_forbidden = "error_code: 115" in error_msg
            
            if is_share_forbidden:
                try:
                    storage.remove_task_by_order(task_order)
                    storage._update_task_orders()
                except Exception as del_err:
                    pass  # 删除失效任务失败，继续执行
            
            storage.update_task_status_by_order(task_order, 'error', parsed_error)
            _publish_task_status(task_order, task_uid)
            _append_task_log(task_order, f'任务执行异常: {parsed_error}', level='ERROR', task_uid=task_uid)
            _publish_task_completed(task_order, task_uid)

    # 启动异步任务并立即返回
    def _execute_task_async_with_history():
        """修改: 手动执行也记录转存历史（SQLite）"""
        from history_db import record_task_history
        start_time = time.strftime('%Y-%m-%d %H:%M:%S')
        try:
            execute_task_async()
        finally:
            try:
                current = storage.resolve_task(task_uid, order=task_order, url=task_url) or {}
                stats = {}
                if hasattr(storage, '_transfer_stats'):
                    stats = storage._transfer_stats.pop(task_order, {}) or {}
                raw_logs = (app.task_logs.get(task_order) or [])[-300:]
                logs = [f"[{e.get('level', 'INFO')}] {e.get('message', '')}" for e in raw_logs]
                # 修改: 只认本次运行真实转存的文件（0 个就是 0 个，不用任务里可能残留的旧数据）
                transferred = list(stats.get('transferred_files') or [])
                record = {
                    'start_time': start_time,
                    'end_time': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'success': current.get('status') != 'error',
                    'message': current.get('message', ''),
                    'file_count': len(transferred),
                    'transferred_files': transferred,
                    'excluded_files': stats.get('excluded_files_list') or list(current.get('exclude_files') or []),
                    'regex_passed_files': list(stats.get('regex_passed_files') or []),
                    'regex_matched_files': list(stats.get('regex_matched_files') or []),
                    'save_dir': stats.get('save_dir') or current.get('save_dir') or '',
                    'compare_dir': stats.get('compare_dir') or current.get('compare_path') or current.get('save_dir') or '',
                    'transfer_folders': list(stats.get('transfer_folders') or (current.get('transfer_folders') or [])),
                    'include_subdirs': stats.get('include_subdirs', current.get('include_subdirs', True)),
                    'keep_folder': stats.get('keep_folder', bool(current.get('keep_folder'))),
                    'regex_pattern': stats.get('regex_pattern') or current.get('regex_pattern') or '',
                    'total_count': stats.get('total', 0),
                    'excluded_count': stats.get('excluded', 0),
                    'filtered_count': stats.get('filtered', 0),
                    'md5_skipped': stats.get('md5_skipped', 0),
                    'logs': logs,
                }
                record_task_history(task_uid=task_uid, order=task_order, record=record, keep=10)
                # 修改: 转存到新文件后自动触发 QMediaSync 刮削
                try:
                    _qms_msg = qms_trigger_after_transfer(storage, current, len(transferred))
                    if _qms_msg:
                        logger.info(f"QMediaSync: {_qms_msg}")
                        _append_task_log(task_order, f'QMediaSync: {_qms_msg}', task_uid=task_uid)
                except Exception as _qms_err:
                    logger.error(f"触发 QMediaSync 失败: {_qms_err}")
            except Exception as rec_err:
                logger.error(f"记录转存历史失败(手动执行): {rec_err}")

    thread = threading.Thread(target=_execute_task_async_with_history)
    thread.daemon = True  # 设置为守护线程
    thread.start()
    
    # 立即返回响应，表示任务已开始执行
    return jsonify({
        'success': True,
        'message': '任务已开始执行',
        'task_uid': task_uid,
        'task_order': task_order,
        'stream_url': f'/api/task/stream/{task_uid}' if task_uid else None
    })

@app.route('/api/users', methods=['GET'])
@login_required
@handle_api_error
def get_users():
    """获取所有用户"""
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
    
    users = storage.list_users()
    current_username = storage.config.get('baidu', {}).get('current_user')
    
    # 标记当前用户
    for user in users:
        user['is_current'] = user.get('username') == current_username
    
    return jsonify({
        'success': True, 
        'users': users,
        'current_user': current_username
    })

@app.route('/api/user/add', methods=['POST'])
@login_required
@handle_api_error
def add_user():
    """添加用户"""
    data = request.get_json()
    username = data.get('username', '').strip()
    cookies = data.get('cookies', '').strip()
    
    if not username or not cookies:
        return jsonify({'success': False, 'message': '用户名和cookies不能为空'})
        
    if storage.add_user_from_cookies(cookies, username):
        init_app()
        return jsonify({'success': True, 'message': '添加用户成功'})
    return jsonify({'success': False, 'message': '添加用户失败'})

@app.route('/api/user/switch', methods=['POST'])
@login_required
@handle_api_error
def switch_user():
    """切换用户"""
    data = request.get_json()
    username = data.get('username')
    
    if not username:
        return jsonify({'success': False, 'message': '用户名不能为空'})
        
    try:
        if storage.switch_user(username):
            # 获取完整的用户信息
            user = storage.get_user(username)
            if not user:
                return jsonify({'success': False, 'message': f'用户 {username} 不存在'})
            
            # 重新初始化应用
            init_app()
            
            # 切换用户后立即获取用户配额信息
            try:
                if storage and hasattr(storage, 'get_user_info'):
                    user_info = storage.get_user_info()
                    if user_info and 'quota' in user_info:
                        quota = user_info['quota']
                        total_gb = round(quota.get('total', 0) / (1024**3), 2)
                        used_gb = round(quota.get('used', 0) / (1024**3), 2)
                        logger.info(f"已切换到用户: {username}，网盘总空间: {total_gb}GB, 已使用: {used_gb}GB")
                        
                        # 将配额信息添加到用户数据中
                        user['quota'] = {
                            'total': quota.get('total', 0),
                            'used': quota.get('used', 0),
                            'total_gb': total_gb,
                            'used_gb': used_gb,
                            'percent': round(quota.get('used', 0) / quota.get('total', 1) * 100, 2) if quota.get('total', 0) > 0 else 0
                        }
            except Exception as e:
                logger.error(f"切换用户后获取配额信息失败: {str(e)}")
            
            # 返回更新后的状态
            return jsonify({
                'success': True, 
                'message': '切换用户成功',
                'current_user': user,
                'login_status': storage.is_valid()
            })
        return jsonify({'success': False, 'message': '切换用户失败'})
    except Exception as e:
        logger.error(f"切换用户失败: {str(e)}")
        return jsonify({'success': False, 'message': f'切换用户失败: {str(e)}'})

@app.route('/api/user/delete', methods=['POST'])
@login_required
@handle_api_error
def delete_user():
    """删除用户"""
    data = request.get_json()
    username = data.get('username')
    
    if not username:
        return jsonify({'success': False, 'message': '用户名不能为空'})
        
    current_user = storage.config['baidu'].get('current_user')
    if current_user == username:
        return jsonify({'success': False, 'message': '不能删除当前使用的用户'})
        
    if storage.remove_user(username):
        return jsonify({'success': True, 'message': '删除用户成功'})
    return jsonify({'success': False, 'message': '删除用户失败'})

@app.route('/api/user/update', methods=['POST'])
@login_required
@handle_api_error
def update_user():
    """更新用户信息"""
    data = request.get_json()
    original_username = data.get('original_username', '').strip()
    username = data.get('username', '').strip()
    cookies = data.get('cookies', '').strip()
    
    if not original_username or not username or not cookies:
        return jsonify({'success': False, 'message': '原始用户名、新用户名和cookies不能为空'})
    
    # 如果是重命名用户
    if original_username != username:
        # 检查新用户名是否已存在
        if username in storage.config['baidu']['users']:
            return jsonify({'success': False, 'message': f'用户名 {username} 已存在'})
        
        # 获取原用户信息
        user_info = storage.get_user(original_username)
        if not user_info:
            return jsonify({'success': False, 'message': f'用户 {original_username} 不存在'})
        
        # 检查cookies是否发生变化
        cookies_changed = user_info.get('cookies', '') != cookies
        
        # 如果仅重命名，无需验证cookies
        if not cookies_changed:
            # 复制用户信息到新用户名
            storage.config['baidu']['users'][username] = storage.config['baidu']['users'][original_username].copy()
            
            # 如果是当前用户，更新当前用户名
            if storage.config['baidu']['current_user'] == original_username:
                storage.config['baidu']['current_user'] = username
            
            # 删除原用户
            storage.remove_user(original_username)
            
            # 保存配置
            storage._save_config()
            
            return jsonify({'success': True, 'message': '用户更新成功'})
        else:
            # 创建新用户
            if storage.add_user_from_cookies(cookies, username):
                # 如果是当前用户，更新当前用户名
                if storage.config['baidu']['current_user'] == original_username:
                    storage.switch_user(username)
                
                # 删除原用户
                storage.remove_user(original_username)
                
                return jsonify({'success': True, 'message': '用户更新成功'})
            else:
                return jsonify({'success': False, 'message': '用户更新失败，cookies可能无效'})
    else:
        # 仅更新cookies
        if storage.update_user(username, cookies):
            init_app()
            return jsonify({'success': True, 'message': '用户更新成功'})
        return jsonify({'success': False, 'message': '用户更新失败，cookies可能无效'})

@app.route('/api/user/<username>/cookies', methods=['GET'])
@login_required
@handle_api_error
def get_user_cookies(username):
    """获取用户cookies"""
    user_info = storage.get_user(username)
    if not user_info:
        return jsonify({'success': False, 'message': f'用户 {username} 不存在'})
    
    return jsonify({'success': True, 'cookies': user_info.get('cookies', '')})

@app.route('/api/user/quota', methods=['GET'])
@login_required
@handle_api_error
def get_user_quota():
    """获取当前用户的网盘配额信息"""
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
        
    try:
        # 获取用户信息，包括配额
        user_info = storage.get_user_info()
        if not user_info or 'quota' not in user_info:
            return jsonify({'success': False, 'message': '无法获取用户配额信息'})
            
        # 提取配额信息
        quota = user_info['quota']
        total = quota.get('total', 0)
        used = quota.get('used', 0)
        
        # 转换为GB并保留2位小数
        total_gb = round(total / (1024**3), 2)
        used_gb = round(used / (1024**3), 2)
        
        return jsonify({
            'success': True, 
            'quota': {
                'total': total,
                'used': used,
                'total_gb': total_gb,
                'used_gb': used_gb,
                'percent': round(used / total * 100, 2) if total > 0 else 0
            }
        })
    except Exception as e:
        logger.error(f"获取用户配额失败: {str(e)}")
        return jsonify({'success': False, 'message': f'获取用户配额失败: {str(e)}'})

@app.route('/api/config', methods=['GET'])
@login_required
@handle_api_error
def get_config():
    """获取配置"""
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
    
    # 获取当前用户的完整信息
    current_user = None
    current_username = storage.config.get('baidu', {}).get('current_user')
    if current_username:
        current_user = storage.get_user(current_username)
    
    auth_config = dict(storage.config.get('auth', {}))
    auth_config.pop('password', None)

    config = {
        'cron': storage.config.get('cron', {}),
        'notify': storage.config.get('notify', {}),
        'scheduler': storage.config.get('scheduler', {}),
        'quota_alert': storage.config.get('quota_alert', {}),
        'share': storage.config.get('share', {}),
        'file_operations': storage.config.get('file_operations', {}),
        'auth': auth_config,
        'baidu': {
            'current_user': current_user  # 返回完整的用户信息
        }
    }
    return jsonify({'success': True, 'config': config})

def format_webhook_body(webhook_body):
    """格式化WEBHOOK_BODY字段，将简化格式转换为标准多行格式"""
    if not webhook_body or isinstance(webhook_body, dict):
        return webhook_body
    
    # 检测是否是简化格式（如：title: "$title"content: "$content"source: "我的项目"）
    import re
    simple_format = re.match(r'title:\s*"([^"]*)"content:\s*"([^"]*)"source:\s*"([^"]*)"', webhook_body)
    
    if simple_format:
        # 转换为标准多行格式
        title = simple_format.group(1)
        content = simple_format.group(2)
        source = simple_format.group(3)
        return f'title: "{title}"\ncontent: "{content}"\nsource: "{source}"'
    
    # 如果不是简化格式，直接返回原始值
    return webhook_body

@app.route('/api/config/update', methods=['POST'])
@login_required
@handle_api_error
def update_config():
    """更新配置"""
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
        
    data = request.get_json()
    
    # 处理通知配置：完全替换notify配置，清除旧字段
    if 'notify' in data:
        # 格式化WEBHOOK_BODY字段
        if 'direct_fields' in data['notify'] and 'WEBHOOK_BODY' in data['notify']['direct_fields']:
            data['notify']['direct_fields']['WEBHOOK_BODY'] = format_webhook_body(data['notify']['direct_fields']['WEBHOOK_BODY'])
        
        # 完全替换整个notify对象，而不是合并
        # 这样可以清除旧的字段（push_plus_token、webhook_url等）
        storage.config['notify'] = {
            'enabled': data['notify'].get('enabled', False),
            'notification_delay': data['notify'].get('notification_delay', 30),
            'direct_fields': data['notify'].get('direct_fields', {})
        }
        
        # 从data中移除notify，避免后续update重复处理
        del data['notify']
    
    # 处理认证配置：保留现有密码，避免前端读取或误覆盖
    if 'auth' in data:
        current_auth = dict(storage.config.get('auth', {}))
        incoming_auth = data.get('auth') or {}
        merged_auth = {
            'users': incoming_auth.get('users', current_auth.get('users', '')),
            'session_timeout': incoming_auth.get('session_timeout', current_auth.get('session_timeout', 3600))
        }

        new_password = incoming_auth.get('password')
        if isinstance(new_password, str) and new_password.strip():
            merged_auth['password'] = new_password
        elif 'password' in current_auth:
            merged_auth['password'] = current_auth['password']

        storage.config['auth'] = merged_auth
        del data['auth']

    # 更新其他配置
    storage.config.update(data)
    storage._save_config()
    
    # 处理调度器配置更新
    if scheduler and ('cron' in data or 'scheduler' in data):
        try:
            was_running = scheduler.is_running
            if was_running:
                scheduler.stop()
                logger.info('调度器已停止')
            
            # 重新初始化调度器
            scheduler._init_scheduler()
            
            # 如果之前在运行，或者配置中指定了自动启动，则启动调度器
            should_start = was_running or data.get('cron', {}).get('auto_install', True)
            if should_start and not scheduler.is_running:
                scheduler.start()
                logger.info('调度器已重新启动')
            
            logger.info('调度器配置已更新')
        except Exception as e:
            logger.error(f'更新调度器配置失败: {str(e)}')
            return jsonify({
                'success': False,
                'message': f'配置已保存，但更新调度器失败: {str(e)}'
            })
    
    # 处理通知配置更新
    if scheduler and 'notify' in data:
        try:
            # 重新初始化通知配置
            scheduler._init_notify()
            logger.info('通知配置已更新')
        except Exception as e:
            logger.error(f'更新通知配置失败: {str(e)}')
            return jsonify({
                'success': False,
                'message': f'配置已保存，但更新通知配置失败: {str(e)}'
            })
    
    return jsonify({'success': True, 'message': '更新配置成功'})

@app.route('/api/notify/test', methods=['POST'])
@login_required
@handle_api_error
def test_notify():
    """测试通知功能"""
    if not storage or not storage.config.get('notify', {}).get('enabled'):
        return jsonify({'success': False, 'message': '通知功能未启用'})
        
    try:
        # 确保通知配置正确加载
        notify_config = storage.config.get('notify', {})
        if notify_config and notify_config.get('enabled'):
            # 重新应用通知配置
            from notify import push_config, send as notify_send
            
            # 应用直接字段配置
            if 'direct_fields' in notify_config:
                for key, value in notify_config.get('direct_fields', {}).items():
                    push_config[key] = value
            # 兼容旧版配置
            elif 'channels' in notify_config and 'pushplus' in notify_config['channels']:
                pushplus = notify_config['channels']['pushplus']
                if 'token' in pushplus:
                    push_config['PUSH_PLUS_TOKEN'] = pushplus['token']
                if 'topic' in pushplus:
                    push_config['PUSH_PLUS_USER'] = pushplus['topic']
            
            # 应用自定义字段
            if 'custom_fields' in notify_config:
                for key, value in notify_config.get('custom_fields', {}).items():
                    push_config[key] = value
            
            # 使用时间戳确保每次内容不同，避免重复内容限制
            import time
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            
            # 使用notify_send发送通知
            notify_send('百度网盘自动追更', f'这是一条测试通知,如果你收到了这条消息,说明通知配置正确! 测试时间: {timestamp}')
            
            return jsonify({'success': True, 'message': '测试通知已发送'})
        else:
            return jsonify({'success': False, 'message': '通知功能未启用'})
    except Exception as e:
        logger.error(f"发送测试通知失败: {str(e)}")
        return jsonify({'success': False, 'message': f'发送测试通知失败: {str(e)}'})


@app.route('/api/task/toggle', methods=['POST'])
@login_required
@handle_api_error
def toggle_task_enabled():
    """启用/停用任务：停用后定时任务不再自动执行"""
    data = request.get_json() or {}
    try:
        task_id = int(data.get('task_id', -1))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': '无效的任务ID'})

    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})

    tasks = storage.config.get('baidu', {}).get('tasks', [])
    task_order = task_id + 1  # 前端task_id从0开始，order从1开始
    task = next((t for t in tasks if t.get('order') == task_order), None)
    if not task:
        return jsonify({'success': False, 'message': f'未找到任务(order={task_order})'})

    task['enabled'] = not task.get('enabled', True)
    storage._save_config()
    return jsonify({
        'success': True,
        'enabled': task['enabled'],
        'message': '任务已启用' if task['enabled'] else '任务已停用'
    })

@app.route('/api/tasks/execute-all', methods=['POST'])
@login_required
@handle_api_error
def execute_all_tasks():
    """批量执行任务"""
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
        
    data = request.get_json() or {}
    task_ids = data.get('task_ids', [])
    task_uids = data.get('task_uids', [])

    if not task_ids and not task_uids:
        return jsonify({'success': False, 'message': '没有指定要执行的任务'})

    # 获取并按order排序的任务列表
    tasks = storage.list_tasks()
    if not tasks:
        return jsonify({'success': False, 'message': '任务列表为空'})

    tasks = sorted(tasks, key=lambda x: x.get('order', float('inf')))

    selected_tasks = []
    seen_uids = set()

    for task_uid in task_uids:
        task = _resolve_request_task(task_ref=task_uid, tasks=tasks)
        if task and task.get('task_uid') not in seen_uids:
            selected_tasks.append(task)
            if task.get('task_uid'):
                seen_uids.add(task.get('task_uid'))

    for raw_task_id in task_ids:
        try:
            task_id = int(raw_task_id)
        except (TypeError, ValueError):
            continue

        task = _resolve_request_task(task_id=task_id, tasks=tasks)
        if not task:
            continue

        task_uid = task.get('task_uid')
        if task_uid and task_uid in seen_uids:
            continue

        selected_tasks.append(task)
        if task_uid:
            seen_uids.add(task_uid)

    if not selected_tasks:
        return jsonify({'success': False, 'message': '未找到指定的任务'})
    
    results = {
        'success': [],
        'skipped': [],
        'failed': [],
        'transferred_files': {}
    }
    
    for selected_task in selected_tasks:
        task = storage.resolve_task(selected_task)
        if not task:
            task = storage.resolve_task(
                task_uid=selected_task.get('task_uid'),
                order=selected_task.get('order'),
                url=selected_task.get('url')
            )

        if not task:
            logger.warning(f"批量执行时任务已不存在: {selected_task}")
            results['failed'].append(selected_task)
            continue

        task_order = task.get('order')
        if not task_order:
            continue

        try:
            result = storage.transfer_share(
                task['url'],
                task.get('pwd'),
                None,
                task.get('save_dir'),
                None,  # progress_callback
                task   # task_config
            )
            
            if result.get('success'):
                if result.get('skipped'):
                    results['skipped'].append(task)
                    storage.update_task_status_by_order(task_order, 'skipped', '没有新文件需要转存')
                else:
                    transferred_files = result.get('transferred_files', [])
                    if transferred_files:
                        results['success'].append(task)
                        results['transferred_files'][task['url']] = transferred_files
                        storage.update_task_status_by_order(
                            task_order, 
                            'success',
                            '转存成功',
                            transferred_files=transferred_files
                        )
                    else:
                        results['skipped'].append(task)
                        storage.update_task_status_by_order(task_order, 'skipped', '没有新文件需要转存')
            else:
                error_msg = result.get('error', '转存失败')
                results['failed'].append(task)
                storage.update_task_status_by_order(task_order, 'failed', error_msg)
                
        except Exception as e:
            error_msg = str(e)
            if "error_code: 115" in error_msg:
                error_msg = "该分享链接已失效（文件禁止分享）"
                try:
                    storage.remove_task_by_order(task_order)
                except Exception as del_err:
                    logger.error(f"删除失效任务失败: {str(del_err)}")
            results['failed'].append(task)
            storage.update_task_status_by_order(task_order, 'failed', error_msg)
    
    # 发送通知
    if results['success'] or results['failed']:
        try:
            notification_content = generate_transfer_notification(results)
            notify_send("百度网盘自动追更", notification_content)
        except Exception as e:
            logger.error(f"发送通知失败: {str(e)}")
    
    return jsonify({
        'success': True,
        'message': f'批量执行完成，成功: {len(results["success"])}，跳过: {len(results["skipped"])}，失败: {len(results["failed"])}',
        'results': results
    })

@app.route('/api/categories', methods=['GET'])
@login_required
@handle_api_error
def get_categories():
    """获取所有任务分类"""
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
    categories = storage.get_task_categories()
    return jsonify({'success': True, 'categories': categories})

@app.route('/api/tasks/category/<category>', methods=['GET'])
@login_required
@handle_api_error
def get_tasks_by_category(category):
    """获取指定分类的任务"""
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
        
    if category == 'uncategorized':
        tasks = storage.get_tasks_by_category(None)
    else:
        tasks = storage.get_tasks_by_category(category)
    
    # 按 order 排序
    tasks.sort(key=lambda x: x.get('order', float('inf')))
    return jsonify({'success': True, 'tasks': tasks})

@app.route('/api/notify/fields', methods=['POST'])
@login_required
@handle_api_error
def add_notify_field():
    """添加自定义通知字段"""
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
        
    data = request.get_json()
    field_name = data.get('name', '').strip()
    field_value = data.get('value', '').strip()
    
    if not field_name:
        return jsonify({'success': False, 'message': '字段名称不能为空'})
    
    # 自动格式化WEBHOOK_BODY字段
    if field_name == 'WEBHOOK_BODY':
        field_value = format_webhook_body(field_value)
        
    notify_config = storage.config.get('notify', {})
    if 'custom_fields' not in notify_config:
        notify_config['custom_fields'] = {}
        
    notify_config['custom_fields'][field_name] = field_value
    storage.config['notify'] = notify_config
    storage._save_config()
    
    return jsonify({'success': True, 'message': '添加通知字段成功'})

@app.route('/api/notify/fields', methods=['DELETE'])
@login_required
@handle_api_error
def delete_notify_field():
    """删除通知字段"""
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
        
    data = request.get_json()
    field_name = data.get('name', '').strip()
    
    if not field_name:
        return jsonify({'success': False, 'message': '字段名称不能为空'})
        
    notify_config = storage.config.get('notify', {})
    
    # 检查字段在哪个配置中
    field_deleted = False
    
    # 1. 检查direct_fields
    if 'direct_fields' in notify_config and field_name in notify_config['direct_fields']:
        del notify_config['direct_fields'][field_name]
        field_deleted = True
    
    # 2. 检查custom_fields (兼容旧版本)
    if not field_deleted and 'custom_fields' in notify_config and field_name in notify_config['custom_fields']:
        del notify_config['custom_fields'][field_name]
        field_deleted = True
    
    if not field_deleted:
        return jsonify({'success': False, 'message': f'未找到字段: {field_name}'})
    
    storage.config['notify'] = notify_config
    storage._save_config()
    
    # 重新初始化通知配置
    if scheduler:
        scheduler._init_notify()
    
    return jsonify({'success': True, 'message': f'字段 {field_name} 已删除'})

@app.route('/api/task/reorder', methods=['POST'])
@login_required
@handle_api_error
def reorder_task():
    """重新排序任务"""
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
        
    data = request.get_json()
    task_id = data.get('task_id')
    new_order = data.get('new_order')
    
    if task_id is None or new_order is None:
        return jsonify({'success': False, 'message': '任务ID和新顺序不能为空'})
    
    # 将task_id转换为order
    task_order = task_id + 1
        
    if storage.reorder_task(task_order, new_order):
        return jsonify({'success': True, 'message': '任务重排序成功'})
    return jsonify({'success': False, 'message': '任务重排序失败'})


# 静态文件路由
@app.route('/static/<path:path>')
def send_static(path):
    return send_from_directory('static', path)

# 前端资源路由 - 直接从static目录提供
@app.route('/assets/<path:path>')
def send_assets(path):
    return send_from_directory('static/assets', path)

@app.route('/favicon/<path:path>')
def send_favicon(path):
    return send_from_directory('static/favicon', path)

# SPA路由支持 - 捕获所有前端路由
@app.route('/<path:path>')
@login_required
def spa_routes(path):
    """SPA前端路由支持 - 将所有未匹配的路由返回index.html"""
    # 排除API路由、登录登出路由、静态资源等
    if path.startswith(('api/', 'login', 'logout', 'static/', 'assets/', 'favicon/')):
        return jsonify({'success': False, 'message': '接口不存在'}), 404
    
    # 返回SPA的index.html，让Vue Router处理路由
    return send_from_directory('static', 'index.html')

# 错误处理
@app.errorhandler(404)
def not_found(error):
    return jsonify({'success': False, 'message': '接口不存在'}), 404

@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({'success': False, 'message': '请求方法不允许'}), 405

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'success': False, 'message': '服务器内部错误'}), 500

def signal_handler(signum, frame):
    """处理退出信号"""
    logger.info("接收到退出信号，正在清理...")
    try:
        cleanup()
    except Exception as e:
        logger.error(f"清理过程出错: {str(e)}")
    finally:
        logger.info("清理完成，正在退出...")
        sys.exit(0)

@app.route('/api/scheduler/reload', methods=['POST'])
@login_required
def reload_scheduler():
    """重新加载调度器，使配置更改生效"""
    try:
        was_running = scheduler.is_running
        if was_running:
            scheduler.stop()
            logger.info('调度器已停止')
        
        # 重新初始化调度器
        scheduler._init_scheduler()
        
        # 如果之前在运行，则重新启动
        if was_running and not scheduler.is_running:
            scheduler.start()
            logger.info('调度器已重新启动')
        
        logger.info('调度器已重新加载')
        return jsonify({
            'success': True,
            'message': '调度器已重新加载'
        })
    except Exception as e:
        logger.error(f'重新加载调度器失败: {str(e)}')
        return jsonify({
            'success': False,
            'message': f'重新加载调度器失败: {str(e)}'
        }), 500

@app.route('/api/tasks/batch-delete', methods=['POST'])
@login_required
@handle_api_error
def batch_delete_tasks():
    """批量删除任务"""
    data = request.get_json()
    task_ids = data.get('task_ids', [])
    
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
        
    if not task_ids:
        return jsonify({'success': False, 'message': '没有指定要删除的任务'})
    
    try:
        # 将task_ids转换为orders（task_id + 1）
        task_orders = [task_id + 1 for task_id in task_ids]
        
        # 批量删除任务
        deleted_count = storage.remove_tasks(task_orders)
        
        if deleted_count > 0:
            return jsonify({
                'success': True,
                'message': f'成功删除{deleted_count}个任务'
            })
        else:
            return jsonify({
                'success': False,
                'message': '没有任务被删除'
            })
            
    except Exception as e:
        error_msg = str(e)
        logger.error(f"批量删除任务失败: {error_msg}")
        return jsonify({
            'success': False,
            'message': f'批量删除任务失败: {error_msg}'
        })

@app.route('/api/auth/login', methods=['POST'])
@handle_api_error
def api_login():
    """API登录接口"""
    username = request.json.get('username') if request.is_json else request.form.get('username')
    password = request.json.get('password') if request.is_json else request.form.get('password')
    
    if not storage:
        return jsonify({'success': False, 'message': '系统未初始化'}), 400
        
    # 验证用户名和密码
    auth_config = storage.config.get('auth', {})
    if (username == auth_config.get('users') and 
        password == auth_config.get('password')):
        session['username'] = username
        session['login_time'] = time.time()
        
        return jsonify({
            'success': True, 
            'message': '登录成功',
            'username': username
        })
    else:
        return jsonify({'success': False, 'message': '用户名或密码错误'}), 401

@app.route('/api/auth/logout', methods=['POST'])
@handle_api_error
def api_logout():
    """API登出接口"""
    session.clear()
    return jsonify({'success': True, 'message': '登出成功'})

@app.route('/api/auth/check', methods=['GET'])
@handle_api_error
def api_check_auth():
    """检查认证状态"""
    if 'username' not in session:
        return jsonify({'success': False, 'message': '未登录'}), 401
        
    # 检查会话是否过期
    auth_config = storage.config.get('auth', {}) if storage else {}
    session_timeout = auth_config.get('session_timeout', 3600)
    if time.time() - session.get('login_time', 0) > session_timeout:
        session.clear()
        return jsonify({'success': False, 'message': '会话已过期'}), 401
        
    return jsonify({
        'success': True, 
        'message': '认证有效',
        'username': session['username']
    })

@app.route('/api/auth/update', methods=['POST'])
@login_required
@handle_api_error
def update_auth():
    """更新登录凭据"""
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
        
    data = request.get_json()
    new_username = data.get('username', '').strip()
    new_password = data.get('password', '').strip()
    old_password = data.get('old_password', '').strip()
    
    if not new_username or not new_password or not old_password:
        return jsonify({'success': False, 'message': '用户名、新密码和旧密码都不能为空'})
    
    # 验证旧密码
    auth_config = storage.config.get('auth', {})
    if old_password != auth_config.get('password'):
        return jsonify({'success': False, 'message': '旧密码错误'})
    
    # 更新配置
    auth_config['users'] = new_username
    auth_config['password'] = new_password
    storage.config['auth'] = auth_config
    storage._save_config()
    
    return jsonify({'success': True, 'message': '登录凭据更新成功'})

@app.route('/api/version/check', methods=['GET'])
@handle_api_error
def check_version():
    """检查最新版本"""
    try:
        import feedparser
        import requests
        from requests.exceptions import RequestException
        import re
        
        # 获取查询参数，确定使用哪个源检查更新
        source = request.args.get('source', 'github')
        
        if source == 'dockerhub':
            # 使用 Docker Hub RSS 检查更新
            feed_url = DOCKER_HUB_RSS
        elif source == 'dockerhub_alt':
            # 使用备用 Docker Hub RSS 源
            feed_url = DOCKER_HUB_RSS_ALT
        elif source in ['msrun', '1ms']:
            # 使用 1ms.run API 获取版本信息
            try:
                params = {
                    "repositories": "kokojacket/baidu-autosave",
                    "page": 1,
                    "page_size": 10,
                    "search": ""
                }
                response = requests.get(MS_RUN_API, params=params, timeout=5)
                response.raise_for_status()
                data = response.json()
                
                if data.get('code') == 0 and data.get('data', {}).get('list'):
                    # 查找最新的正式版本（格式为vX.Y.Z）
                    version_tags = []
                    latest_tag = None
                    
                    # 首先找到latest标签
                    for tag_info in data['data']['list']:
                        if tag_info['tag_name'] == 'latest':
                            latest_tag = tag_info
                            break
                    
                    if latest_tag:
                        # 找到与latest标签具有相同digest的版本标签
                        latest_digest = latest_tag.get('digest')
                        for tag_info in data['data']['list']:
                            if re.match(r'^v\d+\.\d+\.\d+$', tag_info['tag_name']) and tag_info.get('digest') == latest_digest:
                                version_tags.append(tag_info)
                    
                    # 如果没有找到与latest相同digest的版本标签，则收集所有版本标签
                    if not version_tags:
                        for tag_info in data['data']['list']:
                            if re.match(r'^v\d+\.\d+\.\d+$', tag_info['tag_name']):
                                version_tags.append(tag_info)
                    
                    if version_tags:
                        # 按更新时间排序，选择最新的
                        version_tags.sort(key=lambda x: x.get('tag_last_pushed', ''), reverse=True)
                        latest_version = version_tags[0]['tag_name']
                        published = version_tags[0].get('tag_last_pushed')
                        link = f"https://hub.docker.com/layers/kokojacket/baidu-autosave/{latest_version}/images/{version_tags[0].get('digest', '').split(':')[-1]}"
                        
                        logger.info(f"从1ms.run API获取到最新版本: {latest_version}")
                        return jsonify({
                            'success': True,
                            'version': latest_version,
                            'published': published,
                            'link': link,
                            'source': '1ms'
                        })
                
                # 如果没有找到有效的版本信息，返回错误
                logger.warning("1ms.run API未返回有效的版本信息")
                return jsonify({
                    'success': False,
                    'message': '1ms.run API未返回有效的版本信息',
                    'source': '1ms'
                })
                
            except Exception as e:
                logger.warning(f"从1ms.run API获取版本信息失败: {str(e)}")
                return jsonify({
                    'success': False,
                    'message': f'从1ms.run API获取版本信息失败: {str(e)}',
                    'source': '1ms'
                })
        else:
            # 默认使用 GitHub releases feed
            feed_url = f'https://github.com/{GITHUB_REPO}/releases.atom'
        
        # 如果是使用RSS源，则执行以下代码
        if source in ['github', 'dockerhub', 'dockerhub_alt']:
            try:
                # 设置超时，避免长时间等待
                response = requests.get(feed_url, timeout=5)
                response.raise_for_status()  # 如果响应状态码不是200，抛出异常
            except RequestException as e:
                logger.warning(f"获取{source}版本信息失败: {str(e)}")
                return jsonify({
                    'success': False,
                    'message': f'无法获取{source}版本信息: {str(e)}',
                    'source': source
                })
                
            # 解析 feed
            feed = feedparser.parse(response.content)
            if not feed.entries:
                logger.warning(f"{source}未找到版本信息")
                return jsonify({
                    'success': False,
                    'message': f'{source}未找到版本信息',
                    'source': source
                })
                
            # 获取最新版本信息
            if source in ['dockerhub', 'dockerhub_alt']:
                # 首先查找latest标签的条目
                latest_entry = None
                latest_guid = None
                version_entry = None
                
                for entry in feed.entries:
                    if ':latest' in entry.title:
                        latest_entry = entry
                        # 提取镜像ID（guid的@后面部分）
                        guid_match = re.search(r'@([a-f0-9]+)$', entry.guid)
                        if guid_match:
                            latest_guid = guid_match.group(1)
                        break
                
                if not latest_entry:
                    logger.warning("Docker Hub中未找到latest标签")
                    # 如果没有找到latest标签，使用第一个条目
                    latest_entry = feed.entries[0]
                
                # 如果找到了latest的guid，查找对应的版本号条目
                if latest_guid:
                    for entry in feed.entries:
                        # 检查是否是版本号标签（如v1.0.8）并且与latest有相同的guid
                        if re.search(r':v\d+\.\d+\.\d+', entry.title) and latest_guid in entry.guid:
                            version_entry = entry
                            break
                
                # 如果找到了版本号条目，使用它；否则使用latest条目
                entry_to_use = version_entry if version_entry else latest_entry
                
                # 提取版本号
                title = entry_to_use.title
                version_match = re.search(r':(?:v?\d+\.\d+\.\d+|latest)', title)
                latest_version = version_match.group(0)[1:] if version_match else title
                
                # 添加发布日期
                pub_date = entry_to_use.pubDate if hasattr(entry_to_use, 'pubDate') else entry_to_use.published
                
                logger.info(f"从Docker Hub ({source})获取到最新版本: {latest_version}")
                return jsonify({
                    'success': True,
                    'version': latest_version,
                    'published': pub_date,
                    'link': entry_to_use.link,
                    'source': source
                })
            else:
                # GitHub 格式
                title = feed.entries[0].title
                
                # 从标题中提取版本号，支持多种格式：
                # 1. "Release v1.0.8" -> "v1.0.8"
                # 2. "v1.0.8" -> "v1.0.8"
                # 3. "1.0.8" -> "1.0.8"
                version_match = re.search(r'(?:Release\s+)?(v?\d+\.\d+\.\d+)', title)
                if version_match:
                    latest_version = version_match.group(1)
                    # 确保版本号以v开头
                    if not latest_version.startswith('v'):
                        latest_version = 'v' + latest_version
                else:
                    # 如果无法提取版本号，使用原始标题
                    latest_version = title
                
                pub_date = feed.entries[0].published if hasattr(feed.entries[0], 'published') else None
                link = feed.entries[0].link if hasattr(feed.entries[0], 'link') else f"https://github.com/{GITHUB_REPO}/releases/latest"
                
                logger.info(f"从GitHub获取到最新版本: {title} -> 提取版本号: {latest_version}")
                return jsonify({
                    'success': True,
                    'version': latest_version,
                    'published': pub_date,
                    'link': link,
                    'source': 'github'
                })
    except Exception as e:
        logger.error(f"检查版本失败: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'检查版本失败: {str(e)}',
            'source': source if 'source' in locals() else 'unknown'
        })

# 添加轮询API端点
@app.route('/api/tasks/status', methods=['GET'])
@login_required
@handle_api_error
def get_tasks_status():
    """获取所有任务的状态（用于轮询）"""
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
    tasks = storage.list_tasks()
    # 按 order 排序，没有 order 的排在最后
    tasks.sort(key=lambda x: x.get('order', float('inf')))
    return jsonify({'success': True, 'tasks': tasks})

def cleanup_old_task_logs():
    """清理超过1小时的任务日志，释放内存"""
    if not hasattr(app, 'task_logs'):
        return
    
    current_time = datetime.now()
    tasks_to_remove = []
    
    # 获取所有任务状态
    try:
        if storage:
            tasks = storage.list_tasks()
            for task_order in list(app.task_logs.keys()):
                # 查找对应的任务
                task_found = False
                for task in tasks:
                    if task.get('order') == task_order:
                        task_found = True
                        # 如果任务不是运行状态，且日志有内容，检查最后一条日志的时间
                        if task.get('status') != 'running' and app.task_logs[task_order]:
                            last_log = app.task_logs[task_order][-1]
                            if 'timestamp' in last_log:
                                try:
                                    # 解析时间戳（HH:MM:SS格式）
                                    last_time_str = last_log['timestamp']
                                    last_time = datetime.strptime(f"{current_time.strftime('%Y-%m-%d')} {last_time_str}", '%Y-%m-%d %H:%M:%S')
                                    
                                    # 如果是昨天的日志，需要调整日期
                                    if last_time > current_time:
                                        last_time = last_time.replace(day=last_time.day - 1)
                                    
                                    # 如果超过1小时，标记为删除
                                    if (current_time - last_time).total_seconds() > 3600:
                                        tasks_to_remove.append(task_order)
                                except:
                                    # 时间解析失败，保留日志
                                    pass
                        break
                
                # 如果任务不存在，也清理其日志
                if not task_found:
                    tasks_to_remove.append(task_order)
        
        # 删除标记的日志
        for task_order in tasks_to_remove:
            if task_order in app.task_logs:
                del app.task_logs[task_order]
                logger.debug(f"已清理任务{task_order}的历史日志")
                
    except Exception as e:
        logger.error(f"清理任务日志失败: {str(e)}")

@app.route('/api/share/folders', methods=['POST'])
@login_required
@handle_api_error
def list_share_folders():
    """列出分享链接里的顶层文件夹（供"转存文件夹"多选使用）"""
    data = request.get_json() or {}
    url = (data.get('url') or '').strip()
    pwd = (data.get('pwd') or '').strip()
    if not url:
        return jsonify({'success': False, 'message': '请先填写转存链接'})
    sub_path = (data.get('path') or '').strip()
    try:
        storage._access_shared_with_retry(url, pwd)
        paths = storage._shared_paths_with_retry(shared_url=url)
        if sub_path:
            # 修改: 下钻——列出分享内任意层目录的子文件夹
            first = next((p for p in (paths or []) if getattr(p, 'uk', None)), None)
            if first is None:
                return jsonify({'success': False, 'message': '读取失败：分享为空'})
            paths = storage._list_shared_paths_with_retry(
                sub_path, first.uk, first.share_id, first.bdstoken, page=1, size=100)
    except Exception as e:
        logger.error(f"获取分享文件夹列表失败: {str(e)}")
        return jsonify({'success': False, 'message': f'读取失败：{e}'})
    folders = []
    for p in (paths or []):
        if getattr(p, 'is_dir', False):
            raw = str(getattr(p, 'path', '') or '')
            folders.append({'name': raw.rstrip('/').rsplit('/', 1)[-1] or raw, 'path': raw})
    return jsonify({'success': True, 'folders': folders})

@app.route('/api/share/filtered-files', methods=['POST'])
@login_required
@handle_api_error
def share_filtered_files():
    """按任务的文件过滤正则，列出该链接当前能转存进来的文件（供"排除文件"勾选）"""
    data = request.get_json() or {}
    url = (data.get('url') or '').strip()
    pwd = (data.get('pwd') or '').strip()
    if not url:
        return jsonify({'success': False, 'message': '缺少转存链接'})
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})

    task = None
    try:
        task_order = int(data.get('task_id')) + 1
        tasks = storage.config.get('baidu', {}).get('tasks', [])
        task = next((t for t in tasks if t.get('order') == task_order), None)
    except (TypeError, ValueError):
        task = None

    existing_excludes = list((task or {}).get('exclude_files') or [])

    try:
        storage._access_shared_with_retry(url, pwd)
        shared_paths = storage._shared_paths_with_retry(shared_url=url)
        if not shared_paths:
            return jsonify({'success': False, 'message': '获取分享文件列表失败（链接可能已失效）'})
        uk = shared_paths[0].uk
        share_id = shared_paths[0].share_id
        bdstoken = shared_paths[0].bdstoken
        files = []
        for p in shared_paths:
            if getattr(p, 'is_dir', False):
                files.extend(storage._list_shared_dir_files(p, uk, share_id, bdstoken))
            else:
                raw = str(getattr(p, 'path', '') or '')
                files.append({
                    'server_filename': raw.rstrip('/').rsplit('/', 1)[-1],
                    'path': raw,
                    'size': getattr(p, 'size', 0),
                    'isdir': 0,
                    'md5': str(getattr(p, 'md5', '') or '')
                })
    except Exception as e:
        logger.error(f"读取分享文件失败: {str(e)}")
        return jsonify({'success': False, 'message': f'读取失败（链接可能已失效）：{e}'})

    # 修改: 对齐转存时的路径清理（剥掉转存文件夹/单根目录层），再套正则，
    # 否则 ^ 开头的锚定正则会把带文件夹前缀的文件全部滤掉（30/31/32 集看不到的原因）
    selected_folders = [str(p).strip('/') for p in ((task or {}).get('transfer_folders') or []) if str(p).strip()]
    keep_folder = bool((task or {}).get('keep_folder'))
    strip_names = set()
    if selected_folders and not keep_folder:
        strip_names = {p.rsplit('/', 1)[-1] for p in selected_folders}
    is_single_folder = (not selected_folders) and len(shared_paths) == 1 and bool(getattr(shared_paths[0], 'is_dir', False))

    candidates = []
    for f in files:
        path = f.get('path', '')
        clean = path
        if is_single_folder and '/' in clean:
            clean = '/'.join(clean.split('/')[1:])
        elif strip_names:
            segs = clean.strip('/').split('/')
            if len(segs) > 1 and segs[0] in strip_names:
                clean = '/'.join(segs[1:])
        if task:
            try:
                should, _final = storage._apply_regex_rules(clean, task)
                if not should:
                    continue
            except Exception:
                pass
        candidates.append({'path': path, 'size': f.get('size', 0), 'md5': f.get('md5', '') or ''})

    return jsonify({
        'success': True,
        'files': candidates,
        'excluded': existing_excludes
    })

@app.route('/api/task/exclude', methods=['POST'])
@login_required
@handle_api_error
def set_task_excludes():
    """设置任务的排除文件清单（这些文件后续转存时跳过）"""
    data = request.get_json() or {}
    try:
        task_id = int(data.get('task_id', -1))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': '无效的任务ID'})
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
    files = data.get('files')
    if not isinstance(files, list):
        return jsonify({'success': False, 'message': 'files 必须是数组'})
    tasks = storage.config.get('baidu', {}).get('tasks', [])
    task_order = task_id + 1
    task = next((t for t in tasks if t.get('order') == task_order), None)
    if not task:
        return jsonify({'success': False, 'message': f'未找到任务(order={task_order})'})
    task['exclude_files'] = [str(f) for f in files if str(f).strip()]
    storage._save_config()
    return jsonify({
        'success': True,
        'message': f'已保存排除清单（{len(task["exclude_files"])} 个文件）',
        'exclude_files': task['exclude_files']
    })

@app.route('/api/netdisk/folders', methods=['POST'])
@login_required
@handle_api_error
def list_netdisk_folders():
    """列出当前网盘账号指定目录下的子文件夹（供"对比路径"选择框使用）"""
    data = request.get_json() or {}
    path = (data.get('path') or '/').strip() or '/'
    if not path.startswith('/'):
        path = '/' + path
    path = path.rstrip('/') or '/'
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
    try:
        entries = storage._list_dir_entries_with_fallback(path)
    except Exception as e:
        logger.error(f"列出网盘目录失败: {str(e)}")
        return jsonify({'success': False, 'message': f'读取目录失败：{e}'})

    folders = []
    for item in entries or []:
        try:
            if not storage._is_list_entry_dir(item):
                continue
            p = storage._get_list_entry_path(item)
            if not p:
                continue
            p = p.rstrip('/') or '/'
            folders.append({'name': p.rsplit('/', 1)[-1] or p, 'path': p})
        except Exception:
            continue

    # 去重并保持顺序
    seen = set()
    unique_folders = []
    for f in folders:
        if f['path'] in seen:
            continue
        seen.add(f['path'])
        unique_folders.append(f)

    parent = path.rsplit('/', 1)[0] or '/'
    return jsonify({'success': True, 'path': path, 'parent': parent, 'folders': unique_folders})

@app.route('/api/qms/config', methods=['GET'])
@login_required
@handle_api_error
def get_qms_config():
    """读取 QMediaSync 对接配置（密钥只回显掩码）"""
    raw = qms_load_cfg()
    api_key = str(raw.get('api_key') or '')
    out = {k: v for k, v in raw.items() if k not in ('api_key', 'password')}
    out['has_api_key'] = bool(api_key)
    if len(api_key) > 8:
        out['api_key_masked'] = api_key[:8] + '*' * 11
    else:
        out['api_key_masked'] = '*' * len(api_key)
    out['has_password'] = bool(raw.get('password'))
    return jsonify({'success': True, 'config': out})


@app.route('/api/qms/config', methods=['POST'])
@login_required
@handle_api_error
def save_qms_config():
    """保存 QMediaSync 对接配置（密钥留空表示不修改）"""
    data = request.get_json() or {}
    cfg = qms_load_cfg()
    for key in ('enabled', 'host', 'port', 'scheme', 'auth_mode', 'username', 'task_ids', 'auto_trigger'):
        if key in data:
            cfg[key] = data[key]
    if (data.get('api_key') or '').strip():
        cfg['api_key'] = data['api_key'].strip()
    if data.get('clear_api_key'):
        cfg['api_key'] = ''
    if data.get('password'):
        cfg['password'] = data['password']
    if data.get('clear_password'):
        cfg['password'] = ''
    qms_save_cfg(cfg)
    return jsonify({'success': True, 'message': 'QMediaSync 配置已保存'})


@app.route('/api/qms/reveal', methods=['POST'])
@login_required
@handle_api_error
def reveal_qms_key():
    """返回已保存的 API Key 明文（登录后可用，供页面上「小眼睛」查看）"""
    cfg = qms_load_cfg()
    return jsonify({'success': True, 'api_key': str(cfg.get('api_key') or '')})


@app.route('/api/qms/test', methods=['POST'])
@login_required
@handle_api_error
def test_qms_config():
    """测试与 QMediaSync 的连接（用页面当前填写的值，不必先保存）"""
    cfg = qms_merge_cfg(qms_load_cfg(), request.get_json(silent=True) or {})
    try:
        info = QmsClient(cfg).test()
        return jsonify({'success': True, 'message': '连接成功', 'username': info.get('username')})
    except QmsError as e:
        return jsonify({'success': False, 'message': str(e)})
    except Exception as e:
        return jsonify({'success': False, 'message': f'连接失败：{e}'})


@app.route('/api/qms/paths', methods=['GET', 'POST'])
@login_required
@handle_api_error
def list_qms_paths():
    """列出 QMediaSync 里的刮削任务（供选择序号，支持用未保存的连接参数）"""
    cfg = qms_merge_cfg(qms_load_cfg(), request.get_json(silent=True) or {})
    try:
        return jsonify({'success': True, 'paths': QmsClient(cfg).list_scrape_paths()})
    except QmsError as e:
        return jsonify({'success': False, 'message': str(e)})
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取失败：{e}'})


@app.route('/api/qms/sync-paths', methods=['GET', 'POST'])
@login_required
@handle_api_error
def list_qms_sync_paths():
    """列出 QMediaSync 的同步目录（用于刮削成功后自动生成 STRM）"""
    cfg = qms_merge_cfg(qms_load_cfg(), request.get_json(silent=True) or {})
    try:
        return jsonify({'success': True, 'paths': QmsClient(cfg).list_sync_paths()})
    except QmsError as e:
        return jsonify({'success': False, 'message': str(e)})
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取失败：{e}'})


@app.route('/api/qms/links', methods=['GET'])
@login_required
@handle_api_error
def list_qms_links():
    """连接列表：本工具任务 ↔ QMS 刮削目录"""
    cfg = qms_load_cfg()
    links = qms_get_links(cfg)
    tasks = storage.list_tasks() or []
    by_uid = {str(t.get('task_uid')): t for t in tasks if t.get('task_uid')}
    by_order = {t.get('order'): t for t in tasks}
    out = []
    for l in links:
        t = by_uid.get(l['task_uid']) or by_order.get(l['task_order'])
        item = dict(l)
        item['task_exists'] = bool(t)
        item['task_current_name'] = (t or {}).get('name') or l.get('task_name') or ''
        item['task_enabled'] = (t or {}).get('enabled', True) is not False
        out.append(item)
    return jsonify({'success': True, 'links': out})


@app.route('/api/qms/links', methods=['POST'])
@login_required
@handle_api_error
def create_qms_link():
    """创建连接：选一个本工具任务 + 一个 QMS 刮削目录"""
    data = request.get_json() or {}
    task_ref = data.get('task_ref')
    qms_id = data.get('qms_id')
    if qms_id is None or str(qms_id).strip() == '':
        return jsonify({'success': False, 'message': '请选择 QMediaSync 的刮削目录'})
    if task_ref is None or str(task_ref).strip() == '':
        return jsonify({'success': False, 'message': '请选择要绑定的任务'})

    task = storage.resolve_task(task_ref)
    if not task and isinstance(task_ref, (int, str)) and str(task_ref).isdigit():
        task = storage.resolve_task(order=int(task_ref))
    if not task:
        return jsonify({'success': False, 'message': '找不到该任务，请重新选择'})

    cfg = qms_load_cfg()
    links = qms_get_links(cfg)
    task_uid = str(task.get('task_uid') or '')
    task_order = task.get('order')
    for l in links:
        same_task = (task_uid and l['task_uid'] == task_uid) or (
            not task_uid and l.get('task_order') == task_order
        )
        if same_task and str(l.get('qms_id')) == str(qms_id):
            return jsonify({'success': False, 'message': '这条连接已经存在了，不用重复创建'})

    # 顺手把 QMS 侧的路径信息记下来（拿不到也不影响创建）
    qms_path, qms_media = '', ''
    try:
        for p in QmsClient(cfg).list_scrape_paths():
            if str(p.get('id')) == str(qms_id):
                qms_path = p.get('source_path') or ''
                qms_media = p.get('media_type') or ''
                break
    except Exception:
        pass

    # 可选的 STRM 同步目录（刮削成功后自动触发一次同步，生成 .strm）
    strm_id = data.get('strm_id')
    strm_path = ''
    if strm_id is not None and str(strm_id).strip() != '' and str(strm_id).isdigit():
        try:
            for sp in QmsClient(cfg).list_sync_paths():
                if str(sp.get('id')) == str(strm_id):
                    strm_path = sp.get('remote_path') or sp.get('base_cid') or ''
                    break
        except Exception:
            pass
    else:
        strm_id = None

    link = {
        'id': qms_new_link_id(),
        'task_uid': task_uid,
        'task_order': task_order,
        'task_name': task.get('name') or '',
        'qms_id': int(qms_id) if str(qms_id).isdigit() else qms_id,
        'qms_path': qms_path,
        'qms_media_type': qms_media,
        'strm_id': int(strm_id) if str(strm_id or '').isdigit() else None,
        'strm_path': strm_path,
        'enabled': True,
        'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    links.append(link)
    cfg['links'] = links
    qms_save_cfg(cfg)
    return jsonify({'success': True, 'message': '连接已创建', 'link': link})


@app.route('/api/qms/links/update', methods=['POST'])
@login_required
@handle_api_error
def update_qms_link():
    """编辑一条连接：可改绑定的任务、QMS 刮削目录、STRM 同步目录"""
    data = request.get_json() or {}
    lid = str(data.get('id') or '')
    if not lid:
        return jsonify({'success': False, 'message': '缺少连接 ID'})

    cfg = qms_load_cfg()
    links = qms_get_links(cfg)
    target = next((l for l in links if l['id'] == lid), None)
    if not target:
        return jsonify({'success': False, 'message': '连接不存在'})

    # 换绑任务
    task_ref = data.get('task_ref')
    if task_ref is not None and str(task_ref).strip() != '':
        task = storage.resolve_task(task_ref)
        if not task and str(task_ref).isdigit():
            task = storage.resolve_task(order=int(task_ref))
        if not task:
            return jsonify({'success': False, 'message': '找不到该任务，请重新选择'})
        target['task_uid'] = str(task.get('task_uid') or '')
        target['task_order'] = task.get('order')
        target['task_name'] = task.get('name') or ''

    # 换绑刮削目录
    qms_id = data.get('qms_id')
    if qms_id is not None and str(qms_id).strip() != '':
        target['qms_id'] = int(qms_id) if str(qms_id).isdigit() else qms_id
        qms_path, qms_media = '', ''
        try:
            for p in QmsClient(cfg).list_scrape_paths():
                if str(p.get('id')) == str(qms_id):
                    qms_path = p.get('source_path') or ''
                    qms_media = p.get('media_type') or ''
                    break
        except Exception:
            pass
        target['qms_path'] = qms_path
        target['qms_media_type'] = qms_media

    # 换绑（或清空）STRM 同步目录
    if 'strm_id' in data:
        sid = data.get('strm_id')
        if sid is None or str(sid).strip() == '' or not str(sid).isdigit():
            target['strm_id'] = None
            target['strm_path'] = ''
        else:
            target['strm_id'] = int(sid)
            spath = ''
            try:
                for sp in QmsClient(cfg).list_sync_paths():
                    if str(sp.get('id')) == str(sid):
                        spath = sp.get('remote_path') or sp.get('base_cid') or ''
                        break
            except Exception:
                pass
            target['strm_path'] = spath

    cfg['links'] = links
    qms_save_cfg(cfg)
    return jsonify({'success': True, 'message': '连接已更新', 'link': target})


@app.route('/api/qms/links/delete', methods=['POST'])
@login_required
@handle_api_error
def delete_qms_link():
    """删除一条连接"""
    data = request.get_json() or {}
    lid = str(data.get('id') or '')
    if not lid:
        return jsonify({'success': False, 'message': '缺少连接 ID'})
    cfg = qms_load_cfg()
    links = qms_get_links(cfg)
    kept = [l for l in links if l['id'] != lid]
    if len(kept) == len(links):
        return jsonify({'success': False, 'message': '连接不存在'})
    cfg['links'] = kept
    qms_save_cfg(cfg)
    return jsonify({'success': True, 'message': '连接已删除'})


@app.route('/api/qms/links/toggle', methods=['POST'])
@login_required
@handle_api_error
def toggle_qms_link():
    """启用/停用一条连接"""
    data = request.get_json() or {}
    lid = str(data.get('id') or '')
    if not lid:
        return jsonify({'success': False, 'message': '缺少连接 ID'})
    cfg = qms_load_cfg()
    links = qms_get_links(cfg)
    found = False
    for l in links:
        if l['id'] == lid:
            l['enabled'] = bool(data.get('enabled', not l.get('enabled', True)))
            found = True
    if not found:
        return jsonify({'success': False, 'message': '连接不存在'})
    cfg['links'] = links
    qms_save_cfg(cfg)
    return jsonify({'success': True, 'message': '已更新'})


@app.route('/api/qms/links/trigger', methods=['POST'])
@login_required
@handle_api_error
def trigger_qms_link():
    """手动触发一条连接（记日志）"""
    data = request.get_json() or {}
    lid = str(data.get('id') or '')
    cfg = qms_load_cfg()
    link = next((l for l in qms_get_links(cfg) if l['id'] == lid), None)
    if not link:
        return jsonify({'success': False, 'message': '连接不存在'})
    result = qms_trigger_link(link, source='manual')
    cfg = qms_load_cfg()
    cfg['last_trigger_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
    cfg['last_trigger_result'] = qms_build_summary([result])
    cfg['last_trigger_task'] = link.get('task_name') or ''
    cfg['last_trigger_ok'] = bool(result.get('ok'))
    qms_save_cfg(cfg)
    return jsonify({
        'success': bool(result.get('ok')),
        'message': qms_build_summary([result]),
        'result': result,
    })


@app.route('/api/qms/logs', methods=['GET'])
@login_required
@handle_api_error
def list_qms_logs():
    """某条连接的触发日志（默认最近 30 条）"""
    link_id = request.args.get('link_id') or ''
    try:
        limit = int(request.args.get('limit') or 30)
    except (TypeError, ValueError):
        limit = 30
    return jsonify({'success': True, 'logs': qms_get_logs(link_id, limit=limit)})


@app.route('/api/qms/start', methods=['POST'])
@login_required
@handle_api_error
def start_qms_scrape():
    """手动触发 QMediaSync 刮削任务"""
    data = request.get_json() or {}
    cfg = qms_load_cfg()
    links = [l for l in qms_get_links(cfg) if l.get('enabled', True)]
    if data.get('ids'):
        # 兼容按序号直接触发（无法定位连接时不写日志）
        try:
            results = QmsClient(cfg).start(data.get('ids'))
        except QmsError as e:
            return jsonify({'success': False, 'message': str(e)})
        except Exception as e:
            return jsonify({'success': False, 'message': f'触发失败：{e}'})
    else:
        if not links:
            return jsonify({'success': False, 'message': '没有启用的连接'})
        results = [qms_trigger_link(l, source='manual') for l in links]
    summary = qms_build_summary(results)
    cfg['last_trigger_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
    cfg['last_trigger_result'] = summary
    cfg['last_trigger_ok'] = all(r.get('ok') for r in results)
    qms_save_cfg(cfg)
    return jsonify({
        'success': any(r.get('ok') for r in results),
        'message': summary,
        'results': results,
    })


@app.route('/api/task/history/<int:task_id>', methods=['GET'])
@login_required
@handle_api_error
def get_task_history(task_id):
    """获取任务最近10次转存历史（SQLite）"""
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
    tasks = storage.config.get('baidu', {}).get('tasks', [])
    task_order = task_id + 1  # 前端task_id从0开始，order从1开始
    task = next((t for t in tasks if t.get('order') == task_order), None)
    if not task:
        return jsonify({'success': False, 'message': f'未找到任务(order={task_order})'})
    from history_db import get_task_history as db_get
    return jsonify({'success': True, 'history': db_get(task_uid=task.get('task_uid'), order=task_order)})

@app.route('/api/task/log/<int:task_id>', methods=['GET'])
@login_required
@handle_api_error
def get_task_log(task_id):
    """获取指定任务的执行日志（用于轮询）"""
    try:
        # 根据task_id找到真实的任务order
        if not storage:
            return jsonify({'success': False, 'message': '存储未初始化'})

        tasks = storage.list_tasks()
        if not tasks or task_id >= len(tasks):
            return jsonify({'success': True, 'logs': []})

        # 获取真实的task order
        task_order = tasks[task_id].get('order')

        # 定期清理旧日志（每100次请求清理一次）
        _ensure_task_runtime_state()
        app._log_cleanup_counter += 1
        if app._log_cleanup_counter >= 100:
            cleanup_old_task_logs()
            app._log_cleanup_counter = 0

        # 从全局变量中获取任务日志
        if hasattr(app, 'task_logs') and task_order in app.task_logs:
            logs = app.task_logs[task_order]
            return jsonify({'success': True, 'logs': logs})
        else:
            # 如果没有找到任务日志，返回空列表
            return jsonify({'success': True, 'logs': []})
    except Exception as e:
        logger.error(f"获取任务日志失败: {str(e)}")
        return jsonify({'success': False, 'message': f'获取任务日志失败: {str(e)}'})


@app.route('/api/task/stream/<task_ref>', methods=['GET'])
@login_required
def stream_task(task_ref):
    """通过 SSE 实时推送任务状态与日志。"""
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'}), 503

    task = storage.resolve_task(task_ref)
    if not task:
        return jsonify({'success': False, 'message': '任务不存在'}), 404

    task_uid = task.get('task_uid') or task_ref
    task_order = task.get('order')
    task_url = task.get('url')
    _remember_task_stream(task_uid, task_order)

    def event_stream():
        event_queue = queue.Queue(maxsize=200)
        _register_task_stream(task_uid, event_queue)

        try:
            yield _format_sse('connected', {
                'task_uid': task_uid,
                'task_order': task_order,
                'timestamp': datetime.now().strftime('%H:%M:%S')
            })
            yield _format_sse('snapshot', _build_task_stream_snapshot(task))

            while True:
                try:
                    event_name, payload = event_queue.get(timeout=15)
                except queue.Empty:
                    latest_task = storage.resolve_task(task_uid, order=task_order, url=task_url)
                    if latest_task is None:
                        yield _format_sse('completed', {
                            'task': {
                                'order': task_order,
                                'task_uid': task_uid,
                                'status': 'error',
                                'message': '任务已不存在'
                            }
                        })
                        break
                    yield _format_sse('heartbeat', {
                        'timestamp': datetime.now().strftime('%H:%M:%S')
                    })
                    continue

                yield _format_sse(event_name, payload)
                if event_name == 'completed':
                    break
        finally:
            _unregister_task_stream(task_uid, event_queue)

    response = Response(stream_with_context(event_stream()), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Connection'] = 'keep-alive'
    return response

@app.route('/api/task/share', methods=['POST'])
@login_required
@handle_api_error
def share_task():
    """生成任务的分享链接"""
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
    
    data = request.get_json()
    task_id = data.get('task_id')
    custom_password = data.get('password')  # 可选的自定义密码
    custom_period = data.get('period')      # 可选的自定义有效期
    
    if task_id is None:
        return jsonify({'success': False, 'message': '任务ID不能为空'})
    
    # 获取任务信息
    tasks = storage.list_tasks()
    tasks.sort(key=lambda x: x.get('order', float('inf')))
    
    if 0 <= task_id < len(tasks):
        task = tasks[task_id]
        save_dir = task.get('save_dir')
        
        if not save_dir:
            return jsonify({'success': False, 'message': '任务保存目录为空'})
        
        # 获取分享配置
        share_config = storage.config.get('share', {})
        password = custom_password if custom_password is not None else share_config.get('default_password', '1234')
        # 支持0作为永久有效期
        period_days = custom_period if custom_period is not None else share_config.get('default_period_days', 7)
        
        try:
            # 调用BaiduPCS-Py的share命令
            # 注意：share_file函数内部会检查并创建目录
            share_result = storage.share_file(save_dir, password, period_days)
            
            if share_result.get('success'):
                share_info = share_result.get('share_info', {})
                
                # 更新任务的分享信息
                task_order = task.get('order', task_id + 1)
                storage.update_task_share_info(task_order, share_info)
                
                return jsonify({
                    'success': True, 
                    'message': '分享链接生成成功',
                    'share_info': share_info
                })
            else:
                return jsonify({
                    'success': False, 
                    'message': share_result.get('error', '分享链接生成失败')
                })
                
        except Exception as e:
            logger.error(f"生成分享链接失败: {str(e)}")
            return jsonify({'success': False, 'message': f'生成分享链接失败: {str(e)}'})
    else:
        return jsonify({'success': False, 'message': '任务不存在'})

@app.route('/api/config/share', methods=['POST'])
@login_required
@handle_api_error
def update_share_config():
    """更新分享配置"""
    if not storage:
        return jsonify({'success': False, 'message': '存储未初始化'})
        
    data = request.get_json()
    share_config = {
        'default_password': data.get('default_password', '1234'),
        'default_period_days': data.get('default_period_days', 7)
    }
    
    # 更新配置
    storage.config['share'] = share_config
    storage._save_config()
    
    return jsonify({'success': True, 'message': '分享配置已更新'})

if __name__ == '__main__':
    try:
        # 启动时初始化应用
        init_success, init_error = init_app()
        if not init_success:
            logger.error(f"应用初始化失败: {init_error}")
            if init_error:
                logger.warning("将继续启动 Web 界面，但部分功能可能不可用")
        
        # 注册信号处理器
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # 启动HTTP服务器
        server_port = get_server_port()
        logger.info(f"使用标准WSGI服务器，监听端口: {server_port}")
        http_server = WSGIServer(('0.0.0.0', server_port), app, log=None)  # 禁用访问日志

        print(f'Server started at http://0.0.0.0:{server_port}')
        http_server.serve_forever()
    except KeyboardInterrupt:
        logger.info("接收到 Ctrl+C，正在退出...")
        signal_handler(signal.SIGINT, None)
    except Exception as e:
        logger.error(f"应用运行出错: {str(e)}")
        try:
            signal_handler(signal.SIGTERM, None)
        except:
            sys.exit(1) 