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
    "BR-CLI": ("root", "toor"),
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
def validate_ip_in_allowed_ranges(ip_str, prefixlen):
    try:
        ip = ipaddress.IPv4Address(ip_str)
        if not (ip.is_private and not ip.is_loopback):
            return False, "❌ IP не из приватного диапазона"
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
                return True, "✅ В зоне '%s', маска /%d допустима" % (zone['name'], prefixlen)
            else:
                return False, "❌ В зоне '%s', но маска /%d слишком широкая" % (zone['name'], prefixlen)
    return False, "❌ IP не принадлежит ни одной разрешённой зоне"

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

    log_msg("🔍 Начало комплексной проверки ISP Block 2")

    DEVICE_NAMES = {
        "HQ-SRV": "hq-srv.au-team.irpo",
        "BR-SRV": "br-srv.au-team.irpo",
        "HQ-CLI": "hq-cli.au-team.irpo",
        "BR-CLI": "br-cli.au-team.irpo",
        "HQ-RTR": "hq-rtr",
        "BR-RTR": "br-rtr",
        "ISP": "isp.au-team.irpo",
    }

# Все DNS-записи
    FQDN_TABLE = {
        "HQ-SRV": "hq-srv.au-team.irpo",
        "BR-SRV": "br-srv.au-team.irpo",
        "HQ-CLI": "hq-cli.au-team.irpo",
        "BR-CLI": "br-cli.au-team.irpo",
        "HQ-RTR": "hq-rtr.au-team.irpo",
        "BR-RTR": "br-rtr.au-team.irpo",
        "BR-FW": "br-fw.au-team.irpo",
        "ISP-HQ": "docker.au-team.irpo",
        "ISP-BR": "web.au-team.irpo",
     }

# Устройства, для которых требуется PTR
    PTR_DEVICES = {
        "hq-srv.au-team.irpo",
        "hq-cli.au-team.irpo",
        "hq-rtr.au-team.irpo"
    }
