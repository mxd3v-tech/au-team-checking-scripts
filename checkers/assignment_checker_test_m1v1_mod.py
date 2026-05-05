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
    "HQ-SRV": ("root", "toor"), "BR-SRV": ("root", "toor"),
    "HQ-CLI": ("root", "toor"), "BR-CLI": ("root", "toor"),
    "ISP": ("root", "toor"),
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
def validate_ip_in_allowed_ranges(ip_str, prefixlen):
    try: ip = ipaddress.IPv4Address(ip_str)
    except: return False, "Некорректный IP"
    if ip.is_loopback: return False, "Loopback"
    zones = [
        {"name": "HQ-SRV/VLAN100", "network": "10.10.100.0/24", "min_prefixlen": 27},
        {"name": "HQ-CLI/VLAN200", "network": "10.10.200.0/27", "min_prefixlen": 28},
        {"name": "Management", "network": "10.10.30.0/28", "min_prefixlen": 29},
        {"name": "BR-SRV", "network": "10.20.20.0/26", "min_prefixlen": 28},
        {"name": "BR-CLI", "network": "10.20.30.0/26", "min_prefixlen": 29},
    ]
    for z in zones:
        net = ipaddress.IPv4Network(z["network"], strict=False)
        if ip in net:
            return (True, "✅ %s /%d" % (z['name'], prefixlen)) if prefixlen >= z["min_prefixlen"] else (False, "❌ маска /%d широкая" % prefixlen)
    return False, "❌ Не в разрешённой зоне"

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
    log_msg("\n🔍 Модуль 1 (M1-V1), КО 09.02.06-1-2026\n")

    FQDN = {"HQ-SRV": "hq-srv.au-team.irpo", "BR-SRV": "br-srv.au-team.irpo",
            "HQ-CLI": "hq-cli.au-team.irpo", "BR-CLI": "br-cli.au-team.irpo",
            "HQ-RTR": "hq-rtr.au-team.irpo", "BR-RTR": "br-rtr.au-team.irpo",
            "BR-FW": "br-fw.au-team.irpo", "ISP-HQ": "docker.au-team.irpo", "ISP-BR": "web.au-team.irpo"}
    PTR_DEVICES = {"hq-srv.au-team.irpo", "hq-cli.au-team.irpo", "hq-rtr.au-team.irpo"}

    # ==== КО-1: IP+имена (max 2) ====
    log_msg("📌 КО-1: IP-адреса и имена устройств")
    names_ok = 0
    for dev in ["HQ-SRV", "BR-SRV", "HQ-CLI", "BR-CLI", "HQ-RTR", "BR-RTR", "ISP"]:
        if dev not in vm_ports: continue
        expected = FQDN.get(dev, "isp.au-team.irpo") if dev != "ISP" else "isp.au-team.irpo"
        if "RTR" in dev:
            out, _, _ = rtr_exec(vm_ports[dev], *get_rtr_creds(dev), "show hostname")
            safe_log_output(log_lines, "[%s] show hostname" % dev, out)
            actual = ""
            if out:
                lines = [l.strip() for l in out.splitlines() if l.strip()]
                actual = lines[1].lower() if len(lines) >= 2 else (lines[0].lower() if lines else "")
            if actual == dev.lower() or actual == expected.lower().split('.')[0]: names_ok += 1
        else:
            out, _, _ = ssh_exec(vm_ports[dev], "hostname -f", *get_srv_creds(dev))
            safe_log_output(log_lines, "[%s] hostname" % dev, out)
            if out and out.strip().lower() == expected: names_ok += 1

    if names_ok >= 7: award("КО-1: IP+имена", 2, 2)
    elif names_ok >= 2: award("КО-1: IP+имена", 1, 2, "%d устройств" % names_ok)
    else: award("КО-1: IP+имена", 0, 2)

    # ==== КО-2: Подинтерфейсы HQ-RTR (max 2) ====
    log_msg("\n📌 КО-2: Подинтерфейсы HQ-RTR")
    subif_score = 0
    if "HQ-RTR" in vm_ports:
        out1, _, _ = rtr_exec(vm_ports["HQ-RTR"], *get_rtr_creds("HQ-RTR"), "show ip interface brief")
        safe_log_output(log_lines, "[HQ-RTR] interfaces", out1)
        out2, _, _ = rtr_exec(vm_ports["HQ-RTR"], *get_rtr_creds("HQ-RTR"), "show running-config | include ge1/")
        safe_log_output(log_lines, "[HQ-RTR] ge1/", out2)
        subifs = set(re.findall(r'ge1/\d+\.\d+', (out1 or "") + (out2 or "")))
        if len(subifs) >= 3: subif_score = 2
        elif len(subifs) >= 1: subif_score = 1
    award("КО-2: Подинтерфейсы", subif_score, 2)

    # ==== КО-3: ISP маршрутизация+NAT (max 2) ====
    log_msg("\n📌 КО-3: ISP маршрутизация+NAT")
    isp_score = 0
    if "ISP" in vm_ports:
        out_ip, _, _ = ssh_exec(vm_ports["ISP"], "ip -br addr show", *get_srv_creds("ISP"))
        safe_log_output(log_lines, "[ISP] ip", out_ip)
        out_nat, _, _ = ssh_exec(vm_ports["ISP"], "iptables -t nat -L -n 2>/dev/null || nft list ruleset 2>/dev/null", *get_srv_creds("ISP"))
        safe_log_output(log_lines, "[ISP] nat", out_nat)
        ens_ok = 0
        if out_ip:
            for iface, net_str in [('ens4', '172.16.1.0/28'), ('ens5', '172.16.2.0/28')]:
                for line in out_ip.split('\n'):
                    parts = line.split()
                    if len(parts) >= 3 and parts[0] == iface and '/' in parts[2]:
                        try:
                            if ipaddress.IPv4Interface(parts[2]).network == ipaddress.IPv4Network(net_str, strict=False): ens_ok += 1
                        except: pass
        has_nat = out_nat and ("MASQUERADE" in out_nat or "masquerade" in out_nat or "snat" in (out_nat or "").lower())
        if ens_ok == 2 and has_nat: isp_score = 2
        elif ens_ok >= 1: isp_score = 1
    award("КО-3: ISP", isp_score, 2)

    # ==== КО-4: Учётные записи (max 2) ====
    log_msg("\n📌 КО-4: Учётные записи")
    acc_ok, acc_total = 0, 0
    for srv in ["HQ-SRV", "BR-SRV"]:
        if srv not in vm_ports: continue
        acc_total += 1
        out, _, _ = ssh_exec(vm_ports[srv], "id -u sshuser", "sshuser", "P@ssw0rd")
        safe_log_output(log_lines, "[%s] id sshuser" % srv, out)
        out2, _, _ = ssh_exec(vm_ports[srv], "sudo -n id", "sshuser", "P@ssw0rd")
        safe_log_output(log_lines, "[%s] sudo" % srv, out2)
        if (out and out.strip() == "2026") and (out2 and "uid=0(root)" in out2): acc_ok += 1
    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports: continue
        acc_total += 1
        out, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show users localdb")
        safe_log_output(log_lines, "[%s] users" % rtr, out)
        if out and "net_admin" in out: acc_ok += 1

    if acc_ok >= acc_total and acc_total > 0: award("КО-4: Учётные записи", 2, 2)
    elif acc_ok >= acc_total - 1 and acc_total > 0: award("КО-4: Учётные записи", 1, 2)
    else: award("КО-4: Учётные записи", 0, 2)

    # ==== КО-5: SSH (max 1) ====
    log_msg("\n📌 КО-5: SSH")
    ssh_ok_count, ssh_total = 0, 0
    for srv in ["HQ-SRV", "BR-SRV"]:
        if srv not in vm_ports: continue
        cfg, e, _ = ssh_exec(vm_ports[srv], "sshd -T 2>/dev/null", *get_srv_creds(srv))
        safe_log_output(log_lines, "[%s] sshd -T" % srv, cfg)
        if not cfg: continue
        for check_name, pattern in [("MaxAuthTries", r'^maxauthtries\s+2'),
                                     ("AllowUsers", r'^allowusers\s+')]:
            ssh_total += 1
            if any(re.match(pattern, l.strip().lower()) for l in cfg.split('\n')): ssh_ok_count += 1
        ssh_total += 1
        for line in cfg.split('\n'):
            if line.strip().lower().startswith('banner '):
                bp = line.strip().split(None, 1)[1] if len(line.strip().split()) >= 2 else ""
                if bp and bp != "none":
                    bo, _, _ = ssh_exec(vm_ports[srv], "test -f %s && cat %s" % (bp, bp), *get_srv_creds(srv))
                    if bo and "authorized access only" in bo.lower(): ssh_ok_count += 1; break
    if ssh_total > 0 and ssh_ok_count >= ssh_total - 1: award("КО-5: SSH", 1, 1)
    else: award("КО-5: SSH", 0, 1)

    # ==== КО-6: Туннель GRE (max 2) ====
    log_msg("\n📌 КО-6: GRE-туннель")
    gre_ok = 0
    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports: continue
        o1, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show interface tunnel.0")
        safe_log_output(log_lines, "[%s] tunnel" % rtr, o1)
        o2, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show running-config | include gre")
        safe_log_output(log_lines, "[%s] gre" % rtr, o2)
        target = "10.10.10.2" if rtr == "HQ-RTR" else "10.10.10.1"
        o3, _, _ = rtr_exec_with_enable(vm_ports[rtr], *get_rtr_creds(rtr), "ping %s count 3" % target)
        safe_log_output(log_lines, "[%s] ping" % rtr, o3)
        if (o1 and "is up" in o1) and (o2 and "gre" in o2) and (o3 and "Success rate is 100 percent" in o3): gre_ok += 1
    if gre_ok == 2: award("КО-6: GRE-туннель", 2, 2)
    elif gre_ok == 1: award("КО-6: GRE-туннель", 1, 2, "одна сторона")
    else: award("КО-6: GRE-туннель", 0, 2)

    # ==== КО-7: OSPF (max 3) ====
    log_msg("\n📌 КО-7: OSPF")
    ospf_total = 0
    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports: continue
        o1, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show running-config | include ospf")
        safe_log_output(log_lines, "[%s] ospf cfg" % rtr, o1)
        o2, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show ip route")
        safe_log_output(log_lines, "[%s] routes" % rtr, o2)
        o3, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show ip ospf neighbor")
        safe_log_output(log_lines, "[%s] neighbor" % rtr, o3)
        if o1 and "authentication message-digest" in o1 and "md5" in o1: ospf_total += 1
        if o2 and ("10.10.100.0" in o2 or "10.20.20.0" in o2): ospf_total += 1
        if o3:
            for line in o3.split('\n'):
                if 'tunnel.0' in line and ('Full/DR' in line or 'Full/Backup' in line): ospf_total += 1; break
    if ospf_total >= 6: award("КО-7: OSPF", 3, 3)
    elif ospf_total >= 3: award("КО-7: OSPF", 2, 3)
    elif ospf_total >= 1: award("КО-7: OSPF", 1, 3)
    else: award("КО-7: OSPF", 0, 3)

    # ==== КО-8: NAT в офисах (max 2) ====
    log_msg("\n📌 КО-8: NAT в офисах")
    nat_ok = 0
    for cli in ["HQ-CLI", "BR-CLI"]:
        if cli not in vm_ports: continue
        out, _, _ = ssh_exec(vm_ports[cli], "ping -c 2 1.1.1.1", *get_srv_creds(cli))
        safe_log_output(log_lines, "[%s] ping" % cli, out)
        if out and "2 received" in out: nat_ok += 1
    if nat_ok == 2: award("КО-8: NAT офисов", 2, 2)
    elif nat_ok == 1: award("КО-8: NAT офисов", 1, 2, "одна зона")
    else: award("КО-8: NAT офисов", 0, 2)

    # ==== КО-9: DHCP (max 2) ====
    log_msg("\n📌 КО-9: DHCP")
    dhcp_score = 0
    if "HQ-RTR" in vm_ports and "HQ-CLI" in vm_ports:
        out, _, _ = rtr_exec(vm_ports["HQ-RTR"], *get_rtr_creds("HQ-RTR"), "show running-config | include dhcp-server")
        safe_log_output(log_lines, "[HQ-RTR] dhcp", out)
        dhcp_name = None
        if out:
            for l in out.split('\n'):
                l = l.strip()
                if l.startswith('dhcp-server') and 'show' not in l and '!' not in l:
                    n = l[len('dhcp-server'):].strip()
                    if n: dhcp_name = n; break
        checks = {"server": bool(dhcp_name), "pool": False, "client": False}
        if dhcp_name:
            o2, _, _ = rtr_exec(vm_ports["HQ-RTR"], *get_rtr_creds("HQ-RTR"), "show dhcp-server %s" % dhcp_name)
            safe_log_output(log_lines, "[HQ-RTR] dhcp detail", o2)
            if o2:
                m = re.search(r'(\d+\.\d+\.\d+\.\d+)-(\d+\.\d+\.\d+\.\d+)', o2)
                if m:
                    try:
                        net = ipaddress.IPv4Network("10.10.200.0/27", strict=False)
                        if ipaddress.IPv4Address(m.group(1)) in net and ipaddress.IPv4Address(m.group(2)) in net: checks["pool"] = True
                    except: pass
            ip_out, _, _ = ssh_exec(vm_ports["HQ-CLI"], "ip addr show ens3", *get_srv_creds("HQ-CLI"))
            safe_log_output(log_lines, "[HQ-CLI] ens3", ip_out)
            if ip_out:
                dm = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)/\d+.*dynamic', ip_out)
                if dm:
                    try:
                        if ipaddress.IPv4Address(dm.group(1)) in ipaddress.IPv4Network("10.10.200.0/27", strict=False): checks["client"] = True
                    except: pass
        ok = sum(1 for v in checks.values() if v)
        if ok == 3: dhcp_score = 2
        elif ok >= 2: dhcp_score = 1
    award("КО-9: DHCP", dhcp_score, 2)

    # ==== КО-10: DNS (max 3) ====
    log_msg("\n📌 КО-10: DNS")
    dns_a_ok, dns_ptr_ok, dns_a_total, dns_ptr_total, fwd_ok = 0, 0, 0, 0, False
    client = "HQ-CLI" if "HQ-CLI" in vm_ports else ("BR-CLI" if "BR-CLI" in vm_ports else None)
    if client:
        for key, fqdn in FQDN.items():
            if key == "BR-FW" and "BR-FW" not in vm_ports: continue
            dns_a_total += 1
            out, _, _ = ssh_exec(vm_ports[client], "nslookup %s" % fqdn, *get_srv_creds(client))
            safe_log_output(log_lines, "[%s] nslookup %s" % (client, fqdn), out)
            if out and "Address:" in out:
                m = re.search(r'Name:\s*%s\s*\n\s*Address:\s*(\d+\.\d+\.\d+\.\d+)' % re.escape(fqdn), out, re.IGNORECASE)
                if m and not m.group(1).startswith("127."):
                    dns_a_ok += 1
                    if fqdn in PTR_DEVICES:
                        dns_ptr_total += 1
                        ro, _, _ = ssh_exec(vm_ports[client], "nslookup %s" % m.group(1), *get_srv_creds(client))
                        safe_log_output(log_lines, "[%s] PTR %s" % (client, m.group(1)), ro)
                        if ro:
                            rip = '.'.join(reversed(m.group(1).split('.')))
                            if re.search(r'%s\.in-addr\.arpa\s+name\s*=\s*%s\.' % (re.escape(rip), re.escape(fqdn)), ro, re.IGNORECASE): dns_ptr_ok += 1
        fo, _, _ = ssh_exec(vm_ports[client], "nslookup ya.ru", *get_srv_creds(client))
        safe_log_output(log_lines, "[%s] fwd ya.ru" % client, fo)
        fwd_ok = fo and "Address:" in fo and "NXDOMAIN" not in fo

    log_msg("ℹ️ A: %d/%d, PTR: %d/%d, fwd: %s" % (dns_a_ok, dns_a_total, dns_ptr_ok, dns_ptr_total, fwd_ok))
    if dns_a_ok >= dns_a_total and dns_ptr_ok >= dns_ptr_total and fwd_ok and dns_a_total > 0: award("КО-10: DNS", 3, 3)
    elif dns_a_ok >= dns_a_total - 1 and dns_a_total > 0: award("КО-10: DNS", 2, 3)
    elif dns_a_ok >= 1: award("КО-10: DNS", 1, 3)
    else: award("КО-10: DNS", 0, 3)

    # ==== КО-11: Подсети (max 2) ====
    log_msg("\n📌 КО-11: Подсети")
    sub_ok, sub_total = 0, 0
    for dev in ["HQ-SRV", "BR-SRV", "HQ-CLI", "BR-CLI"]:
        if dev not in vm_ports: continue
        sub_total += 1
        out, _, _ = ssh_exec(vm_ports[dev], "ip -br addr show", *get_srv_creds(dev))
        if out:
            for line in out.strip().split('\n'):
                parts = line.split()
                if len(parts) >= 3 and '/' in parts[2] and parts[0] != 'lo':
                    ip_s, mask = parts[2].split('/')
                    if not ip_s.startswith('169.') and not ip_s.startswith('127.'):
                        ok, _ = validate_ip_in_allowed_ranges(ip_s, int(mask))
                        if ok: sub_ok += 1
                        break
    if sub_ok >= sub_total and sub_total > 0: award("КО-11: Подсети", 2, 2)
    elif sub_ok >= 2: award("КО-11: Подсети", 1, 2)
    else: award("КО-11: Подсети", 0, 2)

    # ==== КО-12: Время (max 1) ====
    log_msg("\n📌 КО-12: Временная зона")
    tz_ok = 0
    for srv in ["HQ-SRV", "BR-SRV", "HQ-CLI", "BR-CLI"]:
        if srv not in vm_ports: continue
        out, _, _ = ssh_exec(vm_ports[srv], "timedatectl show -p Timezone --value 2>/dev/null || timedatectl status", *get_srv_creds(srv))
        safe_log_output(log_lines, "[%s] tz" % srv, out)
        if out and "Moscow" in out: tz_ok += 1
    if tz_ok >= 2: award("КО-12: Время", 1, 1)
    else: award("КО-12: Время", 0, 1)

    # ==== ИТОГО ====
    log_msg("\n📊 ИТОГО (M1-V1, КО 09.02.06-1-2026):"); log_msg("=" * 60)
    for item, d in results.items():
        icon = "✅" if d["score"] == d["max"] else ("⚠️" if d["score"] > 0 else "❌")
        log_msg("%s %s: %d/%d" % (icon, item, d["score"], d["max"]))
    log_msg("\n📈 Набрано %.1f из %.1f баллов" % (POINTS, MAX_POINTS))
    return POINTS, log_lines

if __name__ == "__main__":
    pass
