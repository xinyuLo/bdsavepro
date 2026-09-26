# -*- coding: utf-8 -*-
"""QMediaSync 对接模块

能力：
- 连接测试（GET /api/user/info）
- 列刮削任务（GET /api/scrape/pathes）/ 触发刮削（POST /api/scrape/pathes/start {"id":N}）
- 查刮削结果（GET /api/scrape/records —— status=renamed 为成功，failed_reason 非空为失败）
- 列同步目录（GET /api/sync/path-list）/ 触发 STRM 同步（POST /api/sync/path/start {"id":N}）
- 连接列表：本工具任务 ↔ QMS 刮削目录（可选再绑一个同步目录），
  任务转存到新文件后延迟触发刮削，轮询到刮削成功后再触发一次 STRM 生成。

鉴权：优先 API Key（X-API-Key），其次账号密码（POST /api/login + X-CSRF-Token）
"""
import threading
import time
import uuid

import requests

from history_db import get_kv, set_kv, record_qms_log, update_qms_log

DEFAULT_PORT = 12333
TIMEOUT = 15

# 转存后等多久再触发刮削（QMS 可能还没扫到刚落盘的文件）
TRIGGER_DELAY_SECONDS = 10
# 刮削成功后再等多久触发 STRM 同步
STRM_DELAY_SECONDS = 10
# 轮询刮削结果的上限与间隔
SCRAPE_WAIT_TIMEOUT = 300
SCRAPE_POLL_INTERVAL = 5
# 任务跑完后稍等片刻再取逐文件记录，避免刚结束还没落库
SCRAPE_RECORDS_GRACE = 5

QMS_KEY = 'qms'


# ---------------- 配置读写（存 SQLite，config.json 会被转存进度覆盖） ----------------

def load_cfg():
    cfg = get_kv(QMS_KEY)
    return dict(cfg) if isinstance(cfg, dict) else {}


def save_cfg(cfg):
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
        'watch_result': cfg.get('watch_result', True) is not False,
    }


# ---------------- 连接列表 ----------------

def get_links(cfg):
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
            'strm_id': l.get('strm_id'),
            'strm_path': str(l.get('strm_path') or ''),
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
    """把已保存配置与请求里临时传来的参数合并（测试连接/刷新目录不必先保存）"""
    cfg = dict(saved or {})
    incoming = dict(incoming or {})
    for key in ('enabled', 'host', 'port', 'scheme', 'auth_mode', 'username',
                'auto_trigger', 'watch_result'):
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
        """列刮削任务"""
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

    def list_sync_paths(self):
        """列同步目录（QMS 的「同步目录管理」，用于生成 STRM）"""
        data = self._request('get', '/sync/path-list')
        if data.get('code') != 200:
            raise QmsError(data.get('message') or '获取同步目录失败')
        payload = data.get('data') or {}
        lst = payload.get('list') if isinstance(payload, dict) else payload
        out = []
        for it in (lst or []):
            out.append({
                'id': it.get('id'),
                'remote_path': it.get('remote_path') or '',
                'base_cid': it.get('base_cid') or '',
                'local_path': it.get('local_path') or '',
                'source_type': it.get('source_type') or '',
                'account_name': it.get('account_name') or '',
                'enable_cron': bool(it.get('enable_cron')),
                'is_running': it.get('is_running') or 0,
                'last_sync_at': it.get('last_sync_at') or 0,
            })
        out.sort(key=lambda x: (x['id'] is None, x['id']))
        return out

    def scrape_records(self, page=1, page_size=100):
        """拉刮削记录（新的在前）"""
        data = self._request('get', '/scrape/records',
                             params={'page': int(page), 'page_size': int(page_size)})
        if data.get('code') != 200:
            raise QmsError(data.get('message') or '获取刮削记录失败')
        payload = data.get('data') or {}
        return (payload.get('list') or []) if isinstance(payload, dict) else (payload or [])

    def scrape_path_detail(self, qms_id):
        """读单个刮削任务的运行状态。

        updated_at 是"上次执行完成时刻"（实测：任务跑完会前进到完成时刻），
        is_running / is_scraping 表示当前是否在处理。
        """
        data = self._request('get', '/scrape/pathes/%d' % int(qms_id))
        if data.get('code') != 200:
            raise QmsError(data.get('message') or '获取刮削任务详情失败')
        d = data.get('data') or {}
        return {
            'id': d.get('id'),
            'is_running': bool(d.get('is_running')),
            'is_scraping': bool(d.get('is_scraping')),
            'updated_at': _to_int(d.get('updated_at')),
            'source_path': d.get('source_path') or '',
            'dest_path': d.get('dest_path') or '',
        }

    def start(self, ids=None):
        """触发刮削"""
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

    def start_sync(self, path_id):
        """触发某个同步目录生成 STRM"""
        data = self._request('post', '/sync/path/start', {'id': int(path_id)})
        ok = data.get('code') == 200
        return {
            'id': path_id,
            'ok': ok,
            'message': data.get('message') or ('已开始同步' if ok else '触发失败'),
        }


