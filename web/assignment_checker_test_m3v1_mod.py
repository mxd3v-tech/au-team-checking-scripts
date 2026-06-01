import subprocess
import re
import sys
from datetime import datetime

try:
    import pexpect
    PEXPECT_AVAILABLE = True
except ImportError:
    PEXPECT_AVAILABLE = False
    print("⚠️  Модуль pexpect не установлен. Проверка маршрутизаторов будет пропущена.", file=sys.stderr)

# ========== ПАРАМЕТРЫ ВАРИАНТА (В1) ==========
CERT_DAYS = 31
LOGROTATE_MINSIZE = 11
FAIL2BAN_BANTIME_MIN = 2
SSH_PORT = "2011"

# ========== УЧЁТНЫЕ ДАННЫЕ ==========
SRV_CREDENTIALS = {
    "BR-SRV": ("root", "toor"), "HQ-SRV": ("root", "toor"),
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
                   "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10",
                   "-tt", "-p", str(ssh_port), "%s@localhost" % username, command]
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

def safe_log_output(log_lines, prefix, output, error=""):
    log_lines.append("%s:\n%s\n" % (prefix, (output or "") + ("\n" + error if error else "")))

def cert_validity_days(out):
    if not out: return None
    dates = {}
    for m in re.finditer(r'(notBefore|notAfter)=\s*(\w+)\s+(\d+)\s+[\d:]+\s+(\d{4})', out):
        try:
            dates[m.group(1)] = datetime.strptime("%s %d %d" % (m.group(2), int(m.group(3)), int(m.group(4))), "%b %d %Y")
        except ValueError:
            return None
    if "notBefore" in dates and "notAfter" in dates:
        return (dates["notAfter"] - dates["notBefore"]).days
    return None

