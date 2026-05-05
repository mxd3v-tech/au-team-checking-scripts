#!/usr/bin/env python3
"""
PNETLab Checker — единый запускаемый файл.

  python3 main.py          # Flask web-сервер на :5000
  python3 main.py --cli    # curses TUI (интерактивная консоль)
"""
import io
import os
import json
import threading
import queue
import time
import re
import random
import zipfile
import requests
import subprocess
import importlib.util
import traceback
try:
    import curses as _curses_mod
    _CURSES_AVAILABLE = True
except ImportError:
    _CURSES_AVAILABLE = False
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, request, jsonify, send_file, session

app = Flask(__name__)
app.secret_key = 'a_secure_random_string_12345'
BASE_URL = "http://192.168.102.39"
CHECKER_DIR = BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIES_FILE = os.path.join(CHECKER_DIR, "cookies.txt")
VARIANTS_DIR = os.path.join(CHECKER_DIR, "generated_variants")
LOGS_DIR = os.path.join(CHECKER_DIR, "logs")
ROOMS_FILE = os.path.join(CHECKER_DIR, "check_rooms.json")
TEMPLATE_PATH = os.path.join(CHECKER_DIR, "assignment_template.py")
VARIANTS_DOC_PATH = os.path.join(CHECKER_DIR, "L2_KR_test2.txt")
NODES_FILE = os.path.join(CHECKER_DIR, "nodes.json")
os.makedirs(LOGS_DIR, exist_ok=True)

USERS_CACHE = {'data': [], 'timestamp': 0, 'ttl': 30}
USERS_CACHE_LOCK = threading.Lock()
last_check_name = None
last_summary_file = None
last_node_ip_safe = None  # IP узла последней проверки (точки заменены на тире)
last_check_lock = threading.Lock()
last_results = {}
results_lock = threading.Lock()
log_queue = queue.Queue()
variants_lock = threading.Lock()

# --- Состояние текущей проверки ---
check_state = {
    'running': False,
    'cancelled': False,
    'current_user': '',
    'total': 0,
    'done': 0,
}
check_state_lock = threading.Lock()
cancel_event = threading.Event()

# --- Состояние операции запуска нод ---
start_state = {
    'running': False,
    'cancelled': False,
    'current_user': '',
    'total': 0,
    'done': 0,
}
start_state_lock = threading.Lock()
start_cancel_event = threading.Event()
start_log_queue = queue.Queue()
start_results = {}
start_results_lock = threading.Lock()

# --- Состояние операции остановки нод ---
stop_state = {
    'running': False,
    'cancelled': False,
    'current_user': '',
    'total': 0,
    'done': 0,
}
stop_state_lock = threading.Lock()
stop_cancel_event = threading.Event()
stop_log_queue = queue.Queue()
stop_results = {}
stop_results_lock = threading.Lock()


# --- Хелперы запуска нод ---
GROUP_PREFIXES = {
    'm1': 'Student-m1-',
    'm2': 'Student-m2-',
}


def filter_users_by_group(usernames, group):
    """group ∈ {'m1','m2','all'} — возвращает отфильтрованный список имён."""
    if group == 'all':
        return list(usernames)
    prefix = GROUP_PREFIXES.get(group)
    if not prefix:
        return []
    return [u for u in usernames if u.startswith(prefix)]


def _fetch_pod_mapping(sess, ip):
    """Получает (user_to_pod, pod_labs) с узла. Возвращает (mapping, error)."""
    try:
        ur = sess.post(f"http://{ip}/store/public/admin/lab_sessions/mapUser", timeout=15)
        nr = sess.post(f"http://{ip}/store/public/admin/node_sessions/getConsume", timeout=15)
        if ur.status_code not in (200, 202) or nr.status_code not in (200, 202):
            return None, f"mapUser/getConsume вернули {ur.status_code}/{nr.status_code}"
        users_data = safe_parse_users_data(ur.json().get("data", {}))
        nodes_data = nr.json().get("data", [])
    except Exception as e:
        return None, f"Ошибка запроса pod-маппинга: {e}"

    pod_to_user = {}
    for v in users_data.values():
        if isinstance(v, dict) and "pod" in v and "username" in v:
            try:
                pod_to_user[int(v["pod"])] = v["username"]
            except (ValueError, TypeError):
                continue
    user_to_pod = {v: k for k, v in pod_to_user.items()}
    pod_labs = defaultdict(set)
    for item in nodes_data:
        if isinstance(item, dict):
            pod = item.get("node_session_pod")
            lab = item.get("node_session_lab")
            if pod is not None and lab is not None:
                try:
                    pod_labs[int(pod)].add(int(lab))
                except (ValueError, TypeError):
                    continue
    return {"user_to_pod": user_to_pod, "pod_labs": pod_labs}, None


def _resp_summary(resp):
    """Короткое описание HTTP-ответа для лога: status + тело (обрезано)."""
    try:
        body = resp.text or ''
    except Exception:
        body = ''
    body = body.replace('\n', ' ').replace('\r', ' ').strip()
    if len(body) > 160:
        body = body[:160] + '…'
    return f"HTTP {resp.status_code} body={body}"


def _is_ok_response(resp):
    """PNETLab часто отвечает HTTP 200 с {'code':400,'status':'fail'} в теле,
    либо HTTP 200 при успешной операции. Считаем успехом, если HTTP 2xx и
    либо тело не JSON, либо JSON code в (200,201,202) / status='success'."""
    if resp.status_code not in (200, 201, 202):
        return False, f"HTTP {resp.status_code}"
    try:
        d = resp.json()
    except Exception:
        return True, None  # не-JSON ответ — считаем успехом
    if not isinstance(d, dict):
        return True, None
    code = d.get('code')
    status = (d.get('status') or '').lower()
    if code in (200, 201, 202) or status in ('success', 'ok'):
        return True, None
    if code is None and status == '':
        return True, None
    msg = d.get('message') or d.get('msg') or str(d)
    return False, f"code={code} status={status} msg={msg[:120] if isinstance(msg,str) else msg}"


# Шаблоны URL'ов для управления одной нодой (start/stop). Подставляются:
# ip, lab_id, pod, nid, session_node_id (из node_sessions/getConsume), action.
# Подтверждённые рабочие варианты (DevTools):
#   POST /api/labs/session/nodes/start  body={"id": nid}
#   POST /api/labs/session/nodes/stop   body={"id": nid}
_NODE_ACTION_URL_TEMPLATES = [
    ("POST", "http://{ip}/api/labs/session/nodes/{action}"),
    ("PUT",  "http://{ip}/api/labs/session/nodes/{action}"),
    ("PUT",  "http://{ip}/api/labs/session/nodes/{nid}/{action}"),
    ("POST", "http://{ip}/api/labs/session/nodes/{nid}/{action}"),
    ("PUT",  "http://{ip}/api/pod/{pod}/labs/{lab_id}/nodes/{nid}/{action}"),
    ("PUT",  "http://{ip}/api/labs/{lab_id}/nodes/{nid}/{action}"),
    ("POST", "http://{ip}/store/public/admin/node_sessions/{action}"),
    ("PUT",  "http://{ip}/api/labs/session/nodes/{session_node_id}/{action}"),
]
# Раздельные подсказки для start и stop, чтобы один не сбивал другой
_NODE_ACTION_URL_HINT = {
    'start': {"method": None, "template": None},
    'stop':  {"method": None, "template": None},
}
_NODE_ACTION_HINT_LOCK = threading.Lock()


def _try_node_action(sess, ip, lab_id, pod, nid, session_node_id, action, prefer=None):
    """Перебирает кандидатов URL для action ('start'|'stop') одной ноды.
    Возвращает (success, used_method, used_template, last_response)."""
    if action not in ('start', 'stop'):
        raise ValueError(f"unknown action: {action}")
    candidates = list(_NODE_ACTION_URL_TEMPLATES)
    if prefer and prefer in candidates:
        candidates.remove(prefer)
        candidates.insert(0, prefer)

    last_resp = None
    extra_headers = {
        "Referer": f"http://{ip}/legacy/topology",
        "Origin": f"http://{ip}",
        "X-Requested-With": "XMLHttpRequest",
    }
    for method, tmpl in candidates:
        if "{session_node_id}" in tmpl and not session_node_id:
            continue
        url = tmpl.format(ip=ip, lab_id=lab_id, pod=pod, nid=nid,
                          session_node_id=session_node_id or nid, action=action)
        body = None
        if f"/api/labs/session/nodes/{action}" in url:
            body = {"id": nid}
        elif f"/node_sessions/{action}" in url:
            body = {"id": session_node_id or nid}
        try:
            kwargs = {"timeout": 20, "headers": extra_headers}
            if body is not None:
                kwargs["json"] = body
            r = sess.put(url, **kwargs) if method == "PUT" else sess.post(url, **kwargs)
        except Exception as e:
            last_resp = e
            continue
        last_resp = r
        ok, _why = _is_ok_response(r)
        if ok:
            return True, method, tmpl, r
    return False, None, None, last_resp


def _fetch_session_node_ids(sess, ip, lab_id, pod):
    """Достаёт {topology_node_id_str: session_node_id} из node_sessions/getConsume."""
    out = {}
    try:
        r = sess.post(f"http://{ip}/store/public/admin/node_sessions/getConsume", timeout=15)
        if r.status_code not in (200, 202):
            return out
        data = r.json().get("data", [])
        for it in data:
            if not isinstance(it, dict):
                continue
            if int(it.get("node_session_lab", -1)) != int(lab_id):
                continue
            if pod is not None and int(it.get("node_session_pod", -1)) != int(pod):
                continue
            # Возможные ключи под session-id и id ноды в топологии
            sid = it.get("node_session_id") or it.get("id")
            tid = it.get("node_session_node_id") or it.get("node_id") or it.get("nid")
            if sid is not None and tid is not None:
                out[str(tid)] = sid
    except Exception:
        pass
    return out


def _nodes_action_for_one_user(node, username, lab_ids, action, log_cb, cancel_ev,
                               sess=None, user_pod=None, node_workers=4):
    """Запускает все ноды во всех указанных лабах одного пользователя.
    Использует ПЕРЕДАННУЮ серверную сессию (PNETLab держит сессию по api-юзеру
    глобально — параллельные login/factory/join ломают друг друга)."""
    ip = node["ip"]
    own_session = sess is None
    if own_session:
        sess, err = login_to_node(ip, node["username"], node["password"])
        if err or sess is None:
            if log_cb:
                log_cb(f"❌ {username}: авторизация не удалась — {err}")
            return {"started": 0, "total": 0, "labs": [], "error": err or "auth failed"}

    # Если pod не передан — попробуем достать
    if user_pod is None:
        try:
            m, _ = _fetch_pod_mapping(sess, ip)
            if m:
                user_pod = m["user_to_pod"].get(username)
        except Exception:
            pass

    total_started = 0
    total_nodes = 0
    labs_info = []
    for lab_id in lab_ids:
        if cancel_ev is not None and cancel_ev.is_set():
            break
        try:
            jr = sess.post(f"http://{ip}/api/labs/session/factory/join",
                           json={"lab_session": lab_id}, timeout=15)
            ok_join, why = _is_ok_response(jr)
            if not ok_join and log_cb:
                log_cb(f"⚠️ {username} lab={lab_id}: factory/join — {why}; "
                       f"{_resp_summary(jr)}")
            # Небольшая пауза — серверу нужно успеть переключить контекст лабы
            time.sleep(0.3)
            tr = sess.get(f"http://{ip}/api/labs/session/topology", timeout=15)
            if tr.status_code != 200:
                if log_cb:
                    log_cb(f"⚠️ {username} lab={lab_id}: топология {_resp_summary(tr)}")
                labs_info.append({"lab_id": lab_id, "started": 0, "total": 0,
                                  "error": f"topology HTTP {tr.status_code}"})
                continue
            topology = tr.json().get("data", {}).get("nodes", {})
            if not isinstance(topology, dict) or not topology:
                if log_cb:
                    log_cb(f"⚠️ {username} lab={lab_id}: пустая топология")
                labs_info.append({"lab_id": lab_id, "started": 0, "total": 0,
                                  "error": "empty topology"})
                continue

            # Маппинг topology id -> session-node-id (для шаблонов, требующих его)
            session_id_map = _fetch_session_node_ids(sess, ip, lab_id, user_pod)

            started = 0
            failed_samples = []
            node_ids = list(topology.keys())

            # Текущая подсказка о рабочем URL (общая для процесса)
            with _NODE_ACTION_HINT_LOCK:
                hint = (_NODE_ACTION_URL_HINT[action]["method"], _NODE_ACTION_URL_HINT[action]["template"])
            prefer = hint if hint[0] else None

            # Сначала — одиночный probe ноды, чтобы зафиксировать рабочий URL
            # (один раз на процесс). Потом параллелим остальные.
            probe_nid = node_ids[0]
            ok, used_m, used_t, resp = _try_node_action(
                sess, ip, lab_id, user_pod, probe_nid,
                session_id_map.get(str(probe_nid)), action=action, prefer=prefer,
            )
            if ok:
                started += 1
                if used_t and not prefer:
                    with _NODE_ACTION_HINT_LOCK:
                        if _NODE_ACTION_URL_HINT[action]["template"] is None:
                            _NODE_ACTION_URL_HINT[action]["method"] = used_m
                            _NODE_ACTION_URL_HINT[action]["template"] = used_t
                            if log_cb:
                                log_cb(f"ℹ Найден рабочий URL для {action} ноды: "
                                       f"{used_m} {used_t}")
                    prefer = (used_m, used_t)
            else:
                if log_cb and len(failed_samples) < 3:
                    if hasattr(resp, 'status_code'):
                        failed_samples.append(
                            f"node={probe_nid}: все варианты URL вернули ошибку; "
                            f"последний — {_resp_summary(resp)}"
                        )
                    else:
                        failed_samples.append(
                            f"node={probe_nid}: все варианты URL дали исключение ({resp})"
                        )

            # Параллельная обработка остальных нод (одна и та же серверная сессия,
            # factory/join уже сделан — это безопасно)
            rest = node_ids[1:]
            if rest and not (cancel_ev is not None and cancel_ev.is_set()):
                workers = max(1, min(node_workers, len(rest)))
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    fut_to_nid = {
                        ex.submit(
                            _try_node_action,
                            sess, ip, lab_id, user_pod, nid,
                            session_id_map.get(str(nid)), action, prefer
                        ): nid for nid in rest
                    }
                    for fut in as_completed(fut_to_nid):
                        nid = fut_to_nid[fut]
                        try:
                            ok2, _m, _t, resp2 = fut.result()
                        except Exception as e:
                            ok2, resp2 = False, e
                        if ok2:
                            started += 1
                        else:
                            if log_cb and len(failed_samples) < 3:
                                if hasattr(resp2, 'status_code'):
                                    failed_samples.append(
                                        f"node={nid}: все варианты URL вернули ошибку; "
                                        f"последний — {_resp_summary(resp2)}"
                                    )
                                else:
                                    failed_samples.append(
                                        f"node={nid}: все варианты URL дали исключение ({resp2})"
                                    )

            total_started += started
            total_nodes += len(node_ids)
            action_label = "запущено" if action == 'start' else "остановлено"
            labs_info.append({"lab_id": lab_id, "started": started, "total": len(node_ids),
                              "error": None})
            if log_cb:
                log_cb(f"▶ {username} lab={lab_id}: {action_label} {started}/{len(node_ids)} нод")
                for s in failed_samples:
                    log_cb(f"   ↳ {username} lab={lab_id} {s}")
                # Если по этой лабе вообще ничего не стартовало — пишем дамп
                # для диагностики (один раз на лабу).
                if started == 0 and node_ids:
                    # Проверяем, не является ли ошибка серверной (HTTP 500 от PNETLab/Laravel)
                    last_resp_obj = None
                    for _m, _t in _NODE_ACTION_URL_TEMPLATES:
                        # failed_samples уже содержит текст последней ошибки
                        pass
                    has_http500 = any("HTTP 500" in s for s in failed_samples)
                    has_laravel_log = any("laravel.log" in s for s in failed_samples)
                    if has_http500 or has_laravel_log:
                        log_cb(f"   ⚠️ PNETLab вернул HTTP 500 — это серверная ошибка.")
                        log_cb(f"   ⚠️ Возможная причина: нет прав на запись в Laravel-лог.")
                        log_cb(f"   ⚠️ Исправление на сервере PNETLab:")
                        log_cb(f"   ⚠️   chmod -R 777 /opt/unetlab/html/store/storage/logs/")
                        log_cb(f"   ⚠️   chown -R www-data:www-data /opt/unetlab/html/store/storage/")
                    sample_nid = node_ids[0]
                    sample_topo = topology.get(sample_nid, {})
                    log_cb(f"   ▢ DEBUG topology[{sample_nid}] keys: "
                           f"{list(sample_topo.keys()) if isinstance(sample_topo, dict) else type(sample_topo)}")
                    log_cb(f"   ▢ DEBUG session_id_map sample: "
                           f"{dict(list(session_id_map.items())[:3])}")
                    log_cb(f"   ▢ DEBUG factory/join: {_resp_summary(jr)}")
        except Exception as e:
            if log_cb:
                log_cb(f"❌ {username} lab={lab_id}: исключение {e}")
            labs_info.append({"lab_id": lab_id, "started": 0, "total": 0, "error": str(e)})

    return {"started": total_started, "total": total_nodes, "labs": labs_info, "error": None}


