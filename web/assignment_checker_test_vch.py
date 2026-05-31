import subprocess
import re
import json
import sys

# ========== ПАРАМЕТРЫ ВАРИАТИВНОЙ ЧАСТИ (ВЧ) ==========
# КОД 09.02.06-1-2026, вариативная часть. Узел управления Ansible на BR-SRV.
CONTROL_NODE = "BR-SRV"
ANSIBLE_DIR = "/etc/ansible"
INVENTORY_PATH = "/etc/ansible/inventory"
GROUP_VARS_DIR = "/etc/ansible/group_vars"
PLAYBOOK_PATH = "/etc/ansible/gathering.yml"
OUTPUT_PATH = "/etc/ansible/output.yaml"
RUN_AS_USER = "user"  # под этим пользователем должен запускаться ansible

# Требуемые группы инвентаря и их состав (по топологии 2026).
GROUPS = {
    "networking": ["hq-rtr", "br-rtr"],
    "servers": ["hq-srv", "br-srv"],
    "clients": ["hq-cli"],
}
# Ожидаемое значение ansible_user для каждой группы.
# Роутеры (EcoRouterOS) — admin/admin; серверы и клиенты — root/toor.
EXPECTED_USER = {"networking": "admin", "servers": "root", "clients": "root"}

# ========== УЧЁТНЫЕ ДАННЫЕ ==========
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
    """Парсит JSON-вывод `ansible-inventory --list`. Возвращает dict или None."""
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
    """Список хостов группы (учёт регистра имени группы)."""
    if not inv:
        return []
    for key in inv:
        if key.lower() == group.lower() and isinstance(inv[key], dict):
            return inv[key].get("hosts", []) or []
    return []

def host_matches(host_list, needle):
    """Есть ли в списке хост, имя которого начинается с needle (без учёта регистра/домена)."""
    n = needle.lower()
    for h in host_list:
        hl = str(h).lower()
        if hl == n or hl.startswith(n + ".") or hl.startswith(n):
            return True
    return False

def hostvars(inv):
    if not inv:
        return {}
    return inv.get("_meta", {}).get("hostvars", {}) or {}