# ========== ГЛАВНАЯ ==========
# ====== Соответствие критериев оценочной ведомости (КОД 09.02.06-1-2026, Модуль 3) ======
# Логика проверки не меняется: переименование и сортировка применяются только при
# формировании итогового блока (📊…📈), который читают отчёт и веб-интерфейс.
VEDOMOST = [
    ("3.1",  "Логирование rsyslog",                   "КО: rsyslog логирование"),
    ("3.2",  "Мониторинг устройств",                  "КО: Мониторинг (Zabbix)"),
    ("3.3",  "Инвентаризация через Ansible",          "КО: Инвентаризация PC-INFO"),
    ("3.4",  "Центр сертификации (ГОСТ) + HTTPS",     "КО: Центр сертификации (GOST+HTTPS)"),
    ("3.5",  "Импорт пользователей из users.csv",     "КО: Импорт пользователей"),
    ("3.6",  "Резервное копирование конфигов RTR",    "КО: Ansible backup конфигов RTR"),
    ("3.7",  "Шифрованный IP-туннель",                "КО: IPsec туннель"),
    ("3.8",  "Защита SSH (fail2ban)",                 "КО: fail2ban"),
    ("3.9",  "Межсетевой экран на маршрутизаторах",   "КО: Межсетевой экран"),
    ("3.10", "Принт-сервер CUPS",                     "КО: CUPS"),
    ("3.11", "Ротация логов",                         "КО: Ротация логов"),
    ("3.12", "Резервное копирование (сервер и узел)", "КО: Резервное копирование"),
    ("3.13", "Отчёт по ГОСТ Р 7.0.97-2016",           "КО: Отчёт (ГОСТ Р 7.0.97-2016)"),
]


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
    log_msg("\n🔍 Модуль 3 (M3-V1), КОД 09.02.06-1-2026-ПУ\n")

    # ==== КО: Центр сертификации GOST + HTTPS (max 3) ====
    log_msg("\n📌 КО: Центр сертификации (срок %d дн.)" % CERT_DAYS)
    ca_pts = 0
    if "HQ-SRV" in vm_ports:
        ca, _, _ = ssh_exec(vm_ports["HQ-SRV"], "ls /etc/pki/CA/ /root/ca /root/CA 2>/dev/null; find / -maxdepth 4 \\( -name 'ca*.crt' -o -name 'ca*.pem' -o -name 'cacert*' \\) 2>/dev/null | head -5", "root", "toor")
        safe_log_output(log_lines, "[HQ-SRV] CA", ca)
        if ca: ca_pts += 1
    if "ISP" in vm_ports:
        nconf, _, _ = ssh_exec(vm_ports["ISP"], "cat /etc/nginx/sites-enabled.d/* 2>/dev/null; cat /etc/nginx/conf.d/* 2>/dev/null; cat /etc/nginx/sites-enabled/* 2>/dev/null", "root", "toor")
        safe_log_output(log_lines, "[ISP] nginx https", nconf)
        if nconf and (re.search(r'listen\s+443\s+ssl', nconf) or "ssl_certificate" in nconf): ca_pts += 1
        cert, _, _ = ssh_exec(vm_ports["ISP"], "echo | openssl s_client -connect localhost:443 -servername web.au-team.irpo 2>/dev/null | openssl x509 -noout -startdate -enddate 2>/dev/null", "root", "toor")
        safe_log_output(log_lines, "[ISP] cert dates", cert)
        algo, _, _ = ssh_exec(vm_ports["ISP"], "echo | openssl s_client -connect localhost:443 -servername web.au-team.irpo 2>/dev/null | openssl x509 -noout -text 2>/dev/null | grep -i 'Signature Algorithm' | head -1", "root", "toor")
        safe_log_output(log_lines, "[ISP] cert algo", algo)
        days = cert_validity_days(cert)
        gost = bool(algo and re.search(r'gost|1\.2\.643', algo, re.IGNORECASE))
        if days == CERT_DAYS and gost: ca_pts += 1
        elif days == CERT_DAYS or gost:
            log_msg("ℹ️ Сертификат: дней=%s (ожид. %d), ГОСТ=%s" % (days, CERT_DAYS, gost))
    award("КО: Центр сертификации (GOST+HTTPS)", ca_pts, 3)

    # ==== КО: rsyslog логирование (max 2) ====
    log_msg("\n📌 КО: rsyslog на HQ-SRV")
    rs_pts = 0
    if "HQ-SRV" in vm_ports:
        lst, _, _ = ssh_exec(vm_ports["HQ-SRV"], "ss -tulnH sport = :514", "root", "toor")
        safe_log_output(log_lines, "[HQ-SRV] :514", lst)
        if lst: rs_pts += 1
        opt, _, _ = ssh_exec(vm_ports["HQ-SRV"], "ls /opt/", "root", "toor")
        safe_log_output(log_lines, "[HQ-SRV] /opt", opt)
        if opt and re.search(r'(hq-rtr|br-rtr|br-srv)', opt, re.IGNORECASE): rs_pts += 1
    award("КО: rsyslog логирование", rs_pts, 2)

    # ==== КО: Мониторинг (Zabbix) (max 3) ====
    log_msg("\n📌 КО: Мониторинг (Zabbix)")
    mon_pts = 0
    if "HQ-SRV" in vm_ports:
        srv, _, _ = ssh_exec(vm_ports["HQ-SRV"], "docker ps --format '{{.Names}}' 2>/dev/null | grep -i zabbix; systemctl is-active zabbix-server 2>/dev/null", "root", "toor")
        safe_log_output(log_lines, "[HQ-SRV] zabbix server", srv)
        if srv and re.search(r'(zabbix|active)', srv, re.IGNORECASE): mon_pts += 1
        if "HQ-CLI" in vm_ports:
            dns, _, _ = ssh_exec(vm_ports["HQ-CLI"], "nslookup mon.au-team.irpo", "root", "toor")
            safe_log_output(log_lines, "[HQ-CLI] mon dns", dns)
            if dns and "Address" in dns and "NXDOMAIN" not in dns: mon_pts += 1
        agents = 0
        for host in ["HQ-SRV", "BR-SRV"]:
            if host in vm_ports:
                a, _, _ = ssh_exec(vm_ports[host], "systemctl is-active zabbix-agent 2>/dev/null || systemctl is-active zabbix-agent2 2>/dev/null", "root", "toor")
                safe_log_output(log_lines, "[%s] agent" % host, a)
                if a and "active" in a: agents += 1
        if agents >= 2: mon_pts += 1
    award("КО: Мониторинг (Zabbix)", mon_pts, 3)

    # ==== КО: Инвентаризация Ansible PC-INFO (max 2) ====
    log_msg("\n📌 КО: Инвентаризация PC-INFO")
    inv_pts = 0
    if "BR-SRV" in vm_ports:
        pb, _, _ = ssh_exec(vm_ports["BR-SRV"], "ls /etc/ansible/*.yml /etc/ansible/*.yaml 2>/dev/null", "root", "toor")
        safe_log_output(log_lines, "[BR-SRV] playbook", pb)
        if pb: inv_pts += 1
        pc, _, _ = ssh_exec(vm_ports["BR-SRV"], "ls /etc/ansible/PC-INFO/ 2>/dev/null", "root", "toor")
        safe_log_output(log_lines, "[BR-SRV] PC-INFO", pc)
        yml = [f for f in (pc or "").split() if f.endswith(('.yml', '.yaml'))] if pc else []
        if len(yml) >= 2: inv_pts = 2
        elif len(yml) >= 1 and inv_pts >= 1: inv_pts = 1
    award("КО: Инвентаризация PC-INFO", inv_pts, 2)

    # ==== КО: Межсетевой экран (max 2) ====
    log_msg("\n📌 КО: Межсетевой экран")
    fw_ok = 0
    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports: continue
        o, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show running-config | include access-list")
        safe_log_output(log_lines, "[%s] acl" % rtr, o)
        if o and "access-list" in o.lower(): fw_ok += 1
    award("КО: Межсетевой экран", 2 if fw_ok == 2 else (1 if fw_ok == 1 else 0), 2)

    # ==== КО: CUPS (max 1) ====
    log_msg("\n📌 КО: CUPS принт-сервер")
    cups_ok = False
    if "HQ-SRV" in vm_ports:
        st, _, _ = ssh_exec(vm_ports["HQ-SRV"], "systemctl is-active cups", "root", "toor")
        pr, _, _ = ssh_exec(vm_ports["HQ-SRV"], "lpstat -p", "root", "toor")
        safe_log_output(log_lines, "[HQ-SRV] cups", (st or "") + "\n" + (pr or ""))
        srv_ok = st and st.strip() == "active" and pr and re.search(r'(pdf|virtual)', pr, re.IGNORECASE)
        cli_ok = False
        if "HQ-CLI" in vm_ports:
            d, _, _ = ssh_exec(vm_ports["HQ-CLI"], "lpstat -d", "root", "toor")
            safe_log_output(log_lines, "[HQ-CLI] default printer", d)
            cli_ok = d and "system default destination" in d.lower()
        cups_ok = bool(srv_ok and cli_ok)
    award("КО: CUPS", 1 if cups_ok else 0, 1)

    # ==== КО: fail2ban (max 1) ====
    log_msg("\n📌 КО: fail2ban (бан %d мин)" % FAIL2BAN_BANTIME_MIN)
    f2b_ok = False
    if "HQ-SRV" in vm_ports:
        st, _, _ = ssh_exec(vm_ports["HQ-SRV"], "systemctl is-active fail2ban 2>/dev/null", "root", "toor")
        jail, _, _ = ssh_exec(vm_ports["HQ-SRV"], "cat /etc/fail2ban/jail.local 2>/dev/null; cat /etc/fail2ban/jail.d/*.conf 2>/dev/null; cat /etc/fail2ban/jail.d/*.local 2>/dev/null", "root", "toor")
        safe_log_output(log_lines, "[HQ-SRV] fail2ban", (st or "") + "\n" + (jail or ""))
        sec = FAIL2BAN_BANTIME_MIN * 60
        active = st and "active" in st
        maxretry = jail and re.search(r'maxretry\s*=\s*3\b', jail)
        bantime = jail and (re.search(r'bantime\s*=\s*%d\b' % sec, jail) or re.search(r'bantime\s*=\s*%dm\b' % FAIL2BAN_BANTIME_MIN, jail))
        f2b_ok = bool(active and maxretry and bantime)
    award("КО: fail2ban", 1 if f2b_ok else 0, 1)

    # ==== КО: Ротация логов (max 1) ====
    log_msg("\n📌 КО: Ротация логов (minsize %dM)" % LOGROTATE_MINSIZE)
    rot_ok = False
    if "HQ-SRV" in vm_ports:
        lr, _, _ = ssh_exec(vm_ports["HQ-SRV"], "cat /etc/logrotate.d/*opt* 2>/dev/null; grep -rl '/opt' /etc/logrotate.d/ 2>/dev/null | xargs cat 2>/dev/null", "root", "toor")
        safe_log_output(log_lines, "[HQ-SRV] logrotate", lr)
        if lr:
            rot_ok = bool(re.search(r'weekly', lr, re.IGNORECASE)
                          and re.search(r'^\s*compress', lr, re.IGNORECASE | re.MULTILINE)
                          and re.search(r'minsize\s+%d[mM]?' % LOGROTATE_MINSIZE, lr))
    award("КО: Ротация логов", 1 if rot_ok else 0, 1)

    # ==== КО: Резервное копирование (max 3) ====
    log_msg("\n📌 КО: Резервное копирование")
    bak_pts = 0
    svc = "systemctl is-active acronis_mms 2>/dev/null || systemctl is-active cyber-protect-agent 2>/dev/null || systemctl is-active acronis_agent 2>/dev/null"
    if "HQ-SRV" in vm_ports:
        s, _, _ = ssh_exec(vm_ports["HQ-SRV"], svc, "root", "toor")
        safe_log_output(log_lines, "[HQ-SRV] backup srv", s)
        if s and "active" in s: bak_pts += 1
    if "HQ-CLI" in vm_ports:
        a, _, _ = ssh_exec(vm_ports["HQ-CLI"], svc, "root", "toor")
        safe_log_output(log_lines, "[HQ-CLI] backup agent", a)
        if a and "active" in a: bak_pts += 1
        d, _, _ = ssh_exec(vm_ports["HQ-CLI"], "test -d /backup && echo EXISTS", "root", "toor")
        safe_log_output(log_lines, "[HQ-CLI] /backup", d)
        if d and "EXISTS" in d: bak_pts += 1
    award("КО: Резервное копирование", bak_pts, 3)

    # ==== КО: Импорт пользователей users.csv (max 2) ====
    log_msg("\n📌 КО: Импорт пользователей")
    imp_pts = 0
    custom = []
    if "BR-SRV" in vm_ports:
        ul, _, _ = ssh_exec(vm_ports["BR-SRV"], "samba-tool user list", "root", "toor")
        safe_log_output(log_lines, "[BR-SRV] users", ul)
        if ul:
            std = {"administrator", "guest", "krbtgt"}
            custom = [u.strip() for u in ul.splitlines() if u.strip()
                      and u.strip().lower() not in std and not u.strip().lower().startswith("dns-")
                      and not u.strip().lower().startswith("hquser")]
            if custom: imp_pts = 1
        if custom and "HQ-CLI" in vm_ports:
            tu = custom[0]
            au, _, _ = ssh_exec(vm_ports["HQ-CLI"], "whoami", tu, "P@ssw0rd")
            safe_log_output(log_lines, "[HQ-CLI] login %s" % tu, au)
            ok = au and tu.lower() in au.lower()
            if not ok:
                wb, _, _ = ssh_exec(vm_ports["HQ-CLI"], "wbinfo -a '%s%%\"P@ssw0rd\"'" % tu, "root", "toor")
                safe_log_output(log_lines, "[HQ-CLI] wbinfo", wb)
                ok = wb and "succeeded" in wb.lower()
            if ok: imp_pts = 2
    award("КО: Импорт пользователей", imp_pts, 2)

    # ==== КО: IP-туннель с шифрованием (IPsec) (max 2) ====
    log_msg("\n📌 КО: IPsec туннель")
    ipsec_ok = 0
    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports: continue
        o, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show running-config | include ipsec")
        safe_log_output(log_lines, "[%s] ipsec" % rtr, o)
        n, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show ip ospf neighbor")
        safe_log_output(log_lines, "[%s] ospf" % rtr, n)
        if o and "ipsec" in o.lower() and n and "Full" in n: ipsec_ok += 1
    award("КО: IPsec туннель", 2 if ipsec_ok == 2 else (1 if ipsec_ok == 1 else 0), 2)

    # ==== КО: Резервное копирование конфигов RTR через Ansible (max 2) ====
    log_msg("\n📌 КО: Ansible backup конфигов HQ-RTR/BR-RTR")
    rtrbak_pts = 0
    if "BR-SRV" in vm_ports:
        pbs, _, _ = ssh_exec(vm_ports["BR-SRV"], "grep -ril 'hq-rtr\\|br-rtr\\|running-config\\|backup' /etc/ansible/ 2>/dev/null", "root", "toor")
        safe_log_output(log_lines, "[BR-SRV] ansible rtr backup", pbs)
        bdir, _, _ = ssh_exec(vm_ports["BR-SRV"], "find /etc/ansible -maxdepth 3 -type d -iname '*backup*' 2>/dev/null; ls /etc/ansible/backup* 2>/dev/null", "root", "toor")
        safe_log_output(log_lines, "[BR-SRV] backup dir", bdir)
        has_pb = bool(pbs)
        has_out = bool(bdir)
        rtrbak_pts = (1 if has_pb else 0) + (1 if has_out else 0)
    award("КО: Ansible backup конфигов RTR", rtrbak_pts, 2)

    # ==== КО: Отчёт (max 1) — ручная проверка ====
    award("КО: Отчёт (ГОСТ Р 7.0.97-2016)", 0, 1, "требует ручной проверки эксперта")

    # ==== ИТОГО ====
    log_msg("\n📊 ИТОГО (M3-V1, КОД 09.02.06-1-2026-ПУ):"); log_msg("=" * 60)
    for code, title, key in VEDOMOST:
        d = results.get(key)
        if d is None: continue
        icon = "✅" if d["score"] == d["max"] else ("⚠️" if d["score"] > 0 else "❌")
        log_msg("%s %s %s: %d/%d" % (icon, code, title, d["score"], d["max"]))
    log_msg("\n📈 Набрано %.1f из %.1f баллов" % (POINTS, MAX_POINTS))
    return POINTS, log_lines

if __name__ == "__main__":
    pass
