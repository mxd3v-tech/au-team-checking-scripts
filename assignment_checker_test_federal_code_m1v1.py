import subprocess
import re
import sys
import ipaddress

try:
    import pexpect
    PEXPECT_AVAILABLE = True
except ImportError:
    PEXPECT_AVAILABLE = False
    print("⚠️  Модуль pexpect не установлен. Проверка маршрутизаторов будет пропущена.", file=sys.stderr)

# ========== УЧЁТНЫЕ ДАННЫЕ ==========
SRV_CREDENTIALS = {
    "HQ-SRV": ("root", "toor"),
    "BR-SRV": ("root", "toor"),
    "HQ-CLI": ("root", "toor"),
    "ISP": ("root", "toor"),
}

RTR_CREDENTIALS = {
    "HQ-RTR": ("admin", "admin"),
    "BR-RTR": ("admin", "admin"),
}

def get_srv_creds(name):
    return SRV_CREDENTIALS.get(name, ("root", "toor"))

def get_rtr_creds(name):
    return RTR_CREDENTIALS.get(name, ("admin", "admin"))

# ========== ФУНКЦИИ ДЛЯ СЕРВЕРОВ (Linux) ==========
def ssh_exec(ssh_port, command, username='root', password='toor', timeout=30):
    if not ssh_port or ssh_port == "N/A":
        return None, "❌ Порт SSH недоступен: %s" % ssh_port, command
    try:
        ssh_cmd = [
            "sshpass", "-p", password,
            "ssh", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=5",
            "-tt", "-p", str(ssh_port),
            "%s@localhost" % username,
            command
        ]
        process = subprocess.Popen(
            ssh_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            return None, "❌ Таймаут (%d сек)" % timeout, command
        return stdout, stderr, command
    except Exception as e:
        return None, "❌ Ошибка: %s" % str(e), command

# ========== ФУНКЦИИ ДЛЯ МАРШРУТИЗАТОРОВ (EcoRouterOS) ==========
def rtr_exec(port, username, password, command, timeout=30):
    if not PEXPECT_AVAILABLE:
        return None, "pexpect не установлен", command
    try:
        child = pexpect.spawn(
            "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p %s %s@localhost" % (port, username),
            timeout=timeout, encoding='utf-8', codec_errors='ignore'
        )
        child.expect(r"[Pp]assword:", timeout=60)
        child.sendline(password)
        child.expect(r'\S+>', timeout=60)
        child.sendline("terminal length 0")
        child.expect(r'\S+>', timeout=30)
        child.sendline(command)
        child.expect(r'\S+>', timeout=timeout)
        output = child.before.strip()
        child.sendline("exit")
        child.close()
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        return ansi_escape.sub('', output), "", command
    except pexpect.TIMEOUT:
        return None, "Таймаут выполнения команды", command
    except Exception as e:
        return None, "Ошибка pexpect: %s" % str(e), command

def rtr_exec_with_enable(port, username, password, command, timeout=30):
    if not PEXPECT_AVAILABLE:
        return None, "pexpect не установлен", command
    try:
        child = pexpect.spawn(
            "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p %s %s@localhost" % (port, username),
            timeout=timeout, encoding='utf-8', codec_errors='ignore'
        )
        child.expect(r"[Pp]assword:", timeout=60)
        child.sendline(password)
        child.expect(r'\S+>', timeout=60)
        child.sendline("en")
        child.expect(r'\S+#', timeout=60)
        child.sendline("terminal length 0")
        child.expect(r'\S+#', timeout=30)
        child.sendline(command)
        child.expect(r'\S+#', timeout=timeout)
        full_output = child.before.strip()
        child.sendline("exit")
        child.sendline("exit")
        child.close()
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        return ansi_escape.sub('', full_output), "", command
    except pexpect.TIMEOUT:
        return None, "Таймаут выполнения команды", command
    except Exception as e:
        return None, "Ошибка pexpect: %s" % str(e), command

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def reverse_ip(ip):
    return '.'.join(reversed(ip.split('.')))

def safe_log_output(log_lines, prefix, output, error=""):
    full_output = (output or "") + ("\n" + error if error else "")
    log_lines.append("%s:\n%s\n" % (prefix, full_output))

def is_rtr_linux(vm_ports, rtr_name):
    """Определяет, является ли маршрутизатор Linux-based (не EcoRouter)."""
    out, err, _ = ssh_exec(vm_ports.get(rtr_name, ""), "uname -s 2>/dev/null", "root", "toor", timeout=10)
    return out is not None and "Linux" in (out or "")

# ========== ГЛАВНАЯ ФУНКЦИЯ ==========
def run_full_assignment_check(vm_ports):
    POINTS = 0.0
    MAX_POINTS = 13.0
    log_lines = []
    results = {}

    def log_msg(msg):
        log_lines.append(msg)
        print(msg)

    log_msg("\n🔍 Доступные SSH-порты:")
    for device, port in sorted(vm_ports.items()):
        log_msg("  %s: %s" % (device, port))
    log_msg("")

    log_msg("🔍 Начало комплексной проверки Модуля 1")

    DEVICE_NAMES = {
        "HQ-SRV": "hq-srv.au-team.irpo",
        "BR-SRV": "br-srv.au-team.irpo",
        "HQ-CLI": "hq-cli.au-team.irpo",
        "HQ-RTR": "hq-rtr",
        "BR-RTR": "br-rtr",
        "ISP": "isp.au-team.irpo",
    }

    FQDN_TABLE = {
        "HQ-RTR": "hq-rtr.au-team.irpo",
        "BR-RTR": "br-rtr.au-team.irpo",
        "HQ-SRV": "hq-srv.au-team.irpo",
        "HQ-CLI": "hq-cli.au-team.irpo",
        "BR-SRV": "br-srv.au-team.irpo",
        "ISP-HQ": "docker.au-team.irpo",
        "ISP-BR": "web.au-team.irpo",
    }

    PTR_DEVICES = {
        "hq-rtr.au-team.irpo",
        "hq-srv.au-team.irpo",
        "hq-cli.au-team.irpo",
    }

    # Определяем тип маршрутизаторов (EcoRouter или Linux)
    rtr_linux = {}
    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr in vm_ports:
            rtr_linux[rtr] = is_rtr_linux(vm_ports, rtr)
            log_msg("ℹ️ %s: %s" % (rtr, "Linux" if rtr_linux[rtr] else "EcoRouter"))

    # --- Пункт 1: FQDN ---
    log_msg("\n📌 Пункт 1: Имена устройств (FQDN)")
    names_correct = 0
    names_total = 0
    devices_to_check = ["HQ-SRV", "BR-SRV", "HQ-CLI", "HQ-RTR", "BR-RTR"]

    for dev in devices_to_check:
        if dev not in vm_ports:
            log_msg("⚠️ %s: устройство не найдено" % dev)
            continue

        names_total += 1
        expected_fqdn = FQDN_TABLE.get(dev, "")

        if "RTR" in dev and not rtr_linux.get(dev, False):
            out, err, cmd = rtr_exec(vm_ports[dev], *get_rtr_creds(dev), "show hostname")
            log_msg("[%s] Выполняется команда: show hostname" % dev)
            full_out = (out or "") + ("\n" + err if err else "")
            log_msg("[Вывод]\n%s\n" % full_out)
            actual = ""
            if out is not None:
                lines = [line.strip() for line in out.splitlines() if line.strip()]
                if len(lines) >= 2:
                    actual = lines[1].lower()
                elif len(lines) == 1:
                    actual = lines[0].lower()
            if actual == dev.lower() or actual == expected_fqdn.lower():
                log_msg("✅ %s: имя хоста корректно (%s)" % (dev, actual))
                names_correct += 1
            else:
                log_msg("❌ %s: имя хоста некорректно (%s)" % (dev, actual))
        else:
            out, err, cmd = ssh_exec(vm_ports[dev], "hostname -f", *get_srv_creds(dev))
            log_msg("[%s] Выполняется команда: hostname -f" % dev)
            full_out = (out or "") + ("\n" + err if err else "")
            log_msg("[Вывод]\n%s\n" % full_out)
            actual = out.strip().lower() if out else ""
            if actual == expected_fqdn:
                log_msg("✅ %s: FQDN корректен" % dev)
                names_correct += 1
            else:
                log_msg("❌ %s: FQDN некорректен (%s)" % (dev, actual))

    names_ok = names_correct >= 1
    log_msg("ℹ️ Имена устройств: %d из %d корректны" % (names_correct, names_total))
    if names_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 1 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 1 не пройден")
    results["Пункт 1: FQDN"] = names_ok

    # --- Пункт 2: IPv4-адресация ---
    log_msg("\n📌 Пункт 2: IPv4-адресация")
    ip_ok = True
    for dev in ["HQ-SRV", "BR-SRV", "HQ-CLI"]:
        if dev not in vm_ports:
            continue
        out, err, cmd = ssh_exec(vm_ports[dev], "ip -br addr show", *get_srv_creds(dev))
        log_msg("[%s] Выполняется команда: ip -br addr show" % dev)
        full_out = (out or "") + ("\n" + err if err else "")
        log_msg("[Вывод]\n%s\n" % full_out)
        if not out:
            ip_ok = False
            continue
        valid_found = False
        for line in out.strip().split('\n'):
            parts = line.split()
            if len(parts) >= 3 and '/' in parts[2]:
                if parts[0] == 'lo':
                    continue
                ip_str, mask = parts[2].split('/')
                if ip_str.startswith('169.'):
                    continue
                try:
                    ip = ipaddress.IPv4Address(ip_str)
                    if ip.is_private and not ip.is_loopback:
                        log_msg("  → %s: ✅ %s/%s — приватный" % (parts[0], ip_str, mask))
                        valid_found = True
                    else:
                        log_msg("  → %s: ❌ %s/%s — не приватный" % (parts[0], ip_str, mask))
                except ipaddress.AddressValueError:
                    pass
        if not valid_found:
            ip_ok = False
    if ip_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 2 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 2 не пройден")
    results["Пункт 2: IPv4-адресация"] = ip_ok

    # --- Пункт 3: Учётные записи ---
    log_msg("\n📌 Пункт 3: Учётные записи")
    accounts_ok = True
    for srv in ["HQ-SRV", "BR-SRV"]:
        if srv not in vm_ports:
            continue
        out, err, cmd = ssh_exec(vm_ports[srv], "id -u sshuser", "sshuser", "P@ssw0rd")
        log_msg("[%s] Выполняется команда: id -u sshuser" % srv)
        full_out = (out or "") + ("\n" + err if err else "")
        log_msg("[Вывод]\n%s\n" % full_out)
        uid_ok = out and out.strip() == "2026"

        out2, err2, cmd2 = ssh_exec(vm_ports[srv], "sudo -n id", "sshuser", "P@ssw0rd")
        log_msg("[%s] Выполняется команда: sudo -n id" % srv)
        full_out2 = (out2 or "") + ("\n" + err2 if err2 else "")
        log_msg("[Вывод]\n%s\n" % full_out2)
        sudo_ok = out2 and "uid=0(root)" in out2

        if not (uid_ok and sudo_ok):
            accounts_ok = False

    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports:
            continue
        if rtr_linux.get(rtr, False):
            out, err, cmd = ssh_exec(vm_ports[rtr], "id net_admin", "root", "toor")
            log_msg("[%s] Выполняется команда: id net_admin" % rtr)
            full_out = (out or "") + ("\n" + err if err else "")
            log_msg("[Вывод]\n%s\n" % full_out)
            if not (out and "net_admin" in out):
                accounts_ok = False
            out2, err2, _ = ssh_exec(vm_ports[rtr], "sudo -n id", "net_admin", "P@ssw0rd")
            log_msg("[%s] Выполняется команда: sudo -n id (net_admin)" % rtr)
            full_out2 = (out2 or "") + ("\n" + err2 if err2 else "")
            log_msg("[Вывод]\n%s\n" % full_out2)
            if not (out2 and "uid=0(root)" in out2):
                accounts_ok = False
        else:
            out, err, cmd = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show users localdb")
            log_msg("[%s] Выполняется команда: show users localdb" % rtr)
            full_out = (out or "") + ("\n" + err if err else "")
            log_msg("[Вывод]\n%s\n" % full_out)
            if not (out and "net_admin" in out):
                accounts_ok = False

    if accounts_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 3 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 3 не пройден")
    results["Пункт 3: Учётные записи"] = accounts_ok

    # --- Пункт 4: Коммутация VLAN (router-on-a-stick на HQ-RTR) ---
    log_msg("\n📌 Пункт 4: Коммутация VLAN")
    vlan_ok = True
    if "HQ-RTR" not in vm_ports:
        vlan_ok = False
    else:
        if rtr_linux.get("HQ-RTR", False):
            out, err, cmd = ssh_exec(vm_ports["HQ-RTR"], "ip -br link show type vlan", "root", "toor")
            log_msg("[HQ-RTR] Выполняется команда: ip -br link show type vlan")
            full_out = (out or "") + ("\n" + err if err else "")
            log_msg("[Вывод]\n%s\n" % full_out)
            if out:
                has_vlan100 = bool(re.search(r'\.100\b', out))
                has_vlan200 = bool(re.search(r'\.200\b', out))
                has_vlan999 = bool(re.search(r'\.999\b', out))
                if has_vlan100:
                    log_msg("✅ VLAN 100 (HQ-SRV) найден")
                else:
                    log_msg("❌ VLAN 100 (HQ-SRV) не найден")
                if has_vlan200:
                    log_msg("✅ VLAN 200 (HQ-CLI) найден")
                else:
                    log_msg("❌ VLAN 200 (HQ-CLI) не найден")
                if has_vlan999:
                    log_msg("✅ VLAN 999 (mgmt) найден")
                else:
                    log_msg("❌ VLAN 999 (mgmt) не найден")
                if not (has_vlan100 and has_vlan200 and has_vlan999):
                    vlan_ok = False
            else:
                vlan_ok = False
        else:
            out, err, cmd = rtr_exec(vm_ports["HQ-RTR"], *get_rtr_creds("HQ-RTR"), "show ip interface brief")
            log_msg("[HQ-RTR] Выполняется команда: show ip interface brief")
            full_out = (out or "") + ("\n" + err if err else "")
            log_msg("[Вывод]\n%s\n" % full_out)
            if out:
                has_100 = bool(re.search(r'ge\d+/\d+/\d+\.100', out))
                has_200 = bool(re.search(r'ge\d+/\d+/\d+\.200', out))
                has_999 = bool(re.search(r'ge\d+/\d+/\d+\.999', out))
                if has_100:
                    log_msg("✅ Субинтерфейс VLAN 100 найден")
                else:
                    log_msg("❌ Субинтерфейс VLAN 100 не найден")
                if has_200:
                    log_msg("✅ Субинтерфейс VLAN 200 найден")
                else:
                    log_msg("❌ Субинтерфейс VLAN 200 не найден")
                if has_999:
                    log_msg("✅ Субинтерфейс VLAN 999 найден")
                else:
                    log_msg("❌ Субинтерфейс VLAN 999 не найден")
                if not (has_100 and has_200 and has_999):
                    vlan_ok = False
            else:
                vlan_ok = False

    if vlan_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 4 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 4 не пройден")
    results["Пункт 4: Коммутация VLAN"] = vlan_ok

    # --- Пункт 5: Безопасный SSH ---
    log_msg("\n📌 Пункт 5: Безопасный SSH")
    ssh_ok = True
    for srv in ["HQ-SRV", "BR-SRV"]:
        if srv not in vm_ports:
            continue
        config_content, err, cmd = ssh_exec(vm_ports[srv], "sshd -T 2>/dev/null", *get_srv_creds(srv))
        log_msg("[%s] Выполняется команда: sshd -T" % srv)
        full_out = (config_content or "") + ("\n" + err if err else "")
        log_msg("[Вывод]\n%s\n" % full_out)
        if config_content is None:
            ssh_ok = False
            continue

        # Порт 2026
        port_ok = any(line.strip().lower().startswith('port') and '2026' in line
                       for line in config_content.split('\n'))
        if not port_ok:
            log_msg("❌ %s: SSH порт 2026 не настроен" % srv)
            ssh_ok = False
        else:
            log_msg("✅ %s: SSH порт 2026" % srv)

        # AllowUsers sshuser
        allow_ok = any('allowusers' in line.strip().lower() and 'sshuser' in line.lower()
                        for line in config_content.split('\n'))
        if not allow_ok:
            log_msg("❌ %s: AllowUsers sshuser не настроено" % srv)
            ssh_ok = False
        else:
            log_msg("✅ %s: AllowUsers sshuser" % srv)

        # MaxAuthTries 2
        max_auth = any(line.strip().lower().startswith('maxauthtries') and '2' in line
                        for line in config_content.split('\n'))
        if not max_auth:
            log_msg("❌ %s: MaxAuthTries 2 не настроено" % srv)
            ssh_ok = False
        else:
            log_msg("✅ %s: MaxAuthTries 2" % srv)

        # Баннер
        banner_ok = False
        for line in config_content.split('\n'):
            stripped = line.strip().lower()
            if stripped.startswith('banner '):
                banner_path = line.strip().split(None, 1)[1]
                if banner_path and banner_path != "none":
                    banner_out, _, _ = ssh_exec(vm_ports[srv],
                        "test -f %s && cat %s" % (banner_path, banner_path), *get_srv_creds(srv))
                    if banner_out and "authorized access only" in banner_out.lower():
                        banner_ok = True
                        break
        if not banner_ok:
            log_msg("❌ %s: баннер 'Authorized access only' не настроен" % srv)
            ssh_ok = False
        else:
            log_msg("✅ %s: баннер настроен" % srv)

    if ssh_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 5 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 5 не пройден")
    results["Пункт 5: Безопасный SSH"] = ssh_ok

    # --- Пункт 6: IP-туннель (GRE или IP-in-IP) ---
    log_msg("\n📌 Пункт 6: IP-туннель")
    tunnel_ok = True
    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports:
            continue
        if rtr_linux.get(rtr, False):
            out, err, cmd = ssh_exec(vm_ports[rtr], "ip tunnel show", "root", "toor")
            log_msg("[%s] Выполняется команда: ip tunnel show" % rtr)
            full_out = (out or "") + ("\n" + err if err else "")
            log_msg("[Вывод]\n%s\n" % full_out)
            if not (out and ("gre" in out.lower() or "ipip" in out.lower())):
                log_msg("❌ %s: туннель не найден" % rtr)
                tunnel_ok = False
            else:
                log_msg("✅ %s: туннель найден" % rtr)
        else:
            out1, err1, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show interface tunnel.0")
            log_msg("[%s] Выполняется команда: show interface tunnel.0" % rtr)
            full_out1 = (out1 or "") + ("\n" + err1 if err1 else "")
            log_msg("[Вывод]\n%s\n" % full_out1)

            out2, err2, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show running-config | include gre")
            log_msg("[%s] Выполняется команда: show running-config | include gre" % rtr)
            full_out2 = (out2 or "") + ("\n" + err2 if err2 else "")
            log_msg("[Вывод]\n%s\n" % full_out2)

            cond1 = out1 is not None and "is up" in out1
            cond2 = out2 is not None and "gre" in out2
            if not (cond1 and cond2):
                log_msg("❌ %s: туннель не настроен или не активен" % rtr)
                tunnel_ok = False
            else:
                log_msg("✅ %s: туннель активен" % rtr)

    if tunnel_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 6 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 6 не пройден")
    results["Пункт 6: IP-туннель"] = tunnel_ok

    # --- Пункт 7: Динамическая маршрутизация (OSPF) ---
    log_msg("\n📌 Пункт 7: Динамическая маршрутизация")
    ospf_ok = True
    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports:
            continue
        if rtr_linux.get(rtr, False):
            out, err, _ = ssh_exec(vm_ports[rtr],
                "vtysh -c 'show ip ospf neighbor' 2>/dev/null", "root", "toor")
            log_msg("[%s] Выполняется команда: vtysh show ip ospf neighbor" % rtr)
            full_out = (out or "") + ("\n" + err if err else "")
            log_msg("[Вывод]\n%s\n" % full_out)
            if not (out and "Full" in out):
                log_msg("❌ %s: OSPF neighbor не найден" % rtr)
                ospf_ok = False
            else:
                log_msg("✅ %s: OSPF neighbor активен" % rtr)
            # Проверка аутентификации
            auth_out, _, _ = ssh_exec(vm_ports[rtr],
                "vtysh -c 'show running-config' 2>/dev/null | grep -i 'message-digest\\|md5'",
                "root", "toor")
            log_msg("[%s] Проверка аутентификации OSPF" % rtr)
            safe_log_output(log_lines, "[%s] Вывод" % rtr, auth_out, "")
            if not (auth_out and ("message-digest" in auth_out.lower() or "md5" in auth_out.lower())):
                log_msg("❌ %s: аутентификация OSPF не настроена" % rtr)
                ospf_ok = False
        else:
            out1, err1, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show ip ospf neighbor")
            log_msg("[%s] Выполняется команда: show ip ospf neighbor" % rtr)
            full_out1 = (out1 or "") + ("\n" + err1 if err1 else "")
            log_msg("[Вывод]\n%s\n" % full_out1)

            out2, err2, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show running-config | include ospf")
            log_msg("[%s] Выполняется команда: show running-config | include ospf" % rtr)
            full_out2 = (out2 or "") + ("\n" + err2 if err2 else "")
            log_msg("[Вывод]\n%s\n" % full_out2)

            valid_neighbor = False
            if out1:
                for line in out1.split('\n'):
                    if 'tunnel' in line.lower() and 'Full' in line:
                        valid_neighbor = True
                        break

            auth_ok = out2 is not None and "authentication message-digest" in (out2 or "") and "md5" in (out2 or "")

            if not (valid_neighbor and auth_ok):
                log_msg("❌ %s: OSPF некорректен" % rtr)
                ospf_ok = False
            else:
                log_msg("✅ %s: OSPF настроен корректно" % rtr)

    if ospf_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 7 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 7 не пройден")
    results["Пункт 7: Динамическая маршрутизация"] = ospf_ok

    # --- Пункт 8: NAT ---
    log_msg("\n📌 Пункт 8: NAT")
    nat_ok = True
    for dev in ["HQ-SRV", "BR-SRV", "HQ-CLI"]:
        if dev not in vm_ports:
            continue
        out, err, cmd = ssh_exec(vm_ports[dev], "ping -c 2 -W 5 77.88.8.7", *get_srv_creds(dev))
        log_msg("[%s] Выполняется команда: ping -c 2 77.88.8.7" % dev)
        full_out = (out or "") + ("\n" + err if err else "")
        log_msg("[Вывод]\n%s\n" % full_out)
        if not (out and "2 packets transmitted, 2 received" in out):
            log_msg("❌ %s: нет доступа к Интернет" % dev)
            nat_ok = False
        else:
            log_msg("✅ %s: доступ к Интернет есть" % dev)

    if nat_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 8 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 8 не пройден")
    results["Пункт 8: NAT"] = nat_ok

    # --- Пункт 9: DHCP ---
    log_msg("\n📌 Пункт 9: DHCP")
    dhcp_ok = True
    if "HQ-CLI" not in vm_ports or "HQ-RTR" not in vm_ports:
        log_msg("⚠️ HQ-CLI или HQ-RTR не найден")
        dhcp_ok = False
    else:
        if rtr_linux.get("HQ-RTR", False):
            dhcp_status, _, _ = ssh_exec(vm_ports["HQ-RTR"],
                "systemctl is-active dhcpd 2>/dev/null || systemctl is-active isc-dhcp-server 2>/dev/null || systemctl is-active dnsmasq 2>/dev/null",
                "root", "toor")
            log_msg("[HQ-RTR] Проверка DHCP-сервера")
            safe_log_output(log_lines, "[HQ-RTR] Вывод", dhcp_status, "")
            if not (dhcp_status and "active" in dhcp_status):
                log_msg("❌ DHCP-сервер не активен на HQ-RTR")
                dhcp_ok = False
        else:
            out, err, cmd = rtr_exec(vm_ports["HQ-RTR"], *get_rtr_creds("HQ-RTR"),
                "show running-config | include dhcp-server")
            log_msg("[HQ-RTR] Выполняется команда: show running-config | include dhcp-server")
            full_out = (out or "") + ("\n" + err if err else "")
            log_msg("[Вывод]\n%s\n" % full_out)
            if not (out and "dhcp-server" in out):
                log_msg("❌ DHCP-сервер не настроен на HQ-RTR")
                dhcp_ok = False

        # Проверяем что HQ-CLI получил IP по DHCP
        if dhcp_ok:
            ip_out, _, _ = ssh_exec(vm_ports["HQ-CLI"], "ip addr show", *get_srv_creds("HQ-CLI"))
            log_msg("[HQ-CLI] Выполняется команда: ip addr show")
            full_ip = (ip_out or "")
            log_msg("[Вывод]\n%s\n" % full_ip)
            if ip_out:
                match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+).*dynamic', ip_out)
                if match:
                    ip_addr = match.group(1)
                    log_msg("✅ HQ-CLI: динамический IP %s" % ip_addr)
                else:
                    log_msg("❌ HQ-CLI: нет динамического IP")
                    dhcp_ok = False
            else:
                dhcp_ok = False

    if dhcp_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 9 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 9 не пройден")
    results["Пункт 9: DHCP"] = dhcp_ok

    # --- Пункт 10: DNS ---
    log_msg("\n📌 Пункт 10: DNS")
    dns_ok = True
    client_dev = "HQ-CLI" if "HQ-CLI" in vm_ports else None
    if not client_dev:
        log_msg("⚠️ Клиент для DNS не найден")
        dns_ok = False
    else:
        for key, fqdn in FQDN_TABLE.items():
            log_msg("\n→ Проверка записи: %s" % fqdn)

            out, err, cmd = ssh_exec(vm_ports[client_dev], "nslookup %s" % fqdn, *get_srv_creds(client_dev))
            log_msg("[%s] Выполняется команда: nslookup %s" % (client_dev, fqdn))
            full_out = (out or "") + ("\n" + err if err else "")
            log_msg("[Вывод]\n%s\n" % full_out)

            if not out or "Address:" not in out:
                log_msg("❌ Прямой запрос для %s не удался" % fqdn)
                dns_ok = False
                continue

            ip_match = re.search(
                r'Name:\s*%s\s*\n\s*Address:\s*(\d+\.\d+\.\d+\.\d+)' % re.escape(fqdn),
                out, re.IGNORECASE)
            if not ip_match:
                log_msg("❌ Не найден IP для %s" % fqdn)
                dns_ok = False
                continue

            ip_addr = ip_match.group(1)
            if ip_addr.startswith("127."):
                log_msg("⚠️ IP %s — loopback, пропускаем" % ip_addr)
                continue

            log_msg("✅ Получен IP: %s" % ip_addr)

            # Обратный запрос (PTR) — только если требуется
            if fqdn in PTR_DEVICES:
                rev_out, rev_err, _ = ssh_exec(vm_ports[client_dev],
                    "nslookup %s" % ip_addr, *get_srv_creds(client_dev))
                log_msg("[%s] Выполняется команда: nslookup %s" % (client_dev, ip_addr))
                full_rev = (rev_out or "") + ("\n" + rev_err if rev_err else "")
                log_msg("[Вывод]\n%s\n" % full_rev)

                if not rev_out:
                    log_msg("❌ Обратный запрос для %s не удался" % ip_addr)
                    dns_ok = False
                    continue

                ptr_name = reverse_ip(ip_addr) + ".in-addr.arpa"
                reverse_pattern = r'%s\s+name\s*=\s*%s\.' % (
                    re.escape(ptr_name), re.escape(fqdn))
                if re.search(reverse_pattern, rev_out, re.IGNORECASE):
                    log_msg("✅ Обратное разрешение для %s работает" % fqdn)
                else:
                    log_msg("❌ Обратное разрешение для %s не работает" % fqdn)
                    dns_ok = False
            else:
                log_msg("ℹ️ Для %s PTR не требуется" % fqdn)

    if dns_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 10 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 10 не пройден")
    results["Пункт 10: DNS"] = dns_ok

    # --- Пункт 11: Часовой пояс ---
    log_msg("\n📌 Пункт 11: Часовой пояс")
    tz_ok = True
    for dev in ["HQ-SRV", "BR-SRV", "HQ-CLI", "ISP"]:
        if dev not in vm_ports:
            continue
        out, err, _ = ssh_exec(vm_ports[dev],
            "timedatectl show --property=Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null",
            *get_srv_creds(dev))
        log_msg("[%s] Проверка часового пояса" % dev)
        full_out = (out or "") + ("\n" + err if err else "")
        log_msg("[Вывод]\n%s\n" % full_out)
        if out and out.strip() and out.strip() != "UTC" and out.strip() != "Etc/UTC":
            log_msg("✅ %s: часовой пояс — %s" % (dev, out.strip()))
        else:
            log_msg("❌ %s: часовой пояс не настроен (UTC)" % dev)
            tz_ok = False

    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports:
            continue
        if rtr_linux.get(rtr, False):
            out, err, _ = ssh_exec(vm_ports[rtr],
                "timedatectl show --property=Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null",
                "root", "toor")
            log_msg("[%s] Проверка часового пояса" % rtr)
            full_out = (out or "") + ("\n" + err if err else "")
            log_msg("[Вывод]\n%s\n" % full_out)
            if out and out.strip() and out.strip() != "UTC" and out.strip() != "Etc/UTC":
                log_msg("✅ %s: часовой пояс — %s" % (rtr, out.strip()))
            else:
                log_msg("❌ %s: часовой пояс не настроен" % rtr)
                tz_ok = False
        else:
            out, err, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show clock")
            log_msg("[%s] Выполняется команда: show clock" % rtr)
            full_out = (out or "") + ("\n" + err if err else "")
            log_msg("[Вывод]\n%s\n" % full_out)
            if out:
                log_msg("✅ %s: часы — %s" % (rtr, out.strip()))
            else:
                log_msg("⚠️ %s: не удалось проверить часовой пояс" % rtr)

    if tz_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 11 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 11 не пройден")
    results["Пункт 11: Часовой пояс"] = tz_ok

    # --- Пункт 12: Пинги по FQDN ---
    log_msg("\n📌 Пункт 12: Пинги по FQDN")
    ping_ok = True
    if "HQ-CLI" in vm_ports:
        for target in ["br-srv.au-team.irpo", "hq-srv.au-team.irpo"]:
            out, err, cmd = ssh_exec(vm_ports["HQ-CLI"],
                "ping -c 2 -W 5 %s" % target, *get_srv_creds("HQ-CLI"))
            log_msg("[HQ-CLI] Выполняется команда: ping -c 2 %s" % target)
            full_out = (out or "") + ("\n" + err if err else "")
            log_msg("[Вывод]\n%s\n" % full_out)
            if not (out and "2 packets transmitted, 2 received" in out):
                log_msg("❌ Пинг до %s не прошёл" % target)
                ping_ok = False
            else:
                log_msg("✅ Пинг до %s успешен" % target)
    else:
        ping_ok = False

    if ping_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 12 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 12 не пройден")
    results["Пункт 12: Пинги по FQDN"] = ping_ok

    # --- Пункт 13: Проверка ISP ---
    log_msg("\n📌 Пункт 13: Проверка ISP")
    isp_ok = True
    if "ISP" not in vm_ports:
        log_msg("⚠️ ISP не найден")
        isp_ok = False
    else:
        out2, err2, _ = ssh_exec(vm_ports["ISP"], "ip -br addr show", *get_srv_creds("ISP"))
        log_msg("[ISP] Выполняется команда: ip -br addr show")
        full_out2 = (out2 or "") + ("\n" + err2 if err2 else "")
        log_msg("[Вывод]\n%s\n" % full_out2)

        interfaces = {}
        if out2:
            for line in out2.strip().split('\n'):
                parts = line.split()
                if len(parts) >= 3 and '/' in parts[2]:
                    interfaces[parts[0]] = parts[2]

        hq_iface_ok = False
        br_iface_ok = False
        for iface, ip_with_mask in interfaces.items():
            try:
                ip_obj = ipaddress.IPv4Interface(ip_with_mask)
                if ip_obj.network.overlaps(ipaddress.IPv4Network("172.16.1.0/28", strict=False)):
                    hq_iface_ok = True
                    log_msg("✅ %s: %s — сеть HQ (172.16.1.0/28)" % (iface, ip_with_mask))
                if ip_obj.network.overlaps(ipaddress.IPv4Network("172.16.2.0/28", strict=False)):
                    br_iface_ok = True
                    log_msg("✅ %s: %s — сеть BR (172.16.2.0/28)" % (iface, ip_with_mask))
            except Exception:
                pass

        if not hq_iface_ok:
            log_msg("❌ ISP: интерфейс в сети 172.16.1.0/28 не найден")
        if not br_iface_ok:
            log_msg("❌ ISP: интерфейс в сети 172.16.2.0/28 не найден")
        if not (hq_iface_ok and br_iface_ok):
            isp_ok = False

        # Проверка NAT
        nat_out, _, _ = ssh_exec(vm_ports["ISP"],
            "iptables -t nat -L POSTROUTING -n 2>/dev/null", *get_srv_creds("ISP"))
        log_msg("[ISP] Проверка NAT")
        safe_log_output(log_lines, "[ISP] Вывод", nat_out, "")
        if nat_out and ("MASQUERADE" in nat_out or "SNAT" in nat_out):
            log_msg("✅ ISP: NAT настроен")
        else:
            log_msg("❌ ISP: NAT не настроен")
            isp_ok = False

    if isp_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 13 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 13 не пройден")
    results["Пункт 13: ISP"] = isp_ok

    # --- ИТОГОВЫЙ ОТЧЁТ ---
    log_msg("\n📊 ИТОГОВЫЙ ОТЧЁТ:")
    log_msg("=" * 60)
    total_passed = 0
    for item, passed in results.items():
        status = "✅ ПРОЙДЕН" if passed else "❌ НЕ ПРОЙДЕН"
        log_msg("%s: %s" % (item, status))
        if passed:
            total_passed += 1

    log_msg("\n📈 Выполнено %d из %d пунктов." % (total_passed, len(results)))
    return total_passed, log_lines


if __name__ == "__main__":
    pass