# ========== ГЛАВНАЯ ==========
def run_full_assignment_check(vm_ports):
    POINTS = 0.0
    log_lines = []
    results = {}

    def log_msg(msg):
        log_lines.append(msg); print(msg)

    def check(name, passed, detail=""):
        nonlocal POINTS
        results[name] = bool(passed)
        if passed:
            POINTS += 1.0
            log_msg("✅ %s%s" % (name, " — " + detail if detail else ""))
        else:
            log_msg("❌ %s%s" % (name, " — " + detail if detail else ""))

    log_msg("\n🔍 SSH-порты:"); [log_msg("  %s: %s" % (d, p)) for d, p in sorted(vm_ports.items())]
    log_msg("\n🔍 Вариативная часть (ВЧ), КОД 09.02.06-1-2026. Узел управления Ansible на %s\n" % CONTROL_NODE)

    if CONTROL_NODE not in vm_ports or vm_ports[CONTROL_NODE] == "N/A":
        log_msg("❌ %s не найдена — проверка вариативной части невозможна." % CONTROL_NODE)
        log_msg("\n📈 Выполнено %.2f пунктов." % POINTS)
        return POINTS, log_lines

    port = vm_ports[CONTROL_NODE]
    user, pw = get_srv_creds(CONTROL_NODE)

    # ---- Сбор данных с узла управления (по возможности один раз) ----
    ver_out, _, _ = ssh_exec(port, "ansible --version 2>/dev/null", user, pw)
    safe_log_output(log_lines, "[%s] ansible --version" % CONTROL_NODE, ver_out)

    inv_explicit, inv_exp_err, _ = ssh_exec(port, "ansible-inventory -i %s --list 2>/dev/null" % INVENTORY_PATH, user, pw)
    inv_default, _, _ = ssh_exec(port, "cd %s && ansible-inventory --list 2>/dev/null" % ANSIBLE_DIR, user, pw)
    inv = parse_inventory(inv_explicit)
    inv_def = parse_inventory(inv_default)
    hv = hostvars(inv)

    struct_out, _, _ = ssh_exec(
        port,
        "echo '===DIR==='; ls -la %s/ 2>/dev/null; echo '===GV==='; ls -la %s/ 2>/dev/null; "
        "echo '===CFG==='; cat %s/ansible.cfg 2>/dev/null; cat /etc/ansible.cfg 2>/dev/null; "
        "echo '===INV==='; test -f %s && echo INVENTORY_FILE_OK" % (ANSIBLE_DIR, GROUP_VARS_DIR, ANSIBLE_DIR, INVENTORY_PATH),
        user, pw)
    safe_log_output(log_lines, "[%s] структура /etc/ansible" % CONTROL_NODE, struct_out)
    struct_out = struct_out or ""

    gv_out, _, _ = ssh_exec(port, "for f in %s/*; do echo \"### $f\"; cat \"$f\"; done 2>/dev/null" % GROUP_VARS_DIR, user, pw)
    safe_log_output(log_lines, "[%s] group_vars содержимое" % CONTROL_NODE, gv_out)
    gv_out = gv_out or ""

    # ==== Пункт 1: Ansible установлен ====
    check("Пункт 1: Ansible установлен на %s" % CONTROL_NODE,
          bool(ver_out and re.search(r'ansible\b', ver_out, re.IGNORECASE) and re.search(r'\d+\.\d+', ver_out)))

    # ==== Пункт 2: Инвентарь по пути /etc/ansible/inventory и используется по умолчанию ====
    file_ok = "INVENTORY_FILE_OK" in struct_out
    default_ok = bool(inv_def) and any(g.lower() in [k.lower() for k in inv_def] for g in GROUPS)
    explicit_ok = bool(inv) and any(g.lower() in [k.lower() for k in inv] for g in GROUPS)
    check("Пункт 2: Инвентарь %s используется по умолчанию" % INVENTORY_PATH,
          file_ok and (default_ok or explicit_ok),
          "по умолчанию" if default_ok else ("только с -i" if explicit_ok else "не разобран"))

    # ==== Пункт 3: Группа networking с маршрутизаторами ====
    net_hosts = hosts_of_group(inv, "networking")
    net_ok = bool(net_hosts) and all(host_matches(net_hosts, r) for r in GROUPS["networking"])
    check("Пункт 3: Группа networking содержит маршрутизаторы", net_ok,
          "хосты: %s" % (", ".join(net_hosts) if net_hosts else "—"))

    # ==== Пункт 4: Группа servers с серверами ====
    srv_hosts = hosts_of_group(inv, "servers")
    srv_ok = bool(srv_hosts) and all(host_matches(srv_hosts, s) for s in GROUPS["servers"])
    check("Пункт 4: Группа servers содержит серверы", srv_ok,
          "хосты: %s" % (", ".join(srv_hosts) if srv_hosts else "—"))

    # ==== Пункт 5: Группа clients с клиентами ====
    cli_hosts = hosts_of_group(inv, "clients")
    cli_ok = bool(cli_hosts) and all(host_matches(cli_hosts, c) for c in GROUPS["clients"])
    check("Пункт 5: Группа clients содержит клиентов", cli_ok,
          "хосты: %s" % (", ".join(cli_hosts) if cli_hosts else "—"))

    # ==== Пункт 6: Папка group_vars с файлами для всех групп ====
    gv_dir_ok = "===GV===" in struct_out and bool(re.search(r'group_vars', struct_out)) is not None
    gv_listing = struct_out.split("===GV===")[-1].split("===CFG===")[0] if "===GV===" in struct_out else ""
    gv_files_present = sum(1 for g in GROUPS if re.search(r'\b%s\b' % g, gv_listing, re.IGNORECASE))
    check("Пункт 6: group_vars содержит файлы для групп", gv_files_present >= len(GROUPS),
          "найдено файлов групп: %d/%d" % (gv_files_present, len(GROUPS)))

    # ==== Пункт 7: ansible_user задан корректно для каждой группы ====
    user_ok = True
    user_detail = []
    for g in GROUPS:
        expected = EXPECTED_USER[g]
        found = None
        for h in GROUPS[g]:
            for hk in hv:
                if hk.lower().startswith(h.lower()):
                    found = hv[hk].get("ansible_user")
                    break
            if found:
                break
        if found != expected:
            user_ok = False
        user_detail.append("%s=%s(ожид. %s)" % (g, found, expected))
    check("Пункт 7: ansible_user задан для всех групп", user_ok, "; ".join(user_detail))

    # ==== Пункт 8: ansible_ssh_private_key_file задан, ключ существует с правами 600 ====
    key_paths = set()
    for hk in hv:
        kp = hv[hk].get("ansible_ssh_private_key_file")
        if kp:
            key_paths.add(kp)
    key_var_ok = bool(key_paths)
    perms_ok = False
    if key_paths:
        checks = " ; ".join("stat -c '%%a %%n' %s 2>/dev/null" % kp for kp in key_paths)
        perm_out, _, _ = ssh_exec(port, checks, user, pw)
        safe_log_output(log_lines, "[%s] права на ключи" % CONTROL_NODE, perm_out)
        if perm_out:
            modes = re.findall(r'^\s*(\d{3})\s', perm_out, re.MULTILINE)
            perms_ok = bool(modes) and all(m in ("600", "400") for m in modes)
    check("Пункт 8: ansible_ssh_private_key_file задан, ключ с правами 600",
          key_var_ok and perms_ok,
          "ключи: %s" % (", ".join(key_paths) if key_paths else "—"))

    # ==== Пункт 9: ansible all -m ping → pong (под пользователем user) ====
    ping_out, ping_err, _ = ssh_exec(
        port, "runuser -l %s -c 'cd %s && ansible all -m ping' 2>&1" % (RUN_AS_USER, ANSIBLE_DIR),
        user, pw, timeout=90)
    safe_log_output(log_lines, "[%s] ansible all -m ping (от %s)" % (CONTROL_NODE, RUN_AS_USER), ping_out, ping_err)
    ping_out = ping_out or ""
    success_cnt = len(re.findall(r'SUCCESS', ping_out))
    pong_cnt = len(re.findall(r'"ping"\s*:\s*"pong"', ping_out))
    unreachable = len(re.findall(r'UNREACHABLE', ping_out))
    check("Пункт 9: ansible all -m ping возвращает pong",
          (success_cnt >= 1 and pong_cnt >= 1 and unreachable == 0),
          "SUCCESS=%d pong=%d UNREACHABLE=%d" % (success_cnt, pong_cnt, unreachable))

    # ==== Пункт 10: Корректный интерпретатор Python ====
    interp_var = bool(re.search(r'ansible_python_interpreter', gv_out)) or \
                 any("ansible_python_interpreter" in (hv[hk] or {}) for hk in hv)
    no_interp_err = not re.search(r'(was not able to find|No module named|/usr/bin/python: not found|interpreter.*not)', ping_out, re.IGNORECASE)
    check("Пункт 10: Используется корректный интерпретатор Python",
          (interp_var or (pong_cnt >= 1 and no_interp_err)),
          "ansible_python_interpreter задан" if interp_var else "ошибок интерпретатора нет")

    # ==== Пункт 11: Структура каталогов (ansible.cfg, inventory, group_vars) ====
    cfg_present = "===CFG===" in struct_out and bool(struct_out.split("===CFG===")[-1].split("===INV===")[0].strip())
    struct_ok = cfg_present and file_ok and gv_files_present >= 1
    check("Пункт 11: Структура каталогов корректна (ansible.cfg/inventory/group_vars)", struct_ok)

    # ==== Пункт 12: Плейбук /etc/ansible/gathering.yml ====
    pb_out, _, _ = ssh_exec(port, "test -f %s && echo OK; head -40 %s 2>/dev/null" % (PLAYBOOK_PATH, PLAYBOOK_PATH), user, pw)
    safe_log_output(log_lines, "[%s] %s" % (CONTROL_NODE, PLAYBOOK_PATH), pb_out)
    pb_out = pb_out or ""
    check("Пункт 12: Плейбук %s присутствует" % PLAYBOOK_PATH,
          "OK" in pb_out and re.search(r'(hosts:|tasks:|setup|gather)', pb_out, re.IGNORECASE) is not None)

    # ==== Пункт 13: Отчёт /etc/ansible/output.yaml в формате FQDN – АДРЕС ====
    out_out, _, _ = ssh_exec(port, "cat %s 2>/dev/null" % OUTPUT_PATH, user, pw)
    safe_log_output(log_lines, "[%s] %s" % (CONTROL_NODE, OUTPUT_PATH), out_out)
    out_out = out_out or ""
    # формат: ПОЛНОЕ_ДОМЕННОЕ_ИМЯ – АДРЕС  (тире/дефис между именем и IP)
    fmt_ok = bool(re.search(r'[\w.-]+\.au-team\.irpo\s*[–\-]\s*\d{1,3}(?:\.\d{1,3}){3}', out_out)) or \
             bool(re.search(r'[\w.-]+\s*[–\-]\s*\d{1,3}(?:\.\d{1,3}){3}', out_out))
    check("Пункт 13: Отчёт %s в формате FQDN – АДРЕС" % OUTPUT_PATH,
          bool(out_out.strip()) and fmt_ok)

    # --- ИТОГОВЫЙ ОТЧЁТ ---
    log_msg("\n📊 ИТОГОВЫЙ ОТЧЁТ:")
    log_msg("=" * 60)
    total_passed = 0
    for item, passed in results.items():
        log_msg("%s: %s" % (item, "✅ ПРОЙДЕН" if passed else "❌ НЕ ПРОЙДЕН"))
        if passed:
            total_passed += 1

    log_msg("\n📈 Выполнено %.2f из %d пунктов." % (POINTS, len(results)))
    return POINTS, log_lines


if __name__ == "__main__":
    pass
