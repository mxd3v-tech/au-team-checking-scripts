import subprocess
import re
import json
import sys

# ========== ПАРАМЕТРЫ ВАРИАТИВНОЙ ЧАСТИ (ВЧ) — ОЦЕНКА ПО КРИТЕРИЯМ ==========
# КОД 09.02.06-1-2026, вариативная часть. Узел управления Ansible на BR-SRV.
# Таблица 1.4: 10 подкритериев, итоговый максимум = 25 баллов.
# Каждый подкритерий оценивается 0/1/2 и умножается на вес (1 или 1,5).
CONTROL_NODE = "BR-SRV"
ANSIBLE_DIR = "/etc/ansible"
INVENTORY_PATH = "/etc/ansible/inventory"
GROUP_VARS_DIR = "/etc/ansible/group_vars"
RUN_AS_USER = "user"

GROUPS = {
    "networking": ["hq-rtr", "br-rtr"],
    "servers": ["hq-srv", "br-srv"],
    "clients": ["hq-cli"],
}
EXPECTED_USER = {"networking": "admin", "servers": "root", "clients": "root"}

SRV_CREDENTIALS = {
    "BR-SRV": ("root", "toor"), "HQ-SRV": ("root", "toor"),
    "HQ-CLI": ("root", "toor"), "ISP": ("root", "toor"),
}

def get_srv_creds(name): return SRV_CREDENTIALS.get(name, ("root", "toor"))

