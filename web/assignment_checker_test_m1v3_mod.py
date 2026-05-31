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

# ========== ПАРАМЕТРЫ ВАРИАНТА (В3) ==========
VLAN_SRV = 113
VLAN_CLI = 213
VLAN_MGMT = 813
SSHUSER_UID = "2013"
SSH_PORT = "2013"
ISP_NET_HQ = "172.16.50.0/28"
ISP_NET_BR = "172.16.60.0/28"

# ========== УЧЁТНЫЕ ДАННЫЕ ==========
SRV_CREDENTIALS = {
    "HQ-SRV": ("root", "toor"), "BR-SRV": ("root", "toor"),
    "HQ-CLI": ("root", "toor"), "ISP": ("root", "toor"),
}
RTR_CREDENTIALS = {"HQ-RTR": ("admin", "admin"), "BR-RTR": ("admin", "admin")}

def get_srv_creds(name): return SRV_CREDENTIALS.get(name, ("root", "toor"))
def get_rtr_creds(name): return RTR_CREDENTIALS.get(name, ("admin", "admin"))

# ========== SSH / RTR ==========
def ssh_exec(ssh_port, command, username='root', password='toor', timeout=30):
    if not ssh_port or ssh_port == "N/A":
        return None, "❌ Порт SSH недоступен: %s" % ssh_port, command
    try:
        ssh_cmd = ["sshpass", "-p", password, "ssh", "-o", "StrictHostKeyChecking=no",
                   "-o", "UserKnownHostsFile=/dev/null", "-tt", "-p", ssh_port,
                   "%s@localhost" % username, command]
        process = subprocess.Popen(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        stdout, stderr = process.communicate(timeout=timeout)
        return stdout, stderr, command
    except Exception as e:
        return None, "❌ Ошибка: %s" % str(e), command

def rtr_exec(port, username, password, command, timeout=30):
    if not PEXPECT_AVAILABLE: return None, "pexpect не установлен", command
    try:
        child = pexpect.spawn("ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p %s %s@localhost" % (port, username),
                              timeout=timeout, encoding='utf-8', codec_errors='ignore')
        child.expect(r"[Pp]assword:", timeout=60); child.sendline(password)
        child.expect(r'\S+>', timeout=60); child.sendline("terminal length 0")
        child.expect(r'\S+>', timeout=30); child.sendline(command)
        child.expect(r'\S+>', timeout=timeout)
        output = child.before.strip(); child.sendline("exit"); child.close()
        return re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])').sub('', output), "", command
    except pexpect.TIMEOUT: return None, "Таймаут", command
    except Exception as e: return None, "Ошибка: %s" % str(e), command

def rtr_exec_with_enable(port, username, password, command, timeout=30):
    if not PEXPECT_AVAILABLE: return None, "pexpect не установлен", command
    try:
        child = pexpect.spawn("ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p %s %s@localhost" % (port, username),
                              timeout=timeout, encoding='utf-8', codec_errors='ignore')
        child.expect(r"[Pp]assword:", timeout=60); child.sendline(password)
        child.expect(r'\S+>', timeout=60); child.sendline("en")
        child.expect(r'\S+#', timeout=60); child.sendline("terminal length 0")
        child.expect(r'\S+#', timeout=30); child.sendline(command)
        child.expect(r'\S+#', timeout=timeout)
        output = child.before.strip(); child.sendline("exit"); child.sendline("exit"); child.close()
        return re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])').sub('', output), "", command
    except pexpect.TIMEOUT: return None, "Таймаут", command
    except Exception as e: return None, "Ошибка: %s" % str(e), command

# ========== ВСПОМОГАТЕЛЬНЫЕ ==========
SUBNET_CONSTRAINTS = {
    "HQ-SRV": (27, 30), "HQ-CLI": (16, 28), "BR-SRV": (28, 30),
}

def subnet_ok(dev, ip_str, prefixlen):
    try: ip = ipaddress.IPv4Address(ip_str)
    except: return False
    if not ip.is_private or ip.is_loopback or ip.is_link_local: return False
    c = SUBNET_CONSTRAINTS.get(dev)
    if not c: return True
    return c[0] <= prefixlen <= c[1]

def safe_log_output(log_lines, prefix, output, error=""):
    log_lines.append("%s:\n%s\n" % (prefix, (output or "") + ("\n" + error if error else "")))

