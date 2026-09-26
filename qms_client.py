# -*- coding: utf-8 -*-
"""QMediaSync 对接模块

- 连接测试（GET /api/user/info）
- 列刮削任务（GET /api/scrape/pathes）
- 触发刮削启动（POST /api/scrape/pathes/start  body: {"id": <序号>}）
- 连接列表：把「本工具的任务」一对一绑定到「QMS 的刮削目录」，
  任务转存到新文件后只触发它绑定的那些刮削任务。

鉴权：优先 API Key（请求头 X-API-Key），其次账号密码（POST /api/login + X-CSRF-Token）
"""
import threading
import time
import uuid

import requests

from history_db import get_kv, set_kv, record_qms_log

DEFAULT_PORT = 12333
TIMEOUT = 15

# QMS 配置与连接列表存 SQLite（config.json 会被转存进度频繁回写，放里面会被覆盖）
QMS_KEY = 'qms'


def load_cfg():
    """读取 QMS 配置（含连接列表）"""
    cfg = get_kv(QMS_KEY)
    return dict(cfg) if isinstance(cfg, dict) else {}


def save_cfg(cfg):
    """保存 QMS 配置（含连接列表）"""
    set_kv(QMS_KEY, dict(cfg or {}))


def migrate_from_config(storage):
    """把早期写在 config.json 里的 QMS 配置迁到 SQLite，并从 config.json 移除"""
    old = (storage.config or {}).get('qms')
    if not old:
        return False
    stored = get_kv(QMS_KEY)
    moved = False
    if not isinstance(stored, dict):
        save_cfg(old)
        moved = True
    try:
        storage.config.pop('qms', None)
        storage._save_config()
    except Exception:
        pass
    return moved

_sessions = {}
_lock = threading.Lock()


def normalize_config(cfg):
    """整理连接配置（地址栏允许直接粘 http://ip:port）"""
    cfg = dict(cfg or {})
    scheme = (cfg.get('scheme') or 'http').strip() or 'http'
    host = (cfg.get('host') or '').strip()
    port = cfg.get('port')

    if '://' in host:
        scheme, _, host = host.partition('://')
    host = host.strip().strip('/')
    if '/' in host:
        host = host.split('/', 1)[0]
    if ':' in host:
        host, _, tail = host.partition(':')
        if not port:
            port = tail
    try:
        port = int(port) if port else DEFAULT_PORT
    except (TypeError, ValueError):
        port = DEFAULT_PORT

    return {
        'enabled': bool(cfg.get('enabled')),
        'scheme': scheme,
        'host': host,
        'port': port,
        'auth_mode': (cfg.get('auth_mode') or 'api_key').strip() or 'api_key',
        'api_key': (cfg.get('api_key') or '').strip(),
        'username': (cfg.get('username') or '').strip(),
        'password': cfg.get('password') or '',
        'auto_trigger': cfg.get('auto_trigger', True) is not False,
    }


def get_links(cfg):
    """取出连接列表（只返回结构正常的条目）"""
    links = (cfg or {}).get('links') or []
    out = []
    for l in links:
        if not isinstance(l, dict):
            continue
        out.append({
            'id': str(l.get('id') or ''),
            'task_uid': str(l.get('task_uid') or ''),
            'task_order': l.get('task_order'),
            'task_name': str(l.get('task_name') or ''),
            'qms_id': l.get('qms_id'),
            'qms_path': str(l.get('qms_path') or ''),
            'qms_media_type': str(l.get('qms_media_type') or ''),
            'enabled': l.get('enabled', True) is not False,
            'created_at': str(l.get('created_at') or ''),
        })
    return out


def new_link_id():
    return uuid.uuid4().hex[:8]


def match_links_for_task(links, task):
    """找出绑定到某个任务的连接（task_uid 优先，其次 order，再退化为名称）"""
    if not task:
        return []
    uid = str(task.get('task_uid') or '')
    order = task.get('order')
    name = str(task.get('name') or '')
    hit = []
    for l in links:
        if not l.get('enabled', True):
            continue
        if uid and l.get('task_uid') and l['task_uid'] == uid:
            hit.append(l)
            continue
        if order is not None and l.get('task_order') is not None and int(l['task_order']) == int(order):
            hit.append(l)
            continue
        if name and l.get('task_name') and l['task_name'] == name:
            hit.append(l)
    return hit