def start_nodes_for_users(node, usernames, *, all_labs=False,
                          log_cb=None, progress_cb=None,
                          max_workers=6, cancel_ev=None):
    """Запускает все ноды у каждого из указанных пользователей.

    PNETLab держит серверную сессию по api-юзеру глобально: параллельный
    login и factory/join из разных воркеров инвалидируют друг друга
    (HTTP 412 'session timed out'). Поэтому пользователи обрабатываются
    ПОСЛЕДОВАТЕЛЬНО — на одной общей сессии. Параллелизм оставлен только
    внутри одной лабы (PUT/POST nodes/start), где factory/join уже сделан.
    Параметр max_workers тут управляет числом параллельных PUT'ов внутри лабы.
    """
    ip = node["ip"]
    if log_cb:
        log_cb(f"=== Старт операции на узле {node.get('name')} ({ip}) ===")
    sess, err = login_to_node(ip, node["username"], node["password"])
    if err or sess is None:
        msg = f"❌ Не удалось авторизоваться на {ip}: {err}"
        if log_cb:
            log_cb(msg)
        return {u: {"started": 0, "total": 0, "labs": [], "error": err or "auth failed"}
                for u in usernames}

    mapping, err = _fetch_pod_mapping(sess, ip)
    if err:
        if log_cb:
            log_cb(f"❌ {err}")
        return {u: {"started": 0, "total": 0, "labs": [], "error": err}
                for u in usernames}
    user_to_pod = mapping["user_to_pod"]
    pod_labs = mapping["pod_labs"]

    # Подготовка задач: (username, [lab_ids])
    tasks = []
    skipped = {}
    for username in usernames:
        pod = user_to_pod.get(username)
        if pod is None or pod not in pod_labs or not pod_labs[pod]:
            if log_cb:
                log_cb(f"⚠️ {username}: нет активных лаб")
            skipped[username] = {"started": 0, "total": 0, "labs": [],
                                 "error": "no active labs"}
            continue
        sorted_labs = sorted(pod_labs[pod])
        lab_ids = sorted_labs if all_labs else [sorted_labs[0]]
        tasks.append((username, lab_ids))

    results = dict(skipped)
    total = len(tasks)
    done = 0
    if log_cb:
        log_cb(f"Будет запущено пользователей: {total} последовательно "
               f"(node_workers={max_workers}, all_labs={'да' if all_labs else 'нет'})")
    if progress_cb:
        progress_cb(0, total, '')

    if total == 0:
        if log_cb:
            log_cb("=== ЗАВЕРШЕНО (нет задач) ===")
        if progress_cb:
            progress_cb(0, 0, '')
        return results

    for username, lab_ids in tasks:
        if cancel_ev is not None and cancel_ev.is_set():
            if log_cb:
                log_cb("⛔ Отменено пользователем")
            break
        if progress_cb:
            progress_cb(done, total, username)
        try:
            results[username] = _nodes_action_for_one_user(
                node, username, lab_ids, 'start', log_cb, cancel_ev,
                sess=sess, user_pod=user_to_pod.get(username),
                node_workers=max_workers,
            )
        except Exception as e:
            results[username] = {"started": 0, "total": 0, "labs": [], "error": str(e)}
            if log_cb:
                log_cb(f"❌ {username}: исключение — {e}")
            # Если упала вся сессия — переавторизация и продолжаем
            try:
                new_sess, login_err = login_to_node(ip, node["username"], node["password"])
                if new_sess is not None:
                    sess = new_sess
                    if log_cb:
                        log_cb("🔄 Переавторизация выполнена, продолжаем")
            except Exception:
                pass
        done += 1
        if progress_cb:
            progress_cb(done, total, username)

    if log_cb:
        if cancel_ev is not None and cancel_ev.is_set():
            log_cb("=== ПРЕРВАНО ===")
        else:
            log_cb("=== ЗАВЕРШЕНО ===")
    return results


# --- Управление узлами ---
def load_nodes():
    if not os.path.exists(NODES_FILE):
        default_node = {
            "name": "pnet-main",
            "ip": "192.168.102.39",
            "username": "api",
            "password": "jw0mUEnY3"
        }
        save_nodes([default_node])
        return [default_node]
    with open(NODES_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_nodes(nodes):
    with open(NODES_FILE, 'w', encoding='utf-8') as f:
        json.dump(nodes, f, indent=2, ensure_ascii=False)


# --- Вспомогательные функции ---
def load_cookies(filepath):
    cookies = {}
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath) as f:
            for line in f:
                if line.startswith('#') or not line.strip():
                    continue
                parts = line.strip().split('\t')
                if len(parts) >= 7:
                    cookies[parts[5]] = parts[6]
        return cookies
    except Exception as e:
        print(f"Ошибка загрузки cookies: {e}")
        return None