def _to_int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def build_summary(results):
    ok_ids = [r['id'] for r in results if r.get('ok')]
    fails = [r for r in results if not r.get('ok')]
    parts = []
    if ok_ids:
        parts.append('已触发刮削 ' + '、'.join('#%s' % i for i in ok_ids))
    for r in fails:
        parts.append('#' + str(r.get('id')) + ' 失败：' + str(r.get('message') or ''))
    return '；'.join(parts) or '没有可触发的刮削任务'


# ---------------- 刮削结果轮询 ----------------

def _scan_source_paths(cfg, qms_ids):
    """拿这些刮削任务的源路径，用于判断记录是否属于本次触发"""
    try:
        paths = QmsClient(cfg).list_scrape_paths()
    except Exception:
        return []
    want = {int(i) for i in qms_ids}
    return [p['source_path'] for p in paths
            if p.get('id') in want and p.get('source_path')]


def wait_scrape_result(cfg, qms_ids, since_ts, timeout=SCRAPE_WAIT_TIMEOUT,
                       interval=SCRAPE_POLL_INTERVAL):
    """等这批刮削任务跑完，再统计逐文件结果。

    实测 QMS 行为：
    - GET /api/scrape/pathes/{id} → updated_at（上次执行完成时刻）、is_running
    - GET /api/scrape/records → 逐文件记录（status=renamed 为成功）
    - QMS 对"已在数据库中"的文件直接跳过（日志：已在数据库中，跳过），
      此时任务成功但**不会产生任何新记录**

    所以分两步：先等任务跑完（可靠），再数本次产生了哪些记录（用来报数量）。
    返回 dict: done / ok / success / failed / files / no_new / detail
    """
    ids = [int(i) for i in qms_ids]
    source_paths = _scan_source_paths(cfg, ids)
    client = QmsClient(cfg)
    deadline = time.time() + timeout
    finished = set()
    last = {}

    while True:
        for qid in ids:
            if qid in finished:
                continue
            try:
                d = client.scrape_path_detail(qid)
            except Exception as e:  # noqa: BLE001
                return {'done': False, 'ok': False, 'success': 0, 'failed': 0,
                        'detail': '查询刮削任务状态失败：%s' % e, 'files': []}
            last[qid] = d
            # 不在运行中，且上次执行时刻晚于我们的触发时刻 → 本次跑完了
            if (not d['is_running'] and not d['is_scraping']
                    and d['updated_at'] >= int(since_ts)):
                finished.add(qid)
        if len(finished) >= len(ids):
            break
        if time.time() >= deadline:
            break
        time.sleep(interval)

    if len(finished) < len(ids):
        waiting = [i for i in ids if i not in finished]
        state = []
        for i in waiting:
            d = last.get(i) or {}
            where = '处理中' if (d.get('is_running') or d.get('is_scraping')) else '未开始'
            state.append('#%s(%s)' % (i, where))
        return {'done': False, 'ok': False, 'success': 0, 'failed': 0, 'files': [],
                'detail': '等待 %d 秒仍未确认刮削完成：%s — QMS 可能较忙，'
                          '可稍后在 QMS 界面查看结果' % (timeout, '、'.join(state))}

    # ---- 任务已跑完，统计本次产生的逐文件记录 ----
    records = []
    for attempt in range(2):
        try:
            records = client.scrape_records(page=1, page_size=100)
        except Exception:  # noqa: BLE001
            records = []
        matched = 0
        for r in records:
            path = r.get('source_full_path') or r.get('path') or ''
            if source_paths and not any(path.startswith(sp) for sp in source_paths):
                continue
            ts = 0
            for k in ('scraped_at', 'renamed_at', 'updated_at', 'created_at'):
                ts = max(ts, _to_int(r.get(k)))
            if ts >= int(since_ts):
                matched += 1
        if matched:
            break
        if attempt == 0:
            time.sleep(SCRAPE_RECORDS_GRACE)

    success, failed, files, fails_detail = 0, 0, [], []
    for r in records:
        path = r.get('source_full_path') or r.get('path') or ''
        if source_paths and not any(path.startswith(sp) for sp in source_paths):
            continue
        ts = 0
        for k in ('scraped_at', 'renamed_at', 'updated_at', 'created_at'):
            ts = max(ts, _to_int(r.get(k)))
        if ts < int(since_ts):
            continue
        fname = r.get('file_name') or ''
        reason = (r.get('failed_reason') or '').strip()
        status = (r.get('status') or '').strip()
        if reason or status in ('failed', 'error'):
            failed += 1
            if reason:
                fails_detail.append('%s（%s）' % (fname, reason))
        else:
            success += 1
            files.append(fname)

    if success == 0 and failed == 0:
        # 任务成功执行，但 QMS 认为目录里没有需要处理的新文件
        return {'done': True, 'ok': True, 'success': 0, 'failed': 0, 'files': [],
                'no_new': True,
                'detail': '刮削任务已执行完成：本次没有需要处理的新文件'
                          '（这些文件此前已刮削过，QMS 会直接跳过）'}

    if failed:
        detail = '刮削完成：成功 %d 个，失败 %d 个' % (success, failed)
        if fails_detail:
            detail += '；失败原因：' + '、'.join(fails_detail[:3])
    else:
        detail = '刮削完成：成功 %d 个' % success
    return {'done': True, 'ok': failed == 0, 'success': success,
            'failed': failed, 'detail': detail, 'files': files}