def merge_cfg(saved, incoming):
    """把「已保存的配置」和「请求里临时传来的（可能还没保存的）连接参数」合并。

    用途：点「测试连接」「刷新刮削目录」时应当用页面上当前填的值，不必先保存。
    - 普通字段（enabled/host/port/scheme/auth_mode/username）：请求里带了就用请求的；
    - 密钥字段（api_key/password）：请求里是非空值才覆盖，留空表示沿用已保存的。
    """
    cfg = dict(saved or {})
    incoming = dict(incoming or {})
    for key in ('enabled', 'host', 'port', 'scheme', 'auth_mode', 'username'):
        if key in incoming:
            cfg[key] = incoming[key]
    for key in ('api_key', 'password'):
        if str(incoming.get(key) or '').strip():
            cfg[key] = incoming[key]
    if incoming.get('clear_api_key'):
        cfg['api_key'] = ''
    if incoming.get('clear_password'):
        cfg['password'] = ''
    return cfg


class QmsError(Exception):
    """QMediaSync 调用异常（消息可直接展示给用户）"""


class QmsClient:
    def __init__(self, cfg):
        self.cfg = normalize_config(cfg)
        if not self.cfg['host']:
            raise QmsError('未填写 QMediaSync 地址')
        self.base = '%s://%s:%s/api' % (self.cfg['scheme'], self.cfg['host'], self.cfg['port'])

    # ---------------- 鉴权 ----------------

    def _login(self):
        key = self.base + '|' + self.cfg['username']
        with _lock:
            cached = _sessions.get(key)
            if cached and cached.get('exp', 0) > time.time():
                return cached
        if not self.cfg['username'] or not self.cfg['password']:
            raise QmsError('未填写 QMediaSync 用户名或密码')
        sess = requests.Session()
        try:
            resp = sess.post(
                self.base + '/login',
                json={
                    'username': self.cfg['username'],
                    'password': self.cfg['password'],
                    'rememberMe': True,
                },
                timeout=TIMEOUT,
            )
        except requests.exceptions.RequestException as e:
            raise QmsError(self._conn_error(e))
        data = self._json(resp)
        if data.get('code') != 200:
            raise QmsError(data.get('message') or '登录失败（检查用户名/密码）')
        payload = data.get('data') or {}
        csrf = payload.get('csrf_token') or sess.cookies.get('csrf_token') or ''
        cached = {'sess': sess, 'csrf': csrf, 'exp': time.time() + 1800}
        with _lock:
            _sessions[key] = cached
        return cached

    def _prepare(self):
        headers = {'Content-Type': 'application/json'}
        if self.cfg['auth_mode'] == 'password':
            info = self._login()
            if info.get('csrf'):
                headers['X-CSRF-Token'] = info['csrf']
            return info['sess'], headers
        if not self.cfg['api_key']:
            raise QmsError('未填写 API Key（在 QMediaSync 的「API 密钥」页面创建）')
        headers['X-API-Key'] = self.cfg['api_key']
        return requests.Session(), headers

    @staticmethod
    def _json(resp):
        try:
            return resp.json()
        except ValueError:
            raise QmsError('返回内容不是 JSON（HTTP %s，可能地址/端口填错）' % resp.status_code)

    def _conn_error(self, e):
        """把底层网络异常翻译成人话"""
        host = '%s:%s' % (self.cfg['host'], self.cfg['port'])
        text = str(e)
        if 'Connection refused' in text or 'NewConnectionError' in text or 'Max retry' in text:
            return '连不上 %s（连接被拒绝），检查地址、端口，以及 QMediaSync 是否在运行' % host
        if 'timed out' in text.lower():
            return '连接 %s 超时，检查网络或防火墙' % host
        if 'Name or service not known' in text or 'nodename nor servname' in text:
            return '地址 %s 解析不了，检查填的地址对不对' % host
        return '连接 %s 失败：%s' % (host, text)

    def _request(self, method, path, payload=None, params=None, _retry=True):
        sess, headers = self._prepare()
        try:
            resp = getattr(sess, method)(
                self.base + path, headers=headers, json=payload, params=params, timeout=TIMEOUT
            )
        except requests.exceptions.RequestException as e:
            raise QmsError(self._conn_error(e))
        if resp.status_code == 401 and _retry and self.cfg['auth_mode'] == 'password':
            with _lock:
                _sessions.pop(self.base + '|' + self.cfg['username'], None)
            return self._request(method, path, payload, params, _retry=False)
        if resp.status_code in (401, 403):
            raise QmsError('鉴权失败（API Key 无效或未授权）')
        data = self._json(resp)
        if not isinstance(data, dict):
            raise QmsError('返回格式异常')
        return data

    # ---------------- 对外能力 ----------------

    def test(self):
        data = self._request('get', '/user/info')
        if data.get('code') != 200:
            raise QmsError(data.get('message') or '连接失败')
        return {
            'username': (data.get('data') or {}).get('username') or '',
            'base': self.base,
        }

    def list_scrape_paths(self):
        data = self._request('get', '/scrape/pathes')
        if data.get('code') != 200:
            raise QmsError(data.get('message') or '获取刮削任务失败')
        out = []
        for it in (data.get('data') or []):
            out.append({
                'id': it.get('id'),
                'media_type': it.get('media_type') or '',
                'source_path': it.get('source_path') or '',
                'source_type': it.get('source_type') or '',
                'scrape_type': it.get('scrape_type') or '',
                'enable_cron': bool(it.get('enable_cron')),
                'cron_expression': it.get('cron_expression') or '',
            })
        out.sort(key=lambda x: (x['id'] is None, x['id']))
        return out

    def start(self, ids=None):
        ids = [int(i) for i in (ids or [])]
        if not ids:
            raise QmsError('还没有绑定任何刮削任务')
        results = []
        for tid in ids:
            try:
                data = self._request('post', '/scrape/pathes/start', {'id': int(tid)})
                ok = data.get('code') == 200
                results.append({
                    'id': tid,
                    'ok': ok,
                    'message': data.get('message') or ('已开始' if ok else '触发失败'),
                })
            except Exception as e:  # noqa: BLE001
                results.append({'id': tid, 'ok': False, 'message': str(e)})
        return results


