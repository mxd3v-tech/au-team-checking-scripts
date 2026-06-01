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

# ========== ПАРАМЕТРЫ ВАРИАНТА (В2) ==========
VLAN_SRV = 112          # HQ-SRV
VLAN_CLI = 212          # HQ-CLI
VLAN_MGMT = 812         # управление
SSHUSER_UID = "2012"    # идентификатор пользователя sshuser
SSH_PORT = "2012"       # порт безопасного SSH на серверах
ISP_NET_HQ = "172.16.30.0/28"   # ISP — в сторону HQ-RTR
ISP_NET_BR = "172.16.40.0/28"   # ISP — в сторону BR-RTR

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
            "-tt", "-p", ssh_port,
            "%s@localhost" % username,
            command
        ]
        process = subprocess.Popen(
            ssh_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )
        stdout, stderr = process.communicate(timeout=timeout)
        return stdout, stderr, command
    except Exception as e:
        return None, "❌ Ошибка: %s" % str(e), command

# ========== ФУНКЦИИ ДЛЯ МАРШРУТИЗАТОРОВ (EcoRouterOS) ==========
def rtr_exec(port, username, password, command, timeout=30):
    """Выполняет команду на EcoRouterOS (без enable)"""
    if not PEXPECT_AVAILABLE:
        return None, "pexpect не установлен", command
    try:
        child = pexpect.spawn(
            "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p %s %s@localhost" % (port, username),
            timeout=timeout,
            encoding='utf-8',
            codec_errors='ignore'
        )
        child.expect(r"[Pp]assword:", timeout=60)
        child.sendline(password)
        child.expect(r'\S+>', timeout=60)
        child.sendline("terminal length 0")
        child.expect(r'\S+>', timeout=30)
        child.sendline(command)
        child.expect(r'\S+>', timeout=timeout)
        full_output = child.before.strip()
        child.sendline("exit")
        child.close()

        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        clean_output = ansi_escape.sub('', full_output)
        return clean_output, "", command
    except pexpect.TIMEOUT:
        return None, "Таймаут выполнения команды", command
    except Exception as e:
        return None, "Ошибка pexpect: %s" % str(e), command