# ========== ГЛАВНАЯ ==========
def run_full_assignment_check(vm_ports):
    POINTS = 0.0
    MAX_POINTS = 25.0
    log_lines = []
    results = {}

    def log_msg(msg):
        log_lines.append(msg); print(msg)

    def award(name, score, max_score, details=""):
        nonlocal POINTS; POINTS += score
        results[name] = {"score": score, "max": max_score}
        icon = "✅" if score == max_score else ("⚠️" if score > 0 else "❌")
        log_msg("%s %s: %d/%d%s" % (icon, name, score, max_score, " — " + details if details else ""))

    log_msg("\n🔍 SSH-порты:"); [log_msg("  %s: %s" % (d, p)) for d, p in sorted(vm_ports.items())]
    log_msg("\n🔍 Модуль 1 (M1-V3), КОД 09.02.06-1-2026-ПУ\n")

    FQDN = {"HQ-RTR": "hq-rtr.au-team.irpo", "BR-RTR": "br-rtr.au-team.irpo",
            "HQ-SRV": "hq-srv.au-team.irpo", "HQ-CLI": "hq-cli.au-team.irpo",
            "BR-SRV": "br-srv.au-team.irpo", "ISP-HQ": "docker.au-team.irpo", "ISP-BR": "web.au-team.irpo"}
    PTR_DEVICES = {"hq-rtr.au-team.irpo", "hq-srv.au-team.irpo", "hq-cli.au-team.irpo"}

    # ==== КО: Отчёт (max 1) — ручная проверка ====
    award("КО: Отчёт (ГОСТ Р 7.0.97-2016)", 0, 1, "требует ручной проверки эксперта")

    # ==== КО: Временная зона (max 1) ====
    log_msg("\n📌 КО: Временная зона")
    tz_ok, tz_total = 0, 0
    for srv in ["HQ-SRV", "BR-SRV", "HQ-CLI"]:
        if srv not in vm_ports: continue
        tz_total += 1
        out, _, _ = ssh_exec(vm_ports[srv], "timedatectl show -p Timezone --value 2>/dev/null || timedatectl status", *get_srv_creds(srv))
        safe_log_output(log_lines, "[%s] tz" % srv, out)
        if out and "Moscow" in out: tz_ok += 1
    award("КО: Временная зона", 1 if (tz_total and tz_ok >= tz_total) else 0, 1, "%d/%d серверов" % (tz_ok, tz_total))

    # ==== КО: Подинтерфейсы HQ-RTR (max 2) ====
    log_msg("\n📌 КО: Подинтерфейсы HQ-RTR")
    subif_score = 0
    if "HQ-RTR" in vm_ports:
        o1, _, _ = rtr_exec(vm_ports["HQ-RTR"], *get_rtr_creds("HQ-RTR"), "show ip interface brief")
        safe_log_output(log_lines, "[HQ-RTR] interfaces", o1)
        o2, _, _ = rtr_exec(vm_ports["HQ-RTR"], *get_rtr_creds("HQ-RTR"), "show running-config | include ge1/")
        safe_log_output(log_lines, "[HQ-RTR] ge1/", o2)
        subifs = set(re.findall(r'ge1/\d+\.\d+', (o1 or "") + (o2 or "")))
        subif_score = 2 if len(subifs) >= 3 else (1 if len(subifs) >= 1 else 0)
    award("КО: Подинтерфейсы HQ-RTR", subif_score, 2)

    # ==== КО: ISP маршрутизация + NAT (max 2) ====
    log_msg("\n📌 КО: ISP маршрутизация + NAT")
    isp_score = 0
    if "ISP" in vm_ports:
        out_ip, _, _ = ssh_exec(vm_ports["ISP"], "ip -br addr show", *get_srv_creds("ISP"))
        safe_log_output(log_lines, "[ISP] ip", out_ip)
        out_nat, _, _ = ssh_exec(vm_ports["ISP"], "iptables -t nat -S 2>/dev/null; nft list ruleset 2>/dev/null", *get_srv_creds("ISP"))
        safe_log_output(log_lines, "[ISP] nat", out_nat)
        nets_hit = set()
        if out_ip:
            for line in out_ip.split('\n'):
                parts = line.split()
                if len(parts) >= 3 and '/' in parts[2] and parts[0] != 'lo':
                    try:
                        ipi = ipaddress.IPv4Interface(parts[2])
                        if ipi.network.overlaps(ipaddress.IPv4Network(ISP_NET_HQ, strict=False)): nets_hit.add("hq")
                        if ipi.network.overlaps(ipaddress.IPv4Network(ISP_NET_BR, strict=False)): nets_hit.add("br")
                    except: pass
        has_nat = out_nat and re.search(r'masquerade|MASQUERADE|snat|SNAT', out_nat, re.IGNORECASE)
        if len(nets_hit) == 2 and has_nat: isp_score = 2
        elif len(nets_hit) >= 1: isp_score = 1
    award("КО: ISP маршрутизация+NAT", isp_score, 2)

    # ==== КО: DHCP (max 2) ====
    log_msg("\n📌 КО: DHCP на HQ-RTR")
    dhcp_score = 0
    if "HQ-RTR" in vm_ports and "HQ-CLI" in vm_ports:
        out, _, _ = rtr_exec(vm_ports["HQ-RTR"], *get_rtr_creds("HQ-RTR"), "show running-config | include dhcp-server")
        safe_log_output(log_lines, "[HQ-RTR] dhcp", out)
        server_ok = bool(out and re.search(r'^\s*dhcp-server\s+\S+', out, re.MULTILINE))
        dyn_out, _, _ = ssh_exec(vm_ports["HQ-CLI"], "ip -4 addr show | grep dynamic", *get_srv_creds("HQ-CLI"))
        safe_log_output(log_lines, "[HQ-CLI] dynamic", dyn_out)
        client_ok = bool(dyn_out and re.search(r'inet\s+\d+\.\d+\.\d+\.\d+', dyn_out))
        dhcp_score = (1 if server_ok else 0) + (1 if client_ok else 0)
    award("КО: DHCP", dhcp_score, 2)

    # ==== КО: Удалённый доступ SSH (max 1) ====
    log_msg("\n📌 КО: Безопасный SSH")
    ssh_ok_count, ssh_total = 0, 0
    for srv in ["HQ-SRV", "BR-SRV"]:
        if srv not in vm_ports: continue
        ssh_total += 1
        cfg, _, _ = ssh_exec(vm_ports[srv], "sshd -T 2>/dev/null", *get_srv_creds(srv))
        safe_log_output(log_lines, "[%s] sshd -T" % srv, cfg)
        if not cfg: continue
        ll = [l.strip().lower() for l in cfg.split('\n')]
        checks = 0
        if any(l.startswith('port ') and SSH_PORT in l for l in ll): checks += 1
        if any(l.startswith('maxauthtries') and '2' in l for l in ll): checks += 1
        if any(l.startswith('allowusers') and 'sshuser' in l for l in ll): checks += 1
        for line in cfg.split('\n'):
            if line.strip().lower().startswith('banner '):
                bp = line.strip().split(None, 1)[1]
                if bp and bp != "none":
                    bo, _, _ = ssh_exec(vm_ports[srv], "cat %s 2>/dev/null" % bp, *get_srv_creds(srv))
                    if bo and "authorized access only" in bo.lower(): checks += 1
                break
        if checks >= 4: ssh_ok_count += 1
    award("КО: Безопасный SSH", 1 if (ssh_total and ssh_ok_count >= ssh_total) else 0, 1, "%d/%d серверов" % (ssh_ok_count, ssh_total))

    # ==== КО: DNS (max 3) ====
    log_msg("\n📌 КО: DNS")
    dns_a_ok, dns_a_total, dns_ptr_ok, dns_ptr_total, fwd_ok = 0, 0, 0, 0, False
    if "HQ-CLI" in vm_ports:
        cli = "HQ-CLI"
        for key, fqdn in FQDN.items():
            dns_a_total += 1
            out, _, _ = ssh_exec(vm_ports[cli], "nslookup %s" % fqdn, *get_srv_creds(cli))
            safe_log_output(log_lines, "[%s] nslookup %s" % (cli, fqdn), out)
            if out and "Address:" in out:
                m = re.search(r'Name:\s*%s\s*\n\s*Address:\s*(\d+\.\d+\.\d+\.\d+)' % re.escape(fqdn), out, re.IGNORECASE)
                if m and not m.group(1).startswith("127."):
                    dns_a_ok += 1
                    if fqdn in PTR_DEVICES:
                        dns_ptr_total += 1
                        ro, _, _ = ssh_exec(vm_ports[cli], "nslookup %s" % m.group(1), *get_srv_creds(cli))
                        safe_log_output(log_lines, "[%s] PTR %s" % (cli, m.group(1)), ro)
                        rip = '.'.join(reversed(m.group(1).split('.')))
                        if ro and re.search(r'%s\.in-addr\.arpa\s+name\s*=\s*%s\.' % (re.escape(rip), re.escape(fqdn)), ro, re.IGNORECASE):
                            dns_ptr_ok += 1
        fo, _, _ = ssh_exec(vm_ports[cli], "nslookup ya.ru", *get_srv_creds(cli))
        safe_log_output(log_lines, "[%s] fwd ya.ru" % cli, fo)
        fwd_ok = bool(fo and "Address:" in fo and "NXDOMAIN" not in fo)
    log_msg("ℹ️ A: %d/%d, PTR: %d/%d, forward: %s" % (dns_a_ok, dns_a_total, dns_ptr_ok, dns_ptr_total, fwd_ok))
    if dns_a_total and dns_a_ok >= dns_a_total and dns_ptr_ok >= dns_ptr_total and fwd_ok: award("КО: DNS", 3, 3)
    elif dns_a_total and dns_a_ok >= dns_a_total - 1: award("КО: DNS", 2, 3)
    elif dns_a_ok >= 1: award("КО: DNS", 1, 3)
    else: award("КО: DNS", 0, 3)

    # ==== КО: IP-туннель (max 2) ====
    log_msg("\n📌 КО: IP-туннель")
    tun_ok = 0
    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports: continue
        o1, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show interface tunnel.0")
        safe_log_output(log_lines, "[%s] tunnel" % rtr, o1)
        o2, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show running-config | include tunnel")
        safe_log_output(log_lines, "[%s] tun cfg" % rtr, o2)
        cfg = ((o1 or "") + "\n" + (o2 or "")).lower()
        if (o1 and "is up" in o1.lower()) and ("gre" in cfg or "ipip" in cfg or "ip-in-ip" in cfg): tun_ok += 1
    award("КО: IP-туннель", 2 if tun_ok == 2 else (1 if tun_ok == 1 else 0), 2)

    # ==== КО: NAT в офисах (max 2) ====
    log_msg("\n📌 КО: Выход в Интернет")
    inet_ok, inet_total = 0, 0
    for host in ["HQ-CLI", "HQ-SRV"]:
        if host not in vm_ports: continue
        inet_total += 1
        out, _, _ = ssh_exec(vm_ports[host], "ping -c 2 1.1.1.1", *get_srv_creds(host))
        safe_log_output(log_lines, "[%s] ping" % host, out)
        if out and "2 received" in out: inet_ok += 1
    award("КО: Выход в Интернет (NAT)", 2 if (inet_total and inet_ok >= inet_total) else (1 if inet_ok >= 1 else 0), 2)

    # ==== КО: Разделение на подсети (max 2) ====
    log_msg("\n📌 КО: Подсети (VLSM)")
    sub_ok, sub_total = 0, 0
    for dev in ["HQ-SRV", "BR-SRV", "HQ-CLI"]:
        if dev not in vm_ports: continue
        sub_total += 1
        out, _, _ = ssh_exec(vm_ports[dev], "ip -br addr show", *get_srv_creds(dev))
        safe_log_output(log_lines, "[%s] ip" % dev, out)
        if out:
            for line in out.strip().split('\n'):
                parts = line.split()
                if len(parts) >= 3 and '/' in parts[2] and parts[0] != 'lo':
                    ip_s, mask = parts[2].split('/')
                    if not ip_s.startswith('169.') and not ip_s.startswith('127.') and subnet_ok(dev, ip_s, int(mask)):
                        sub_ok += 1; break
    award("КО: Подсети (VLSM)", 2 if (sub_total and sub_ok >= sub_total) else (1 if sub_ok >= 2 else 0), 2, "%d/%d" % (sub_ok, sub_total))

    # ==== КО: IP-адреса и имена (max 2) ====
    log_msg("\n📌 КО: IP-адреса и имена устройств")
    names_ok, names_total = 0, 0
    for dev in ["HQ-SRV", "BR-SRV", "HQ-CLI", "HQ-RTR", "BR-RTR", "ISP"]:
        if dev not in vm_ports: continue
        names_total += 1
        expected = "isp.au-team.irpo" if dev == "ISP" else FQDN.get(dev, "")
        if "RTR" in dev:
            out, _, _ = rtr_exec(vm_ports[dev], *get_rtr_creds(dev), "show hostname")
            safe_log_output(log_lines, "[%s] hostname" % dev, out)
            actual = ""
            if out:
                lines = [l.strip() for l in out.splitlines() if l.strip()]
                actual = lines[1].lower() if len(lines) >= 2 else (lines[0].lower() if lines else "")
            if actual == dev.lower() or actual == expected.lower(): names_ok += 1
        else:
            out, _, _ = ssh_exec(vm_ports[dev], "hostname -f", *get_srv_creds(dev))
            safe_log_output(log_lines, "[%s] hostname" % dev, out)
            if out and out.strip().lower() == expected: names_ok += 1
    award("КО: IP-адреса и имена", 2 if (names_total and names_ok >= names_total) else (1 if names_ok >= 2 else 0), 2, "%d/%d" % (names_ok, names_total))

    # ==== КО: Динамическая маршрутизация OSPF (max 3) ====
    log_msg("\n📌 КО: OSPF")
    ospf_pts = 0
    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports: continue
        o1, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show running-config | include ospf")
        safe_log_output(log_lines, "[%s] ospf cfg" % rtr, o1)
        o2, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show ip route")
        safe_log_output(log_lines, "[%s] routes" % rtr, o2)
        o3, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show ip ospf neighbor")
        safe_log_output(log_lines, "[%s] neighbor" % rtr, o3)
        if o1 and "authentication message-digest" in o1 and "md5" in o1: ospf_pts += 1
        if o2 and re.search(r'^\s*O\b', o2, re.MULTILINE): ospf_pts += 1
        if o3 and any('tunnel.0' in l and 'Full' in l for l in o3.split('\n')): ospf_pts += 1
    # 6 возможных признаков (3 на каждый роутер) → 3 балла
    award("КО: OSPF", 3 if ospf_pts >= 6 else (2 if ospf_pts >= 4 else (1 if ospf_pts >= 1 else 0)), 3)

    # ==== КО: Локальные учётные записи (max 2) ====
    log_msg("\n📌 КО: Локальные учётные записи")
    acc_ok, acc_total = 0, 0
    for srv in ["HQ-SRV", "BR-SRV"]:
        if srv not in vm_ports: continue
        acc_total += 1
        out, _, _ = ssh_exec(vm_ports[srv], "id -u sshuser", "sshuser", "P@ssw0rd")
        safe_log_output(log_lines, "[%s] id sshuser" % srv, out)
        out2, _, _ = ssh_exec(vm_ports[srv], "sudo -n id", "sshuser", "P@ssw0rd")
        safe_log_output(log_lines, "[%s] sudo" % srv, out2)
        if (out and out.strip() == SSHUSER_UID) and (out2 and "uid=0(root)" in out2): acc_ok += 1
    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports: continue
        acc_total += 1
        out, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show users localdb")
        safe_log_output(log_lines, "[%s] users" % rtr, out)
        if out and "net_admin" in out: acc_ok += 1
    award("КО: Локальные учётные записи", 2 if (acc_total and acc_ok >= acc_total) else (1 if acc_ok >= acc_total - 1 and acc_total else 0), 2, "%d/%d" % (acc_ok, acc_total))

    # ==== ИТОГО ====
    log_msg("\n📊 ИТОГО (M1-V1, КОД 09.02.06-1-2026-ПУ):"); log_msg("=" * 60)
    for item, d in results.items():
        icon = "✅" if d["score"] == d["max"] else ("⚠️" if d["score"] > 0 else "❌")
        log_msg("%s %s: %d/%d" % (icon, item, d["score"], d["max"]))
    log_msg("\n📈 Набрано %.1f из %.1f баллов" % (POINTS, MAX_POINTS))
    return POINTS, log_lines

if __name__ == "__main__":
    pass
