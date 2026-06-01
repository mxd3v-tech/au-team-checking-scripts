import subprocess
import re
import sys

try:
    import pexpect
    PEXPECT_AVAILABLE = True
except ImportError:
    PEXPECT_AVAILABLE = False
    print("⚠️  Модуль pexpect не установлен. Проверка маршрутизаторов будет пропущена.", file=sys.stderr)

# ========== ПАРАМЕТРЫ ВАРИАНТА (В3) ==========
RAID_LEVEL = "raid5"
RAID_DEV = "md3"
RAID_DISKS = 3
NTP_STRATUM = 8
DOCKER_DB_IMAGE = "postgres"
DOCKER_DB_NAME = "testdb3"
DOCKER_DB_USER = "test3c"
APP_PORT = "8083"
WEB_DB_USER = "web3c"
NGINX_AUTH_LOGIN = "Kazimirc"
SSH_PORT = "2013"

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

# ========== ГЛАВНАЯ ==========
# ====== Соответствие критериев оценочной ведомости (КОД 09.02.06-1-2026, Модуль 2) ======
# Логика проверки не меняется: переименование и сортировка применяются только при
# формировании итогового блока (📊…📈), который читают отчёт и веб-интерфейс.
VEDOMOST = [
    ("2.1",  "Пользователи и группа в домене",       "КО: Пользователи + группа hq"),
    ("2.2",  "Права sudo доменных пользователей",     "КО: Sudo-права доменных пользователей"),
    ("2.3",  "Файловый сервер NFS",                   "КО: NFS"),
    ("2.4",  "Сервер времени (NTP)",                  "КО: NTP"),
    ("2.5",  "Дисковый массив (RAID)",                "КО: RAID"),
    ("2.6",  "Инфраструктура как сервис (Ansible)",   "КО: Ansible (инфраструктура как сервис)"),
    ("2.7",  "Веб-сервер в контейнере (Docker)",      "КО: Docker веб-контейнер"),
    ("2.8",  "Веб-сервис (apache)",                   "КО: Веб-сервис apache"),
    ("2.9",  "Динамическая трансляция портов",        "КО: Проброс портов"),
    ("2.10", "Контроллер домена samba-dc",            "КО: Samba-DC + ввод в домен"),
    ("2.11", "Обратный прокси nginx на ISP",          "КО: Nginx обратный прокси"),
    ("2.12", "Web-аутентификация на ISP",             "КО: Web-аутентификация"),
    ("2.13", "Веб-браузер на HQ-CLI",                 "КО: Веб-браузер"),
    ("2.14", "Отчёт по ГОСТ Р 7.0.97-2016",           "КО: Отчёт (ГОСТ Р 7.0.97-2016)"),
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
    log_msg("\n🔍 Модуль 2 (M2-V3), КОД 09.02.06-1-2026-ПУ\n")

    # ==== КО: Samba-DC + ввод в домен (max 3) ====
    log_msg("\n📌 КО: Samba-DC на BR-SRV")
    dc_score = 0
    domain_ok = hq_joined = False
    if "BR-SRV" in vm_ports:
        out, _, _ = ssh_exec(vm_ports["BR-SRV"], "samba-tool domain info 127.0.0.1", "root", "toor")
        safe_log_output(log_lines, "[BR-SRV] domain info", out)
        domain_ok = bool(out and re.search(r'Domain\s*:\s*au-team\.irpo', out, re.IGNORECASE))
        comp_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "samba-tool computer list", "root", "toor")
        safe_log_output(log_lines, "[BR-SRV] computer list", comp_out)
        hq_joined = bool(comp_out and re.search(r'hq-cli', comp_out, re.IGNORECASE))
        if not hq_joined and "HQ-CLI" in vm_ports:
            w, _, _ = ssh_exec(vm_ports["HQ-CLI"], "realm list 2>/dev/null; net ads testjoin 2>/dev/null", "root", "toor")
            safe_log_output(log_lines, "[HQ-CLI] join", w)
            hq_joined = bool(w and ("au-team.irpo" in w.lower() or "join is ok" in w.lower()))
    if domain_ok and hq_joined: dc_score = 3
    elif domain_ok: dc_score = 1
    award("КО: Samba-DC + ввод в домен", dc_score, 3, "домен=%s, HQ-CLI=%s" % (domain_ok, hq_joined))

    # ==== КО: Пользователи + группа hq (max 2) ====
    log_msg("\n📌 КО: Доменные пользователи и группа hq")
    users_score = 0
    if "BR-SRV" in vm_ports:
        users_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "samba-tool user list", "root", "toor")
        safe_log_output(log_lines, "[BR-SRV] user list", users_out)
        grp_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "samba-tool group listmembers hq", "root", "toor")
        safe_log_output(log_lines, "[BR-SRV] group hq", grp_out)
        hq_users = ["hquser%d" % i for i in range(1, 6)]
        users_exist = users_out and all(u in users_out for u in hq_users)
        in_group = grp_out and all(u in grp_out for u in hq_users)
        if users_exist and in_group: users_score = 2
        elif users_exist: users_score = 1
    award("КО: Пользователи + группа hq", users_score, 2)

    # ==== КО: Sudo-права доменных пользователей (max 2) ====
    log_msg("\n📌 КО: Ограниченный sudo для группы hq")
    sudo_score = 0
    if "HQ-CLI" in vm_ports:
        tu, tp = "hquser1", "P@ssw0rd"
        auth, ae, _ = ssh_exec(vm_ports["HQ-CLI"], "whoami", tu, tp)
        safe_log_output(log_lines, "[HQ-CLI] auth hquser1", auth, ae)
        if not (auth and tu in (auth or "").lower()):
            wb, _, _ = ssh_exec(vm_ports["HQ-CLI"], "wbinfo -a '%s%%\"%s\"'" % (tu, tp), "root", "toor")
            safe_log_output(log_lines, "[HQ-CLI] wbinfo", wb)
        allowed_ok = True
        for c in ["cat /etc/passwd", "grep root /etc/passwd", "id"]:
            o, e, _ = ssh_exec(vm_ports["HQ-CLI"], "echo '%s' | sudo -S %s" % (tp, c), tu, tp)
            safe_log_output(log_lines, "[HQ-CLI] sudo %s" % c, o, e)
            if not o or "password is required" in (e or "") or "not allowed" in (e or "").lower():
                allowed_ok = False
        of, ef, _ = ssh_exec(vm_ports["HQ-CLI"], "echo '%s' | sudo -S ps aux" % tp, tu, tp)
        safe_log_output(log_lines, "[HQ-CLI] sudo ps (forbidden)", of, ef)
        forbidden_blocked = not (of and "PID" in of and "USER" in of)
        sudo_score = (1 if allowed_ok else 0) + (1 if forbidden_blocked else 0)
    award("КО: Sudo-права доменных пользователей", sudo_score, 2)

    # ==== КО: NFS (max 2) ====
    log_msg("\n📌 КО: NFS")
    nfs_score = 0
    if "HQ-SRV" in vm_ports:
        exp, _, _ = ssh_exec(vm_ports["HQ-SRV"], "showmount -e localhost", "root", "toor")
        safe_log_output(log_lines, "[HQ-SRV] exports", exp)
        if exp and "/raid/nfs" in exp: nfs_score += 1
    if "HQ-CLI" in vm_ports:
        m, _, _ = ssh_exec(vm_ports["HQ-CLI"], "findmnt /mnt/nfs --noheadings", "root", "toor")
        safe_log_output(log_lines, "[HQ-CLI] mnt", m)
        if m and "/mnt/nfs" in m and "nfs" in m.lower(): nfs_score += 1
    award("КО: NFS", nfs_score, 2)

    # ==== КО: NTP (max 1) ====
    log_msg("\n📌 КО: NTP (стратум %d)" % NTP_STRATUM)
    ntp_ok = True
    if "ISP" in vm_ports:
        sc, _, _ = ssh_exec(vm_ports["ISP"], "grep -ri 'local stratum' /etc/chrony*", "root", "toor")
        safe_log_output(log_lines, "[ISP] stratum", sc)
        if not (sc and re.search(r'local\s+stratum\s+%d\b' % NTP_STRATUM, sc)): ntp_ok = False
        for c in ["HQ-SRV", "HQ-CLI", "BR-SRV"]:
            if c in vm_ports:
                o, _, _ = ssh_exec(vm_ports[c], "chronyc sources", "root", "toor")
                safe_log_output(log_lines, "[%s] chronyc" % c, o)
                if not (o and "^*" in o): ntp_ok = False
    else:
        ntp_ok = False
    award("КО: NTP", 1 if ntp_ok else 0, 1)

    # ==== КО: Nginx обратный прокси (max 3) ====
    log_msg("\n📌 КО: Nginx обратный прокси")
    nginx_score = 0
    if "ISP" in vm_ports:
        conf, _, _ = ssh_exec(vm_ports["ISP"], "cat /etc/nginx/sites-enabled.d/* 2>/dev/null; cat /etc/nginx/conf.d/* 2>/dev/null; cat /etc/nginx/sites-enabled/* 2>/dev/null", "root", "toor")
        safe_log_output(log_lines, "[ISP] nginx", conf)
        if conf:
            has_web = "web.au-team.irpo" in conf
            has_docker = "docker.au-team.irpo" in conf
            has_proxy = "proxy_pass" in conf
            nginx_score = (1 if has_web else 0) + (1 if has_docker else 0) + (1 if has_proxy else 0)
    award("КО: Nginx обратный прокси", nginx_score, 3)

    # ==== КО: Ansible IaaS (max 2) ====
    log_msg("\n📌 КО: Ansible (BR-SRV)")
    ans_score = 0
    if "BR-SRV" in vm_ports:
        inv, _, _ = ssh_exec(vm_ports["BR-SRV"], "cat /etc/ansible/hosts 2>/dev/null; cat /etc/ansible/inventory 2>/dev/null", "root", "toor")
        safe_log_output(log_lines, "[BR-SRV] inventory", inv)
        ping, _, _ = ssh_exec(vm_ports["BR-SRV"], "ansible all -m ping", "root", "toor")
        safe_log_output(log_lines, "[BR-SRV] ping", ping)
        pong = ping.count('"pong"') if ping else 0
        clean = ping and "UNREACHABLE" not in ping and "FAILED" not in ping
        inv_ok = inv and all(h in inv.lower() for h in ["hq-srv", "hq-cli", "hq-rtr", "br-rtr"])
        if pong >= 4 and clean and inv_ok: ans_score = 2
        elif pong >= 2: ans_score = 1
    award("КО: Ansible (инфраструктура как сервис)", ans_score, 2)

    # ==== КО: RAID (max 1) ====
    log_msg("\n📌 КО: RAID (%s /dev/%s)" % (RAID_LEVEL, RAID_DEV))
    raid_ok = False
    if "HQ-SRV" in vm_ports:
        md, _, _ = ssh_exec(vm_ports["HQ-SRV"], "mdadm --detail /dev/%s 2>/dev/null" % RAID_DEV, "root", "toor")
        safe_log_output(log_lines, "[HQ-SRV] mdadm", md)
        mnt, _, _ = ssh_exec(vm_ports["HQ-SRV"], "findmnt /raid --noheadings", "root", "toor")
        safe_log_output(log_lines, "[HQ-SRV] mount", mnt)
        fst, _, _ = ssh_exec(vm_ports["HQ-SRV"], "findmnt --fstab /raid --noheadings", "root", "toor")
        safe_log_output(log_lines, "[HQ-SRV] fstab", fst)
        raid_ok = bool(md and RAID_DEV in md and RAID_LEVEL in md.lower()
                       and mnt and RAID_DEV in mnt and "ext4" in mnt and fst)
    award("КО: RAID", 1 if raid_ok else 0, 1)

    # ==== КО: Docker веб-контейнер (max 3) ====
    log_msg("\n📌 КО: Docker-стек (site + db)")
    dscore = 0
    if "BR-SRV" in vm_ports:
        ps, _, _ = ssh_exec(vm_ports["BR-SRV"], "docker ps --format '{{.Names}} {{.Image}}'", "root", "toor")
        safe_log_output(log_lines, "[BR-SRV] docker ps", ps)
        site_ok = bool(ps and re.search(r'^site\b', ps, re.MULTILINE))
        db_ok = bool(ps and re.search(r'^db\b', ps, re.MULTILINE) and DOCKER_DB_IMAGE in ps.lower())
        port, _, _ = ssh_exec(vm_ports["BR-SRV"], "ss -tlnH sport = :%s" % APP_PORT, "root", "toor")
        safe_log_output(log_lines, "[BR-SRV] port %s" % APP_PORT, port)
        port_ok = bool(port)
        curl, _, _ = ssh_exec(vm_ports["BR-SRV"], "curl -s http://localhost:%s" % APP_PORT, "root", "toor")
        safe_log_output(log_lines, "[BR-SRV] curl", curl)
        curl_ok = bool(curl and ("<html" in curl.lower() or "site" in curl.lower()))
        signals = sum([site_ok, db_ok, port_ok, curl_ok])
        dscore = 3 if signals >= 4 else (2 if signals >= 2 else (1 if signals >= 1 else 0))
    award("КО: Docker веб-контейнер", dscore, 3)

    # ==== КО: Веб-сервис apache (max 2) ====
    log_msg("\n📌 КО: Веб-сервис apache на HQ-SRV")
    wscore = 0
    if "HQ-SRV" in vm_ports:
        h, _, _ = ssh_exec(vm_ports["HQ-SRV"], "systemctl is-active httpd 2>/dev/null || systemctl is-active apache2 2>/dev/null", "root", "toor")
        safe_log_output(log_lines, "[HQ-SRV] httpd", h)
        db, _, _ = ssh_exec(vm_ports["HQ-SRV"], "mysql -u %s -pP@ssw0rd -BNe \"SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME='webdb'\"" % WEB_DB_USER, "root", "toor")
        safe_log_output(log_lines, "[HQ-SRV] webdb", db)
        php, _, _ = ssh_exec(vm_ports["HQ-SRV"], "grep -l webdb /var/www/html/index.php 2>/dev/null", "root", "toor")
        safe_log_output(log_lines, "[HQ-SRV] index.php", php)
        curl, _, _ = ssh_exec(vm_ports["HQ-SRV"], "curl -s http://localhost", "root", "toor")
        safe_log_output(log_lines, "[HQ-SRV] curl", curl)
        signals = sum([bool(h and "active" in h), bool(db and "webdb" in db), bool(php), bool(curl and "<html" in curl.lower())])
        wscore = 2 if signals >= 4 else (1 if signals >= 2 else 0)
    award("КО: Веб-сервис apache", wscore, 2)

    # ==== КО: Проброс портов на маршрутизаторах (max 1) ====
    log_msg("\n📌 КО: Проброс портов")
    pf_ok = False
    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports: continue
        o, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show running-config | include nat")
        safe_log_output(log_lines, "[%s] nat" % rtr, o)
        if o and re.search(r'(dst-nat|port|nat)', o, re.IGNORECASE) and (APP_PORT in o or SSH_PORT in o):
            pf_ok = True
    award("КО: Проброс портов", 1 if pf_ok else 0, 1)

    # ==== КО: Web-аутентификация (max 1) ====
    log_msg("\n📌 КО: Web-аутентификация (%s)" % NGINX_AUTH_LOGIN)
    auth_ok = False
    if "ISP" in vm_ports:
        ht, _, _ = ssh_exec(vm_ports["ISP"], "htpasswd -vb /etc/nginx/.htpasswd %s 'P@ssw0rd' 2>&1" % NGINX_AUTH_LOGIN, "root", "toor")
        safe_log_output(log_lines, "[ISP] htpasswd", ht)
        ab, _, _ = ssh_exec(vm_ports["ISP"], "grep -r 'auth_basic' /etc/nginx/ 2>/dev/null", "root", "toor")
        safe_log_output(log_lines, "[ISP] auth_basic", ab)
        auth_ok = bool(ht and ("correct" in ht.lower() or "verified" in ht.lower())) and bool(ab and "auth_basic" in ab)
    award("КО: Web-аутентификация", 1 if auth_ok else 0, 1)

    # ==== КО: Веб-браузер (max 1) ====
    log_msg("\n📌 КО: Яндекс Браузер")
    br_ok = False
    if "HQ-CLI" in vm_ports:
        w, _, _ = ssh_exec(vm_ports["HQ-CLI"], "which yandex-browser-stable 2>/dev/null || which yandex-browser 2>/dev/null || rpm -q yandex-browser-stable 2>/dev/null", "root", "toor")
        safe_log_output(log_lines, "[HQ-CLI] browser", w)
        br_ok = bool(w and "/" in w and "no " not in w.lower())
    award("КО: Веб-браузер", 1 if br_ok else 0, 1)

    # ==== КО: Отчёт (max 1) — ручная проверка ====
    award("КО: Отчёт (ГОСТ Р 7.0.97-2016)", 0, 1, "требует ручной проверки эксперта")

    # ==== ИТОГО ====
    log_msg("\n📊 ИТОГО (M2-V1, КОД 09.02.06-1-2026-ПУ):"); log_msg("=" * 60)
    for code, title, key in VEDOMOST:
        d = results.get(key)
        if d is None: continue
        icon = "✅" if d["score"] == d["max"] else ("⚠️" if d["score"] > 0 else "❌")
        log_msg("%s %s %s: %d/%d" % (icon, code, title, d["score"], d["max"]))
    log_msg("\n📈 Набрано %.1f из %.1f баллов" % (POINTS, MAX_POINTS))
    return POINTS, log_lines

if __name__ == "__main__":
    pass