def rtr_exec_with_enable(port, username, password, command, timeout=30):
    """Выполняет команду в режиме enable (#)"""
    if not PEXPECT_AVAILABLE:
        return None, "pexpect не установлен", command
    try:
        child = pexpect.spawn(
            "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p %s %s@localhost" % (port, username),
            timeout=timeout,
            encoding='utf-8',
            codec_errors='ignore'
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
        clean_output = ansi_escape.sub('', full_output)
        return clean_output, "", command
    except pexpect.TIMEOUT:
        return None, "Таймаут выполнения команды", command
    except Exception as e:
        return None, "Ошибка pexpect: %s" % str(e), command

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
# Ограничения на размер подсети по заданию (кол-во адресов → длина префикса).
# Точные подсети участник выбирает сам — проверяем только приватность и размер.
#   HQ-SRV (VLAN %d): не более 32 адресов  → /27 и уже
#   HQ-CLI (VLAN %d): не менее 16 адресов  → /28 и шире
#   BR-SRV:           не более 16 адресов  → /28 и уже
SUBNET_CONSTRAINTS = {
    "HQ-SRV": {"min_prefix": 27, "max_prefix": 30, "desc": "не более 32 адресов (/27 и уже)"},
    "HQ-CLI": {"min_prefix": 16, "max_prefix": 28, "desc": "не менее 16 адресов (/28 и шире)"},
    "BR-SRV": {"min_prefix": 28, "max_prefix": 30, "desc": "не более 16 адресов (/28 и уже)"},
}

def validate_device_ip(dev, ip_str, prefixlen):
    """Проверка адреса по ограничениям задания (приватность + размер подсети)."""
    try:
        ip = ipaddress.IPv4Address(ip_str)
    except ipaddress.AddressValueError:
        return False, "❌ некорректный IPv4-адрес"
    if not ip.is_private or ip.is_loopback or ip.is_link_local:
        return False, "❌ %s не из приватного диапазона RFC1918" % ip_str

    c = SUBNET_CONSTRAINTS.get(dev)
    if not c:
        return True, "✅ %s — приватный" % ip_str
    if prefixlen < c["min_prefix"]:
        return False, "❌ /%d слишком широкая (%s)" % (prefixlen, c["desc"])
    if prefixlen > c["max_prefix"]:
        return False, "❌ /%d слишком узкая (%s)" % (prefixlen, c["desc"])
    return True, "✅ %s//%d — %s" % (ip_str, prefixlen, c["desc"])

def reverse_ip(ip):
    return '.'.join(reversed(ip.split('.')))

def safe_log_output(log_lines, prefix, output, error=""):
    full_output = (output or "") + ("\n" + error if error else "")
    log_lines.append("%s:\n%s\n" % (prefix, full_output))

# ========== ГЛАВНАЯ ФУНКЦИЯ ==========
def run_full_assignment_check(vm_ports):
    POINTS = 0.0
    MAX_POINTS = 13.0
    log_lines = []
    results = {}

    def log_msg(msg):
        log_lines.append(msg)
        print(msg)

    log_msg("🔍 Начало комплексной проверки Модуля 1 (M1-V2)")

    DEVICE_NAMES = {
        "HQ-SRV": "hq-srv.au-team.irpo",
        "BR-SRV": "br-srv.au-team.irpo",
        "HQ-CLI": "hq-cli.au-team.irpo",
        "HQ-RTR": "hq-rtr",
        "BR-RTR": "br-rtr",
        "ISP": "isp.au-team.irpo",
    }

    # DNS-записи (таблица 3 задания)
    FQDN_TABLE = {
        "HQ-RTR": "hq-rtr.au-team.irpo",
        "BR-RTR": "br-rtr.au-team.irpo",
        "HQ-SRV": "hq-srv.au-team.irpo",
        "HQ-CLI": "hq-cli.au-team.irpo",
        "BR-SRV": "br-srv.au-team.irpo",
        "ISP-HQ": "docker.au-team.irpo",   # интерфейс ISP в сторону HQ-RTR
        "ISP-BR": "web.au-team.irpo",      # интерфейс ISP в сторону BR-RTR
    }

    # Устройства, для которых требуется PTR
    PTR_DEVICES = {
        "hq-rtr.au-team.irpo",
        "hq-srv.au-team.irpo",
        "hq-cli.au-team.irpo",
    }

    # --- Пункт 1: FQDN ---
    log_msg("\n📌 Пункт 1: Имена устройств (FQDN)")
    names_correct = 0
    names_total = 0
    devices_to_check = ["HQ-SRV", "BR-SRV", "HQ-CLI", "HQ-RTR", "BR-RTR"]

    for dev in devices_to_check:
        if dev not in vm_ports:
            log_msg("⚠️ %s: устройство не найдено" % DEVICE_NAMES[dev])
            continue

        names_total += 1
        expected_fqdn = FQDN_TABLE[dev]
        dev_name = DEVICE_NAMES[dev]

        if "RTR" in dev:
            out, err, cmd = rtr_exec(vm_ports[dev], *get_rtr_creds(dev), "show hostname")
            log_msg("[%s] Выполняется команда: show hostname" % dev_name)
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
                log_msg("✅ %s: имя хоста корректно (получено: %s)" % (dev_name, actual))
                names_correct += 1
            else:
                log_msg("❌ %s: имя хоста некорректно (получено: %s)" % (dev_name, actual))

        else:
            out, err, cmd = ssh_exec(vm_ports[dev], "hostname -f", *get_srv_creds(dev))
            log_msg("[%s] Выполняется команда: hostname -f" % dev_name)
            full_out = (out or "") + ("\n" + err if err else "")
            log_msg("[Вывод]\n%s\n" % full_out)
            actual = out.strip().lower() if out else ""
            if actual == expected_fqdn:
                log_msg("✅ %s: FQDN корректен" % dev_name)
                names_correct += 1
            else:
                log_msg("❌ %s: FQDN некорректен (получено: %s)" % (dev_name, actual))

    names_ok = names_correct >= 1
    log_msg("ℹ️ Имена устройств: %d из %d корректны" % (names_correct, names_total))
    if names_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 1 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 1 не пройден")
    results["Пункт 1: FQDN"] = names_ok

    # --- Пункт 2: IPv4-адресация (по ограничениям) ---
    log_msg("\n📌 Пункт 2: IPv4-адресация")
    ip_ok = True
    for dev in ["HQ-SRV", "BR-SRV", "HQ-CLI"]:
        if dev not in vm_ports:
            continue
        dev_name = DEVICE_NAMES[dev]
        out, err, cmd = ssh_exec(vm_ports[dev], "ip -br addr show", *get_srv_creds(dev))
        log_msg("[%s] Выполняется команда: ip -br addr show" % dev_name)
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
                ip, mask = parts[2].split('/')
                if ip.startswith('169.'):
                    continue
                ok, msg = validate_device_ip(dev, ip, int(mask))
                log_msg("  → %s: %s" % (parts[0], msg))
                if ok:
                    valid_found = True
        if not valid_found:
            log_msg("❌ %s: подходящий приватный адрес не найден" % dev_name)
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
        dev_name = DEVICE_NAMES[srv]
        out, err, cmd = ssh_exec(vm_ports[srv], "id -u sshuser", "sshuser", "P@ssw0rd")
        log_msg("[%s] Выполняется команда: id -u sshuser" % dev_name)
        full_out = (out or "") + ("\n" + err if err else "")
        log_msg("[Вывод]\n%s\n" % full_out)
        uid_ok = out and out.strip() == SSHUSER_UID
        if not uid_ok:
            log_msg("❌ %s: UID sshuser ≠ %s" % (dev_name, SSHUSER_UID))

        out2, err2, cmd2 = ssh_exec(vm_ports[srv], "sudo -n id", "sshuser", "P@ssw0rd")
        log_msg("[%s] Выполняется команда: sudo -n id" % dev_name)
        full_out2 = (out2 or "") + ("\n" + err2 if err2 else "")
        log_msg("[Вывод]\n%s\n" % full_out2)
        sudo_ok = out2 and "uid=0(root)" in out2

        if not (uid_ok and sudo_ok):
            accounts_ok = False

    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports:
            continue
        dev_name = DEVICE_NAMES[rtr]
        out, err, cmd = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show users localdb")
        log_msg("[%s] Выполняется команда: show users localdb" % dev_name)
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

    # --- Пункт 4: Коммутация OVS на HQ-SW ---
    log_msg("\n📌 Пункт 4: Коммутация OVS")
    ovs_ok = True
    if "HQ-SW" in vm_ports:
        dev_name = "hq-sw.au-team.irpo"
        out, err, cmd = ssh_exec(vm_ports["HQ-SW"], "ovs-vsctl show", "root", "toor")
        log_msg("[%s] Выполняется команда: ovs-vsctl show" % dev_name)
        full_out = (out or "") + ("\n" + err if err else "")
        log_msg("[Вывод]\n%s\n" % full_out)
        if out:
            out_lower = out.lower()
            has_vlan_srv = bool(re.search(r'tag:\s*%d' % VLAN_SRV, out_lower))
            has_vlan_cli = bool(re.search(r'tag:\s*%d' % VLAN_CLI, out_lower))
            trunk_match = re.search(r'trunks:\s*\[([^\]]+)\]', out_lower)
            has_trunk = False
            if trunk_match:
                trunk_vlans = set(v.strip() for v in trunk_match.group(1).split(','))
                has_trunk = {str(VLAN_SRV), str(VLAN_CLI), str(VLAN_MGMT)}.issubset(trunk_vlans)

            if has_vlan_srv:
                log_msg("✅ VLAN %d (HQ-SRV) найден" % VLAN_SRV)
            else:
                log_msg("❌ VLAN %d (HQ-SRV) не найден" % VLAN_SRV)
            if has_vlan_cli:
                log_msg("✅ VLAN %d (HQ-CLI) найден" % VLAN_CLI)
            else:
                log_msg("❌ VLAN %d (HQ-CLI) не найден" % VLAN_CLI)
            if has_trunk:
                log_msg("✅ Trunk с VLAN %d, %d, %d найден" % (VLAN_SRV, VLAN_CLI, VLAN_MGMT))
            else:
                log_msg("❌ Trunk с VLAN %d, %d, %d не найден" % (VLAN_SRV, VLAN_CLI, VLAN_MGMT))

            if not (has_vlan_srv and has_vlan_cli and has_trunk):
                ovs_ok = False
        else:
            ovs_ok = False
    else:
        ovs_ok = False
    if ovs_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 4 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 4 не пройден")
    results["Пункт 4: Коммутация"] = ovs_ok

    # --- Пункт 5: Безопасный SSH ---
    log_msg("\n📌 Пункт 5: Безопасный SSH")
    ssh_ok = True
    for srv in ["HQ-SRV", "BR-SRV"]:
        if srv not in vm_ports:
            continue
        dev_name = DEVICE_NAMES[srv]
        config_content, err, cmd = ssh_exec(vm_ports[srv], "sshd -T 2>/dev/null", *get_srv_creds(srv))
        log_msg("[%s] Выполняется команда: sshd -T" % dev_name)
        full_out = (config_content or "") + ("\n" + err if err else "")
        log_msg("[Вывод]\n%s\n" % full_out)
        if config_content is None:
            ssh_ok = False
            continue
        lines_lower = [l.strip().lower() for l in config_content.split('\n')]
        port_ok = any(l.startswith('port ') and SSH_PORT in l for l in lines_lower)
        max_auth = any(l.startswith('maxauthtries') and '2' in l for l in lines_lower)
        allow_ok = any(l.startswith('allowusers') and 'sshuser' in l for l in lines_lower)
        banner_ok = False
        for line in config_content.split('\n'):
            stripped = line.strip().lower()
            if stripped.startswith('banner '):
                banner_path = line.strip().split(None, 1)[1]
                if banner_path and banner_path != "none":
                    banner_out, _, cmd_banner = ssh_exec(vm_ports[srv], "test -f %s && cat %s" % (banner_path, banner_path), *get_srv_creds(srv))
                    log_msg("[%s] Проверка баннера: %s" % (dev_name, banner_path))
                    full_banner = (banner_out or "") + ("\n" + _ if _ else "")
                    log_msg("[Вывод]\n%s\n" % full_banner)
                    if banner_out and "authorized access only" in banner_out.lower():
                        banner_ok = True
                        break
        if not port_ok:
            log_msg("❌ %s: порт SSH %s не настроен" % (dev_name, SSH_PORT))
        if not max_auth:
            log_msg("❌ %s: MaxAuthTries 2 не настроено" % dev_name)
        if not allow_ok:
            log_msg("❌ %s: AllowUsers sshuser не настроено" % dev_name)
        if not banner_ok:
            log_msg("❌ %s: баннер «Authorized access only» не настроен" % dev_name)
        if not (port_ok and max_auth and allow_ok and banner_ok):
            ssh_ok = False
    if ssh_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 5 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 5 не пройден")
    results["Пункт 5: Безопасный SSH"] = ssh_ok

    # --- Пункт 6: Интерфейсы на RTR ---
    log_msg("\n📌 Пункт 6: Интерфейсы на RTR")
    intf_ok = True
    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports:
            continue
        dev_name = DEVICE_NAMES[rtr]
        out1, err1, cmd1 = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show ip interface brief")
        log_msg("[%s] Выполняется команда: show ip interface brief" % dev_name)
        full_out1 = (out1 or "") + ("\n" + err1 if err1 else "")
        log_msg("[Вывод]\n%s\n" % full_out1)
        out2, err2, cmd2 = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show running-config | include ge1/")
        log_msg("[%s] Выполняется команда: show running-config | include ge1/" % dev_name)
        full_out2 = (out2 or "") + ("\n" + err2 if err2 else "")
        log_msg("[Вывод]\n%s\n" % full_out2)
        if not (out1 and out2):
            intf_ok = False
    if intf_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 6 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 6 не пройден")
    results["Пункт 6: Интерфейсы на RTR"] = intf_ok

    # --- Пункт 7: IP-туннель (GRE или IP in IP) ---
    log_msg("\n📌 Пункт 7: IP-туннель (GRE или IP in IP)")
    gre_ok = True
    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports:
            continue
        dev_name = DEVICE_NAMES[rtr]
        out1, err1, cmd1 = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show interface tunnel.0")
        log_msg("[%s] Выполняется команда: show interface tunnel.0" % dev_name)
        full_out1 = (out1 or "") + ("\n" + err1 if err1 else "")
        log_msg("[Вывод]\n%s\n" % full_out1)
        out2, err2, cmd2 = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show running-config | include tunnel")
        log_msg("[%s] Выполняется команда: show running-config | include tunnel" % dev_name)
        full_out2 = (out2 or "") + ("\n" + err2 if err2 else "")
        log_msg("[Вывод]\n%s\n" % full_out2)

        cfg = ((out1 or "") + "\n" + (out2 or "")).lower()
        cond_up = out1 is not None and "is up" in out1.lower()
        cond_mode = ("gre" in cfg) or ("ipip" in cfg) or ("ip-in-ip" in cfg) or ("ip in ip" in cfg)

        if cond_up and cond_mode:
            log_msg("✅ %s: IP-туннель поднят (режим GRE/IPIP)" % dev_name)
        else:
            log_msg("❌ %s: IP-туннель не поднят или режим не определён" % dev_name)
            gre_ok = False
    if gre_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 7 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 7 не пройден")
    results["Пункт 7: IP-туннель"] = gre_ok

    # --- Пункт 8: Динамическая маршрутизация (OSPF) ---
    log_msg("\n📌 Пункт 8: Динамическая маршрутизация (OSPF)")
    ospf_ok = True
    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports:
            continue
        dev_name = DEVICE_NAMES[rtr]
        out2, err2, cmd2 = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show ip route")
        log_msg("[%s] Выполняется команда: show ip route" % dev_name)
        full_out2 = (out2 or "") + ("\n" + err2 if err2 else "")
        log_msg("[Вывод]\n%s\n" % full_out2)
        out3, err3, cmd3 = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show ip ospf neighbor")
        log_msg("[%s] Выполняется команда: show ip ospf neighbor" % dev_name)
        full_out3 = (out3 or "") + ("\n" + err3 if err3 else "")
        log_msg("[Вывод]\n%s\n" % full_out3)
        out4, err4, cmd4 = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show running-config | include ospf")
        log_msg("[%s] Выполняется команда: show running-config | include ospf" % dev_name)
        full_out4 = (out4 or "") + ("\n" + err4 if err4 else "")
        log_msg("[Вывод]\n%s\n" % full_out4)

        auth_ok = out4 is not None and "authentication message-digest" in out4 and "md5" in out4
        # OSPF-сосед должен подниматься через интерфейс туннеля
        valid_tunnel_neighbor = False
        if out3 is not None:
            for line in out3.split('\n'):
                if 'tunnel.0' in line and 'Full' in line:
                    valid_tunnel_neighbor = True
                    break
        # наличие маршрутов, выученных по OSPF
        has_ospf_route = out2 is not None and re.search(r'^\s*O\b', out2, re.MULTILINE) is not None

        if not auth_ok:
            log_msg("❌ %s: парольная защита OSPF (md5) не настроена" % dev_name)
        if not valid_tunnel_neighbor:
            log_msg("❌ %s: OSPF-сосед через tunnel.0 не в состоянии Full" % dev_name)
        if not has_ospf_route:
            log_msg("⚠️ %s: маршруты OSPF не обнаружены" % dev_name)

        if auth_ok and valid_tunnel_neighbor:
            log_msg("✅ %s: OSPF настроен корректно" % dev_name)
        else:
            ospf_ok = False
    if ospf_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 8 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 8 не пройден")
    results["Пункт 8: OSPF"] = ospf_ok

    # --- Пункт 9: NAT (доступ в интернет) ---
    log_msg("\n📌 Пункт 9: NAT")
    nat_ok = True
    if "HQ-CLI" in vm_ports:
        dev_name = DEVICE_NAMES["HQ-CLI"]
        out, err, cmd = ssh_exec(vm_ports["HQ-CLI"], "ping -c 2 1.1.1.1", *get_srv_creds("HQ-CLI"))
        log_msg("[%s] Выполняется команда: ping -c 2 1.1.1.1" % dev_name)
        full_out = (out or "") + ("\n" + err if err else "")
        log_msg("[Вывод]\n%s\n" % full_out)
        if not (out and "2 packets transmitted, 2 received" in out):
            nat_ok = False
    else:
        nat_ok = False
    if nat_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 9 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 9 не пройден")
    results["Пункт 9: NAT"] = nat_ok

    # --- Пункт 10: DHCP для HQ-CLI ---
    log_msg("\n📌 Пункт 10: DHCP")
    dhcp_ok = True
    if "HQ-CLI" in vm_ports and "HQ-RTR" in vm_ports:
        dev_name_rtr = DEVICE_NAMES["HQ-RTR"]
        out, err, cmd = rtr_exec(vm_ports["HQ-RTR"], *get_rtr_creds("HQ-RTR"), "show running-config | include dhcp-server")
        log_msg("[%s] Выполняется команда: show running-config | include dhcp-server" % dev_name_rtr)
        full_out = (out or "") + ("\n" + err if err else "")
        log_msg("[Вывод]\n%s\n" % full_out)

        dhcp_server_name = None
        if out:
            lines = [
                line.strip() for line in out.split('\n')
                if 'dhcp-server' in line
                and not line.strip().startswith('!')
                and 'show' not in line
                and '|' not in line
            ]
            if lines:
                first_line = lines[0].strip()
                if first_line.startswith('dhcp-server'):
                    name_part = first_line[len('dhcp-server'):].strip()
                    if name_part:
                        dhcp_server_name = name_part
                        log_msg("✅ Найден DHCP-сервер: %s" % dhcp_server_name)
                    else:
                        log_msg("❌ Имя сервера пустое")
                        dhcp_ok = False
                else:
                    log_msg("❌ Строка не начинается с 'dhcp-server'")
                    dhcp_ok = False
            else:
                log_msg("❌ Не найдено ни одной валидной строки с 'dhcp-server'")
                dhcp_ok = False
        else:
            log_msg("❌ Не удалось получить конфигурацию RTR")
            dhcp_ok = False

        # Клиент HQ-CLI должен получить динамический адрес
        dev_name_cli = DEVICE_NAMES["HQ-CLI"]
        ip_out, _, cmd_ip = ssh_exec(vm_ports["HQ-CLI"], "ip -br addr show", *get_srv_creds("HQ-CLI"))
        log_msg("[%s] Выполняется команда: ip -br addr show" % dev_name_cli)
        full_ip = (ip_out or "") + ("\n" + _ if _ else "")
        log_msg("[Вывод]\n%s\n" % full_ip)

        dyn_out, _, _ = ssh_exec(vm_ports["HQ-CLI"], "ip -4 addr show | grep dynamic", *get_srv_creds("HQ-CLI"))
        log_msg("[%s] Выполняется команда: ip -4 addr show | grep dynamic" % dev_name_cli)
        full_dyn = (dyn_out or "") + ("\n" + _ if _ else "")
        log_msg("[Вывод]\n%s\n" % full_dyn)
        if dyn_out and re.search(r'inet\s+\d+\.\d+\.\d+\.\d+', dyn_out):
            log_msg("✅ HQ-CLI: получен динамический IP-адрес по DHCP")
        else:
            log_msg("❌ HQ-CLI: динамический IP-адрес не получен")
            dhcp_ok = False
    else:
        log_msg("⚠️ HQ-CLI или HQ-RTR не найден")
        dhcp_ok = False
    if dhcp_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 10 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 10 не пройден")
    results["Пункт 10: DHCP"] = dhcp_ok

    # --- Пункт 11: DNS ---
    log_msg("\n📌 Пункт 11: DNS")
    dns_ok = True
    client_dev = "HQ-CLI"
    if client_dev not in vm_ports:
        log_msg("⚠️ Клиент HQ-CLI для DNS не найден")
        dns_ok = False
    else:
        dev_name_cli = DEVICE_NAMES[client_dev]
        for key, fqdn in FQDN_TABLE.items():
            log_msg("\n→ Проверка записи: %s" % fqdn)

            out, err, cmd = ssh_exec(vm_ports[client_dev], "nslookup %s" % fqdn, *get_srv_creds(client_dev))
            log_msg("[%s] Выполняется команда: nslookup %s" % (dev_name_cli, fqdn))
            full_out = (out or "") + ("\n" + err if err else "")
            log_msg("[Вывод]\n%s\n" % full_out)

            if not out or "Address:" not in out:
                log_msg("❌ Прямой запрос для %s не удался" % fqdn)
                dns_ok = False
                continue

            ip_match = re.search(r'Name:\s*%s\s*\n\s*Address:\s*(\d+\.\d+\.\d+\.\d+)' % re.escape(fqdn), out, re.IGNORECASE)
            if not ip_match:
                log_msg("❌ Не найден IP для %s" % fqdn)
                dns_ok = False
                continue

            ip_addr = ip_match.group(1)
            if ip_addr.startswith("127."):
                log_msg("⚠️ IP %s — loopback, пропускаем" % ip_addr)
                continue

            log_msg("✅ Получен IP: %s" % ip_addr)

            if fqdn in PTR_DEVICES:
                rev_out, rev_err, rev_cmd = ssh_exec(vm_ports[client_dev], "nslookup %s" % ip_addr, *get_srv_creds(client_dev))
                log_msg("[%s] Выполняется команда: nslookup %s" % (dev_name_cli, ip_addr))
                full_rev = (rev_out or "") + ("\n" + rev_err if rev_err else "")
                log_msg("[Вывод]\n%s\n" % full_rev)

                if not rev_out:
                    log_msg("❌ Обратный запрос для %s не удался" % ip_addr)
                    dns_ok = False
                    continue

                ptr_name = reverse_ip(ip_addr) + ".in-addr.arpa"
                reverse_pattern = r'%s\s+name\s*=\s*%s\.' % (re.escape(ptr_name), re.escape(fqdn))
                if re.search(reverse_pattern, rev_out, re.IGNORECASE):
                    log_msg("✅ Обратное разрешение для %s работает" % fqdn)
                else:
                    log_msg("❌ Обратное разрешение для %s не работает" % fqdn)
                    dns_ok = False
            else:
                log_msg("ℹ️ Для %s PTR не требуется — пропускаем обратную проверку" % fqdn)

    if dns_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 11 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 11 не пройден")
    results["Пункт 11: DNS"] = dns_ok

    # --- Пункт 12: Пинги по FQDN ---
    log_msg("\n📌 Пункт 12: Пинги по FQDN")
    ping_ok = True
    if "HQ-CLI" in vm_ports:
        dev_hq = DEVICE_NAMES["HQ-CLI"]
        for target in ["hq-srv.au-team.irpo", "br-srv.au-team.irpo"]:
            out, err, cmd = ssh_exec(vm_ports["HQ-CLI"], "ping -c 2 %s" % target, *get_srv_creds("HQ-CLI"))
            log_msg("[%s] Выполняется команда: ping -c 2 %s" % (dev_hq, target))
            full_out = (out or "") + ("\n" + err if err else "")
            log_msg("[Вывод]\n%s\n" % full_out)
            if not (out and "2 packets transmitted, 2 received" in out):
                ping_ok = False
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
        log_msg("⚠️ Устройство ISP не найдено")
        isp_ok = False
    else:
        dev_name = DEVICE_NAMES["ISP"]

        out, err, cmd = ssh_exec(vm_ports["ISP"], "hostname -f", *get_srv_creds("ISP"))
        log_msg("[%s] Выполняется команда: hostname -f" % dev_name)
        full_out = (out or "") + ("\n" + err if err else "")
        log_msg("[Вывод]\n%s\n" % full_out)
        if not (out and out.strip().lower() == "isp.au-team.irpo"):
            log_msg("⚠️ ISP: FQDN отличается от isp.au-team.irpo (некритично)")

        out2, err2, cmd2 = ssh_exec(vm_ports["ISP"], "ip -br addr show", *get_srv_creds("ISP"))
        log_msg("[%s] Выполняется команда: ip -br addr show" % dev_name)
        full_out2 = (out2 or "") + ("\n" + err2 if err2 else "")
        log_msg("[Вывод]\n%s\n" % full_out2)

        net_hq = ipaddress.IPv4Network(ISP_NET_HQ, strict=False)
        net_br = ipaddress.IPv4Network(ISP_NET_BR, strict=False)
        hq_iface_ok = False
        br_iface_ok = False
        if out2:
            for line in out2.strip().split('\n'):
                parts = line.split()
                if len(parts) >= 3 and '/' in parts[2]:
                    if parts[0] == 'lo':
                        continue
                    try:
                        ipi = ipaddress.IPv4Interface(parts[2])
                    except ValueError:
                        continue
                    if ipi.network.overlaps(net_hq):
                        hq_iface_ok = True
                        log_msg("✅ %s: %s — сеть в сторону HQ-RTR (%s)" % (parts[0], parts[2], ISP_NET_HQ))
                    if ipi.network.overlaps(net_br):
                        br_iface_ok = True
                        log_msg("✅ %s: %s — сеть в сторону BR-RTR (%s)" % (parts[0], parts[2], ISP_NET_BR))

        if not hq_iface_ok:
            log_msg("❌ ISP: интерфейс в сети %s не найден" % ISP_NET_HQ)
        if not br_iface_ok:
            log_msg("❌ ISP: интерфейс в сети %s не найден" % ISP_NET_BR)

        out3, err3, cmd3 = ssh_exec(vm_ports["ISP"], "ip route show", *get_srv_creds("ISP"))
        log_msg("[%s] Выполняется команда: ip route show" % dev_name)
        full_out3 = (out3 or "") + ("\n" + err3 if err3 else "")
        log_msg("[Вывод]\n%s\n" % full_out3)

        if hq_iface_ok and br_iface_ok:
            log_msg("✅ Пункт 13: ISP настроен корректно")
        else:
            isp_ok = False

    if isp_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 13 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 13 не пройден")
    results["Пункт 13: Проверка ISP"] = isp_ok

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