def get_authorized_session():
    s = requests.Session()
    s.headers.update({
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{BASE_URL}/",
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json, text/plain, */*"
    })
    cookies = load_cookies(COOKIES_FILE)
    if cookies:
        for name, value in cookies.items():
            s.cookies.set(name, value)
    else:
        payload = {"username": "api", "password": "jw0mUEnY3", "html": "1"}
        resp = s.post(f"{BASE_URL}/store/public/auth/login/login", json=payload)
        if resp.status_code in [200, 202]:
            token = s.cookies.get("token")
            if token:
                with open(COOKIES_FILE, "w") as f:
                    f.write("# Netscape HTTP Cookie File\n")
                    f.write(f"192.168.102.39\tFALSE\t/\tFALSE\t0\ttoken\t{token}\n")
    return s

def parse_qemu_by_lab(lab_id):
    name_to_ports = {}
    try:
        proc = subprocess.Popen(['ps', 'aux'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        stdout, _ = proc.communicate()
        for line in stdout.splitlines():
            if "qemu-system" not in line or f"/opt/unetlab/tmp/{lab_id}/" not in line:
                continue
            name_match = re.search(r'-name\s+([^\s]+)', line)
            if not name_match:
                continue
            name = name_match.group(1)
            ssh_match = re.search(r'hostfwd=tcp::(\d+)-:(22|2026)', line)
            name_to_ports[name] = ssh_match.group(1) if ssh_match else "N/A"
    except Exception as e:
        print(f"Ошибка ps aux: {e}")
    return name_to_ports

def login_to_node(ip, username, password):
    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"http://{ip}/",
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json, text/plain, */*"
    }
    s = requests.Session()
    s.headers.update(headers)
    login_endpoints = [
        "/store/public/auth/login/login",
        "/store/public/auth/login/offline",
        "/api/auth/login",
    ]
    payload = {"username": username, "password": password, "html": "1"}
    last_error = None
    for endpoint in login_endpoints:
        try:
            resp = s.post(f"http://{ip}{endpoint}", json=payload, timeout=10)
            if resp.status_code in [200, 202]:
                if s.cookies.get("token"):
                    return s, None
                try:
                    data = resp.json()
                    if data.get("code") == 200 or data.get("status") == "success":
                        return s, None
                except (ValueError, json.JSONDecodeError):
                    pass
                last_error = f"Авторизация прошла (статус {resp.status_code}), но токен не получен."
            else:
                last_error = f"Эндпоинт {endpoint} вернул статус {resp.status_code}."
        except requests.exceptions.ConnectionError:
            # Не выходим сразу: PNETLab может слушать только на части
            # эндпоинтов (например, /api/auth/login на новых сборках).
            # Продолжаем перебирать оставшиеся варианты.
            last_error = f"Не удалось подключиться к {ip} через {endpoint}."
            continue
        except requests.exceptions.Timeout:
            last_error = f"Таймаут при подключении к {ip} через {endpoint}."
            continue
    return None, last_error or f"Не удалось авторизоваться на узле {ip}."

def safe_parse_users_data(raw_data):
    if isinstance(raw_data, str):
        try:
            return json.loads(raw_data)
        except json.JSONDecodeError:
            return {}
    elif isinstance(raw_data, dict):
        return raw_data
    return {}

# Алиас для совместимости с CLI-кодом
safe_parse = safe_parse_users_data


# --- API функции ---
def get_available_users():
    current_time = time.time()
    # Под локом, чтобы при истечении TTL не запустить параллельно несколько
    # сетевых походов на PNETLab (thundering-herd) и не получить рассогласованную
    # запись data/timestamp.
    with USERS_CACHE_LOCK:
        if current_time - USERS_CACHE['timestamp'] < USERS_CACHE['ttl']:
            return USERS_CACHE['data']
    try:
        s = get_authorized_session()
        resp_users = s.post(f"{BASE_URL}/store/public/admin/lab_sessions/mapUser")
        resp_nodes = s.post(f"{BASE_URL}/store/public/admin/node_sessions/getConsume")
        if resp_users.status_code not in [200, 202] or resp_nodes.status_code not in [200, 202]:
            return []
        users_data = safe_parse_users_data(resp_users.json().get("data", {}))
        nodes_data = resp_nodes.json().get("data", [])
        pod_to_user = {}
        for v in users_data.values():
            if isinstance(v, dict) and "pod" in v and "username" in v:
                try:
                    pod_to_user[int(v["pod"])] = v["username"]
                except (ValueError, TypeError):
                    continue
        pod_labs = defaultdict(set)
        for node in nodes_data:
            if isinstance(node, dict):
                pod = node.get("node_session_pod")
                lab = node.get("node_session_lab")
                if pod is not None and lab is not None:
                    try:
                        pod_labs[int(pod)].add(int(lab))
                    except (ValueError, TypeError):
                        continue
        user_list = []
        for pod, labs in sorted(pod_labs.items()):
            user = pod_to_user.get(pod, f"unknown(pod={pod})")
            user_list.append({"name": user, "pod": pod, "lab_count": len(labs)})
        with USERS_CACHE_LOCK:
            USERS_CACHE['data'] = user_list
            USERS_CACHE['timestamp'] = current_time
        return user_list
    except Exception as e:
        print(f"Ошибка в get_available_users: {e}")
        return []

def get_available_checks():
    checks = []
    for f in sorted(os.listdir(CHECKER_DIR)):
        if f.startswith("assignment_checker_") and f.endswith(".py"):
            name = f[len("assignment_checker_"):-3]
            checks.append({"name": name, "description": f"Проверка {name}", "type": "static"})
    if os.path.exists(VARIANTS_DIR):
        for f in sorted(os.listdir(VARIANTS_DIR)):
            if f.startswith("assignment_checker_task") and f.endswith(".py"):
                name = f[len("assignment_checker_"):-3]
                desc_file = os.path.join(VARIANTS_DIR, f"task{name.split('task')[-1]}_description.txt")
                desc = "Описание недоступно."
                if os.path.exists(desc_file):
                    try:
                        with open(desc_file, 'r', encoding='utf-8') as df:
                            desc = df.read().strip()
                            if len(desc) > 100:
                                desc = desc.split('\n')[0][:100] + "..."
                    except:
                        desc = f"Сгенерированный вариант: {name}"
                checks.append({"name": name, "description": desc, "type": "generated"})
    return checks

def generate_assignment_variants():
    # Lock защищает от двух параллельных нажатий "сгенерировать вариант",
    # которые ранее могли выбрать одинаковый номер и затереть файлы друг друга.
    with variants_lock:
        return _generate_assignment_variants_locked()


def _generate_assignment_variants_locked():
    if not os.path.exists(TEMPLATE_PATH) or not os.path.exists(VARIANTS_DOC_PATH):
        return {"success": False, "message": "Шаблон или описание задания не найдены"}
    os.makedirs(VARIANTS_DIR, exist_ok=True)
    with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
        template = f.read()
    with open(VARIANTS_DOC_PATH, 'r', encoding='utf-8') as f:
        desc = f.read()
    vlan1 = random.randint(80, 95)
    vlan2 = random.randint(80, 95)
    while vlan2 == vlan1:
        vlan2 = random.randint(80, 95)
    ip1 = vlan1
    ip2 = vlan2
    root = random.choice(["SW-HQ1", "SW-HQ2"])
    qinq = random.randint(170, 190)
    content = template
    content = content.replace("{VLAN_PC1_2_5_BASE}", str(vlan1))
    content = content.replace("{VLAN_PC3_4_BASE}", str(vlan2))
    content = content.replace("{IP_OCTET_PC1_2_5_BASE}", str(ip1))
    content = content.replace("{IP_OCTET_PC3_4_BASE}", str(ip2))
    content = content.replace("{ROOT_BRIDGE_HQ}", root)
    content = content.replace("{QINQ_OUTER_VLAN}", str(qinq))
    desc = desc.replace("VLAN 85", f"VLAN {vlan1}")
    desc = desc.replace("VLAN 90", f"VLAN {vlan2}")
    desc = desc.replace("85.85.85.0/24", f"{ip1}.{ip1}.{ip1}.0/24")
    desc = desc.replace("90.90.90.0/24", f"{ip2}.{ip2}.{ip2}.0/24")
    desc = desc.replace("SW-HQ1 должен быть корневым мостом", f"{root} должен быть корневым мостом")
    desc = desc.replace("VLAN 180", f"VLAN {qinq}")
    existing = [f for f in os.listdir(VARIANTS_DIR) if f.startswith("assignment_checker_task") and f.endswith(".py")]
    used = {int(re.search(r'task(\d+)\.py', f).group(1)) for f in existing if re.search(r'task(\d+)\.py', f)}
    # Берём минимальный свободный номер вместо next(iter(set(...))): порядок
    # элементов set зависит от хеш-сидов и непредсказуем, что приводило к
    # коллизиям при параллельных вызовах.
    free_numbers = set(range(100, 1000)) - used
    if free_numbers:
        new_num = min(free_numbers)
    else:
        new_num = random.randint(100, 999)
    py = f"assignment_checker_task{new_num}.py"
    txt = f"task{new_num}_description.txt"
    with open(os.path.join(VARIANTS_DIR, py), 'w', encoding='utf-8') as f:
        f.write(content)
    with open(os.path.join(VARIANTS_DIR, txt), 'w', encoding='utf-8') as f:
        f.write(desc)
    return {"success": True, "message": f"Сгенерирован вариант task{new_num}"}


# --- Flask маршруты ---
@app.route('/')
def index():
    if session.get('logged_in'):
        users = get_available_users()
        checks = get_available_checks()
        nodes = load_nodes()
        return render_template('index.html', login_required=False, users=users, checks=checks, nodes=nodes)
    return render_template('index.html', login_required=True, users=[], checks=[], nodes=[])

@app.route('/login', methods=['POST'])
def login():
    if request.form.get('username') == 'admin' and request.form.get('password') == 'password':
        session['logged_in'] = True
        return jsonify(success=True)
    return jsonify(success=False, message="Неверный логин или пароль.")

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return jsonify(success=True)


# --- Узлы ---
@app.route('/get_nodes')
def get_nodes():
    if not session.get('logged_in'):
        return jsonify(success=False, message="Необходима авторизация.")
    nodes = load_nodes()
    return jsonify(success=True, nodes=[{"name": n["name"], "ip": n["ip"], "username": n["username"]} for n in nodes])

@app.route('/add_node', methods=['POST'])
def add_node():
    if not session.get('logged_in'):
        return jsonify(success=False, message="Необходима авторизация.")
    data = request.get_json()
    name, ip, username, password = data.get('name'), data.get('ip'), data.get('username'), data.get('password')
    if not all([name, ip, username, password]):
        return jsonify(success=False, message="Все поля обязательны.")
    if not re.match(r'^\d{1,3}(\.\d{1,3}){3}$', ip):
        return jsonify(success=False, message="Некорректный IP-адрес.")
    nodes = load_nodes()
    if any(n['name'] == name for n in nodes):
        return jsonify(success=False, message="Узел с таким именем уже существует.")
    nodes.append({"name": name, "ip": ip, "username": username, "password": password})
    save_nodes(nodes)
    return jsonify(success=True)

@app.route('/delete_node/<node_name>', methods=['DELETE'])
def delete_node(node_name):
    if not session.get('logged_in'):
        return jsonify(success=False, message="Необходима авторизация.")
    nodes = load_nodes()
    nodes = [n for n in nodes if n["name"] != node_name]
    save_nodes(nodes)
    return jsonify(success=True, message="Узел удалён.")

@app.route('/get_node_info/<node_name>')
def get_node_info(node_name):
    if not session.get('logged_in'):
        return jsonify(success=False)
    nodes = load_nodes()
    node = next((n for n in nodes if n["name"] == node_name), None)
    if not node:
        return jsonify(success=False, message="Узел не найден.")
    try:
        ip = node["ip"]
        s, error = login_to_node(ip, node["username"], node["password"])
        if error:
            return jsonify(success=False, message=error)
        users_resp = s.post(f"http://{ip}/store/public/admin/lab_sessions/mapUser", timeout=10)
        nodes_resp = s.post(f"http://{ip}/store/public/admin/node_sessions/getConsume", timeout=10)
        try:
            users_data = safe_parse_users_data(users_resp.json().get("data", {}))
        except (ValueError, json.JSONDecodeError):
            users_data = {}
        try:
            nodes_data = nodes_resp.json().get("data", [])
        except (ValueError, json.JSONDecodeError):
            nodes_data = []
        pod_to_user = {int(v["pod"]): v["username"] for v in users_data.values()
                       if isinstance(v, dict) and "pod" in v and "username" in v}
        pod_labs = defaultdict(set)
        for item in nodes_data:
            if isinstance(item, dict):
                pod = item.get("node_session_pod")
                lab = item.get("node_session_lab")
                if pod is not None and lab is not None:
                    pod_labs[int(pod)].add(int(lab))
        users = [{"username": pod_to_user.get(pod, f"unknown(pod={pod})"), "pod": pod,
                  "running_labs": sorted(labs)} for pod, labs in sorted(pod_labs.items())]
        return jsonify(success=True, info={
            "name": node["name"], "ip": ip, "users": users,
            "total_running_labs": len(nodes_data)
        })
    except Exception as e:
        return jsonify(success=False, message=str(e))

@app.route('/get_node_users/<node_name>')
def get_node_users_route(node_name):
    if not session.get('logged_in'):
        return jsonify(success=False)
    nodes = load_nodes()
    node = next((n for n in nodes if n["name"] == node_name), None)
    if not node:
        return jsonify(success=False, message="Узел не найден.")
    try:
        ip = node["ip"]
        s, error = login_to_node(ip, node["username"], node["password"])
        if error:
            return jsonify(success=False, message=error)
        users_resp = s.post(f"http://{ip}/store/public/admin/lab_sessions/mapUser", timeout=10)
        nodes_resp = s.post(f"http://{ip}/store/public/admin/node_sessions/getConsume", timeout=10)
        try:
            users_data = safe_parse_users_data(users_resp.json().get("data", {}))
        except (ValueError, json.JSONDecodeError):
            users_data = {}
        try:
            nodes_data = nodes_resp.json().get("data", [])
        except (ValueError, json.JSONDecodeError):
            nodes_data = []
        pod_to_user = {}
        for v in users_data.values():
            if isinstance(v, dict) and "pod" in v and "username" in v:
                try:
                    pod_to_user[int(v["pod"])] = v["username"]
                except (ValueError, TypeError):
                    continue
        pod_labs = defaultdict(set)
        for item in nodes_data:
            if isinstance(item, dict):
                pod = item.get("node_session_pod")
                lab = item.get("node_session_lab")
                if pod is not None and lab is not None:
                    pod_labs[int(pod)].add(int(lab))
        users = [{"name": pod_to_user.get(pod, f"unknown(pod={pod})"), "pod": pod,
                  "lab_count": len(labs), "node": node_name}
                 for pod, labs in sorted(pod_labs.items())]
        return jsonify(success=True, users=users)
    except Exception as e:
        return jsonify(success=False, message=str(e))


# --- Управление лабораториями ---
@app.route('/stop_all_labs/<node_name>')
def stop_all_labs(node_name):
    if not session.get('logged_in'):
        return jsonify(success=False, message="Необходима авторизация.")
    nodes = load_nodes()
    node = next((n for n in nodes if n["name"] == node_name), None)
    if not node:
        return jsonify(success=False, message="Узел не найден.")
    try:
        ip = node["ip"]
        session_obj, error = login_to_node(ip, node["username"], node["password"])
        if error:
            return jsonify(success=False, message=error)
        resp_nodes = session_obj.post(f"http://{ip}/store/public/admin/node_sessions/getConsume", timeout=10)
        if resp_nodes.status_code not in [200, 202]:
            return jsonify(success=False, message=f"Ошибка получения списка нод. Статус: {resp_nodes.status_code}")
        nodes_data = resp_nodes.json().get("data", [])
        lab_ids = list(set(item.get("node_session_lab") for item in nodes_data if item.get("node_session_lab") is not None))
        if not lab_ids:
            return jsonify(success=True, message="Нет активных лабораторий для остановки.")
        deleted_count = 0
        for lab_id in lab_ids:
            session_obj.post(f"http://{ip}/api/labs/session/factory/join", json={"lab_session": lab_id})
            del_resp = session_obj.post(f"http://{ip}/api/labs/session/nodes/stop")
            if del_resp.status_code in [200, 201, 202]:
                try:
                    result = del_resp.json()
                    if result.get("code") == 200 or result.get("message") == "success":
                        deleted_count += 1
                except:
                    deleted_count += 1
        return jsonify(success=True, message=f"Успешно остановлены ноды в {deleted_count} лабораториях из {len(lab_ids)}.")
    except Exception as e:
        return jsonify(success=False, message=f"Ошибка при остановке нод: {str(e)}")

@app.route('/start_lab_nodes/<node_name>/<int:lab_id>', methods=['POST'])
def start_lab_nodes(node_name, lab_id):
    """Запускает все ноды в указанной лаборатории."""
    if not session.get('logged_in'):
        return jsonify(success=False, message="Необходима авторизация.")
    nodes = load_nodes()
    node = next((n for n in nodes if n["name"] == node_name), None)
    if not node:
        return jsonify(success=False, message="Узел не найден.")
    try:
        ip = node["ip"]
        sess, error = login_to_node(ip, node["username"], node["password"])
        if error:
            return jsonify(success=False, message=error)
        # Присоединяемся к лаб-сессии
        sess.post(f"http://{ip}/api/labs/session/factory/join", json={"lab_session": lab_id})
        # Получаем топологию и запускаем каждую ноду
        topo_resp = sess.get(f"http://{ip}/api/labs/session/topology", timeout=10)
        if topo_resp.status_code != 200:
            return jsonify(success=False, message="Ошибка получения топологии.")
        topology = topo_resp.json().get("data", {}).get("nodes", {})
        if not isinstance(topology, dict) or not topology:
            return jsonify(success=False, message="Топология пуста или недоступна.")
        started = 0
        for node_id in topology.keys():
            r = sess.put(f"http://{ip}/api/labs/session/nodes/{node_id}/start", timeout=10)
            if r.status_code in [200, 201, 202]:
                started += 1
        return jsonify(success=True, message=f"Запущено нод: {started} из {len(topology)}.")
    except Exception as e:
        return jsonify(success=False, message=str(e))


# --- Запуск нод по пользователям/группам ---
def _run_start_nodes_in_thread(node, usernames, all_labs):
    """Фоновый воркер: вызывает start_nodes_for_users и обновляет состояние."""
    global start_results

    def log_cb(line):
        start_log_queue.put(line)

    def progress_cb(done, total, current):
        with start_state_lock:
            start_state['done'] = done
            start_state['total'] = total
            start_state['current_user'] = current

    try:
        results = start_nodes_for_users(
            node, usernames,
            all_labs=all_labs,
            log_cb=log_cb,
            progress_cb=progress_cb,
            max_workers=6,
            cancel_ev=start_cancel_event,
        )
        with start_results_lock:
            start_results = results
        ok = sum(1 for r in results.values() if r.get('total', 0) > 0 and not r.get('error'))
        total_started = sum(r.get('started', 0) for r in results.values())
        total_nodes = sum(r.get('total', 0) for r in results.values())
        start_log_queue.put(f"📊 Итог: пользователей с лабами {ok}/{len(results)}, "
                            f"запущено нод {total_started}/{total_nodes}")

        # Фаза верификации: ждём 60 с и проверяем наличие QEMU-процессов
        if not start_cancel_event.is_set():
            start_log_queue.put("⏳ Верификация: ожидаем 60 с загрузки ВМ...")
            for _ in range(60):
                if start_cancel_event.is_set():
                    break
                time.sleep(1)

        if not start_cancel_event.is_set():
            needs_retry = []
            for username, res in results.items():
                if res.get('error') or not res.get('labs'):
                    continue
                for lab_info in res['labs']:
                    lab_id = lab_info.get('lab_id')
                    if lab_id is None:
                        continue
                    qemu = parse_qemu_by_lab(lab_id)
                    expected = lab_info.get('total', 0)
                    found = len(qemu)
                    sym = "✅" if found >= expected else "⚠️"
                    start_log_queue.put(f"  {sym} {username} lab={lab_id}: "
                                        f"QEMU-портов {found}/{expected}")
                    if found < expected and username not in needs_retry:
                        needs_retry.append(username)

            if needs_retry and not start_cancel_event.is_set():
                start_log_queue.put(f"🔄 Повторный запуск для {len(needs_retry)} польз.: "
                                    f"{', '.join(needs_retry)}")
                retry_sess, retry_err = login_to_node(
                    node["ip"], node["username"], node["password"])
                if retry_sess and not retry_err:
                    retry_mapping, _ = _fetch_pod_mapping(retry_sess, node["ip"])
                    retry_u2p = retry_mapping["user_to_pod"] if retry_mapping else {}
                    retry_plabs = retry_mapping["pod_labs"] if retry_mapping else {}
                    for username in needs_retry:
                        if start_cancel_event.is_set():
                            break
                        pod = retry_u2p.get(username)
                        if pod is None:
                            continue
                        lab_ids_r = sorted(retry_plabs.get(pod, set()))
                        if not lab_ids_r:
                            continue
                        _nodes_action_for_one_user(
                            node, username,
                            lab_ids_r if all_labs else [lab_ids_r[0]],
                            'start', log_cb, start_cancel_event,
                            sess=retry_sess, user_pod=pod,
                        )
                else:
                    start_log_queue.put(f"⚠️ Не удалось переавторизоваться для retry: {retry_err}")

                # Ждём ещё 60 с и делаем финальную проверку
                start_log_queue.put("⏳ Ожидаем ещё 60 с...")
                for _ in range(60):
                    if start_cancel_event.is_set():
                        break
                    time.sleep(1)

                start_log_queue.put("📋 Финальная проверка QEMU-портов:")
                for username in needs_retry:
                    res = results.get(username, {})
                    for lab_info in res.get('labs', []):
                        lab_id = lab_info.get('lab_id')
                        if lab_id is None:
                            continue
                        qemu = parse_qemu_by_lab(lab_id)
                        expected = lab_info.get('total', 0)
                        found = len(qemu)
                        sym = "✅" if found >= expected else "❌"
                        start_log_queue.put(f"  {sym} {username} lab={lab_id}: "
                                            f"{found}/{expected} ВМ")
    except Exception as e:
        start_log_queue.put(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        import traceback
        start_log_queue.put(traceback.format_exc())
    finally:
        with start_state_lock:
            start_state['running'] = False
            start_state['current_user'] = ''
            if start_state['total']:
                start_state['done'] = start_state['total']


def _begin_start_nodes(node, usernames, all_labs):
    """Атомарно резервирует флаг running и запускает фон. Возвращает (ok, msg)."""
    if not usernames:
        return False, "Не выбрано ни одного пользователя."
    with start_state_lock:
        if start_state['running']:
            return False, "Операция запуска нод уже идёт."
        start_state['running'] = True
        start_state['cancelled'] = False
        start_state['current_user'] = ''
        start_state['total'] = len(usernames)
        start_state['done'] = 0
    start_cancel_event.clear()
    while not start_log_queue.empty():
        try:
            start_log_queue.get_nowait()
        except Exception:
            break
    with start_results_lock:
        start_results.clear()
    threading.Thread(
        target=_run_start_nodes_in_thread,
        args=(node, usernames, all_labs),
        daemon=True,
    ).start()
    return True, None


def stop_nodes_for_users(node, usernames, *, all_labs=False,
                         log_cb=None, progress_cb=None,
                         max_workers=6, cancel_ev=None):
    """Останавливает ноды у каждого из указанных пользователей (без wipe data)."""
    ip = node["ip"]
    if log_cb:
        log_cb(f"=== Остановка нод на узле {node.get('name')} ({ip}) ===")
    sess, err = login_to_node(ip, node["username"], node["password"])
    if err or sess is None:
        msg = f"❌ Не удалось авторизоваться на {ip}: {err}"
        if log_cb:
            log_cb(msg)
        return {u: {"started": 0, "total": 0, "labs": [], "error": err or "auth failed"}
                for u in usernames}

    mapping, err = _fetch_pod_mapping(sess, ip)
    if err:
        if log_cb:
            log_cb(f"❌ {err}")
        return {u: {"started": 0, "total": 0, "labs": [], "error": err}
                for u in usernames}
    user_to_pod = mapping["user_to_pod"]
    pod_labs = mapping["pod_labs"]

    tasks = []
    skipped = {}
    for username in usernames:
        pod = user_to_pod.get(username)
        if pod is None or pod not in pod_labs or not pod_labs[pod]:
            if log_cb:
                log_cb(f"⚠️ {username}: нет активных лаб")
            skipped[username] = {"started": 0, "total": 0, "labs": [],
                                 "error": "no active labs"}
            continue
        sorted_labs = sorted(pod_labs[pod])
        lab_ids = sorted_labs if all_labs else [sorted_labs[0]]
        tasks.append((username, lab_ids))

    results = dict(skipped)
    total = len(tasks)
    done = 0
    if log_cb:
        log_cb(f"Будет остановлено пользователей: {total} последовательно "
               f"(all_labs={'да' if all_labs else 'нет'})")
    if progress_cb:
        progress_cb(0, total, '')
    if total == 0:
        if log_cb:
            log_cb("=== ЗАВЕРШЕНО (нет задач) ===")
        if progress_cb:
            progress_cb(0, 0, '')
        return results

    for username, lab_ids in tasks:
        if cancel_ev is not None and cancel_ev.is_set():
            if log_cb:
                log_cb("⛔ Отменено пользователем")
            break
        if progress_cb:
            progress_cb(done, total, username)
        try:
            results[username] = _nodes_action_for_one_user(
                node, username, lab_ids, 'stop', log_cb, cancel_ev,
                sess=sess, user_pod=user_to_pod.get(username),
                node_workers=max_workers,
            )
        except Exception as e:
            results[username] = {"started": 0, "total": 0, "labs": [], "error": str(e)}
            if log_cb:
                log_cb(f"❌ {username}: исключение — {e}")
            try:
                new_sess, login_err = login_to_node(ip, node["username"], node["password"])
                if new_sess is not None:
                    sess = new_sess
                    if log_cb:
                        log_cb("🔄 Переавторизация выполнена, продолжаем")
            except Exception:
                pass
        done += 1
        if progress_cb:
            progress_cb(done, total, username)

    if log_cb:
        if cancel_ev is not None and cancel_ev.is_set():
            log_cb("=== ПРЕРВАНО ===")
        else:
            log_cb("=== ЗАВЕРШЕНО ===")
    return results


def _run_stop_nodes_in_thread(node, usernames, all_labs):
    global stop_results

    def log_cb(line):
        stop_log_queue.put(line)

    def progress_cb(done, total, current):
        with stop_state_lock:
            stop_state['done'] = done
            stop_state['total'] = total
            stop_state['current_user'] = current

    try:
        results = stop_nodes_for_users(
            node, usernames,
            all_labs=all_labs,
            log_cb=log_cb,
            progress_cb=progress_cb,
            max_workers=6,
            cancel_ev=stop_cancel_event,
        )
        with stop_results_lock:
            stop_results = results
        ok = sum(1 for r in results.values() if r.get('total', 0) > 0 and not r.get('error'))
        total_stopped = sum(r.get('started', 0) for r in results.values())
        total_nodes = sum(r.get('total', 0) for r in results.values())
        stop_log_queue.put(f"📊 Итог: пользователей с лабами {ok}/{len(results)}, "
                           f"остановлено нод {total_stopped}/{total_nodes}")
    except Exception as e:
        stop_log_queue.put(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        import traceback
        stop_log_queue.put(traceback.format_exc())
    finally:
        with stop_state_lock:
            stop_state['running'] = False
            stop_state['current_user'] = ''
            if stop_state['total']:
                stop_state['done'] = stop_state['total']


def _begin_stop_nodes(node, usernames, all_labs):
    if not usernames:
        return False, "Не выбрано ни одного пользователя."
    with stop_state_lock:
        if stop_state['running']:
            return False, "Операция остановки нод уже идёт."
        stop_state['running'] = True
        stop_state['cancelled'] = False
        stop_state['current_user'] = ''
        stop_state['total'] = len(usernames)
        stop_state['done'] = 0
    stop_cancel_event.clear()
    while not stop_log_queue.empty():
        try:
            stop_log_queue.get_nowait()
        except Exception:
            break
    with stop_results_lock:
        stop_results.clear()
    threading.Thread(
        target=_run_stop_nodes_in_thread,
        args=(node, usernames, all_labs),
        daemon=True,
    ).start()
    return True, None


def _resolve_node(node_name):
    nodes = load_nodes()
    if not nodes:
        return None, "Нет настроенных узлов."
    if node_name:
        node = next((n for n in nodes if n["name"] == node_name), None)
        if not node:
            return None, f"Узел '{node_name}' не найден."
        return node, None
    return nodes[0], None


@app.route('/start_nodes', methods=['POST'])
def start_nodes_route():
    """Запустить ноды для явно указанного списка пользователей."""
    if not session.get('logged_in'):
        return jsonify(success=False, message="Необходима авторизация.")
    data = request.get_json(silent=True) or {}
    node_name = data.get('node_name', '')
    usernames = data.get('usernames') or []
    all_labs = bool(data.get('all_labs', False))
    if not isinstance(usernames, list) or not usernames:
        return jsonify(success=False, message="Список пользователей пуст.")
    node, err = _resolve_node(node_name)
    if err:
        return jsonify(success=False, message=err)
    ok, msg = _begin_start_nodes(node, list(dict.fromkeys(usernames)), all_labs)
    if not ok:
        return jsonify(success=False, message=msg)
    return jsonify(success=True)


@app.route('/start_nodes_group', methods=['POST'])
def start_nodes_group_route():
    """Запустить ноды для группы (m1 / m2 / all) — сервер сам отфильтрует."""
    if not session.get('logged_in'):
        return jsonify(success=False, message="Необходима авторизация.")
    data = request.get_json(silent=True) or {}
    node_name = data.get('node_name', '')
    group = (data.get('group') or '').lower()
    all_labs = bool(data.get('all_labs', False))
    if group not in ('m1', 'm2', 'all'):
        return jsonify(success=False, message="Неизвестная группа.")
    node, err = _resolve_node(node_name)
    if err:
        return jsonify(success=False, message=err)
    # Получаем актуальный список пользователей узла
    sess, login_err = login_to_node(node["ip"], node["username"], node["password"])
    if login_err or sess is None:
        return jsonify(success=False, message=login_err or "Авторизация не удалась.")
    mapping, fetch_err = _fetch_pod_mapping(sess, node["ip"])
    if fetch_err:
        return jsonify(success=False, message=fetch_err)
    all_users = list(mapping["user_to_pod"].keys())
    selected = filter_users_by_group(all_users, group)
    if not selected:
        return jsonify(success=False,
                       message=f"В группе '{group}' нет активных пользователей.")
    ok, msg = _begin_start_nodes(node, selected, all_labs)
    if not ok:
        return jsonify(success=False, message=msg)
    return jsonify(success=True, count=len(selected), users=selected)


@app.route('/get_start_status')
def get_start_status():
    with start_state_lock:
        return jsonify(
            running=start_state['running'],
            cancelled=start_state['cancelled'],
            current_user=start_state['current_user'],
            total=start_state['total'],
            done=start_state['done'],
        )


@app.route('/get_start_logs')
def get_start_logs():
    logs = []
    while not start_log_queue.empty():
        try:
            logs.append(start_log_queue.get_nowait())
        except Exception:
            break
    return jsonify(logs=logs)


@app.route('/cancel_start', methods=['POST'])
def cancel_start():
    if not session.get('logged_in'):
        return jsonify(success=False, message="Необходима авторизация.")
    start_cancel_event.set()
    with start_state_lock:
        start_state['cancelled'] = True
    start_log_queue.put("⛔ Запрошена отмена операции...")
    return jsonify(success=True)


@app.route('/get_start_results')
def get_start_results():
    with start_results_lock:
        snapshot = dict(start_results)
    return jsonify(results=snapshot)


# --- Остановка нод по пользователям/группам ---
@app.route('/stop_nodes', methods=['POST'])
def stop_nodes_route():
    if not session.get('logged_in'):
        return jsonify(success=False, message="Необходима авторизация.")
    data = request.get_json(silent=True) or {}
    node_name = data.get('node_name', '')
    usernames = data.get('usernames') or []
    all_labs = bool(data.get('all_labs', False))
    if not isinstance(usernames, list) or not usernames:
        return jsonify(success=False, message="Список пользователей пуст.")
    node, err = _resolve_node(node_name)
    if err:
        return jsonify(success=False, message=err)
    ok, msg = _begin_stop_nodes(node, list(dict.fromkeys(usernames)), all_labs)
    if not ok:
        return jsonify(success=False, message=msg)
    return jsonify(success=True)


@app.route('/stop_nodes_group', methods=['POST'])
def stop_nodes_group_route():
    if not session.get('logged_in'):
        return jsonify(success=False, message="Необходима авторизация.")
    data = request.get_json(silent=True) or {}
    node_name = data.get('node_name', '')
    group = (data.get('group') or '').lower()
    all_labs = bool(data.get('all_labs', False))
    if group not in ('m1', 'm2', 'all'):
        return jsonify(success=False, message="Неизвестная группа.")
    node, err = _resolve_node(node_name)
    if err:
        return jsonify(success=False, message=err)
    sess, login_err = login_to_node(node["ip"], node["username"], node["password"])
    if login_err or sess is None:
        return jsonify(success=False, message=login_err or "Авторизация не удалась.")
    mapping, fetch_err = _fetch_pod_mapping(sess, node["ip"])
    if fetch_err:
        return jsonify(success=False, message=fetch_err)
    all_users = list(mapping["user_to_pod"].keys())
    selected = filter_users_by_group(all_users, group)
    if not selected:
        return jsonify(success=False,
                       message=f"В группе '{group}' нет активных пользователей.")
    ok, msg = _begin_stop_nodes(node, selected, all_labs)
    if not ok:
        return jsonify(success=False, message=msg)
    return jsonify(success=True, count=len(selected), users=selected)


@app.route('/get_stop_status')
def get_stop_status():
    with stop_state_lock:
        return jsonify(
            running=stop_state['running'],
            cancelled=stop_state['cancelled'],
            current_user=stop_state['current_user'],
            total=stop_state['total'],
            done=stop_state['done'],
        )


@app.route('/get_stop_logs')
def get_stop_logs():
    logs = []
    while not stop_log_queue.empty():
        try:
            logs.append(stop_log_queue.get_nowait())
        except Exception:
            break
    return jsonify(logs=logs)


@app.route('/cancel_stop', methods=['POST'])
def cancel_stop():
    if not session.get('logged_in'):
        return jsonify(success=False, message="Необходима авторизация.")
    stop_cancel_event.set()
    with stop_state_lock:
        stop_state['cancelled'] = True
    stop_log_queue.put("⛔ Запрошена отмена операции...")
    return jsonify(success=True)


@app.route('/get_stop_results')
def get_stop_results():
    with stop_results_lock:
        snapshot = dict(stop_results)
    return jsonify(results=snapshot)


# --- Варианты ---
@app.route('/generate_variants', methods=['POST'])
def generate_variants():
    if not session.get('logged_in'):
        return jsonify(success=False, message="Необходима авторизация.")
    return jsonify(generate_assignment_variants())

@app.route('/delete_variant/<variant_name>', methods=['POST'])
def delete_variant(variant_name):
    if not session.get('logged_in'):
        return jsonify(success=False, message="Необходима авторизация.")
    if not re.match(r'^assignment_checker_task\d+\.py$', variant_name):
        return jsonify(success=False, message="Неверное имя файла."), 400
    task_num = re.search(r'task(\d+)\.py', variant_name).group(1)
    py = os.path.join(VARIANTS_DIR, variant_name)
    txt = os.path.join(VARIANTS_DIR, f"task{task_num}_description.txt")
    try:
        if os.path.exists(py): os.remove(py)
        if os.path.exists(txt): os.remove(txt)
        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, message=str(e))

@app.route('/view_variant/<variant_name>')
def view_variant(variant_name):
    if not session.get('logged_in') or not re.match(r'^assignment_checker_task\d+\.py$', variant_name):
        return jsonify(success=False)
    path = os.path.join(VARIANTS_DIR, variant_name)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return jsonify(success=True, content=f.read())
    except:
        return jsonify(success=False, message="Файл не найден")

@app.route('/view_task_description/<task_num>')
def view_task_description(task_num):
    if not session.get('logged_in') or not task_num.isdigit():
        return jsonify(success=False)
    path = os.path.join(VARIANTS_DIR, f"task{task_num}_description.txt")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return jsonify(success=True, content=f.read())
    except:
        return jsonify(success=False, message="Файл не найден")


# --- Проверка ---
@app.route('/start_check', methods=['POST'])
def start_check():
    if not session.get('logged_in'):
        return jsonify(success=False, message="Необходима авторизация.")
    data = request.get_json()
    user_variants = data.get('user_variants', {})
    node_name = data.get('node_name', '')
    if not user_variants:
        return jsonify(success=False, message="Не выбраны пользователи или варианты.")
    nodes = load_nodes()
    node = next((n for n in nodes if n["name"] == node_name), nodes[0] if nodes else None)
    if not node:
        return jsonify(success=False, message="Узел не найден.")
    # Атомарно проверяем и резервируем флаг running, чтобы два одновременных
    # запроса не могли оба пройти guard и запустить два потока проверки.
    with check_state_lock:
        if check_state['running']:
            return jsonify(success=False, message="Проверка уже запущена.")
        check_state['running'] = True
        check_state['cancelled'] = False
        check_state['current_user'] = ''
        check_state['total'] = len(user_variants)
        check_state['done'] = 0
    global last_check_name
    current_check_name = next(iter(user_variants.values())) if user_variants else "unknown_check"
    with last_check_lock:
        last_check_name = current_check_name
    cancel_event.clear()
    # сбрасываем очередь логов
    while not log_queue.empty():
        try:
            log_queue.get_nowait()
        except:
            break
    threading.Thread(
        target=run_check_in_thread,
        args=(user_variants, node, current_check_name),
        daemon=True,
    ).start()
    return jsonify(success=True)

@app.route('/cancel_check', methods=['POST'])
def cancel_check():
    if not session.get('logged_in'):
        return jsonify(success=False, message="Необходима авторизация.")
    cancel_event.set()
    with check_state_lock:
        check_state['cancelled'] = True
    log_queue.put("⛔ Проверка отменена пользователем.")
    return jsonify(success=True)

@app.route('/get_check_status')
def get_check_status():
    with check_state_lock:
        return jsonify(
            running=check_state['running'],
            cancelled=check_state['cancelled'],
            current_user=check_state['current_user'],
            total=check_state['total'],
            done=check_state['done'],
        )

def run_check_in_thread(user_variants, node, check_name_override=None):
    global last_results, last_summary_file, last_node_ip_safe
    try:
        node_base_url = f"http://{node['ip']}"
        sess, error = login_to_node(node["ip"], node["username"], node["password"])
        if error or sess is None:
            log_queue.put(f"❌ Не удалось авторизоваться на узле {node['ip']}: {error}")
            return
        resp_users = sess.post(f"{node_base_url}/store/public/admin/lab_sessions/mapUser")
        if resp_users.status_code not in [200, 202]:
            log_queue.put("❌ Ошибка запроса пользователей")
            return
        users_data_raw = resp_users.json().get("data", {})
        if isinstance(users_data_raw, str) and "login" in users_data_raw.lower():
            log_queue.put("🔄 Сессия устарела. Попытка переавторизации...")
            sess, error = login_to_node(node["ip"], node["username"], node["password"])
            if sess is None:
                log_queue.put(f"❌ Переавторизация не удалась: {error}")
                return
            resp_users = sess.post(f"{node_base_url}/store/public/admin/lab_sessions/mapUser")
            if resp_users.status_code not in [200, 202]:
                log_queue.put("❌ Ошибка после переавторизации")
                return
            users_data_raw = resp_users.json().get("data", {})
        users_data = safe_parse_users_data(users_data_raw)
        resp_nodes = sess.post(f"{node_base_url}/store/public/admin/node_sessions/getConsume")
        if resp_nodes.status_code not in [200, 202]:
            log_queue.put("❌ Ошибка запроса нод")
            return
        nodes_data = resp_nodes.json().get("data", [])
        pod_to_user = {}
        for v in users_data.values():
            if isinstance(v, dict) and "pod" in v and "username" in v:
                try:
                    pod_to_user[int(v["pod"])] = v["username"]
                except (ValueError, TypeError):
                    continue
        user_to_pod = {v: k for k, v in pod_to_user.items()}
        pod_labs = defaultdict(set)
        for node_item in nodes_data:
            if isinstance(node_item, dict):
                pod = node_item.get("node_session_pod")
                lab = node_item.get("node_session_lab")
                if pod is not None and lab is not None:
                    try:
                        pod_labs[int(pod)].add(int(lab))
                    except (ValueError, TypeError):
                        continue

        # Используем имя проверки, зафиксированное в /start_check, чтобы
        # параллельный второй запуск не подменил summary-файл текущего потока.
        local_check_name = check_name_override or "unknown_check"
        node_ip_safe = node['ip'].replace('.', '-')
        last_node_ip_safe = node_ip_safe
        summary_filename = os.path.join(LOGS_DIR, f"summary_{local_check_name}-{node_ip_safe}.txt")
        last_summary_file = summary_filename
        with open(summary_filename, "w", encoding="utf-8") as f:
            f.write("Имя пользователя\tВариант\tБалл\n")

        results = {}
        items = list(user_variants.items())
        for idx, (username, check_name) in enumerate(items):
            if cancel_event.is_set():
                log_queue.put("⛔ Проверка прервана.")
                break

            with check_state_lock:
                check_state['current_user'] = username
                check_state['done'] = idx

            log_queue.put(f"{'='*20} ПРОВЕРКА: {username} ({check_name}) {'='*20}")
            pod = user_to_pod.get(username)
            if pod is None or pod not in pod_labs:
                log_queue.put(f"❌ Пользователь {username} не найден или нет лаб")
                results[username] = {'check_name': check_name, 'score': 0, 'log_file': None}
                with open(summary_filename, "a", encoding="utf-8") as f:
                    f.write(f"{username}\t{check_name}\t0\n")
                continue

            lab_id = sorted(pod_labs[pod])[0]
            sess.post(f"{node_base_url}/api/labs/session/factory/join", json={"lab_session": lab_id})
            topo_resp = sess.get(f"{node_base_url}/api/labs/session/topology")
            if topo_resp.status_code != 200:
                log_queue.put(f"❌ Ошибка топологии для {username}")
                results[username] = {'check_name': check_name, 'score': 0, 'log_file': None}
                continue
            topology = topo_resp.json().get("data", {}).get("nodes", {})
            if not isinstance(topology, dict):
                log_queue.put("❌ Некорректная топология")
                results[username] = {'check_name': check_name, 'score': 0, 'log_file': None}
                continue

            name_to_ports = parse_qemu_by_lab(lab_id)

            # Для нод, которых нет в ps aux, пробуем fallback: map_port из топологии.
            # Это поле PNETLab сам выставляет при запуске ВМ и оно остаётся актуальным,
            # даже если hostfwd в командной строке QEMU парсится с другим форматом.
            vm_ports = {}
            for info in topology.values():
                name = info.get("name")
                if not name:
                    continue
                port = name_to_ports.get(name)
                if port is None or port == "N/A":
                    # Пробуем map_port из самой топологии (PNETLab ставит его при старте)
                    map_port = info.get("map_port")
                    if map_port and str(map_port) not in ("0", "N/A", ""):
                        port = str(map_port)
                        log_queue.put(f"ℹ {username}: {name} — ps aux N/A, "
                                      f"используем map_port={port} из топологии")
                    else:
                        port = "N/A"
                vm_ports[name] = port

            # BR-FW не проверяется чекером — N/A для неё нормально, не логируем.
            na_vms = [name for name, port in vm_ports.items()
                      if port == "N/A" and name != "BR-FW"]
            if na_vms:
                log_queue.put(f"⚠️ {username}: ВМ не найдены в QEMU и в map_port: {na_vms}. "
                              f"Проверка продолжается (задания на этих ВМ не будут засчитаны).")

            import importlib.util
            module_name = f"assignment_checker_{check_name}"
            checker_path = os.path.join(VARIANTS_DIR, f"{module_name}.py")
            if os.path.exists(checker_path):
                spec = importlib.util.spec_from_file_location(module_name, checker_path)
                checker = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(checker)
            else:
                checker = __import__(module_name, fromlist=['run_full_assignment_check'])
            run_check = getattr(checker, 'run_full_assignment_check', None)
            if not run_check:
                log_queue.put(f"❌ Функция проверки не найдена в {check_name}")
                results[username] = {'check_name': check_name, 'score': 0, 'log_file': None}
                continue

            score, log_lines = run_check(vm_ports)
            for line in log_lines:
                log_queue.put(line)
            log_queue.put(f"🧮 ИТОГО: {username} — {score} по {check_name}")

            user_log_filename = os.path.join(LOGS_DIR, f"outstend_{check_name}_{username.replace('-', '_')}-{node_ip_safe}.txt")
            # Многие чекеры возвращают строки уже с завершающим '\n'; добавлять
            # ещё один разделитель через '\n'.join приводит к пустым строкам в
            # выгружаемом логе. Нормализуем переводы строк перед записью.
            normalized_lines = [
                (line if line.endswith('\n') else line + '\n')
                for line in log_lines
            ]
            with open(user_log_filename, "w", encoding="utf-8") as f:
                f.writelines(normalized_lines)
            log_queue.put(f"✅ Лог сохранён: {user_log_filename}")
            with open(summary_filename, "a", encoding="utf-8") as f:
                f.write(f"{username}\t{check_name}\t{score}\n")
            results[username] = {'check_name': check_name, 'score': score, 'log_file': user_log_filename}

        with results_lock:
            last_results = results
        log_queue.put(f"✅ Сводная таблица сохранена: {summary_filename}")
        log_queue.put("- ВСЕ ПРОВЕРКИ ЗАВЕРШЕНЫ -")
    except Exception as e:
        log_queue.put(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        import traceback
        log_queue.put(traceback.format_exc())
    finally:
        with check_state_lock:
            check_state['running'] = False
            check_state['current_user'] = ''
            check_state['done'] = check_state['total']


@app.route('/get_logs')
def get_logs():
    logs = []
    while not log_queue.empty():
        try:
            logs.append(log_queue.get_nowait())
        except:
            break
    return jsonify(logs=logs)

@app.route('/get_results')
def get_results():
    with results_lock:
        # Снимаем мгновенный снимок, чтобы /get_results не падал, если
        # фоновый поток подменяет last_results прямо во время сериализации.
        snapshot = dict(last_results)
    return jsonify(results=snapshot)

@app.route('/download_log/<username>')
def download_log(username):
    with results_lock:
        entry = last_results.get(username)
    if entry is None:
        return "Результат для пользователя не найден", 404
    log_file = entry.get('log_file')
    if not log_file or not os.path.exists(log_file):
        return "Лог не найден", 404
    return send_file(log_file, as_attachment=True)

@app.route('/download_summary')
def download_summary():
    if not last_summary_file:
        return "Сводная таблица ещё не создана.", 400
    return send_file(last_summary_file, as_attachment=True) if os.path.exists(last_summary_file) else ("Таблица не найдена", 404)

@app.route('/download_all_logs')
def download_all_logs():
    """Скачать все логи текущей проверки одним ZIP-архивом."""
    if not session.get('logged_in'):
        return "Необходима авторизация.", 403
    with results_lock:
        # Копируем словарь, чтобы не итерировать его во время мутации потоком проверки.
        results_snapshot = dict(last_results)
    if not results_snapshot:
        return "Нет результатов для скачивания.", 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for username, info in results_snapshot.items():
            log_file = info.get('log_file')
            if log_file and os.path.exists(log_file):
                zf.write(log_file, os.path.basename(log_file))
        # Добавляем сводную таблицу
        if last_check_name:
            summary = os.path.join(CHECKER_DIR, f"summary_{last_check_name}.txt")
            if os.path.exists(summary):
                zf.write(summary, os.path.basename(summary))
    buf.seek(0)
    from datetime import datetime
    ts = datetime.now().strftime("%d_%m_%Y_%H:%M")
    ip_part = f"_{last_node_ip_safe}" if last_node_ip_safe else ""
    archive_name = f"results_{last_check_name or 'check'}{ip_part}_{ts}.zip"
    return send_file(buf, as_attachment=True, download_name=archive_name, mimetype='application/zip')


@app.route('/generate_report', methods=['POST'])
def generate_report():
    """Принимает ZIP-архивы, генерирует Excel-отчёт и возвращает его."""
    if not session.get('logged_in'):
        return "Необходима авторизация.", 403

    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        return "openpyxl не установлен. Выполните: pip install openpyxl", 500

    files = request.files.getlist('files')
    if not files:
        return "Файлы не переданы.", 400

    # Импортируем make_report из репозитория (или из текущей папки если скопирован)
    import importlib.util, re as _re, zipfile as _zipfile
    from pathlib import Path as _Path
    from datetime import datetime as _dt

    # --- inline-парсеры (дублируем логику make_report.py чтобы не зависеть от пути) ---

    def _parse_log(text):
        result = {"score_points": None, "score_ko": None, "report_block": None, "is_mod": False}
        lines = text.splitlines()
        start = end = -1
        for i in range(len(lines) - 1, -1, -1):
            if "📈" in lines[i] and end == -1:
                end = i
            if "📊" in lines[i] and end != -1:
                start = i
                break
        if start != -1 and end != -1:
            result["report_block"] = "\n".join(lines[start:end + 1])
        m = _re.findall(r"Набрано\s+([\d.]+)\s+из", text)
        if m:
            v = float(m[-1]); result["score_ko"] = int(v) if v == int(v) else v; result["is_mod"] = True
        m = _re.findall(r"Выполнено\s+([\d.]+)\s+из", text)
        if m:
            v = float(m[-1]); result["score_points"] = int(v) if v == int(v) else v
        return result

    def _parse_log_filename(filename):
        name = _Path(filename).stem
        if name.startswith("outstend_"): name = name[9:]
        ip_m = _re.search(r"-(\d{1,3}-\d{1,3}-\d{1,3}-\d{1,3})$", name)
        node_ip = ""
        if ip_m: node_ip = ip_m.group(1).replace("-", "."); name = name[:ip_m.start()]
        vm = _re.match(r"(test_(?:federal_code_)?m\dv\d(?:_mod)?)", name)
        if vm:
            variant = vm.group(1); student = name[len(variant):].lstrip("_")
        else:
            parts = name.split("_"); variant = "_".join(parts[:3]); student = "_".join(parts[3:])
        return {"variant": variant, "student": student, "node_ip": node_ip}

    def _parse_zip_filename(filename):
        name = _Path(filename).stem
        if name.startswith("results_"): name = name[8:]
        dm = _re.search(r"_(\d{2}_\d{2}_\d{4}_\d{2}[:\-]\d{2})$", name)
        ts = ""
        if dm: ts = dm.group(1); name = name[:dm.start()]
        im = _re.search(r"_(\d{1,3}-\d{1,3}-\d{1,3}-\d{1,3})$", name)
        node_ip = ""
        if im: node_ip = im.group(1).replace("-", "."); name = name[:im.start()]
        return {"variant": name, "node_ip": node_ip, "timestamp": ts}

    def _variant_to_module(variant):
        m = _re.search(r"m(\d)", variant); return f"MOD-{m.group(1)}" if m else "OTHER"

    # --- Сбор данных ---
    data = {"MOD-1": {}, "MOD-2": {}, "MOD-3": {}}
    for upload in files:
        try:
            zf = _zipfile.ZipFile(io.BytesIO(upload.read()))
        except Exception:
            continue
        zip_meta = _parse_zip_filename(upload.filename)
        for entry in zf.namelist():
            if not entry.endswith(".txt") or not entry.startswith("outstend_"):
                continue
            try:
                text = zf.read(entry).decode("utf-8", errors="replace")
            except Exception:
                continue
            fm = _parse_log_filename(entry)
            variant = fm["variant"]; student = fm["student"]
            node_ip = fm["node_ip"] or zip_meta["node_ip"]
            module = _variant_to_module(variant)
            if module not in data: continue
            parsed = _parse_log(text)
            record = {"variant": variant, "node_ip": node_ip,
                      "score_points": parsed["score_points"], "score_ko": parsed["score_ko"],
                      "report_block": parsed["report_block"], "is_mod": parsed["is_mod"]}
            if student not in data[module]: data[module][student] = {}
            key = "mod" if "_mod" in variant else "base"
            if key not in data[module][student]:
                data[module][student][key] = record
            elif record["score_ko"] is not None and data[module][student][key]["score_ko"] is None:
                data[module][student][key] = record
        zf.close()

    # --- Стили ---
    HFILL = PatternFill("solid", fgColor="1F4E79")
    HFONT = Font(bold=True, color="FFFFFF", size=11)
    SFILL = PatternFill("solid", fgColor="2E75B6")
    SFONT = Font(bold=True, color="FFFFFF", size=10)
    AFILL = PatternFill("solid", fgColor="DEEAF1")
    THIN  = Side(style="thin", color="BFBFBF")
    BORD  = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    WRAP  = Alignment(wrap_text=True, vertical="top")
    CTR   = Alignment(horizontal="center", vertical="center", wrap_text=True)

    def _hdr(cell, sub=False):
        cell.fill = SFILL if sub else HFILL
        cell.font = SFONT if sub else HFONT
        cell.alignment = CTR; cell.border = BORD

    def _cell(cell, alt=False, wrap=True):
        if alt: cell.fill = AFILL
        cell.border = BORD; cell.alignment = WRAP if wrap else CTR

    MOD_HDRS = ["Имя пользователя", "Узел", "Вариант",
                "Пункты задания", "Балл по КО", "Итог по логам"]
    COL_W = [22, 16, 18, 14, 11, 80]

    wb = openpyxl.Workbook(); wb.remove(wb.active)

    for mod_name in ("MOD-1", "MOD-2", "MOD-3"):
        ws = wb.create_sheet(mod_name)
        ws.title = mod_name
        ws.merge_cells("A1:F1")
        t = ws["A1"]
        t.value = f"Результаты проверки — {mod_name}"
        t.fill = HFILL; t.font = Font(bold=True, color="FFFFFF", size=13)
        t.alignment = CTR; t.border = BORD; ws.row_dimensions[1].height = 24
        for ci, (h, w) in enumerate(zip(MOD_HDRS, COL_W), 1):
            c = ws.cell(row=2, column=ci, value=h); _hdr(c, sub=True)
            ws.column_dimensions[get_column_letter(ci)].width = w
        ws.row_dimensions[2].height = 20

        mod_data = data.get(mod_name, {})
        for ri, student in enumerate(sorted(mod_data.keys())):
            row = ri + 3; alt = (ri % 2 == 1)
            recs = mod_data[student]
            base = recs.get("base", {}); mod = recs.get("mod", {})
            variant  = (base or mod).get("variant", "")
            node_ip  = (base or mod).get("node_ip", "")
            score_p  = base.get("score_points")
            score_ko = mod.get("score_ko") if mod else base.get("score_ko")
            report   = (mod or base).get("report_block", "")
            vals = [student, node_ip, variant, score_p, score_ko, report]
            for ci, val in enumerate(vals, 1):
                c = ws.cell(row=row, column=ci, value=val)
                _cell(c, alt=alt, wrap=(ci == 6))
                if ci in (4, 5): c.alignment = CTR
            if report:
                ws.row_dimensions[row].height = max(15, min(report.count("\n") * 14, 400))
        ws.freeze_panes = "A3"

    # Лист ИТОГ
    ws_t = wb.create_sheet("ИТОГ"); ws_t.title = "ИТОГ"
    ITHDR = ["Имя пользователя", "MOD-1 (25)", "MOD-2 (25)", "MOD-3 (25)", "Итого (75)", "% выполнения"]
    ITCW  = [22, 12, 12, 12, 12, 14]
    ws_t.merge_cells("A1:F1")
    t = ws_t["A1"]; t.value = "Итоговая сводка по всем модулям"
    t.fill = HFILL; t.font = Font(bold=True, color="FFFFFF", size=13)
    t.alignment = CTR; t.border = BORD; ws_t.row_dimensions[1].height = 24
    for ci, (h, w) in enumerate(zip(ITHDR, ITCW), 1):
        c = ws_t.cell(row=2, column=ci, value=h); _hdr(c, sub=True)
        ws_t.column_dimensions[get_column_letter(ci)].width = w
    ws_t.row_dimensions[2].height = 20
    all_students = set()
    for md in data.values(): all_students.update(md.keys())
    for ri, student in enumerate(sorted(all_students)):
        row = ri + 3; alt = (ri % 2 == 1)
        scores = []
        for mn in ("MOD-1", "MOD-2", "MOD-3"):
            recs = data.get(mn, {}).get(student, {})
            sc = recs.get("mod", {}).get("score_ko") if recs.get("mod") else recs.get("base", {}).get("score_ko")
            scores.append(sc)
        total = sum(s for s in scores if s is not None)
        pct = round(total / 75 * 100, 1) if total else 0
        for ci, val in enumerate([student] + scores + [total, pct], 1):
            c = ws_t.cell(row=row, column=ci, value=val)
            _cell(c, alt=alt, wrap=False); c.alignment = CTR
    ws_t.freeze_panes = "A3"

    buf = io.BytesIO()
    wb.save(buf); buf.seek(0)
    ts_str = _dt.now().strftime("%d_%m_%Y_%H-%M")
    return send_file(buf, as_attachment=True,
                     download_name=f"report_{ts_str}.xlsx",
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI-секция: curses TUI (только если запущено с --cli)
# ═══════════════════════════════════════════════════════════════════════════════

ROOMS_FILE_CLI = ROOMS_FILE  # уже задан в constants выше


def load_rooms():
    if not os.path.exists(ROOMS_FILE):
        return []
    with open(ROOMS_FILE, encoding='utf-8') as f:
        return json.load(f)


def save_rooms(rooms):
    with open(ROOMS_FILE, 'w', encoding='utf-8') as f:
        json.dump(rooms, f, ensure_ascii=False, indent=2)


def get_node_users(node):
    """CLI-версия: возвращает ([{name, pod, lab_count}], error)."""
    ip = node["ip"]
    sess, err = login_to_node(ip, node["username"], node["password"])
    if err:
        return [], err
    try:
        ur = sess.post(f"http://{ip}/store/public/admin/lab_sessions/mapUser", timeout=10)
        nr = sess.post(f"http://{ip}/store/public/admin/node_sessions/getConsume", timeout=10)
        users_data = safe_parse(ur.json().get("data", {}))
        nodes_data = nr.json().get("data", [])
    except Exception as e:
        return [], str(e)
    pod_to_user = {}
    for v in users_data.values():
        if isinstance(v, dict) and "pod" in v and "username" in v:
            try:
                pod_to_user[int(v["pod"])] = v["username"]
            except Exception:
                pass
    pod_labs = defaultdict(set)
    for item in nodes_data:
        if isinstance(item, dict):
            try:
                pod_labs[int(item["node_session_pod"])].add(int(item["node_session_lab"]))
            except Exception:
                pass
    users = [
        {"name": pod_to_user.get(pod, f"unknown(pod={pod})"),
         "pod": pod, "lab_count": len(labs)}
        for pod, labs in sorted(pod_labs.items())
    ]
    return users, None


# CLI-версия запуска нод (отдельные URL-шаблоны и hint, независимые от веб-версии)
_CLI_NODE_START_TEMPLATES = [
    ("POST", "http://{ip}/api/labs/session/nodes/start"),
    ("PUT",  "http://{ip}/api/labs/session/nodes/start"),
    ("PUT",  "http://{ip}/api/labs/session/nodes/{nid}/start"),
    ("POST", "http://{ip}/api/labs/session/nodes/{nid}/start"),
    ("PUT",  "http://{ip}/api/pod/{pod}/labs/{lab_id}/nodes/{nid}/start"),
    ("PUT",  "http://{ip}/api/labs/{lab_id}/nodes/{nid}/start"),
    ("POST", "http://{ip}/store/public/admin/node_sessions/start"),
    ("PUT",  "http://{ip}/api/labs/session/nodes/{session_node_id}/start"),
]
_CLI_NODE_START_HINT = {"method": None, "template": None}
_CLI_NODE_START_HINT_LOCK = threading.Lock()


def _cli_try_start_node(sess, ip, lab_id, pod, nid, session_node_id, prefer=None):
    cands = list(_CLI_NODE_START_TEMPLATES)
    if prefer and prefer in cands:
        cands.remove(prefer)
        cands.insert(0, prefer)
    extra = {"Referer": f"http://{ip}/legacy/topology",
              "Origin": f"http://{ip}",
              "X-Requested-With": "XMLHttpRequest"}
    last = None
    for method, tmpl in cands:
        if "{session_node_id}" in tmpl and not session_node_id:
            continue
        url = tmpl.format(ip=ip, lab_id=lab_id, pod=pod, nid=nid,
                          session_node_id=session_node_id or nid)
        body = None
        if "/api/labs/session/nodes/start" in url:
            body = {"id": nid}
        elif "/node_sessions/start" in url:
            body = {"id": session_node_id or nid}
        try:
            kwargs = {"timeout": 20, "headers": extra}
            if body:
                kwargs["json"] = body
            r = sess.put(url, **kwargs) if method == "PUT" else sess.post(url, **kwargs)
        except Exception as e:
            last = e
            continue
        last = r
        ok, _ = _is_ok_response(r)
        if ok:
            return True, method, tmpl, r
    return False, None, None, last


def _cli_fetch_session_node_ids(sess, ip, lab_id, pod):
    out = {}
    try:
        r = sess.post(f"http://{ip}/store/public/admin/node_sessions/getConsume", timeout=15)
        if r.status_code not in (200, 202):
            return out
        for it in r.json().get("data", []):
            if not isinstance(it, dict):
                continue
            if int(it.get("node_session_lab", -1)) != int(lab_id):
                continue
            if pod is not None and int(it.get("node_session_pod", -1)) != int(pod):
                continue
            sid = it.get("node_session_id") or it.get("id")
            tid = it.get("node_session_node_id") or it.get("node_id") or it.get("nid")
            if sid is not None and tid is not None:
                out[str(tid)] = sid
    except Exception:
        pass
    return out


def _cli_start_nodes_for_one_user(node, username, lab_ids, log_q, cancel_ev,
                                   sess=None, user_pod=None, node_workers=4):
    ip = node["ip"]
    if sess is None:
        sess, err = login_to_node(ip, node["username"], node["password"])
        if err or sess is None:
            log_q.put(f"❌ {username}: авторизация не удалась — {err}")
            return {"started": 0, "total": 0, "error": err or "auth failed"}
    if user_pod is None:
        try:
            m, _ = _fetch_pod_mapping(sess, ip)
            if m:
                user_pod = m["user_to_pod"].get(username)
        except Exception:
            pass
    total_started = 0
    total_nodes = 0
    for lab_id in lab_ids:
        if cancel_ev.is_set():
            break
        try:
            jr = sess.post(f"http://{ip}/api/labs/session/factory/join",
                           json={"lab_session": lab_id}, timeout=15)
            ok_join, why = _is_ok_response(jr)
            if not ok_join:
                log_q.put(f"⚠ {username} lab={lab_id}: factory/join — {why}")
            time.sleep(0.3)
            tr = sess.get(f"http://{ip}/api/labs/session/topology", timeout=15)
            if tr.status_code != 200:
                log_q.put(f"⚠ {username} lab={lab_id}: topology {_resp_summary(tr)}")
                continue
            topo = tr.json().get("data", {}).get("nodes", {})
            if not isinstance(topo, dict) or not topo:
                log_q.put(f"⚠ {username} lab={lab_id}: пустая топология")
                continue
            session_id_map = _cli_fetch_session_node_ids(sess, ip, lab_id, user_pod)
            with _CLI_NODE_START_HINT_LOCK:
                hint = (_CLI_NODE_START_HINT["method"], _CLI_NODE_START_HINT["template"])
            prefer = hint if hint[0] else None
            started = 0
            failed_samples = []
            node_ids = list(topo.keys())
            probe_nid = node_ids[0]
            ok, used_m, used_t, resp = _cli_try_start_node(
                sess, ip, lab_id, user_pod, probe_nid,
                session_id_map.get(str(probe_nid)), prefer=prefer)
            if ok:
                started += 1
                if used_t and not prefer:
                    with _CLI_NODE_START_HINT_LOCK:
                        if _CLI_NODE_START_HINT["template"] is None:
                            _CLI_NODE_START_HINT["method"] = used_m
                            _CLI_NODE_START_HINT["template"] = used_t
                            log_q.put(f"ℹ Найден рабочий URL: {used_m} {used_t}")
                    prefer = (used_m, used_t)
            else:
                if len(failed_samples) < 3:
                    failed_samples.append(f"node={probe_nid}: {resp}")
            rest = node_ids[1:]
            if rest and not cancel_ev.is_set():
                workers = max(1, min(node_workers, len(rest)))
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    fut_to_nid = {
                        ex.submit(_cli_try_start_node, sess, ip, lab_id, user_pod,
                                  nid, session_id_map.get(str(nid)), prefer): nid
                        for nid in rest
                    }
                    for fut in as_completed(fut_to_nid):
                        nid = fut_to_nid[fut]
                        try:
                            ok2, _m, _t, resp2 = fut.result()
                        except Exception as e:
                            ok2, resp2 = False, e
                        if ok2:
                            started += 1
                        elif len(failed_samples) < 3:
                            failed_samples.append(f"node={nid}: {resp2}")
            total_started += started
            total_nodes += len(topo)
            log_q.put(f"▶ {username} lab={lab_id}: запущено {started}/{len(topo)} нод")
            for s in failed_samples:
                log_q.put(f"   ↳ {s}")
        except Exception as e:
            log_q.put(f"❌ {username} lab={lab_id}: {e}")
    return {"started": total_started, "total": total_nodes, "error": None}


def start_nodes_for_users_cli(node, usernames, *, all_labs=False,
                               log_q=None, progress_cb=None,
                               max_workers=6, cancel_ev=None):
    if log_q is None:
        log_q = queue.Queue()
    if cancel_ev is None:
        cancel_ev = threading.Event()
    ip = node["ip"]
    log_q.put(f"=== Старт операции на узле {node.get('name')} ({ip}) ===")
    sess, err = login_to_node(ip, node["username"], node["password"])
    if err or sess is None:
        log_q.put(f"❌ Авторизация: {err}")
        log_q.put("=== ЗАВЕРШЕНО ===")
        return {}
    mapping, ferr = _fetch_pod_mapping(sess, ip)
    if ferr:
        log_q.put(f"❌ {ferr}")
        log_q.put("=== ЗАВЕРШЕНО ===")
        return {}
    user_to_pod = mapping["user_to_pod"]
    pod_labs = mapping["pod_labs"]
    tasks = []
    results = {}
    for username in usernames:
        pod = user_to_pod.get(username)
        if pod is None or pod not in pod_labs or not pod_labs[pod]:
            log_q.put(f"⚠ {username}: нет активных лаб")
            results[username] = {"started": 0, "total": 0, "error": "no active labs"}
            continue
        sl = sorted(pod_labs[pod])
        tasks.append((username, sl if all_labs else [sl[0]]))
    total = len(tasks)
    log_q.put(f"Будет запущено: {total} пользователей последовательно")
    if progress_cb:
        progress_cb(0, total, '')
    if total == 0:
        log_q.put("=== ЗАВЕРШЕНО ===")
        return results
    done = 0
    for u, lids in tasks:
        if cancel_ev.is_set():
            log_q.put("⛔ Отменено")
            break
        if progress_cb:
            progress_cb(done, total, u)
        try:
            results[u] = _cli_start_nodes_for_one_user(
                node, u, lids, log_q, cancel_ev,
                sess=sess, user_pod=user_to_pod.get(u),
                node_workers=max_workers)
        except Exception as e:
            results[u] = {"started": 0, "total": 0, "error": str(e)}
            log_q.put(f"❌ {u}: {e}")
        done += 1
        if progress_cb:
            progress_cb(done, total, u)
    log_q.put("=== ПРЕРВАНО ===" if cancel_ev.is_set() else "=== ЗАВЕРШЕНО ===")
    return results


def run_check_for_users(node, user_variants, log_q, cancel_ev, progress_cb=None):
    """CLI-версия проверки. Логи в log_q. Возвращает dict результатов."""
    results = {}
    ip = node["ip"]
    sess, err = login_to_node(ip, node["username"], node["password"])
    if err:
        log_q.put(f"ОШИБКА авторизации: {err}")
        return results
    ur = sess.post(f"http://{ip}/store/public/admin/lab_sessions/mapUser")
    nr = sess.post(f"http://{ip}/store/public/admin/node_sessions/getConsume")
    users_data = safe_parse(ur.json().get("data", {}))
    nodes_data = nr.json().get("data", [])
    pod_to_user = {}
    for v in users_data.values():
        if isinstance(v, dict) and "pod" in v and "username" in v:
            try:
                pod_to_user[int(v["pod"])] = v["username"]
            except Exception:
                pass
    user_to_pod = {v: k for k, v in pod_to_user.items()}
    pod_labs = defaultdict(set)
    for item in nodes_data:
        if isinstance(item, dict):
            try:
                pod_labs[int(item["node_session_pod"])].add(int(item["node_session_lab"]))
            except Exception:
                pass
    total = len(user_variants)
    for idx, (username, check_name) in enumerate(user_variants.items()):
        if cancel_ev.is_set():
            log_q.put("Проверка прервана.")
            break
        if progress_cb:
            progress_cb(idx, total, username)
        log_q.put("=" * 50)
        log_q.put(f"ПРОВЕРКА: {username} ({check_name})")
        pod = user_to_pod.get(username)
        if pod is None or pod not in pod_labs:
            log_q.put(f"  {username}: нет лаб")
            results[username] = {'check': check_name, 'score': 0, 'log': []}
            continue
        lab_id = sorted(pod_labs[pod])[0]
        sess.post(f"http://{ip}/api/labs/session/factory/join", json={"lab_session": lab_id})
        topo_r = sess.get(f"http://{ip}/api/labs/session/topology")
        if topo_r.status_code != 200:
            log_q.put(f"  Ошибка топологии для {username}")
            results[username] = {'check': check_name, 'score': 0, 'log': []}
            continue
        topology = topo_r.json().get("data", {}).get("nodes", {})
        if not isinstance(topology, dict):
            results[username] = {'check': check_name, 'score': 0, 'log': []}
            continue
        ports = parse_qemu_by_lab(lab_id)
        vm_ports = {info.get("name"): ports.get(info.get("name"), "N/A")
                    for info in topology.values() if info.get("name")}
        module_name = f"assignment_checker_{check_name}"
        checker_path = os.path.join(VARIANTS_DIR, f"{module_name}.py")
        try:
            if os.path.exists(checker_path):
                spec = importlib.util.spec_from_file_location(module_name, checker_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
            else:
                mod = __import__(module_name, fromlist=['run_full_assignment_check'])
            fn = getattr(mod, 'run_full_assignment_check', None)
            if not fn:
                log_q.put(f"  Функция не найдена в {check_name}")
                continue
            score, lines = fn(vm_ports)
        except Exception as e:
            log_q.put(f"  Исключение в чекере: {e}")
            score, lines = 0, [traceback.format_exc()]
        for line in lines:
            log_q.put(line)
        log_q.put(f"ИТОГО: {username} — {score}")
        log_file = os.path.join(LOGS_DIR, f"outstend_{check_name}_{username.replace('-', '_')}.txt")
        with open(log_file, "w", encoding="utf-8") as fh:
            fh.write('\n'.join(lines))
        results[username] = {'check': check_name, 'score': score, 'log': lines, 'log_file': log_file}
    if progress_cb:
        progress_cb(total, total, '')
    log_q.put("=== ВСЕ ПРОВЕРКИ ЗАВЕРШЕНЫ ===")
    return results


# ─────────────────────────────────────────────────────────────────────────────
#  TUI helpers (только при запуске с --cli)
# ─────────────────────────────────────────────────────────────────────────────

def curses_menu(stdscr, title, items, allow_back=True):
    import curses
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(2, curses.COLOR_CYAN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    idx = 0
    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        hdr = f"  PNETLAB Checker CLI  |  {title}  "
        stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
        stdscr.addstr(0, 0, hdr[:w-1])
        stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)
        stdscr.addstr(1, 0, "─" * (w - 1))
        start = max(0, idx - (h - 6))
        for i, item in enumerate(items[start:start + h - 5], start=start):
            row = i - start + 2
            if row >= h - 3:
                break
            if i == idx:
                stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
                stdscr.addstr(row, 0, f"► {item}"[:w-1].ljust(w-1))
                stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)
            else:
                stdscr.addstr(row, 0, f"  {item}"[:w-1])
        stdscr.addstr(h - 2, 0, "─" * (w - 1))
        hint = " ↑↓ навигация  Enter выбор" + ("  ESC/q назад" if allow_back else "")
        stdscr.attron(curses.color_pair(3))
        stdscr.addstr(h - 1, 0, hint[:w-1])
        stdscr.attroff(curses.color_pair(3))
        stdscr.refresh()
        key = stdscr.getch()
        if key in (curses.KEY_UP, ord('k')):
            idx = (idx - 1) % len(items)
        elif key in (curses.KEY_DOWN, ord('j')):
            idx = (idx + 1) % len(items)
        elif key in (curses.KEY_ENTER, 10, 13):
            return idx
        elif key in (27, ord('q'), ord('Q')) and allow_back:
            return -1


def curses_confirm(stdscr, question):
    import curses
    h, w = stdscr.getmaxyx()
    stdscr.clear()
    stdscr.addstr(h // 2 - 1, 2, question[:w-3])
    stdscr.addstr(h // 2 + 1, 2, "  [Enter] Да    [ESC/n] Нет")
    stdscr.refresh()
    while True:
        k = stdscr.getch()
        if k in (curses.KEY_ENTER, 10, 13):
            return True
        if k in (27, ord('n'), ord('N'), ord('q')):
            return False


def curses_input(stdscr, prompt, mask=False):
    import curses
    curses.echo()
    curses.curs_set(1)
    h, w = stdscr.getmaxyx()
    stdscr.addstr(h - 4, 2, prompt[:w-3])
    stdscr.addstr(h - 3, 2, "> ")
    stdscr.refresh()
    buf = []
    while True:
        ch = stdscr.getch()
        if ch in (curses.KEY_ENTER, 10, 13):
            break
        elif ch in (127, curses.KEY_BACKSPACE, 8):
            if buf:
                buf.pop()
                stdscr.addstr(h - 3, 2, "> " + ("*" if mask else "") * len(buf) + " ")
                stdscr.move(h - 3, 4 + len(buf))
        elif 32 <= ch < 256:
            buf.append(chr(ch))
            if not mask:
                stdscr.addstr(h - 3, 4 + len(buf) - 1, chr(ch))
            else:
                stdscr.addstr(h - 3, 4 + len(buf) - 1, "*")
    curses.noecho()
    curses.curs_set(0)
    return "".join(buf)


def curses_pager(stdscr, title, lines):
    import curses
    curses.init_pair(2, curses.COLOR_CYAN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    offset = 0
    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
        stdscr.addstr(0, 0, f"  {title}"[:w-1])
        stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)
        stdscr.addstr(1, 0, "─" * (w - 1))
        visible = h - 4
        for i, line in enumerate(lines[offset:offset + visible]):
            stdscr.addstr(i + 2, 0, line[:w-1])
        stdscr.addstr(h - 2, 0, "─" * (w - 1))
        pct = f"{offset+1}-{min(offset+visible, len(lines))}/{len(lines)}"
        stdscr.attron(curses.color_pair(3))
        stdscr.addstr(h - 1, 0, f" ↑↓/PgUp/PgDn прокрутка  q выход  {pct}"[:w-1])
        stdscr.attroff(curses.color_pair(3))
        stdscr.refresh()
        k = stdscr.getch()
        if k in (curses.KEY_UP, ord('k')):
            offset = max(0, offset - 1)
        elif k in (curses.KEY_DOWN, ord('j')):
            offset = min(max(0, len(lines) - visible), offset + 1)
        elif k == curses.KEY_PPAGE:
            offset = max(0, offset - visible)
        elif k == curses.KEY_NPAGE:
            offset = min(max(0, len(lines) - visible), offset + visible)
        elif k in (ord('q'), ord('Q'), 27):
            break


def show_message(stdscr, msg, error=False):
    import curses
    curses.init_pair(4, curses.COLOR_RED if error else curses.COLOR_GREEN, -1)
    h, w = stdscr.getmaxyx()
    stdscr.clear()
    stdscr.attron(curses.color_pair(4) | curses.A_BOLD)
    for i, line in enumerate(msg.splitlines()):
        stdscr.addstr(h // 2 + i, 2, line[:w-3])
    stdscr.attroff(curses.color_pair(4) | curses.A_BOLD)
    stdscr.addstr(h // 2 + len(msg.splitlines()) + 2, 2, "  [любая клавиша] продолжить")
    stdscr.refresh()
    stdscr.getch()


def show_message_nonblock(stdscr, msg):
    h, w = stdscr.getmaxyx()
    stdscr.clear()
    stdscr.addstr(h // 2, 2, msg[:w-3])
    stdscr.refresh()


def curses_live_log(stdscr, log_q, cancel_ev, progress_ref):
    import curses
    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(2, curses.COLOR_CYAN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(5, curses.COLOR_GREEN, -1)
    curses.init_pair(6, curses.COLOR_RED, -1)
    stdscr.nodelay(True)
    lines = []
    done = False
    while True:
        while True:
            try:
                line = log_q.get_nowait()
                lines.append(line)
                if "ЗАВЕРШЕН" in line or "прерв" in line.lower():
                    done = True
            except queue.Empty:
                break
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
        stdscr.addstr(0, 0, "  Лог операции"[:w-1])
        stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)
        stdscr.addstr(1, 0, "─" * (w - 1))
        visible = h - 5
        display = lines[max(0, len(lines) - visible):]
        for i, ln in enumerate(display):
            pair = 0
            if "ИТОГО" in ln or "сохранён" in ln or "ЗАВЕРШЕН" in ln:
                pair = 5
            elif "ОШИБКА" in ln or "Ошибка" in ln or "прерв" in ln:
                pair = 6
            elif ln.startswith("="):
                pair = 3
            if pair:
                stdscr.attron(curses.color_pair(pair))
            try:
                stdscr.addstr(i + 2, 0, ln[:w-1])
            except Exception:
                pass
            if pair:
                stdscr.attroff(curses.color_pair(pair))
        pref = progress_ref[0]
        pct = int(pref[0] / pref[1] * (w - 20)) if pref[1] > 0 else 0
        bar = "[" + "#" * pct + "." * ((w - 22) - pct) + "]"
        stdscr.attron(curses.color_pair(5))
        try:
            stdscr.addstr(h - 3, 0, f" {pref[0]}/{pref[1]} {bar} {pref[2]}"[:w-1])
        except Exception:
            pass
        stdscr.attroff(curses.color_pair(5))
        stdscr.addstr(h - 2, 0, "─" * (w - 1))
        stdscr.attron(curses.color_pair(3))
        try:
            stdscr.addstr(h - 1, 0, " c — отменить   q — выйти из просмотра"[:w-1])
        except Exception:
            pass
        stdscr.attroff(curses.color_pair(3))
        stdscr.refresh()
        if done:
            stdscr.nodelay(False)
            try:
                stdscr.addstr(h - 1, 0, " Завершено. Нажмите любую клавишу..."[:w-1])
            except Exception:
                pass
            stdscr.refresh()
            stdscr.getch()
            break
        k = stdscr.getch()
        if k == ord('c'):
            cancel_ev.set()
        elif k == ord('q'):
            break
        time.sleep(0.1)
    stdscr.nodelay(False)


def screen_multiselect(stdscr, title, items):
    import curses
    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(2, curses.COLOR_CYAN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    selected = set()
    idx = 0
    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
        stdscr.addstr(0, 0, f"  {title}"[:w-1])
        stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)
        stdscr.addstr(1, 0, "─" * (w - 1))
        start = max(0, idx - (h - 6))
        for i, item in enumerate(items[start:start + h - 5], start=start):
            row = i - start + 2
            if row >= h - 3:
                break
            mark = "[x]" if i in selected else "[ ]"
            if i == idx:
                stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
                stdscr.addstr(row, 0, ("► " + mark + " " + item)[:w-1].ljust(w-1))
                stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)
            else:
                stdscr.addstr(row, 0, f"  {mark} {item}"[:w-1])
        stdscr.addstr(h - 2, 0, "─" * (w - 1))
        stdscr.attron(curses.color_pair(3))
        stdscr.addstr(h - 1, 0,
                      f" Space выбрать  a выбрать всё  Enter подтвердить ({len(selected)})  ESC отмена"[:w-1])
        stdscr.attroff(curses.color_pair(3))
        stdscr.refresh()
        k = stdscr.getch()
        if k in (curses.KEY_UP, ord('k')):
            idx = (idx - 1) % len(items)
        elif k in (curses.KEY_DOWN, ord('j')):
            idx = (idx + 1) % len(items)
        elif k == ord(' '):
            if idx in selected:
                selected.discard(idx)
            else:
                selected.add(idx)
        elif k == ord('a'):
            if len(selected) == len(items):
                selected.clear()
            else:
                selected = set(range(len(items)))
        elif k in (curses.KEY_ENTER, 10, 13):
            return [items[i] for i in sorted(selected)]
        elif k in (27, ord('q')):
            return []


# ─────────────────────────────────────────────────────────────────────────────
#  Screen handlers
# ─────────────────────────────────────────────────────────────────────────────

def screen_nodes(stdscr):
    while True:
        nodes = load_nodes()
        items = [f"{n['name']}  ({n['ip']})" for n in nodes] + ["+ Добавить новый узел", "< Назад"]
        choice = curses_menu(stdscr, "Управление узлами", items)
        if choice == -1 or choice == len(items) - 1:
            return
        if choice == len(items) - 2:
            stdscr.clear()
            name = curses_input(stdscr, "Название узла:")
            ip   = curses_input(stdscr, "IP-адрес:")
            user = curses_input(stdscr, "Логин:")
            pwd  = curses_input(stdscr, "Пароль:", mask=True)
            if not all([name, ip, user, pwd]):
                show_message(stdscr, "Все поля обязательны", error=True)
                continue
            nodes.append({"name": name, "ip": ip, "username": user, "password": pwd})
            save_nodes(nodes)
            show_message(stdscr, f"Узел '{name}' добавлен.")
        else:
            screen_node_detail(stdscr, nodes[choice], nodes)


def screen_node_detail(stdscr, node, all_nodes):
    while True:
        items = [
            "Инфо / пользователи",
            "Остановить все ноды во всех лабах",
            "Запустить ноды лабы...",
            "Удалить узел",
            "< Назад",
        ]
        choice = curses_menu(stdscr, f"Узел: {node['name']} ({node['ip']})", items)
        if choice == -1 or choice == 4:
            return
        if choice == 0:
            users, err = get_node_users(node)
            if err:
                show_message(stdscr, f"Ошибка: {err}", error=True)
                continue
            lines = [f"Узел: {node['name']}  IP: {node['ip']}", ""]
            if not users:
                lines.append("  Нет активных пользователей")
            else:
                lines.append(f"  {'Пользователь':<28} {'Pod':>6}  {'Лабы':>5}")
                lines.append("  " + "─" * 44)
                for u in users:
                    lines.append(f"  {u['name']:<28} {u['pod']:>6}  {u['lab_count']:>5}")
            curses_pager(stdscr, "Пользователи узла", lines)
        elif choice == 1:
            if curses_confirm(stdscr, f"Остановить все ноды на {node['name']}?"):
                ip = node["ip"]
                sess, err = login_to_node(ip, node["username"], node["password"])
                if err:
                    show_message(stdscr, f"Ошибка: {err}", error=True)
                    continue
                try:
                    nr = sess.post(f"http://{ip}/store/public/admin/node_sessions/getConsume", timeout=10)
                    nd = nr.json().get("data", [])
                    lab_ids = list(set(i.get("node_session_lab") for i in nd if i.get("node_session_lab")))
                    deleted = 0
                    for lid in lab_ids:
                        sess.post(f"http://{ip}/api/labs/session/factory/join", json={"lab_session": lid})
                        r = sess.post(f"http://{ip}/api/labs/session/nodes/stop")
                        if r.status_code in [200, 201, 202]:
                            deleted += 1
                    show_message(stdscr, f"Остановлены ноды в {deleted} из {len(lab_ids)} лаб.")
                except Exception as e:
                    show_message(stdscr, f"Ошибка: {e}", error=True)
        elif choice == 2:
            lab_id_str = curses_input(stdscr, "Введите ID лабы (число):")
            if not lab_id_str.strip().isdigit():
                show_message(stdscr, "ID должен быть числом", error=True)
                continue
            lab_id = int(lab_id_str.strip())
            ip = node["ip"]
            sess, err = login_to_node(ip, node["username"], node["password"])
            if err:
                show_message(stdscr, f"Ошибка: {err}", error=True)
                continue
            try:
                sess.post(f"http://{ip}/api/labs/session/factory/join", json={"lab_session": lab_id})
                tr = sess.get(f"http://{ip}/api/labs/session/topology", timeout=10)
                topo = tr.json().get("data", {}).get("nodes", {})
                if not topo:
                    show_message(stdscr, "Топология пуста", error=True)
                    continue
                started = 0
                for nid in topo.keys():
                    r = sess.put(f"http://{ip}/api/labs/session/nodes/{nid}/start", timeout=10)
                    if r.status_code in [200, 201, 202]:
                        started += 1
                show_message(stdscr, f"Запущено: {started} из {len(topo)}")
            except Exception as e:
                show_message(stdscr, f"Ошибка: {e}", error=True)
        elif choice == 3:
            if curses_confirm(stdscr, f"Удалить узел '{node['name']}'?"):
                new_nodes = [n for n in all_nodes if n["name"] != node["name"]]
                save_nodes(new_nodes)
                show_message(stdscr, f"Узел '{node['name']}' удалён.")
                return


def screen_rooms(stdscr):
    while True:
        rooms = load_rooms()
        items = [f"{r['name']}  [{r.get('node','')}]  ({len(r.get('users',[]))} польз.)" for r in rooms]
        items += ["+ Создать комнату", "< Назад"]
        choice = curses_menu(stdscr, "Комнаты проверки", items)
        if choice == -1 or choice == len(items) - 1:
            return
        if choice == len(items) - 2:
            screen_room_create(stdscr)
        else:
            screen_room_run(stdscr, rooms[choice])


def screen_room_create(stdscr):
    nodes = load_nodes()
    if not nodes:
        show_message(stdscr, "Нет узлов. Сначала добавьте узел.", error=True)
        return
    stdscr.clear()
    name = curses_input(stdscr, "Название комнаты:")
    if not name:
        return
    node_items = [f"{n['name']} ({n['ip']})" for n in nodes]
    ni = curses_menu(stdscr, "Выберите узел для комнаты", node_items)
    if ni == -1:
        return
    node = nodes[ni]
    show_message_nonblock(stdscr, "Загружаю пользователей...")
    users_data, err = get_node_users(node)
    if err or not users_data:
        show_message(stdscr, f"Не удалось получить пользователей: {err}", error=True)
        return
    selected_users = screen_multiselect(stdscr, "Выберите пользователей", [u['name'] for u in users_data])
    if not selected_users:
        show_message(stdscr, "Не выбрано ни одного пользователя.", error=True)
        return
    checks_list = [c['name'] for c in get_available_checks()]
    if not checks_list:
        show_message(stdscr, "Нет доступных чекеров", error=True)
        return
    ci = curses_menu(stdscr, "Выберите вариант проверки", checks_list)
    if ci == -1:
        return
    check = checks_list[ci]
    rooms = load_rooms()
    rooms.append({"name": name, "node": node["name"], "users": selected_users, "check": check})
    save_rooms(rooms)
    show_message(stdscr, f"Комната '{name}' создана ({len(selected_users)} пользователей).")


def screen_room_run(stdscr, room):
    while True:
        items = [
            f"Запустить проверку ({len(room.get('users',[]))} польз., {room.get('check','')})",
            "Просмотр последних результатов",
            "Скачать все логи (ZIP)",
            "Удалить комнату",
            "< Назад",
        ]
        choice = curses_menu(stdscr, f"Комната: {room['name']}", items)
        if choice == -1 or choice == 4:
            return
        if choice == 0:
            nodes = load_nodes()
            node = next((n for n in nodes if n["name"] == room.get("node")), nodes[0] if nodes else None)
            if not node:
                show_message(stdscr, "Узел не найден", error=True)
                continue
            user_variants = {u: room.get("check", "") for u in room.get("users", [])}
            if not user_variants:
                show_message(stdscr, "В комнате нет пользователей", error=True)
                continue
            log_q = queue.Queue()
            cancel_ev = threading.Event()
            progress_ref = [[0, len(user_variants), ""]]
            def progress_cb(done, total, user):
                progress_ref[0] = [done, total, user]
            results_holder = [None]
            def check_thread():
                results_holder[0] = run_check_for_users(node, user_variants, log_q, cancel_ev, progress_cb)
            t = threading.Thread(target=check_thread, daemon=True)
            t.start()
            curses_live_log(stdscr, log_q, cancel_ev, progress_ref)
            t.join(timeout=2)
            if results_holder[0]:
                room['_last_results'] = results_holder[0]
                summary_path = os.path.join(LOGS_DIR, f"summary_{room['name'].replace(' ','_')}.txt")
                with open(summary_path, "w", encoding="utf-8") as f:
                    f.write("Пользователь\tВариант\tБалл\n")
                    for u, r in results_holder[0].items():
                        f.write(f"{u}\t{r['check']}\t{r['score']}\n")
        elif choice == 1:
            res = room.get("_last_results")
            if not res:
                show_message(stdscr, "Нет результатов. Запустите проверку.", error=True)
                continue
            lines = [f"{'Пользователь':<30} {'Вариант':<25} {'Балл':>6}", "─" * 65]
            for u, r in res.items():
                lines.append(f"{u:<30} {r['check']:<25} {r['score']:>6}")
            curses_pager(stdscr, "Результаты: " + room["name"], lines)
        elif choice == 2:
            res = room.get("_last_results")
            if not res:
                show_message(stdscr, "Нет результатов для скачивания.", error=True)
                continue
            archive = os.path.join(BASE_DIR, f"results_{room['name'].replace(' ','_')}.zip")
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zf:
                for u, r in res.items():
                    lf = r.get('log_file')
                    if lf and os.path.exists(lf):
                        zf.write(lf, os.path.basename(lf))
                summary_path = os.path.join(LOGS_DIR, f"summary_{room['name'].replace(' ','_')}.txt")
                if os.path.exists(summary_path):
                    zf.write(summary_path, os.path.basename(summary_path))
            show_message(stdscr, f"Архив сохранён:\n{archive}")
        elif choice == 3:
            if curses_confirm(stdscr, f"Удалить комнату '{room['name']}'?"):
                rooms = load_rooms()
                rooms = [r for r in rooms if r["name"] != room["name"]]
                save_rooms(rooms)
                show_message(stdscr, f"Комната '{room['name']}' удалена.")
                return


def screen_start_nodes(stdscr):
    nodes = load_nodes()
    if not nodes:
        show_message(stdscr, "Нет узлов. Добавьте узел в разделе 'Узлы'.", error=True)
        return
    ni = curses_menu(stdscr, "Запуск нод — выберите узел",
                     [f"{n['name']} ({n['ip']})" for n in nodes])
    if ni == -1:
        return
    node = nodes[ni]
    while True:
        items = [
            "Запустить группу m1 (Student-m1-*)",
            "Запустить группу m2 (Student-m2-*)",
            "Запустить всех пользователей узла",
            "Выбрать пользователей вручную...",
            "< Назад",
        ]
        choice = curses_menu(stdscr, f"Узел: {node['name']} — что запустить?", items)
        if choice == -1 or choice == 4:
            return
        all_labs = curses_confirm(stdscr,
            "Запустить ВСЕ лабы пользователя? (Enter = да, ESC/n = только первая)")
        show_message_nonblock(stdscr, "Загружаю пользователей с узла...")
        users_data, err = get_node_users(node)
        if err:
            show_message(stdscr, f"Ошибка: {err}", error=True)
            continue
        if not users_data:
            show_message(stdscr, "На узле нет активных пользователей.", error=True)
            continue
        all_names = [u['name'] for u in users_data]
        if choice == 0:
            usernames = filter_users_by_group(all_names, 'm1')
        elif choice == 1:
            usernames = filter_users_by_group(all_names, 'm2')
        elif choice == 2:
            usernames = list(all_names)
        else:
            usernames = screen_multiselect(stdscr,
                "Выберите пользователей (Space — отметить, Enter — подтвердить)", all_names)
        if not usernames:
            show_message(stdscr, "Не выбрано ни одного пользователя.", error=True)
            continue
        if not curses_confirm(stdscr,
                f"Запустить ноды для {len(usernames)} польз. (all_labs={'да' if all_labs else 'нет'})?"):
            continue
        log_q = queue.Queue()
        cancel_ev = threading.Event()
        progress_ref = [[0, len(usernames), '']]
        def progress_cb(done, total, user):
            progress_ref[0] = [done, total, user]
        def runner():
            start_nodes_for_users_cli(node, usernames, all_labs=all_labs,
                                      log_q=log_q, progress_cb=progress_cb,
                                      max_workers=6, cancel_ev=cancel_ev)
        t = threading.Thread(target=runner, daemon=True)
        t.start()
        curses_live_log(stdscr, log_q, cancel_ev, progress_ref)
        t.join(timeout=2)


def screen_quick_check(stdscr):
    nodes = load_nodes()
    if not nodes:
        show_message(stdscr, "Нет узлов. Добавьте узел в разделе 'Узлы'.", error=True)
        return
    ni = curses_menu(stdscr, "Быстрая проверка — выберите узел",
                     [f"{n['name']} ({n['ip']})" for n in nodes])
    if ni == -1:
        return
    node = nodes[ni]
    show_message_nonblock(stdscr, "Загружаю пользователей...")
    users_data, err = get_node_users(node)
    if err or not users_data:
        show_message(stdscr, f"Не удалось получить пользователей: {err}", error=True)
        return
    selected_users = screen_multiselect(stdscr, "Выберите пользователей для проверки",
                                        [u['name'] for u in users_data])
    if not selected_users:
        return
    checks_list = [c['name'] for c in get_available_checks()]
    if not checks_list:
        show_message(stdscr, "Нет доступных чекеров", error=True)
        return
    ci = curses_menu(stdscr, "Выберите вариант проверки", checks_list)
    if ci == -1:
        return
    check = checks_list[ci]
    user_variants = {u: check for u in selected_users}
    log_q = queue.Queue()
    cancel_ev = threading.Event()
    progress_ref = [[0, len(user_variants), ""]]
    def progress_cb(done, total, user):
        progress_ref[0] = [done, total, user]
    results_holder = [None]
    def check_thread():
        results_holder[0] = run_check_for_users(node, user_variants, log_q, cancel_ev, progress_cb)
    t = threading.Thread(target=check_thread, daemon=True)
    t.start()
    curses_live_log(stdscr, log_q, cancel_ev, progress_ref)
    t.join(timeout=2)
    if results_holder[0]:
        lines = [f"{'Пользователь':<30} {'Балл':>6}", "─" * 38]
        for u, r in results_holder[0].items():
            lines.append(f"{u:<30} {r['score']:>6}")
        curses_pager(stdscr, "Результаты быстрой проверки", lines)


def _cli_main(stdscr):
    import curses
    curses.curs_set(0)
    while True:
        items = [
            "Комнаты проверки",
            "Быстрая проверка",
            "Запуск нод проекта",
            "Управление узлами",
            "Выход",
        ]
        choice = curses_menu(stdscr, "Главное меню", items, allow_back=False)
        if choice == 0:
            screen_rooms(stdscr)
        elif choice == 1:
            screen_quick_check(stdscr)
        elif choice == 2:
            screen_start_nodes(stdscr)
        elif choice == 3:
            screen_nodes(stdscr)
        elif choice in (-1, 4):
            break


def main_cli():
    if not _CURSES_AVAILABLE:
        print("Модуль 'curses' недоступен на этой платформе.")
        return
    import curses
    try:
        curses.wrapper(_cli_main)
    except KeyboardInterrupt:
        pass
    print("До свидания.")


# ═══════════════════════════════════════════════════════════════════════════════
#  Точка входа
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import sys
    if '--cli' in sys.argv:
        main_cli()
    else:
        app.run(debug=True, host='0.0.0.0', port=5000)