def build_summary(results):
    ok_ids = [r['id'] for r in results if r.get('ok')]
    fails = [r for r in results if not r.get('ok')]
    parts = []
    if ok_ids:
        parts.append('已触发刮削 ' + '、'.join('#%s' % i for i in ok_ids))
    for r in fails:
        parts.append('#' + str(r.get('id')) + ' 失败：' + str(r.get('message') or ''))
    return '；'.join(parts) or '没有可触发的刮削任务'


def trigger_link(link, source='manual', keep=30):
    """触发一条连接对应的刮削任务，并写入触发日志

    link: 连接字典（需含 id / qms_id / task_name）
    返回 {id, ok, message}
    """
    cfg = load_cfg()
    qms_id = link.get('qms_id')
    task_name = link.get('task_name') or ''
    if not str(qms_id or '').isdigit():
        result = {'id': qms_id, 'ok': False, 'message': '刮削序号无效'}
    else:
        try:
            results = QmsClient(cfg).start([int(qms_id)])
            result = results[0] if results else {'id': qms_id, 'ok': False, 'message': '没有返回结果'}
        except Exception as e:  # noqa: BLE001
            result = {'id': qms_id, 'ok': False, 'message': str(e)}
    try:
        record_qms_log(
            link_id=link.get('id'),
            task_name=task_name,
            qms_id=qms_id,
            success=bool(result.get('ok')),
            message=result.get('message') or '',
            source=source,
            keep=keep,
        )
    except Exception:
        pass
    return result


def trigger_after_transfer(storage, task, transferred_count, dry_run=False):
    """任务转存到新文件后，触发它绑定的 QMS 刮削任务。

    task 传任务字典（用 task_uid/order/name 匹配连接列表）；
    dry_run 只做匹配不真触发（用于自检）。
    """
    cfg = load_cfg()
    norm = normalize_config(cfg)
    if not norm['enabled'] or not norm['auto_trigger']:
        return None
    if not transferred_count:
        return None

    links = match_links_for_task(get_links(cfg), task)
    if not links:
        return None

    valid = [l for l in links if str(l.get('qms_id') or '').isdigit()]
    if not valid:
        return None

    task_name = (task or {}).get('name') or ('任务%s' % (task or {}).get('order', ''))
    ids = sorted({int(l['qms_id']) for l in valid})
    if dry_run:
        return '（预演）任务「%s」绑定刮削 %s，将触发' % (task_name, '、'.join('#%s' % i for i in ids))

    now = time.strftime('%Y-%m-%d %H:%M:%S')
    try:
        results = [trigger_link(l, source='auto') for l in valid]
        summary = build_summary(results)
        cfg['last_trigger_at'] = now
        cfg['last_trigger_result'] = summary
        cfg['last_trigger_task'] = task_name
        cfg['last_trigger_ok'] = all(r.get('ok') for r in results)
        save_cfg(cfg)
        return summary
    except Exception as e:  # noqa: BLE001
        cfg['last_trigger_at'] = now
        cfg['last_trigger_result'] = '触发失败：%s' % e
        cfg['last_trigger_task'] = task_name
        cfg['last_trigger_ok'] = False
        save_cfg(cfg)
        return '触发失败：%s' % e
