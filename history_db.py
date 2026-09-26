# -*- coding: utf-8 -*-
"""转存历史 + 应用级配置存储（SQLite）

单独放 SQLite 的原因：config.json 每次转存会被进度回写上百次，多个写入者互相覆盖，
放在里面的数据（历史记录、QMS 对接配置/连接列表）会被吃掉。
"""
import json
import os
import sqlite3
import threading
import time

_DB = os.path.join('config', 'history.db')
_lock = threading.Lock()


def _connect():
    conn = sqlite3.connect(_DB, timeout=20)
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def _init():
    with _lock:
        conn = _connect()
        conn.execute(
            '''CREATE TABLE IF NOT EXISTS task_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_uid TEXT,
                order_no INTEGER,
                start_time TEXT,
                end_time TEXT,
                success INTEGER,
                message TEXT,
                file_count INTEGER,
                record TEXT
            )'''
        )
        conn.execute(
            '''CREATE TABLE IF NOT EXISTS app_kv (
                k TEXT PRIMARY KEY,
                v TEXT,
                updated_at TEXT
            )'''
        )
        conn.execute(
            '''CREATE TABLE IF NOT EXISTS qms_trigger_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link_id TEXT,
                task_name TEXT,
                qms_id INTEGER,
                trigger_at TEXT,
                success INTEGER,
                message TEXT,
                source TEXT
            )'''
        )
        conn.commit()
        conn.close()


# ---------------- 转存历史 ----------------

def record_task_history(task_uid=None, order=None, record=None, keep=10):
    """写入一条转存历史，并只保留每个任务最近 keep 条"""
    _init()
    record = record or {}
    with _lock:
        conn = _connect()
        conn.execute(
            'INSERT INTO task_history (task_uid, order_no, start_time, end_time, success, message, file_count, record)'
            ' VALUES (?,?,?,?,?,?,?,?)',
            (
                str(task_uid or ''),
                int(order or 0),
                record.get('start_time', ''),
                record.get('end_time', ''),
                1 if record.get('success') else 0,
                record.get('message', ''),
                int(record.get('file_count', 0)),
                json.dumps(record, ensure_ascii=False),
            ),
        )
        conn.commit()
        conn.execute(
            '''DELETE FROM task_history WHERE task_uid=? AND id NOT IN (
                SELECT id FROM task_history WHERE task_uid=? ORDER BY id DESC LIMIT ?)''',
            (str(task_uid or ''), str(task_uid or ''), int(keep)),
        )
        conn.commit()
        conn.close()


def get_task_history(task_uid=None, order=None, limit=10):
    """读取某个任务的转存历史（新的在前）"""
    _init()
    with _lock:
        conn = _connect()
        cur = conn.execute(
            'SELECT record FROM task_history WHERE task_uid=? ORDER BY id DESC LIMIT ?',
            (str(task_uid or ''), int(limit)),
        )
        rows = [json.loads(r[0]) for r in cur.fetchall()]
        conn.close()
        return rows


# ---------------- 应用级配置（键值对） ----------------

def get_kv(key, default=None):
    """读一个应用级配置项（JSON 反序列化）"""
    _init()
    with _lock:
        conn = _connect()
        cur = conn.execute('SELECT v FROM app_kv WHERE k=?', (str(key),))
        row = cur.fetchone()
        conn.close()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return default


def set_kv(key, value):
    """写一个应用级配置项（JSON 序列化，覆盖写）"""
    _init()
    with _lock:
        conn = _connect()
        conn.execute(
            'INSERT INTO app_kv (k, v, updated_at) VALUES (?,?,?) '
            'ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated_at=excluded.updated_at',
            (str(key), json.dumps(value, ensure_ascii=False), time.strftime('%Y-%m-%d %H:%M:%S')),
        )
        conn.commit()
        conn.close()


def delete_kv(key):
    _init()
    with _lock:
        conn = _connect()
        conn.execute('DELETE FROM app_kv WHERE k=?', (str(key),))
        conn.commit()
        conn.close()


# ---------------- QMediaSync 触发日志 ----------------

def record_qms_log(link_id, task_name='', qms_id=None, success=False, message='', source='manual', keep=30):
    """记一条刮削触发日志，每条连接只保留最近 keep 条"""
    _init()
    link_id = str(link_id or '')
    with _lock:
        conn = _connect()
        conn.execute(
            'INSERT INTO qms_trigger_log (link_id, task_name, qms_id, trigger_at, success, message, source)'
            ' VALUES (?,?,?,?,?,?,?)',
            (
                link_id,
                str(task_name or ''),
                int(qms_id) if str(qms_id or '').isdigit() else None,
                time.strftime('%Y-%m-%d %H:%M:%S'),
                1 if success else 0,
                str(message or ''),
                str(source or 'manual'),
            ),
        )
        conn.commit()
        conn.execute(
            '''DELETE FROM qms_trigger_log WHERE link_id=? AND id NOT IN (
                SELECT id FROM qms_trigger_log WHERE link_id=? ORDER BY id DESC LIMIT ?)''',
            (link_id, link_id, int(keep)),
        )
        conn.commit()
        conn.close()


def get_qms_logs(link_id, limit=30):
    """读某条连接的触发日志（新的在前）"""
    _init()
    with _lock:
        conn = _connect()
        cur = conn.execute(
            'SELECT trigger_at, success, message, source, qms_id FROM qms_trigger_log'
            ' WHERE link_id=? ORDER BY id DESC LIMIT ?',
            (str(link_id or ''), int(limit)),
        )
        rows = [
            {
                'trigger_at': r[0],
                'success': bool(r[1]),
                'message': r[2],
                'source': r[3],
                'qms_id': r[4],
            }
            for r in cur.fetchall()
        ]
        conn.close()
        return rows
