import subprocess
import re
import os
import ipaddress
import sys

try:
    import pexpect
    PEXPECT_AVAILABLE = True
except ImportError:
    PEXPECT_AVAILABLE = False
    print("⚠️  Модуль pexpect не установлен. Проверка маршрутизаторов будет пропущена.", file=sys.stderr)

ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def clean_ansi(text):
    return ANSI_ESCAPE.sub('', text) if text else text

def safe_log_output(log_lines, prefix, output, error=""):
    full_output = (output or "") + ("\n" + error if error else "")
    log_lines.append(f"{prefix}:\n{full_output}\n")

# ========== ФУНКЦИИ ДЛЯ СЕРВЕРОВ (Linux) ==========
def ssh_exec(ssh_port, command, username='root', password='toor', timeout=30):
    if not ssh_port or ssh_port == "N/A":
        return None, f"❌ Порт SSH недоступен: {ssh_port}", f"Выполняется команда: {command}"
    process = None
    try:
        ssh_cmd = [
            "sshpass", "-p", password,
            "ssh", "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-tt", "-p", ssh_port,
            f"{username}@localhost",
            command
        ]
        process = subprocess.Popen(
            ssh_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        stdout, stderr = process.communicate(timeout=timeout)
        return stdout, stderr, f"Выполняется команда: {command}"
    except subprocess.TimeoutExpired:
        # Без явного kill дочерний sshpass/ssh остаётся висеть зомби, держа
        # порт и соединение, и в долгих проверках это превращается в
        # лавину незакрытых процессов.
        if process is not None:
            try:
                process.kill()
                process.communicate()
            except Exception:
                pass
        return None, f"❌ Таймаут команды ({timeout}s): {command}", f"Выполняется команда: {command}"
    except Exception as e:
        if process is not None and process.poll() is None:
            try:
                process.kill()
                process.communicate()
            except Exception:
                pass
        return None, f"❌ Ошибка: {e}", f"Выполняется команда: {command}"

# ========== ФУНКЦИИ ДЛЯ МАРШРУТИЗАТОРОВ (EcoRouterOS) ==========
def rtr_exec_with_enable(port, username, password, command, timeout=90):
    """Выполняет команду в режиме enable (#)"""
    if not PEXPECT_AVAILABLE:
        return None, "pexpect не установлен", f"Выполняется команда: {command}"
    if not port or port == "N/A":
        # Без этой проверки pexpect.spawn вызовет ssh "-p N/A", и проверка
        # будет ждать таймаут (~90s) на каждом отсутствующем роутере.
        return None, f"❌ Порт SSH недоступен: {port}", f"Выполняется команда: {command}"
    try:
        child = pexpect.spawn(
            f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p {port} {username}@localhost",
            timeout=timeout,
            encoding='utf-8',
            codec_errors='ignore'
        )
        child.expect(r"[Pp]assword:", timeout=30)
        child.sendline(password)
        child.expect(r'\S+>', timeout=30)
        child.sendline("en")
        child.expect(r'\S+#', timeout=30)
        child.sendline("terminal length 0")
        child.expect(r'\S+#', timeout=10)
        child.sendline(command)
        child.expect(r'\S+#', timeout=60)
        output = child.before.strip()
        child.sendline("exit")
        child.sendline("exit")
        child.close()
        return clean_ansi(output), "", f"Выполняется команда: {command}"
    except Exception as e:
        return None, f"Ошибка pexpect: {e}", f"Выполняется команда: {command}"

def rtr_exec(port, username, password, command, timeout=90):
    """Выполняет команду в пользовательском режиме (>)"""
    if not PEXPECT_AVAILABLE:
        return None, "pexpect не установлен", f"Выполняется команда: {command}"
    if not port or port == "N/A":
        return None, f"❌ Порт SSH недоступен: {port}", f"Выполняется команда: {command}"
    try:
        child = pexpect.spawn(
            f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p {port} {username}@localhost",
            timeout=timeout,
            encoding='utf-8',
            codec_errors='ignore'
        )
        child.expect(r"[Pp]assword:", timeout=30)
        child.sendline(password)
        child.expect(r'\S+>', timeout=30)
        child.sendline("terminal length 0")
        child.expect(r'\S+>', timeout=10)
        child.sendline(command)
        child.expect(r'\S+>', timeout=60)
        output = child.before.strip()
        child.sendline("exit")
        child.close()
        return clean_ansi(output), "", f"Выполняется команда: {command}"
    except Exception as e:
        return None, f"Ошибка pexpect: {e}", f"Выполняется команда: {command}"

def rtr_get_hostname(port, username, password, timeout=60):
    if not PEXPECT_AVAILABLE:
        return None, "pexpect не установлен", ""
    if not port or port == "N/A":
        return None, f"❌ Порт SSH недоступен: {port}", ""
    try:
        child = pexpect.spawn(
            f"ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p {port} {username}@localhost",
            timeout=timeout,
            encoding='utf-8',
            codec_errors='ignore'
        )
        child.expect(r"[Pp]assword:", timeout=30)
        child.sendline(password)
        child.expect(r'\S+>', timeout=30)
        prompt = child.after.strip()
        hostname = prompt.rstrip('>').strip()
        child.sendline("exit")
        child.close()
        return clean_ansi(hostname), "", ""
    except Exception as e:
        return None, f"Ошибка получения hostname: {e}", ""

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def validate_ip_in_allowed_ranges(ip_str, prefixlen):
    try:
        ip = ipaddress.IPv4Address(ip_str)
    except ipaddress.AddressValueError:
        return False, "Некорректный IPv4-адрес"

    zones = [
        {"name": "HQ-SRV/VLAN100", "network": "10.10.100.0/24", "min_prefixlen": 27},
        {"name": "HQ-CLI/VLAN200", "network": "10.10.200.0/27", "min_prefixlen": 28},
        {"name": "Management (HQ)", "network": "10.10.30.0/28", "min_prefixlen": 29},
        {"name": "BR-SRV", "network": "10.20.20.0/26", "min_prefixlen": 28},
        {"name": "BR-CLI", "network": "10.20.30.0/26", "min_prefixlen": 29},
    ]

    for zone in zones:
        net = ipaddress.IPv4Network(zone["network"], strict=False)
        if ip in net:
            if prefixlen >= zone["min_prefixlen"]:
                return True, f"✅ В зоне '{zone['name']}', маска /{prefixlen} допустима"
            else:
                return False, f"❌ В зоне '{zone['name']}', но маска /{prefixlen} слишком широкая (требуется ≥/{zone['min_prefixlen']})"

    return False, "❌ IP не принадлежит ни одной разрешённой зоне"

FQDN_TABLE = {
    "HQ-SRV": "hq-srv.au-team.irpo",
    "BR-SRV": "br-srv.au-team.irpo",
    "HQ-CLI": "hq-cli.au-team.irpo",
    "BR-CLI": "br-cli.au-team.irpo",
    "HQ-RTR": "hq-rtr.au-team.irpo",
    "BR-RTR": "br-rtr.au-team.irpo",
    "ISP": "isp.au-team.irpo",  # ← добавлено
}

def reverse_ip(ip):
    """Преобразует IP в формат PTR: 10.10.100.2 → 2.100.10.10"""
    return '.'.join(reversed(ip.split('.')))

# ========== ГЛАВНАЯ ФУНКЦИЯ ==========
def run_full_assignment_check(vm_ports):
    log_lines = []
    results = {}

    log_lines.append("🔍 Начало комплексной проверки ISP Block 2\n")
    log_lines.append("="*60 + "\n")

    SRV_CREDENTIALS = {
        "HQ-SRV": {"username": "root", "password": "toor"},
        "BR-SRV": {"username": "root", "password": "toor"},
        "HQ-CLI": {"username": "root", "password": "toor"},
        "BR-CLI": {"username": "root", "password": "toor"},
        "ISP": {"username": "root", "password": "toor"},  # ← добавлено
    }
    RTR_CREDENTIALS = {
        "HQ-RTR": {"username": "admin", "password": "admin"},
        "BR-RTR": {"username": "admin", "password": "admin"},
    }

    def get_srv_creds(name):
        return SRV_CREDENTIALS.get(name, {"username": "root", "password": "toor"})

    def get_rtr_creds(name):
        return RTR_CREDENTIALS.get(name, {"username": "admin", "password": "admin"})

    # --- Пункт 1: FQDN ---
    log_lines.append("📌 Пункт 1: Имена устройств (FQDN)\n")
    names_ok = True
    for dev in FQDN_TABLE.keys():
        if dev not in vm_ports:
            log_lines.append(f"⚠️ {dev}: устройство не найдено\n")
            names_ok = False
            continue

        expected_fqdn = FQDN_TABLE[dev]
        if "RTR" in dev:
            hostname, _, _ = rtr_get_hostname(vm_ports[dev], **get_rtr_creds(dev))
            short_name = dev.lower()
            if hostname == short_name:
                log_lines.append(f"✅ {dev}: имя хоста = {hostname}\n")
            else:
                log_lines.append(f"❌ {dev}: ожидалось {short_name}, получено {hostname}\n")
                names_ok = False
        else:
            stdout, stderr, cmd_log = ssh_exec(vm_ports[dev], "hostname -f", **get_srv_creds(dev))
            log_lines.append(cmd_log + "\n")
            safe_log_output(log_lines, f"[{dev}] Вывод", stdout, stderr)
            actual = stdout.strip().lower() if stdout else ""
            if actual == expected_fqdn:
                log_lines.append(f"✅ {dev}: FQDN = {actual}\n")
            else:
                log_lines.append(f"❌ {dev}: ожидалось {expected_fqdn}, получено {actual}\n")
                names_ok = False
    results["Пункт 1: FQDN"] = names_ok

    # --- Пункт 2: IPv4-адресация ---
    log_lines.append("\n📌 Пункт 2: IPv4-адресация\n")
    ip_ok = True
    for dev in ["HQ-SRV", "BR-SRV", "HQ-CLI", "BR-CLI"]:
        if dev not in vm_ports:
            continue
        port = vm_ports[dev]
        user, pwd = get_srv_creds(dev)['username'], get_srv_creds(dev)['password']
        cmd = "ip -br addr show"
        stdout, stderr, cmd_log = ssh_exec(port, cmd, username=user, password=pwd)
        log_lines.append(cmd_log + "\n")
        safe_log_output(log_lines, f"[{dev}] Вывод", stdout, stderr)

        found_valid = False
        if stdout:
            for line in stdout.strip().split('\n'):
                parts = line.split()
                if len(parts) >= 3 and '/' in parts[2]:
                    iface = parts[0]
                    ip_with_mask = parts[2]
                    try:
                        ip_str, mask = ip_with_mask.split('/', 1)
                        ok, msg = validate_ip_in_allowed_ranges(ip_str, int(mask))
                        log_lines.append(f"  → {iface}: {ip_with_mask} — {msg}\n")
                        if ok:
                            found_valid = True
                    except:
                        pass
        if not found_valid:
            log_lines.append(f"❌ {dev}: не найдено корректных IP.\n")
            ip_ok = False
    results["Пункт 2: IPv4-адресация"] = ip_ok

    # --- Пункт 3: Учётные записи ---
    log_lines.append("\n📌 Пункт 3: Учётные записи\n")
    accounts_ok = True

    for srv in ["HQ-SRV", "BR-SRV"]:
        if srv not in vm_ports:
            continue
        port = vm_ports[srv]
        user, pwd = get_srv_creds(srv)['username'], get_srv_creds(srv)['password']

        for cmd in ["whoami", "id", "date"]:
            stdout, stderr, cmd_log = ssh_exec(port, cmd, username="sshuser", password="P@ssw0rd")
            log_lines.append(cmd_log + "\n")
            safe_log_output(log_lines, f"[{srv}] Вывод", stdout, stderr)

        cmd_sudo = "sudo -n id"
        sudo_stdout, sudo_stderr, cmd_log = ssh_exec(port, cmd_sudo, username="sshuser", password="P@ssw0rd")
        log_lines.append(cmd_log + "\n")
        safe_log_output(log_lines, f"[{srv}] Вывод", sudo_stdout, sudo_stderr)
        sudo_ok = sudo_stdout and "uid=0(root)" in sudo_stdout

        cmd_uid = "id -u sshuser"
        uid_out, _, cmd_log = ssh_exec(port, cmd_uid, username=user, password=pwd)
        log_lines.append(cmd_log + "\n")
        log_lines.append(f"[{srv}] Вывод:\n{uid_out or 'N/A'}\n")
        uid_ok = uid_out and uid_out.strip() == "2026"

        if uid_ok and sudo_ok:
            log_lines.append(f"✅ {srv}: sshuser корректен\n")
        else:
            log_lines.append(f"❌ {srv}: sshuser не прошёл проверку\n")
            accounts_ok = False

    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports:
            continue
        port = vm_ports[rtr]
        user, pwd = get_rtr_creds(rtr)['username'], get_rtr_creds(rtr)['password']
        cmd_localdb = "show users localdb"
        out, err, cmd_log = rtr_exec(port, user, pwd, cmd_localdb)
        log_lines.append(cmd_log + "\n")
        safe_log_output(log_lines, f"[{rtr}] Вывод", out, err)
        if out and "net_admin" in out:
            log_lines.append(f"✅ {rtr}: пользователь net_admin найден в localdb\n")
        else:
            log_lines.append(f"❌ {rtr}: net_admin не найден в localdb\n")
            accounts_ok = False

    results["Пункт 3: Учётные записи"] = accounts_ok

    # --- Пункт 4: Коммутация (OVS) ---
    log_lines.append("\n📌 Пункт 4: Коммутация (OVS)\n")
    vlan_ok = True
    if "HQ-SW" in vm_ports:
        port = vm_ports["HQ-SW"]
        cmd_ovs = "ovs-vsctl show"
        stdout, stderr, cmd_log = ssh_exec(port, cmd_ovs, username="root", password="toor")
        log_lines.append(cmd_log + "\n")
        safe_log_output(log_lines, "[HQ-SW] Вывод", stdout, stderr)

        required_checks = [
            ("ens4", "tag: 100"),
            ("ens5", "tag: 200"),
            ("ens3", "trunks: [100, 200, 999]")
        ]
        all_found = True
        for port_name, expected in required_checks:
            if port_name not in stdout or expected not in stdout:
                log_lines.append(f"❌ HQ-SW: не найдено '{expected}' для порта '{port_name}'\n")
                all_found = False
        if all_found:
            log_lines.append("✅ HQ-SW: конфигурация OVS корректна\n")
        else:
            vlan_ok = False
    else:
        log_lines.append("⚠️ HQ-SW не найден\n")
        vlan_ok = False
    results["Пункт 4: Коммутация"] = vlan_ok

    # --- Пункт 5: Безопасный SSH ---
    log_lines.append("\n📌 Пункт 5: Безопасный SSH\n")
    ssh_sec_ok = True
    for srv in ["HQ-SRV", "BR-SRV"]:
        if srv not in vm_ports:
            continue
        port = vm_ports[srv]
        user, pwd = get_srv_creds(srv)['username'], get_srv_creds(srv)['password']

        config_content = None
        used_path = None
        for path in ["/etc/openssh/sshd_config", "/etc/ssh/sshd_config"]:
            stdout, stderr, cmd_log = ssh_exec(port, f"cat {path}", username=user, password=pwd)
            if stdout is not None and "Permission denied" not in (stderr or "") and "No such file" not in (stderr or ""):
                config_content = stdout
                used_path = path
                log_lines.append(cmd_log + "\n")
                safe_log_output(log_lines, f"[{srv}] Вывод", stdout, stderr)
                break

        if config_content is None:
            log_lines.append(f"❌ {srv}: Не удалось прочитать sshd_config\n")
            ssh_sec_ok = False
            continue

        max_auth_ok = any(
            line.strip().startswith('MaxAuthTries') and not line.strip().startswith('#') and '2' in line.split()
            for line in config_content.split('\n')
        )

        banner_ok = False
        for line in config_content.split('\n'):
            if line.strip().startswith('Banner') and not line.strip().startswith('#'):
                parts = line.split()
                if len(parts) >= 2:
                    banner_path = parts[1]
                    b_stdout, b_stderr, b_cmd_log = ssh_exec(port, f"cat {banner_path}", username=user, password=pwd)
                    log_lines.append(b_cmd_log + "\n")
                    safe_log_output(log_lines, f"[{srv}] Баннер ({banner_path})", b_stdout, b_stderr)
                    if "authorized access only" in (b_stdout or "").lower():
                        banner_ok = True
                    break

        if max_auth_ok and banner_ok:
            log_lines.append(f"✅ {srv}: MaxAuthTries=2 и баннер настроены\n")
        else:
            log_lines.append(f"❌ {srv}: SSH-безопасность не настроена\n")
            ssh_sec_ok = False

    results["Пункт 5: Безопасный SSH"] = ssh_sec_ok

    # --- Пункт 6: Интерфейсы на RTR ---
    log_lines.append("\n📌 Пункт 6: Интерфейсы на RTR\n")
    intf_ok = True
    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports:
            continue
        port = vm_ports[rtr]
        user, pwd = get_rtr_creds(rtr)['username'], get_rtr_creds(rtr)['password']

        out1, err1, cmd1 = rtr_exec(port, user, pwd, "show ip interface brief")
        log_lines.append(cmd1 + "\n")
        safe_log_output(log_lines, f"[{rtr}] Вывод", out1, err1)

        out2, err2, cmd2 = rtr_exec(port, user, pwd, "show running-config | include ge1/")
        log_lines.append(cmd2 + "\n")
        safe_log_output(log_lines, f"[{rtr}] Вывод", out2, err2)

        if out1 and out2:
            log_lines.append(f"✅ {rtr}: интерфейсы отображаются\n")
        else:
            log_lines.append(f"❌ {rtr}: не удалось получить информацию об интерфейсах\n")
            intf_ok = False

    results["Пункт 6: Интерфейсы на RTR"] = intf_ok

    # --- Пункт 7: GRE-туннель ---
    log_lines.append("\n📌 Пункт 7: GRE-туннель\n")
    gre_ok = True
    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports:
            continue
        port = vm_ports[rtr]
        user, pwd = get_rtr_creds(rtr)['username'], get_rtr_creds(rtr)['password']

        out1, err1, cmd1 = rtr_exec(port, user, pwd, "show interface tunnel.0")
        log_lines.append(cmd1 + "\n")
        safe_log_output(log_lines, f"[{rtr}] Вывод", out1, err1)

        out2, err2, cmd2 = rtr_exec(port, user, pwd, "show running-config | include gre")
        log_lines.append(cmd2 + "\n")
        safe_log_output(log_lines, f"[{rtr}] Вывод", out2, err2)

        target = "10.10.10.2" if rtr == "HQ-RTR" else "10.10.10.1"
        ping_cmd = f"ping {target} count 3"
        ping_out, ping_err, ping_log = rtr_exec_with_enable(port, user, pwd, ping_cmd)
        log_lines.append(ping_log + "\n")
        safe_log_output(log_lines, f"[{rtr}] Вывод", ping_out, ping_err)

        if out1 and "is up" in out1 and out2 and "gre" in out2 and ping_out and ("3 packets transmitted, 3 received" in ping_out or "100.0% packet loss" not in ping_out):
            log_lines.append(f"✅ {rtr}: GRE-туннель работает\n")
        else:
            log_lines.append(f"❌ {rtr}: GRE-туннель не работает\n")
            gre_ok = False

    results["Пункт 7: GRE-туннель"] = gre_ok

    # --- Пункт 8: OSPF (исправлено — точная проверка аутентификации) ---
    log_lines.append("\n📌 Пункт 8: OSPF\n")
    ospf_ok = True
    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports:
            continue
        port = vm_ports[rtr]
        user, pwd = get_rtr_creds(rtr)['username'], get_rtr_creds(rtr)['password']

        out1, err1, cmd1 = rtr_exec(port, user, pwd, "show ip protocols ospf")
        log_lines.append(cmd1 + "\n")
        safe_log_output(log_lines, f"[{rtr}] Вывод", out1, err1)

        out2, err2, cmd2 = rtr_exec(port, user, pwd, "show ip route")
        log_lines.append(cmd2 + "\n")
        safe_log_output(log_lines, f"[{rtr}] Вывод", out2, err2)

        out3, err3, cmd3 = rtr_exec(port, user, pwd, "show ip ospf neighbor")
        log_lines.append(cmd3 + "\n")
        safe_log_output(log_lines, f"[{rtr}] Вывод", out3, err3)

        # Проверка аутентификации через show run
        out4, err4, cmd4 = rtr_exec(port, user, pwd, "show running-config | include ospf")
        log_lines.append(cmd4 + "\n")
        safe_log_output(log_lines, f"[{rtr}] Вывод", out4, err4)

        auth_ok = False
        has_auth = False
        has_md5 = False
        if out4:
            lines = [line.strip() for line in out4.split('\n') if 'ospf' in line]
            for line in lines:
                if 'authentication message-digest' in line:
                    has_auth = True
                if 'md5' in line:
                    has_md5 = True
            if has_auth and has_md5:
                auth_ok = True

        has_route = "10.10.100.0" in (out2 or "") or "10.20.20.0" in (out2 or "")

        # Анализ соседей: ищем ровно 1 запись, соответствующую требованиям
        valid_tunnel_neighbor = False
        if out3:
            lines = out3.split('\n')
            for line in lines:
                if not line.strip():
                    continue
                if 'Neighbor ID' in line or 'Total number' in line or 'OSPF process' in line:
                    continue

                # Разбиваем строку на колонки
                parts = line.split()
                if len(parts) < 6:
                    continue

                neighbor_ip = parts[0]
                state = parts[2]
                interface = parts[5]

                # Проверка: IP из 10.10.10.0/24
                try:
                    ip_obj = ipaddress.IPv4Address(neighbor_ip)
                    in_tunnel_net = ip_obj in ipaddress.IPv4Network("10.10.10.0/24")
                except:
                    in_tunnel_net = False

                # Проверка состояния
                is_full = state.startswith("Full/")

                # Проверка интерфейса
                is_tunnel = interface == "tunnel.0"

                if in_tunnel_net and is_full and is_tunnel:
                    valid_tunnel_neighbor = True
                    log_lines.append(f"  → Найден валидный OSPF-сосед: {neighbor_ip} ({state}) на {interface}\n")
                    break

        # Логирование условий
        log_lines.append(f"  → Аутентификация (по конфигу): {'✅' if auth_ok else '❌'}\n")
        log_lines.append(f"  → Маршруты: {'✅' if has_route else '❌'}\n")
        log_lines.append(f"  → Валидный сосед по tunnel.0: {'✅' if valid_tunnel_neighbor else '❌'}\n")

        if auth_ok and has_route and valid_tunnel_neighbor:
            log_lines.append(f"✅ {rtr}: OSPF настроен корректно\n")
        else:
            log_lines.append(f"❌ {rtr}: OSPF настроен некорректно\n")
            ospf_ok = False

    results["Пункт 8: OSPF"] = ospf_ok

    # --- Пункт 9: NAT ---
    log_lines.append("\n📌 Пункт 9: NAT\n")
    nat_ok = True
    for cli in ["HQ-CLI", "BR-CLI"]:
        if cli not in vm_ports:
            continue
        port = vm_ports[cli]
        cmd_ping = "ping -c 2 1.1.1.1"
        stdout, stderr, cmd_log = ssh_exec(port, cmd_ping, username="root", password="toor")
        log_lines.append(cmd_log + "\n")
        safe_log_output(log_lines, f"[{cli}] Вывод", stdout, stderr)

        if stdout and "2 packets transmitted, 2 received" in stdout:
            log_lines.append(f"✅ {cli}: NAT работает\n")
        else:
            log_lines.append(f"❌ {cli}: NAT не работает\n")
            nat_ok = False

    results["Пункт 9: NAT"] = nat_ok

    # --- Пункт 10: DHCP ---
    log_lines.append("\n📌 Пункт 10: DHCP\n")
    dhcp_ok = True
    if "HQ-CLI" in vm_ports and "HQ-RTR" in vm_ports:
        rtr_port = vm_ports["HQ-RTR"]
        user, pwd = get_rtr_creds("HQ-RTR")['username'], get_rtr_creds("HQ-RTR")['password']
        out, err, cmd = rtr_exec(rtr_port, user, pwd, "show running-config | include dhcp-server")
        log_lines.append(cmd + "\n")
        safe_log_output(log_lines, "[HQ-RTR] Вывод", out, err)

        dhcp_server_name = None
        if out:
            lines = [line.strip() for line in out.split('\n') if 'dhcp-server' in line]
            for line in lines:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == 'dhcp-server':
                    dhcp_server_name = parts[1]
                    break

        if dhcp_server_name:
            out2, err2, cmd2 = rtr_exec(rtr_port, user, pwd, f"show dhcp-server {dhcp_server_name}")
            log_lines.append(cmd2 + "\n")
            safe_log_output(log_lines, f"[HQ-RTR] Конфиг DHCP-сервера '{dhcp_server_name}'", out2, err2)

            cli_port = vm_ports["HQ-CLI"]
            ssh_exec(cli_port, "dhclient -r ens3 && dhclient ens3", username="root", password="toor")
            ip_out, _, cmd_ip = ssh_exec(cli_port, "ip -br addr show ens3", username="root", password="toor")
            log_lines.append(cmd_ip + "\n")
            safe_log_output(log_lines, "[HQ-CLI] Вывод", ip_out, "")

            if ip_out and "10.10.200." in ip_out:
                log_lines.append("✅ HQ-CLI: получил IP по DHCP на ens3\n")
            else:
                log_lines.append("❌ HQ-CLI: не получил IP по DHCP на ens3\n")
                dhcp_ok = False
        else:
            log_lines.append("❌ HQ-RTR: DHCP-сервер не найден\n")
            dhcp_ok = False
    else:
        log_lines.append("⚠️ HQ-CLI или HQ-RTR не найден\n")
        dhcp_ok = False

    results["Пункт 10: DHCP"] = dhcp_ok

    # --- Пункт 11: DNS (исправлено — с обратным IP) ---
    log_lines.append("\n📌 Пункт 11: DNS\n")
    dns_ok = True

    # Выбираем клиентское устройство для проверки
    client_dev = "HQ-CLI" if "HQ-CLI" in vm_ports else "BR-CLI"
    if client_dev not in vm_ports:
        log_lines.append("⚠️ Ни один клиентский хост (HQ-CLI/BR-CLI) не найден\n")
        dns_ok = False
    else:
        client_port = vm_ports[client_dev]
        for dev, expected_fqdn in FQDN_TABLE.items():
            log_lines.append(f"\n→ Проверка устройства: {dev} ({expected_fqdn})\n")

            # Шаг 1: Прямой запрос с клиента
            cmd_forward = f"nslookup {expected_fqdn}"
            stdout, stderr, cmd_log = ssh_exec(client_port, cmd_forward, username="root", password="toor")
            log_lines.append(cmd_log + "\n")
            safe_log_output(log_lines, f"[{client_dev}] Прямой запрос", stdout, stderr)

            if not stdout or "Address:" not in stdout:
                log_lines.append(f"❌ Прямой запрос для {expected_fqdn} не удался\n")
                dns_ok = False
                continue

            # Извлекаем IP из строки: Name: <fqdn>\nAddress: <IP>
            pattern = rf'Name:\s*{re.escape(expected_fqdn)}\s*\n\s*Address:\s*(\d+\.\d+\.\d+\.\d+)'
            match = re.search(pattern, stdout, re.IGNORECASE)
            if not match:
                log_lines.append(f"❌ Не найден IP для {expected_fqdn} в прямом запросе\n")
                dns_ok = False
                continue

            ip_addr = match.group(1)
            if ip_addr.startswith("127."):
                log_lines.append(f"⚠️ IP {ip_addr} — loopback, пропускаем\n")
                continue

            log_lines.append(f"✅ Получен IP: {ip_addr}\n")

            # Шаг 2: Обратный запрос с клиента
            cmd_reverse = f"nslookup {ip_addr}"
            rev_out, rev_err, rev_cmd_log = ssh_exec(client_port, cmd_reverse, username="root", password="toor")
            log_lines.append(rev_cmd_log + "\n")
            safe_log_output(log_lines, f"[{client_dev}] Обратный запрос для {ip_addr}", rev_out, rev_err)

            if not rev_out:
                log_lines.append(f"❌ Обратный запрос для {ip_addr} не удался\n")
                dns_ok = False
                continue

            # Убираем предупреждения
            rev_out_clean = re.sub(r'Warning:.*?\n', '', rev_out, flags=re.DOTALL)

            # Формируем PTR-имя: 10.10.100.2 → 2.100.10.10.in-addr.arpa
            ptr_name = reverse_ip(ip_addr) + ".in-addr.arpa"
            # Ищем: <ptr_name>	name = <FQDN>.
            reverse_pattern = rf'{re.escape(ptr_name)}\s+name\s*=\s*{re.escape(expected_fqdn)}\.'
            if re.search(reverse_pattern, rev_out_clean, re.IGNORECASE):
                log_lines.append(f"✅ Обратное разрешение для {expected_fqdn} работает\n")
            else:
                log_lines.append(f"❌ Обратное разрешение для {expected_fqdn} не работает\n")
                dns_ok = False

    results["Пункт 11: DNS"] = dns_ok

    # --- Пункт 12: Пинги по FQDN между офисами ---
    log_lines.append("\n📌 Пункт 12: Пинги по FQDN между офисами\n")
    ping_fqdn_ok = True
    if "HQ-CLI" in vm_ports and "BR-CLI" in vm_ports:
        for target in ["br-srv.au-team.irpo", "br-cli.au-team.irpo"]:
            cmd = f"ping -c 2 {target}"
            out, err, cmd_log = ssh_exec(vm_ports["HQ-CLI"], cmd, username="root", password="toor")
            log_lines.append(cmd_log + "\n")
            safe_log_output(log_lines, f"[HQ-CLI → {target}]", out, err)
            if not (out and "2 packets transmitted, 2 received" in out):
                ping_fqdn_ok = False

        for target in ["hq-srv.au-team.irpo", "hq-cli.au-team.irpo"]:
            cmd = f"ping -c 2 {target}"
            out, err, cmd_log = ssh_exec(vm_ports["BR-CLI"], cmd, username="root", password="toor")
            log_lines.append(cmd_log + "\n")
            safe_log_output(log_lines, f"[BR-CLI → {target}]", out, err)
            if not (out and "2 packets transmitted, 2 received" in out):
                ping_fqdn_ok = False

        if ping_fqdn_ok:
            log_lines.append("✅ Пинги по FQDN между офисами работают\n")
        else:
            log_lines.append("❌ Пинги по FQDN между офисами не работают\n")
    else:
        log_lines.append("⚠️ HQ-CLI или BR-CLI не найден\n")
        ping_fqdn_ok = False

    results["Пункт 12: Пинги по FQDN"] = ping_fqdn_ok

    # --- Пункт 13: Проверка ISP ---
    log_lines.append("\n📌 Пункт 13: Проверка ISP\n")
    isp_ok = True
    if "ISP" not in vm_ports:
        log_lines.append("⚠️ Устройство ISP не найдено\n")
        isp_ok = False
    else:
        isp_port = vm_ports["ISP"]
        user, pwd = get_srv_creds("ISP")['username'], get_srv_creds("ISP")['password']

        # 1. Имя хоста
        stdout, stderr, cmd_log = ssh_exec(isp_port, "hostname -f", username=user, password=pwd)
        log_lines.append(cmd_log + "\n")
        safe_log_output(log_lines, "[ISP] Вывод", stdout, stderr)
        actual_fqdn = stdout.strip().lower() if stdout else ""
        if actual_fqdn == "isp.au-team.irpo":
            log_lines.append("✅ ISP: FQDN корректен\n")
        else:
            log_lines.append(f"❌ ISP: ожидается isp.au-team.irpo, получено {actual_fqdn}\n")
            isp_ok = False

        # 2. Адресация
        cmd_ip = "ip -br addr show"
        stdout, stderr, cmd_log = ssh_exec(isp_port, cmd_ip, username=user, password=pwd)
        log_lines.append(cmd_log + "\n")
        safe_log_output(log_lines, "[ISP] Вывод", stdout, stderr)

        interfaces = {}
        if stdout:
            for line in stdout.strip().split('\n'):
                parts = line.split()
                if len(parts) >= 3 and '/' in parts[2]:
                    iface = parts[0]
                    ip_with_mask = parts[2]
                    interfaces[iface] = ip_with_mask

        # Проверка ens4: 172.16.1.0/28
        ens4_ok = False
        if 'ens4' in interfaces:
            ip, mask = interfaces['ens4'].split('/')
            if ipaddress.IPv4Address(ip) in ipaddress.IPv4Network("172.16.1.0/28") and int(mask) == 28:
                ens4_ok = True
                log_lines.append("✅ ISP: ens4 в 172.16.1.0/28\n")
            else:
                log_lines.append(f"❌ ISP: ens4 = {interfaces['ens4']} — не в 172.16.1.0/28\n")
        else:
            log_lines.append("❌ ISP: интерфейс ens4 не найден\n")

        # Проверка ens5: 172.16.2.0/28
        ens5_ok = False
        if 'ens5' in interfaces:
            ip, mask = interfaces['ens5'].split('/')
            if ipaddress.IPv4Address(ip) in ipaddress.IPv4Network("172.16.2.0/28") and int(mask) == 28:
                ens5_ok = True
                log_lines.append("✅ ISP: ens5 в 172.16.2.0/28\n")
            else:
                log_lines.append(f"❌ ISP: ens5 = {interfaces['ens5']} — не в 172.16.2.0/28\n")
        else:
            log_lines.append("❌ ISP: интерфейс ens5 не найден\n")

        # Проверка ens3: должен иметь IP (DHCP от провайдера)
        ens3_ok = 'ens3' in interfaces and interfaces['ens3'] != ''
        if ens3_ok:
            log_lines.append(f"✅ ISP: ens3 получил IP по DHCP: {interfaces['ens3']}\n")
        else:
            log_lines.append("❌ ISP: ens3 не получил IP\n")

        # 3. Маршрут по умолчанию
        cmd_route = "ip route show default"
        stdout, stderr, cmd_log = ssh_exec(isp_port, cmd_route, username=user, password=pwd)
        log_lines.append(cmd_log + "\n")
        safe_log_output(log_lines, "[ISP] Вывод", stdout, stderr)
        if stdout and "default via" in stdout:
            log_lines.append("✅ ISP: маршрут по умолчанию настроен\n")
        else:
            log_lines.append("❌ ISP: маршрут по умолчанию отсутствует\n")
            isp_ok = False

        if ens4_ok and ens5_ok and ens3_ok:
            log_lines.append("✅ ISP: адресация корректна\n")
        else:
            isp_ok = False

    results["Пункт 13: Проверка ISP"] = isp_ok

    # --- ИТОГОВЫЙ ОТЧЁТ ---
    log_lines.append("\n" + "="*60)
    log_lines.append("📊 ИТОГОВЫЙ ОТЧЁТ:\n")
    total_passed = 0
    for item, passed in results.items():
        status = "✅ ПРОЙДЕН" if passed else "❌ НЕ ПРОЙДЕН"
        log_lines.append(f"{item}: {status}\n")
        if passed:
            total_passed += 1

    log_lines.append(f"\n📈 Выполнено {total_passed} из {len(results)} пунктов.\n")

    return total_passed, log_lines


if __name__ == "__main__":
    vm_ports = {
    "HQ-SRV": "40045",
    "BR-SRV": "40049",
    "HQ-CLI": "40046",
    "BR-CLI": "40050",
    "HQ-RTR": "40043",
    "BR-RTR": "40047",
    "HQ-SW": "40044",
    "ISP": "40042"
    }

    try:
        total, log_lines = run_full_assignment_check(vm_ports)
        for line in log_lines:
            print(line, end='')
    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