def trigger_strm(link, log_id=None, source='auto'):
    """触发该连接绑定的 STRM 同步（若有绑定）"""
    strm_id = link.get('strm_id')
    if not str(strm_id or '').isdigit():
        return None
    cfg = load_cfg()
    try:
        res = QmsClient(cfg).start_sync(int(strm_id))
    except Exception as e:  # noqa: BLE001
        res = {'id': strm_id, 'ok': False, 'message': str(e)}
    msg = 'STRM 同步 #%s：%s' % (strm_id, res.get('message') or '')
    if log_id:
        try:
            update_qms_log(log_id, strm_result=msg,
                           ok_delta=1 if res.get('ok') else 0)
        except Exception:
            pass
    return res


def _follow_up(link, log_id, qms_ids, since_ts):
    """后台线程：等刮削结果 → 有实际处理过文件才触发 STRM → 回写日志"""
    cfg = load_cfg()
    if not normalize_config(cfg)['watch_result']:
        return
    result = wait_scrape_result(cfg, qms_ids, since_ts)
    try:
        update_qms_log(log_id, scrape_result=result.get('detail') or '')
    except Exception:
        pass
    if not result.get('done') or not result.get('ok'):
        return
    # "本次没有新文件" 不触发 STRM：没有新处理的文件，就没有新 STRM 要生成
    if not result.get('success'):
        return
    if not str(link.get('strm_id') or '').isdigit():
        return
    time.sleep(STRM_DELAY_SECONDS)
    trigger_strm(link, log_id=log_id)


def trigger_link(link, source='manual', keep=30, watch=True):
    """触发一条连接的刮削任务，写日志，并在后台跟踪结果

    返回 {id, ok, message, log_id}
    """
    cfg = load_cfg()
    qms_id = link.get('qms_id')
    task_name = link.get('task_name') or ''
    if not str(qms_id or '').isdigit():
        result = {'id': qms_id, 'ok': False, 'message': '刮削序号无效', 'log_id': None}
    else:
        try:
            results = QmsClient(cfg).start([int(qms_id)])
            result = results[0] if results else {'id': qms_id, 'ok': False, 'message': '没有返回结果'}
        except Exception as e:  # noqa: BLE001
            result = {'id': qms_id, 'ok': False, 'message': str(e)}

    extra = ''
    if str(link.get('strm_id') or '').isdigit():
        extra = '，成功后自动触发 STRM #%s' % link.get('strm_id')
    message = '%s%s' % (result.get('message') or '', extra)

    log_id = None
    try:
        log_id = record_qms_log(
            link_id=link.get('id'),
            task_name=task_name,
            qms_id=qms_id,
            success=bool(result.get('ok')),
            message=message,
            source=source,
            keep=keep,
        )
    except Exception:
        pass
    result['log_id'] = log_id

    # 触发成功 + 开了结果跟踪 → 起后台线程轮询
    if result.get('ok') and watch and str(qms_id or '').isdigit():
        norm = normalize_config(cfg)
        if norm['watch_result']:
            t = threading.Thread(
                target=_follow_up,
                args=(link, log_id, [int(qms_id)], int(time.time())),
                daemon=True,
                name='qms-watch-%s' % link.get('id'),
            )
            t.start()
    return result


def trigger_after_transfer(storage, task, transferred_count, dry_run=False):
    """任务转存到新文件后，延迟 TRIGGER_DELAY_SECONDS 再触发它绑定的刮削。

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
        return ('（预演）任务「%s」转存完成后将等 %d 秒，再触发刮削 %s'
                % (task_name, TRIGGER_DELAY_SECONDS, '、'.join('#%s' % i for i in ids)))

    def _delayed():
        time.sleep(TRIGGER_DELAY_SECONDS)
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        try:
            results = [trigger_link(l, source='auto') for l in valid]
            summary = build_summary(results)
            c = load_cfg()
            c['last_trigger_at'] = now
            c['last_trigger_result'] = summary
            c['last_trigger_task'] = task_name
            c['last_trigger_ok'] = all(r.get('ok') for r in results)
            save_cfg(c)
        except Exception as e:  # noqa: BLE001
            c = load_cfg()
            c['last_trigger_at'] = now
            c['last_trigger_result'] = '触发失败：%s' % e
            c['last_trigger_task'] = task_name
            c['last_trigger_ok'] = False
            save_cfg(c)

    threading.Thread(target=_delayed, daemon=True, name='qms-delay').start()
    return '转存完成，%d 秒后自动触发刮削 %s' % (TRIGGER_DELAY_SECONDS,
                                        '、'.join('#%s' % i for i in ids))