# --- Пункт 1: FQDN ---
    log_msg("\n📌 Пункт 1: Имена устройств (FQDN)")
    names_ok = True
    devices_to_check = ["HQ-SRV", "BR-SRV", "HQ-CLI", "BR-CLI", "HQ-RTR", "BR-RTR"]

    for dev in devices_to_check:
        if dev not in vm_ports:
            log_msg("⚠️ %s: устройство не найдено" % DEVICE_NAMES[dev])
            names_ok = False
            continue

        expected_fqdn = FQDN_TABLE[dev]
        dev_name = DEVICE_NAMES[dev]

        if "RTR" in dev:
            # Роутеры: show hostname → вторая непустая строка
            out, err, cmd = rtr_exec(vm_ports[dev], *get_rtr_creds(dev), "show hostname")
            log_msg("[%s] Выполняется команда: show hostname" % dev_name)
            full_out = (out or "") + ("\n" + err if err else "")
            log_msg("[Вывод]\n%s\n" % full_out)

            actual = ""
            if out is not None:
                lines = [line.strip() for line in out.splitlines() if line.strip()]
                if len(lines) >= 2:
                    actual = lines[1].lower()  # вторая непустая строка
                elif len(lines) == 1:
                    actual = lines[0].lower()  # fallback

            if actual == dev.lower() or actual == expected_fqdn.lower():
                log_msg("✅ %s: имя хоста корректно (получено: %s)" % (dev_name, actual))
            else:
                log_msg("❌ %s: имя хоста некорректно (получено: %s)" % (dev_name, actual))
                names_ok = False

        else:
            # Серверы: hostname -f
            out, err, cmd = ssh_exec(vm_ports[dev], "hostname -f", *get_srv_creds(dev))
            log_msg("[%s] Выполняется команда: hostname -f" % dev_name)
            full_out = (out or "") + ("\n" + err if err else "")
            log_msg("[Вывод]\n%s\n" % full_out)
            actual = out.strip().lower() if out else ""
            if actual == expected_fqdn:
                log_msg("✅ %s: FQDN корректен" % dev_name)
            else:
                log_msg("❌ %s: FQDN некорректен (получено: %s)" % (dev_name, actual))
                names_ok = False

    if names_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 1 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 1 не пройден")
    results["Пункт 1: FQDN"] = names_ok

    # --- Пункт 2: IPv4-адресация ---
    log_msg("\n📌 Пункт 2: IPv4-адресация")
    ip_ok = True
    for dev in ["HQ-SRV", "BR-SRV", "HQ-CLI", "BR-CLI"]:
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
                ip, mask = parts[2].split('/')
                ok, msg = validate_ip_in_allowed_ranges(ip, int(mask))
                log_msg("  → %s: %s" % (parts[0], msg))
                if ok:
                    valid_found = True
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
        dev_name = DEVICE_NAMES[srv]
        out, err, cmd = ssh_exec(vm_ports[srv], "id -u sshuser", "sshuser", "P@ssw0rd")
        log_msg("[%s] Выполняется команда: id -u sshuser" % dev_name)
        full_out = (out or "") + ("\n" + err if err else "")
        log_msg("[Вывод]\n%s\n" % full_out)
        uid_ok = out and out.strip() == "2026"

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

    # --- Пункт 4: Коммутация OVS ---
    log_msg("\n📌 Пункт 4: Коммутация OVS")
    ovs_ok = True
    if "HQ-SW" in vm_ports:
        dev_name = "hq-sw.au-team.irpo"
        out, err, cmd = ssh_exec(vm_ports["HQ-SW"], "ovs-vsctl show", "root", "toor")
        log_msg("[%s] Выполняется команда: ovs-vsctl show" % dev_name)
        full_out = (out or "") + ("\n" + err if err else "")
        log_msg("[Вывод]\n%s\n" % full_out)
        if out:
            checks = [
                "tag: 100" in out and "ens4" in out,
                "tag: 200" in out and "ens5" in out,
                "trunks: [100, 200, 999]" in out and "ens3" in out
            ]
            if not all(checks):
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
        config_content = None
        for path in ["/etc/openssh/sshd_config", "/etc/ssh/sshd_config"]:
            out, err, cmd = ssh_exec(vm_ports[srv], "cat %s" % path, *get_srv_creds(srv))
            if out is not None and "Permission denied" not in (err or "") and "No such file" not in (err or ""):
                config_content = out
                log_msg("[%s] Выполняется команда: cat %s" % (dev_name, path))
                full_out = (out or "") + ("\n" + err if err else "")
                log_msg("[Вывод]\n%s\n" % full_out)
                break
        if config_content is None:
            ssh_ok = False
            continue
        max_auth = any('MaxAuthTries' in line and '2' in line and not line.strip().startswith('#') for line in config_content.split('\n'))
        banner_ok = False
        for line in config_content.split('\n'):
            if 'Banner' in line and not line.strip().startswith('#'):
                parts = line.split()
                if len(parts) >= 2:
                    banner_path = parts[1]
                    banner_out, _, cmd_banner = ssh_exec(vm_ports[srv], "cat %s" % banner_path, *get_srv_creds(srv))
                    log_msg("[%s] Выполняется команда: cat %s" % (dev_name, banner_path))
                    full_banner = (banner_out or "") + ("\n" + _ if _ else "")
                    log_msg("[Вывод]\n%s\n" % full_banner)
                    if banner_out and "authorized access only" in banner_out.lower():
                        banner_ok = True
                        break
        if not (max_auth and banner_ok):
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

    # --- Пункт 7: GRE-туннель ---
    log_msg("\n📌 Пункт 7: GRE-туннель")
    gre_ok = True
    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports:
            continue
        dev_name = DEVICE_NAMES[rtr]
        out1, err1, cmd1 = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show interface tunnel.0")
        log_msg("[%s] Выполняется команда: show interface tunnel.0" % dev_name)
        full_out1 = (out1 or "") + ("\n" + err1 if err1 else "")
        log_msg("[Вывод]\n%s\n" % full_out1)
        out2, err2, cmd2 = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show running-config | include gre")
        log_msg("[%s] Выполняется команда: show running-config | include gre" % dev_name)
        full_out2 = (out2 or "") + ("\n" + err2 if err2 else "")
        log_msg("[Вывод]\n%s\n" % full_out2)

        target = "10.10.10.2" if rtr == "HQ-RTR" else "10.10.10.1"
        ping_out, ping_err, ping_cmd = rtr_exec_with_enable(vm_ports[rtr], *get_rtr_creds(rtr), "ping %s count 3" % target)
        log_msg("[%s] Выполняется команда: ping %s count 3" % (dev_name, target))
        full_ping = (ping_out or "") + ("\n" + ping_err if ping_err else "")
        log_msg("[Вывод]\n%s\n" % full_ping)

        cond1 = out1 is not None and "is up" in out1
        cond2 = out2 is not None and "gre" in out2
        cond3 = ping_out is not None and "3 packets transmitted, 3 received" in ping_out

        if cond1 and cond2 and cond3:
            log_msg("✅ %s: GRE-туннель работает" % dev_name)
        else:
            log_msg("❌ %s: GRE-туннель не работает" % dev_name)
            gre_ok = False
    if gre_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 7 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 7 не пройден")
    results["Пункт 7: GRE-туннель"] = gre_ok

    # --- Пункт 8: OSPF ---
    log_msg("\n📌 Пункт 8: OSPF")
    ospf_ok = True
    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports:
            continue
        dev_name = DEVICE_NAMES[rtr]
        out1, err1, cmd1 = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show ip protocols ospf")
        log_msg("[%s] Выполняется команда: show ip protocols ospf" % dev_name)
        full_out1 = (out1 or "") + ("\n" + err1 if err1 else "")
        log_msg("[Вывод]\n%s\n" % full_out1)
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
        has_route = out2 is not None and ("10.10.100.0" in out2 or "10.20.20.0" in out2)

        valid_tunnel_neighbor = False
        if out3 is not None:
            for line in out3.split('\n'):
                if 'tunnel.0' in line and ('Full/DR' in line or 'Full/Backup' in line):
                    valid_tunnel_neighbor = True
                    break

        if auth_ok and has_route and valid_tunnel_neighbor:
            log_msg("✅ %s: OSPF настроен корректно" % dev_name)
        else:
            log_msg("❌ %s: OSPF настроен некорректно" % dev_name)
            ospf_ok = False
    if ospf_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 8 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 8 не пройден")
    results["Пункт 8: OSPF"] = ospf_ok

    # --- Пункт 9: NAT ---
    log_msg("\n📌 Пункт 9: NAT")
    nat_ok = True
    for cli in ["HQ-CLI", "BR-CLI"]:
        if cli not in vm_ports:
            continue
        dev_name = DEVICE_NAMES[cli]
        out, err, cmd = ssh_exec(vm_ports[cli], "ping -c 2 1.1.1.1", *get_srv_creds(cli))
        log_msg("[%s] Выполняется команда: ping -c 2 1.1.1.1" % dev_name)
        full_out = (out or "") + ("\n" + err if err else "")
        log_msg("[Вывод]\n%s\n" % full_out)
        if not (out and "2 packets transmitted, 2 received" in out):
            nat_ok = False
    if nat_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 9 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 9 не пройден")
    results["Пункт 9: NAT"] = nat_ok

    # --- Пункт 10: DHCP ---
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

        if dhcp_server_name:
            out2, err2, cmd2 = rtr_exec(vm_ports["HQ-RTR"], *get_rtr_creds("HQ-RTR"), "show dhcp-server %s" % dhcp_server_name)
            log_msg("[%s] Выполняется команда: show dhcp-server %s" % (dev_name_rtr, dhcp_server_name))
            full_out2 = (out2 or "") + ("\n" + err2 if err2 else "")
            log_msg("[Вывод]\n%s\n" % full_out2)

            # 🔍 Ищем ЛЮБОЙ диапазон IP вида X.X.X.X-X.X.X.X
            pool_in_network = False
            if out2:
                # Ищем первую строку с диапазоном
                match = re.search(r'(\d+\.\d+\.\d+\.\d+)-(\d+\.\d+\.\d+\.\d+)', out2)
                if match:
                    start_ip = match.group(1)
                    end_ip = match.group(2)
                    try:
                        start = ipaddress.IPv4Address(start_ip)
                        end = ipaddress.IPv4Address(end_ip)
                        network = ipaddress.IPv4Network("10.10.200.0/27", strict=False)

                        if start in network and end in network:
                            log_msg("✅ Найден пул %s-%s из сети 10.10.200.0/27" % (start_ip, end_ip))
                            pool_in_network = True
                        else:
                            log_msg("❌ Пул %s-%s не входит в 10.10.200.0/27" % (start_ip, end_ip))
                            dhcp_ok = False
                    except Exception as e:
                        log_msg("❌ Ошибка при проверке пула: %s" % e)
                        dhcp_ok = False
                else:
                    log_msg("❌ Не найден диапазон IP вида X.X.X.X-X.X.X.X")
                    dhcp_ok = False
            else:
                log_msg("❌ Не удалось получить конфигурацию сервера")
                dhcp_ok = False

            if pool_in_network:
                dev_name_cli = DEVICE_NAMES["HQ-CLI"]
                ip_out, _, cmd_ip = ssh_exec(vm_ports["HQ-CLI"], "ip addr show ens3", *get_srv_creds("HQ-CLI"))
                log_msg("[%s] Выполняется команда: ip addr show ens3" % dev_name_cli)
                full_ip = (ip_out or "") + ("\n" + _ if _ else "")
                log_msg("[Вывод]\n%s\n" % full_ip)

                if ip_out:
                    match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+).*dynamic', ip_out)
                    if match:
                        ip_addr = match.group(1)
                        try:
                            ip_obj = ipaddress.IPv4Address(ip_addr)
                            network = ipaddress.IPv4Network("10.10.200.0/27", strict=False)
                            if ip_obj in network:
                                log_msg("✅ HQ-CLI: имеет динамический IP из пула DHCP (10.10.200.0/27) на ens3")
                            else:
                                log_msg("❌ HQ-CLI: IP не из пула 10.10.200.0/27")
                                dhcp_ok = False
                        except:
                            log_msg("❌ HQ-CLI: некорректный IP на ens3")
                            dhcp_ok = False
                    else:
                        log_msg("❌ HQ-CLI: интерфейс ens3 не имеет динамического IP-адреса")
                        dhcp_ok = False
                else:
                    log_msg("❌ HQ-CLI: не удалось получить информацию об IP")
                    dhcp_ok = False
            else:
                dhcp_ok = False
        else:
            log_msg("❌ HQ-RTR: DHCP-сервер не найден")
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
    client_dev = "HQ-CLI" if "HQ-CLI" in vm_ports else "BR-CLI"
    if client_dev not in vm_ports:
        log_msg("⚠️ Клиент для DNS не найден")
        dns_ok = False
    else:
        dev_name_cli = DEVICE_NAMES[client_dev]
        for key, fqdn in FQDN_TABLE.items():
            # Пропускаем BR-FW, если его нет в топологии
            if key == "BR-FW" and "BR-FW" not in vm_ports:
                continue

            log_msg("\n→ Проверка записи: %s" % fqdn)
            
            # === Прямой запрос (A-запись) ===
            out, err, cmd = ssh_exec(vm_ports[client_dev], "nslookup %s" % fqdn, *get_srv_creds(client_dev))
            log_msg("[%s] Выполняется команда: nslookup %s" % (dev_name_cli, fqdn))
            full_out = (out or "") + ("\n" + err if err else "")
            log_msg("[Вывод]\n%s\n" % full_out)

            if not out or "Address:" not in out:
                log_msg("❌ Прямой запрос для %s не удался" % fqdn)
                dns_ok = False
                continue

            # Извлекаем IP
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

            # === Обратный запрос (PTR) — только если требуется ===
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
    if "HQ-CLI" in vm_ports and "BR-CLI" in vm_ports:
        dev_hq = DEVICE_NAMES["HQ-CLI"]
        dev_br = DEVICE_NAMES["BR-CLI"]
        for target in ["br-srv.au-team.irpo", "br-cli.au-team.irpo"]:
            out, err, cmd = ssh_exec(vm_ports["HQ-CLI"], "ping -c 2 %s" % target, *get_srv_creds("HQ-CLI"))
            log_msg("[%s] Выполняется команда: ping -c 2 %s" % (dev_hq, target))
            full_out = (out or "") + ("\n" + err if err else "")
            log_msg("[Вывод]\n%s\n" % full_out)
            if not (out and "2 packets transmitted, 2 received" in out):
                ping_ok = False

        for target in ["hq-srv.au-team.irpo", "hq-cli.au-team.irpo"]:
            out, err, cmd = ssh_exec(vm_ports["BR-CLI"], "ping -c 2 %s" % target, *get_srv_creds("BR-CLI"))
            log_msg("[%s] Выполняется команда: ping -c 2 %s" % (dev_br, target))
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
        
        # Проверка FQDN
        out, err, cmd = ssh_exec(vm_ports["ISP"], "hostname -f", *get_srv_creds("ISP"))
        log_msg("[%s] Выполняется команда: hostname -f" % dev_name)
        full_out = (out or "") + ("\n" + err if err else "")
        log_msg("[Вывод]\n%s\n" % full_out)
        if not (out and out.strip().lower() == "isp.au-team.irpo"):
            log_msg("❌ ISP: FQDN некорректен")
            isp_ok = False
        else:
            log_msg("✅ ISP: FQDN корректен")

        # Проверка IP-адресов
        out2, err2, cmd2 = ssh_exec(vm_ports["ISP"], "ip -br addr show", *get_srv_creds("ISP"))
        log_msg("[%s] Выполняется команда: ip -br addr show" % dev_name)
        full_out2 = (out2 or "") + ("\n" + err2 if err2 else "")
        log_msg("[Вывод]\n%s\n" % full_out2)

        interfaces = {}
        if out2:
            for line in out2.strip().split('\n'):
                parts = line.split()
                if len(parts) >= 3 and '/' in parts[2]:
                    iface = parts[0]
                    ip_with_mask = parts[2]
                    interfaces[iface] = ip_with_mask

        # Проверка ens4: 172.16.1.0/28
        ens4_ok = False
        if 'ens4' in interfaces:
            ip_with_mask = interfaces['ens4']
            try:
                ip_obj = ipaddress.IPv4Interface(ip_with_mask)
                network = ipaddress.IPv4Network("172.16.1.0/28", strict=False)
                if ip_obj.network == network:
                    ens4_ok = True
                    log_msg("✅ ens4: %s — корректно (172.16.1.0/28)" % ip_with_mask)
                else:
                    log_msg("❌ ens4: %s — не входит в 172.16.1.0/28" % ip_with_mask)
            except:
                log_msg("❌ ens4: некорректный IP-адрес")
        else:
            log_msg("❌ ens4: интерфейс не найден")

        # Проверка ens5: 172.16.2.0/28
        ens5_ok = False
        if 'ens5' in interfaces:
            ip_with_mask = interfaces['ens5']
            try:
                ip_obj = ipaddress.IPv4Interface(ip_with_mask)
                network = ipaddress.IPv4Network("172.16.2.0/28", strict=False)
                if ip_obj.network == network:
                    ens5_ok = True
                    log_msg("✅ ens5: %s — корректно (172.16.2.0/28)" % ip_with_mask)
                else:
                    log_msg("❌ ens5: %s — не входит в 172.16.2.0/28" % ip_with_mask)
            except:
                log_msg("❌ ens5: некорректный IP-адрес")
        else:
            log_msg("❌ ens5: интерфейс не найден")

        # Вывод маршрутов (без проверки)
        out3, err3, cmd3 = ssh_exec(vm_ports["ISP"], "ip route show", *get_srv_creds("ISP"))
        log_msg("[%s] Выполняется команда: ip route show" % dev_name)
        full_out3 = (out3 or "") + ("\n" + err3 if err3 else "")
        log_msg("[Вывод]\n%s\n" % full_out3)

        # Итог
        if ens4_ok and ens5_ok:
            log_msg("✅ Пункт 13: ISP настроен корректно")
        else:
            log_msg("❌ Пункт 13: ISP настроен некорректно")
            isp_ok = False

    if isp_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 13 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 13 не пройден")
    results["Пункт 13: Проверка ISP"] = isp_ok

    # --- ИТОГОВЫЙ ОТЧЁТ ---
    log_msg("\n📊 ИТОГОВЫЙ ОТЧЁТ:")
    log_msg("="*60)
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