# ========== SSH ==========
def ssh_exec(ssh_port, command, username='root', password='toor', timeout=40):
    if not ssh_port or ssh_port == "N/A":
        return None, "❌ Порт SSH недоступен: %s" % ssh_port, command
    try:
        ssh_cmd = ["sshpass", "-p", password, "ssh", "-o", "StrictHostKeyChecking=no",
                   "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10",
                   "-tt", "-p", str(ssh_port), "%s@localhost" % username, command]
        process = subprocess.Popen(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        stdout, stderr = process.communicate(timeout=timeout)
        return stdout, stderr, command
    except subprocess.TimeoutExpired:
        process.kill()
        return None, "❌ Таймаут выполнения команды (%d сек)" % timeout, command
    except FileNotFoundError:
        return None, "❌ Команда 'sshpass' не найдена. Установите пакет sshpass.", command
    except Exception as e:
        return None, "❌ Ошибка выполнения: %s" % str(e), command

def safe_log_output(log_lines, prefix, output, error=""):
    log_lines.append("%s:\n%s\n" % (prefix, (output or "") + ("\n" + error if error else "")))

# ========== РАЗБОР ИНВЕНТАРЯ ==========
def parse_inventory(raw):
    if not raw:
        return None
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except (ValueError, json.JSONDecodeError):
        return None

def hosts_of_group(inv, group):
    if not inv:
        return []
    for key in inv:
        if key.lower() == group.lower() and isinstance(inv[key], dict):
            return inv[key].get("hosts", []) or []
    return []

def host_matches(host_list, needle):
    n = needle.lower()
    for h in host_list:
        hl = str(h).lower()
        if hl == n or hl.startswith(n):
            return True
    return False

def hostvars(inv):
    if not inv:
        return {}
    return inv.get("_meta", {}).get("hostvars", {}) or {}

# ========== ГЛАВНАЯ ==========
def run_full_assignment_check(vm_ports):
    POINTS = 0.0
    MAX_POINTS = 25.0
    log_lines = []
    results = {}

    def log_msg(msg):
        log_lines.append(msg); print(msg)

    def award(name, raw, weight, details=""):
        """raw — оценка подкритерия 0/1/2; вес — 1 или 1,5; итог = raw*вес."""
        nonlocal POINTS
        max_score = 2 * weight
        score = raw * weight
        POINTS += score
        results[name] = {"score": score, "max": max_score}
        icon = "✅" if score == max_score else ("⚠️" if score > 0 else "❌")
        log_msg("%s %s: %s/%s%s" % (icon, name, _fmt(score), _fmt(max_score), " — " + details if details else ""))

    def _fmt(v):
        return ("%g" % v)

    log_msg("\n🔍 SSH-порты:"); [log_msg("  %s: %s" % (d, p)) for d, p in sorted(vm_ports.items())]
    log_msg("\n🔍 Вариативная часть (ВЧ), КОД 09.02.06-1-2026. Узел управления Ansible на %s\n" % CONTROL_NODE)

    if CONTROL_NODE not in vm_ports or vm_ports[CONTROL_NODE] == "N/A":
        log_msg("❌ %s не найдена — оценка вариативной части невозможна." % CONTROL_NODE)
        log_msg("\n📈 Набрано %.2f из %.2f баллов." % (POINTS, MAX_POINTS))
        return POINTS, log_lines

    port = vm_ports[CONTROL_NODE]
    suser, spw = get_srv_creds(CONTROL_NODE)

    # ---- Сбор данных ----
    ver_out, _, _ = ssh_exec(port, "ansible --version 2>/dev/null", suser, spw)
    safe_log_output(log_lines, "[%s] ansible --version" % CONTROL_NODE, ver_out)
    ver_out = ver_out or ""

    inv_explicit, _, _ = ssh_exec(port, "ansible-inventory -i %s --list 2>/dev/null" % INVENTORY_PATH, suser, spw)
    inv_default, _, _ = ssh_exec(port, "cd %s && ansible-inventory --list 2>/dev/null" % ANSIBLE_DIR, suser, spw)
    inv = parse_inventory(inv_explicit)
    inv_def = parse_inventory(inv_default)
    hv = hostvars(inv)

    struct_out, _, _ = ssh_exec(
        port,
        "echo '===DIR==='; ls -la %s/ 2>/dev/null; echo '===GV==='; ls -la %s/ 2>/dev/null; "
        "echo '===CFG==='; cat %s/ansible.cfg 2>/dev/null; cat /etc/ansible.cfg 2>/dev/null; "
        "echo '===INV==='; test -f %s && echo INVENTORY_FILE_OK" % (ANSIBLE_DIR, GROUP_VARS_DIR, ANSIBLE_DIR, INVENTORY_PATH),
        suser, spw)
    safe_log_output(log_lines, "[%s] структура /etc/ansible" % CONTROL_NODE, struct_out)
    struct_out = struct_out or ""

    gv_out, _, _ = ssh_exec(port, "for f in %s/*; do echo \"### $f\"; cat \"$f\"; done 2>/dev/null" % GROUP_VARS_DIR, suser, spw)
    safe_log_output(log_lines, "[%s] group_vars содержимое" % CONTROL_NODE, gv_out)
    gv_out = gv_out or ""

    file_ok = "INVENTORY_FILE_OK" in struct_out
    gv_listing = struct_out.split("===GV===")[-1].split("===CFG===")[0] if "===GV===" in struct_out else ""

    # ==== КО1: Ansible установлен (вес 1, max 2) ====
    if re.search(r'ansible\b', ver_out, re.IGNORECASE) and re.search(r'\d+\.\d+', ver_out):
        award("КО1: Ansible установлен", 2, 1)
    elif re.search(r'ansible', ver_out, re.IGNORECASE):
        award("КО1: Ansible установлен", 1, 1, "версия не определена")
    else:
        award("КО1: Ansible установлен", 0, 1, "ansible не найден")

    # ==== КО2: Инвентарь по умолчанию из требуемого каталога (вес 1.5, max 3) ====
    default_ok = bool(inv_def) and any(g.lower() in [k.lower() for k in inv_def] for g in GROUPS)
    explicit_ok = bool(inv) and any(g.lower() in [k.lower() for k in inv] for g in GROUPS)
    if file_ok and default_ok:
        award("КО2: Инвентарь %s по умолчанию" % INVENTORY_PATH, 2, 1.5)
    elif file_ok and explicit_ok:
        award("КО2: Инвентарь %s по умолчанию" % INVENTORY_PATH, 1, 1.5, "работает только с -i")
    else:
        award("КО2: Инвентарь %s по умолчанию" % INVENTORY_PATH, 0, 1.5, "файл не найден/не разобран")

    # ==== КО3: Группа маршрутизаторов (вес 1.5, max 3) ====
    net_hosts = hosts_of_group(inv, "networking")
    if net_hosts and all(host_matches(net_hosts, r) for r in GROUPS["networking"]):
        award("КО3: Группа networking с маршрутизаторами", 2, 1.5, ", ".join(net_hosts))
    elif net_hosts:
        award("КО3: Группа networking с маршрутизаторами", 1, 1.5, "неполный состав: %s" % ", ".join(net_hosts))
    else:
        award("КО3: Группа networking с маршрутизаторами", 0, 1.5, "группа отсутствует/пуста")

    # ==== КО4: Группа серверов (вес 1.5, max 3) ====
    srv_hosts = hosts_of_group(inv, "servers")
    if srv_hosts and all(host_matches(srv_hosts, s) for s in GROUPS["servers"]):
        award("КО4: Группа servers с серверами", 2, 1.5, ", ".join(srv_hosts))
    elif srv_hosts:
        award("КО4: Группа servers с серверами", 1, 1.5, "неполный состав: %s" % ", ".join(srv_hosts))
    else:
        award("КО4: Группа servers с серверами", 0, 1.5, "группа отсутствует/пуста")

    # ==== КО5: Переменные групп в group_vars (вес 1, max 2) ====
    gv_dir_present = "===GV===" in struct_out and bool(gv_listing.strip()) and "total" in gv_listing.lower()
    gv_files = sum(1 for g in GROUPS if re.search(r'\b%s\b' % g, gv_listing, re.IGNORECASE))
    if gv_dir_present and gv_files >= len(GROUPS):
        award("КО5: Переменные в group_vars", 2, 1, "файлов групп: %d/%d" % (gv_files, len(GROUPS)))
    elif gv_dir_present and gv_files >= 1:
        award("КО5: Переменные в group_vars", 1, 1, "файлов групп: %d/%d" % (gv_files, len(GROUPS)))
    else:
        award("КО5: Переменные в group_vars", 0, 1, "папка group_vars отсутствует/пуста")

    # ==== КО6: ansible_user задан для каждой группы (вес 1, max 2) ====
    found_users = {}
    for g in GROUPS:
        val = None
        for h in GROUPS[g]:
            for hk in hv:
                if hk.lower().startswith(h.lower()):
                    val = hv[hk].get("ansible_user")
                    break
            if val:
                break
        found_users[g] = val
    correct = sum(1 for g in GROUPS if found_users[g] == EXPECTED_USER[g])
    defined = sum(1 for g in GROUPS if found_users[g])
    udetail = "; ".join("%s=%s" % (g, found_users[g]) for g in GROUPS)
    if correct == len(GROUPS):
        award("КО6: ansible_user задан верно", 2, 1, udetail)
    elif defined >= 1:
        award("КО6: ansible_user задан верно", 1, 1, udetail)
    else:
        award("КО6: ansible_user задан верно", 0, 1, "не определён")

    # ==== КО7: ansible_ssh_private_key_file + ключ с правами 600 (вес 1.5, max 3) ====
    key_groups = {}
    for g in GROUPS:
        kp = None
        for h in GROUPS[g]:
            for hk in hv:
                if hk.lower().startswith(h.lower()):
                    kp = hv[hk].get("ansible_ssh_private_key_file")
                    break
            if kp:
                break
        key_groups[g] = kp
    key_paths = set(v for v in key_groups.values() if v)
    perms_ok = False
    loose = False
    if key_paths:
        checks = " ; ".join("stat -c '%%a %%n' %s 2>/dev/null" % kp for kp in key_paths)
        perm_out, _, _ = ssh_exec(port, checks, suser, spw)
        safe_log_output(log_lines, "[%s] права на ключи" % CONTROL_NODE, perm_out)
        if perm_out:
            modes = re.findall(r'^\s*(\d{3,4})\s', perm_out, re.MULTILINE)
            if modes and all(m in ("600", "400") for m in modes):
                perms_ok = True
            elif modes:
                loose = True
    keys_defined = sum(1 for g in GROUPS if key_groups[g])
    if keys_defined == len(GROUPS) and perms_ok:
        award("КО7: ansible_ssh_private_key_file + права 600", 2, 1.5, ", ".join(key_paths))
    elif keys_defined >= 1 and (perms_ok or loose):
        award("КО7: ansible_ssh_private_key_file + права 600", 1, 1.5,
              "не для всех групп или открытые права")
    else:
        award("КО7: ansible_ssh_private_key_file + права 600", 0, 1.5, "переменная/ключ отсутствует")

    # ==== КО8: ansible all -m ping → pong (вес 1, max 2) ====
    ping_out, ping_err, _ = ssh_exec(
        port, "runuser -l %s -c 'cd %s && ansible all -m ping' 2>&1" % (RUN_AS_USER, ANSIBLE_DIR),
        suser, spw, timeout=90)
    safe_log_output(log_lines, "[%s] ansible all -m ping (от %s)" % (CONTROL_NODE, RUN_AS_USER), ping_out, ping_err)
    ping_out = ping_out or ""
    pong_cnt = len(re.findall(r'"ping"\s*:\s*"pong"', ping_out))
    success_cnt = len(re.findall(r'SUCCESS', ping_out))
    unreachable = len(re.findall(r'UNREACHABLE', ping_out))
    failed = len(re.findall(r'FAILED|"failed"\s*:\s*true', ping_out))
    if pong_cnt >= 1 and unreachable == 0 and failed == 0:
        award("КО8: ansible -m ping → pong", 2, 1, "pong=%d" % pong_cnt)
    elif pong_cnt >= 1 or success_cnt >= 1:
        award("КО8: ansible -m ping → pong", 1, 1, "pong=%d UNREACHABLE=%d FAILED=%d" % (pong_cnt, unreachable, failed))
    else:
        award("КО8: ansible -m ping → pong", 0, 1, "нет ответов pong")

    # ==== КО9: Корректный интерпретатор Python (вес 1, max 2) ====
    interp_var = bool(re.search(r'ansible_python_interpreter', gv_out)) or \
                 any("ansible_python_interpreter" in (hv.get(hk) or {}) for hk in hv)
    interp_err = bool(re.search(r'(was not able to find|No module named|/usr/bin/python.*not found|interpreter.*not found)', ping_out, re.IGNORECASE))
    interp_warn = bool(re.search(r'(discovered_interpreter|DEPRECATION.*python|interpreter)', ping_out, re.IGNORECASE)) and not interp_var
    if (interp_var or (pong_cnt >= 1 and not interp_err)) and not interp_warn:
        award("КО9: Корректный интерпретатор Python", 2, 1,
              "ansible_python_interpreter задан" if interp_var else "ping без ошибок интерпретатора")
    elif pong_cnt >= 1 and not interp_err:
        award("КО9: Корректный интерпретатор Python", 1, 1, "есть предупреждения об интерпретаторе")
    else:
        award("КО9: Корректный интерпретатор Python", 0, 1, "ошибка интерпретатора / нет связи")

    # ==== КО10: Структура каталогов (вес 1.5, max 3) ====
    cfg_present = "===CFG===" in struct_out and bool(struct_out.split("===CFG===")[-1].split("===INV===")[0].strip())
    if cfg_present and file_ok and gv_files >= len(GROUPS):
        award("КО10: Структура каталогов", 2, 1.5, "ansible.cfg + inventory + group_vars")
    elif file_ok and (cfg_present or gv_files >= 1):
        award("КО10: Структура каталогов", 1, 1.5, "незначительные отклонения")
    else:
        award("КО10: Структура каталогов", 0, 1.5, "структура отсутствует")

    # --- ИТОГ (в порядке оценочной ведомости, раздел 4) ---
    # Логика проверки не меняется: КО1…КО10 уже идут в порядке ведомости (4.1…4.10),
    # поэтому при формировании итогового блока сопоставляем их по позиции с кодами.
    VEDOMOST = [
        ("4.1",  "Установка Ansible"),
        ("4.2",  "Инвентарь по умолчанию"),
        ("4.3",  "Группа маршрутизаторов"),
        ("4.4",  "Группа серверов"),
        ("4.5",  "Переменные в group_vars"),
        ("4.6",  "Пользователь ansible_user"),
        ("4.7",  "SSH-ключ (права 600)"),
        ("4.8",  "Связность (ansible ping)"),
        ("4.9",  "Интерпретатор Python"),
        ("4.10", "Структура каталогов"),
    ]
    log_msg("\n📊 ИТОГ ПО КРИТЕРИЯМ ОЦЕНИВАНИЯ (ВЧ):")
    log_msg("=" * 60)
    for (code, title), (name, r) in zip(VEDOMOST, results.items()):
        icon = "✅" if r["score"] == r["max"] else ("⚠️" if r["score"] > 0 else "❌")
        log_msg("%s %s %s: %s/%s" % (icon, code, title, _fmt(r["score"]), _fmt(r["max"])))
    log_msg("\n📈 Набрано %.2f из %.2f баллов." % (POINTS, MAX_POINTS))
    return POINTS, log_lines


if __name__ == "__main__":
    pass
