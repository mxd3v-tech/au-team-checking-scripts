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

# ========== ПАРАМЕТРЫ ВАРИАНТА (В3) ==========
CERT_DAYS = 33              # срок действия выдаваемых сертификатов
LOGROTATE_MINSIZE = 13      # минимальный размер логов для ротации, МБ
FAIL2BAN_BANTIME_MIN = 4    # время бана fail2ban, минут

# ========== УЧЁТНЫЕ ДАННЫЕ ==========
SRV_CREDENTIALS = {
    "BR-SRV": ("root", "toor"),
    "HQ-SRV": ("root", "toor"),
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
        return None, "❌ Порт SSH недоступен: %s" % ssh_port, "Выполняется команда: %s" % command
    try:
        ssh_cmd = [
            "sshpass", "-p", password,
            "ssh",
            "-o", "StrictHostKeyChecking=no",
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
            universal_newlines=True,
            bufsize=1
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            return None, "❌ Таймаут выполнения команды (%d сек)" % timeout, "Выполняется команда: %s" % command
        return stdout, stderr, "Выполняется команда: %s" % command
    except FileNotFoundError:
        return None, "❌ Команда 'sshpass' не найдена. Установите пакет sshpass.", "Выполняется команда: %s" % command
    except Exception as e:
        return None, "❌ Ошибка выполнения: %s" % str(e), "Выполняется команда: %s" % command

# ========== ФУНКЦИИ ДЛЯ МАРШРУТИЗАТОРОВ (EcoRouterOS) ==========
def rtr_exec(port, username, password, command, timeout=30):
    if not PEXPECT_AVAILABLE:
        return None, "pexpect не установлен", "Выполняется команда: %s" % command
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
        output = child.before.strip()
        child.sendline("exit")
        child.close()
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        return ansi_escape.sub('', output), "", "Выполняется команда: %s" % command
    except pexpect.TIMEOUT:
        return None, "Таймаут выполнения команды", "Выполняется команда: %s" % command
    except Exception as e:
        return None, "Ошибка pexpect: %s" % str(e), "Выполняется команда: %s" % command

def rtr_exec_with_enable(port, username, password, command, timeout=30):
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

def safe_log_output(log_lines, prefix, output, error=""):
    full_output = (output or "") + ("\n" + error if error else "")
    log_lines.append("%s:\n%s\n" % (prefix, full_output))

def cert_validity_days(openssl_dates_output):
    """Вычисляет срок действия сертификата (в днях) из вывода openssl x509 -startdate -enddate."""
    if not openssl_dates_output:
        return None
    dates = {}
    for m in re.finditer(r'(notBefore|notAfter)=\s*(\w+)\s+(\d+)\s+[\d:]+\s+(\d{4})', openssl_dates_output):
        key, mon, day, year = m.group(1), m.group(2), int(m.group(3)), int(m.group(4))
        try:
            dt = datetime.strptime("%s %d %d" % (mon, day, year), "%b %d %Y")
            dates[key] = dt
        except ValueError:
            return None
    if "notBefore" in dates and "notAfter" in dates:
        return (dates["notAfter"] - dates["notBefore"]).days
    return None

# ========== ГЛАВНАЯ ФУНКЦИЯ ==========
def run_full_assignment_check(vm_ports):
    POINTS = 0.0
    MAX_POINTS = 10.0
    log_lines = []
    results = {}

    def log_msg(msg):
        log_lines.append(msg)
        print(msg)

    log_msg("\n🔍 Доступные SSH-порты:")
    for device, port in sorted(vm_ports.items()):
        log_msg(f"  {device}: {port}")
    log_msg("")

    log_msg("🔍 Начало комплексной проверки Модуля 3 (M3-V3)")

    # --- Пункт 1: Импорт пользователей из users.csv ---
    log_msg("\n📌 Пункт 1: Импорт пользователей из users.csv")
    import_ok = True
    custom_users = []

    if "BR-SRV" not in vm_ports:
        log_msg("⚠️ BR-SRV не найден")
        import_ok = False
    else:
        users_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "samba-tool user list", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: samba-tool user list")
        safe_log_output(log_lines, "[BR-SRV] Вывод", users_out, "")

        if not users_out:
            log_msg("❌ Не удалось получить список пользователей")
            import_ok = False
        else:
            standard_users = {"administrator", "guest", "krbtgt"}
            user_list = [u.strip() for u in users_out.splitlines() if u.strip()]
            custom_users = [u for u in user_list
                            if u.lower() not in standard_users and not u.lower().startswith("dns-")
                            and not u.lower().startswith("hquser")]
            if len(custom_users) < 1:
                log_msg("❌ Импортированные из users.csv пользователи не найдены")
                import_ok = False
            else:
                log_msg(f"✅ Найдено {len(custom_users)} импортированных пользователей")

        if import_ok and "HQ-CLI" in vm_ports and custom_users:
            test_user = custom_users[0]
            test_pass = "P@ssw0rd"

            auth_out, auth_err, _ = ssh_exec(vm_ports["HQ-CLI"], "whoami", test_user, test_pass)
            auth_success = auth_out and test_user.lower() in auth_out.strip().lower()
            log_msg("[HQ-CLI] SSH вход как %s: %s" % (test_user, "OK" if auth_success else "FAIL"))
            safe_log_output(log_lines, "[HQ-CLI] Вывод", auth_out, auth_err)

            if not auth_success:
                wb_out, _, _ = ssh_exec(vm_ports["HQ-CLI"],
                    "wbinfo -a '%s%%\"%s\"'" % (test_user, test_pass), "root", "toor")
                log_msg("[HQ-CLI] wbinfo -a %s" % test_user)
                safe_log_output(log_lines, "[HQ-CLI] Вывод", wb_out, "")
                if wb_out and "succeeded" in wb_out.lower():
                    auth_success = True

            if not auth_success:
                log_msg("❌ Импортированные пользователи недоступны на HQ-CLI")
                import_ok = False
            else:
                log_msg("✅ Импортированные пользователи доступны на HQ-CLI")

    if import_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 1 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 1 не пройден")
    results["Пункт 1: Импорт пользователей"] = import_ok

    # --- Пункт 2: Центр сертификации на HQ-SRV ---
    log_msg("\n📌 Пункт 2: Центр сертификации на HQ-SRV (срок сертификата %d дней)" % CERT_DAYS)
    ca_ok = True

    if "HQ-SRV" not in vm_ports:
        log_msg("⚠️ HQ-SRV не найден")
        ca_ok = False
    else:
        ca_cert, _, _ = ssh_exec(vm_ports["HQ-SRV"],
            "ls /etc/pki/CA/ /root/ca /root/CA 2>/dev/null; find / -maxdepth 4 \\( -name 'ca*.crt' -o -name 'ca*.pem' -o -name 'cacert*' \\) 2>/dev/null | head -5",
            "root", "toor")
        log_msg("[HQ-SRV] Поиск CA-сертификата")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", ca_cert, "")
        if not ca_cert:
            log_msg("❌ CA-сертификат не найден")
            ca_ok = False

        # HTTPS на nginx (ISP) + срок действия и алгоритм сертификата веб-сервера
        if "ISP" in vm_ports:
            nginx_conf, _, _ = ssh_exec(vm_ports["ISP"],
                "cat /etc/nginx/sites-enabled.d/* 2>/dev/null; cat /etc/nginx/conf.d/* 2>/dev/null; cat /etc/nginx/sites-enabled/* 2>/dev/null",
                "root", "toor")
            log_msg("[ISP] Проверка конфигурации nginx (HTTPS)")
            safe_log_output(log_lines, "[ISP] Вывод", nginx_conf, "")
            if nginx_conf:
                has_ssl = bool(re.search(r'listen\s+443\s+ssl', nginx_conf)) or "ssl_certificate" in nginx_conf
                if has_ssl:
                    log_msg("✅ Nginx настроен на HTTPS (443/ssl)")
                else:
                    log_msg("❌ Nginx не настроен на HTTPS")
                    ca_ok = False
            else:
                log_msg("❌ Конфигурация nginx не найдена")
                ca_ok = False

            # Срок действия сертификата веб-сервера (строгая проверка варианта)
            cert_out, _, _ = ssh_exec(vm_ports["ISP"],
                "echo | openssl s_client -connect localhost:443 -servername web.au-team.irpo 2>/dev/null | openssl x509 -noout -startdate -enddate 2>/dev/null",
                "root", "toor")
            log_msg("[ISP] Проверка срока действия сертификата web.au-team.irpo")
            safe_log_output(log_lines, "[ISP] Вывод", cert_out, "")
            days = cert_validity_days(cert_out)
            if days is None:
                log_msg("❌ Не удалось получить срок действия сертификата")
                ca_ok = False
            elif days == CERT_DAYS:
                log_msg("✅ Срок действия сертификата = %d дней" % days)
            else:
                log_msg("❌ Срок действия сертификата = %d дней (ожидалось %d)" % (days, CERT_DAYS))
                ca_ok = False

            # Отечественные алгоритмы (ГОСТ) — информационно
            algo_out, _, _ = ssh_exec(vm_ports["ISP"],
                "echo | openssl s_client -connect localhost:443 -servername web.au-team.irpo 2>/dev/null | openssl x509 -noout -text 2>/dev/null | grep -i 'Signature Algorithm' | head -1",
                "root", "toor")
            log_msg("[ISP] Алгоритм подписи сертификата")
            safe_log_output(log_lines, "[ISP] Вывод", algo_out, "")
            if algo_out and re.search(r'gost|1\.2\.643', algo_out, re.IGNORECASE):
                log_msg("✅ Используются отечественные алгоритмы (ГОСТ)")
            else:
                log_msg("⚠️ Не удалось подтвердить использование ГОСТ-алгоритмов")

        if "HQ-CLI" in vm_ports:
            trust_out, _, _ = ssh_exec(vm_ports["HQ-CLI"], "trust list 2>/dev/null | grep -i au-team", "root", "toor")
            log_msg("[HQ-CLI] Выполняется команда: trust list | grep au-team")
            safe_log_output(log_lines, "[HQ-CLI] Вывод", trust_out, "")
            # Информационно — доверие может быть настроено разными способами

    if ca_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 2 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 2 не пройден")
    results["Пункт 2: Центр сертификации"] = ca_ok

    # --- Пункт 3: Защищённый IP-туннель (IPsec) ---
    log_msg("\n📌 Пункт 3: Защищённый туннель (IPsec)")
    ipsec_ok = True

    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports:
            log_msg(f"⚠️ {rtr} не найден")
            ipsec_ok = False
            continue

        ipsec_out, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show running-config | include ipsec")
        log_msg(f"[{rtr}] Выполняется команда: show running-config | include ipsec")
        safe_log_output(log_lines, f"[{rtr}] Вывод", ipsec_out, "")
        if not (ipsec_out and "ipsec" in ipsec_out.lower()):
            log_msg(f"❌ {rtr}: IPsec не настроен")
            ipsec_ok = False

        ospf_out, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show ip ospf neighbor")
        log_msg(f"[{rtr}] Выполняется команда: show ip ospf neighbor")
        safe_log_output(log_lines, f"[{rtr}] Вывод", ospf_out, "")
        if not (ospf_out and "Full" in ospf_out):
            log_msg(f"❌ {rtr}: OSPF-сосед не в состоянии Full после перенастройки туннеля")
            ipsec_ok = False
        else:
            log_msg(f"✅ {rtr}: OSPF-сосед активен")

    if ipsec_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 3 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 3 не пройден")
    results["Пункт 3: IPsec"] = ipsec_ok

    # --- Пункт 4: Межсетевой экран на маршрутизаторах ---
    log_msg("\n📌 Пункт 4: Межсетевой экран (HQ-RTR, BR-RTR)")
    fw_ok = True

    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports:
            fw_ok = False
            continue
        fw_out, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show running-config | include access-list")
        log_msg(f"[{rtr}] Выполняется команда: show running-config | include access-list")
        safe_log_output(log_lines, f"[{rtr}] Вывод", fw_out, "")
        if not (fw_out and "access-list" in fw_out.lower()):
            log_msg(f"❌ {rtr}: ACL межсетевого экрана не настроен")
            fw_ok = False
        else:
            log_msg(f"✅ {rtr}: ACL найден")

    # Функциональный тест: HTTPS до веб-сайта работает (разрешённый протокол)
    if "ISP" in vm_ports:
        https_out, _, _ = ssh_exec(vm_ports["ISP"],
            "curl -sk -o /dev/null -w '%{http_code}' https://localhost 2>/dev/null", "root", "toor")
        log_msg("[ISP] Проверка доступности HTTPS")
        safe_log_output(log_lines, "[ISP] Вывод", https_out, "")

    if fw_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 4 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 4 не пройден")
    results["Пункт 4: Межсетевой экран"] = fw_ok

    # --- Пункт 5: CUPS принт-сервер на HQ-SRV ---
    log_msg("\n📌 Пункт 5: CUPS принт-сервер на HQ-SRV")
    cups_ok = True

    if "HQ-SRV" not in vm_ports:
        cups_ok = False
    else:
        cups_status, _, _ = ssh_exec(vm_ports["HQ-SRV"], "systemctl is-active cups", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: systemctl is-active cups")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", cups_status, "")
        if not (cups_status and cups_status.strip() == "active"):
            log_msg("❌ CUPS не запущен")
            cups_ok = False

        printers_out, _, _ = ssh_exec(vm_ports["HQ-SRV"], "lpstat -p", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: lpstat -p")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", printers_out, "")
        if not (printers_out and re.search(r'(pdf|virtual)', printers_out, re.IGNORECASE)):
            log_msg("❌ Виртуальный PDF-принтер не найден")
            cups_ok = False
        else:
            log_msg("✅ PDF-принтер найден")

    if cups_ok and "HQ-CLI" in vm_ports:
        default_printer, _, _ = ssh_exec(vm_ports["HQ-CLI"], "lpstat -d", "root", "toor")
        log_msg("[HQ-CLI] Выполняется команда: lpstat -d")
        safe_log_output(log_lines, "[HQ-CLI] Вывод", default_printer, "")
        if not (default_printer and "system default destination" in default_printer.lower()):
            log_msg("❌ Принтер по умолчанию не настроен на HQ-CLI")
            cups_ok = False
        else:
            log_msg("✅ Принтер по умолчанию настроен на HQ-CLI")

    if cups_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 5 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 5 не пройден")
    results["Пункт 5: CUPS"] = cups_ok

    # --- Пункт 6: Syslog (rsyslog) на HQ-SRV ---
    log_msg("\n📌 Пункт 6: Syslog на HQ-SRV (logrotate minsize %dM)" % LOGROTATE_MINSIZE)
    syslog_ok = True

    if "HQ-SRV" not in vm_ports:
        syslog_ok = False
    else:
        listen_out, _, _ = ssh_exec(vm_ports["HQ-SRV"], "ss -tulnH sport = :514", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: ss -tulnH sport = :514")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", listen_out, "")
        if not listen_out:
            log_msg("❌ rsyslog не слушает порт 514")
            syslog_ok = False

        opt_dirs, _, _ = ssh_exec(vm_ports["HQ-SRV"], "ls -la /opt/", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: ls -la /opt/")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", opt_dirs, "")
        # ожидаем поддиректории с именами устройств
        if opt_dirs and re.search(r'(hq-rtr|br-rtr|br-srv)', opt_dirs, re.IGNORECASE):
            log_msg("✅ В /opt присутствуют поддиректории устройств")
        else:
            log_msg("❌ В /opt не найдены поддиректории устройств (hq-rtr/br-rtr/br-srv)")
            syslog_ok = False

        logrotate_out, _, _ = ssh_exec(vm_ports["HQ-SRV"],
            "cat /etc/logrotate.d/*opt* 2>/dev/null; grep -rl '/opt' /etc/logrotate.d/ 2>/dev/null | xargs cat 2>/dev/null",
            "root", "toor")
        log_msg("[HQ-SRV] Проверка logrotate для /opt")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", logrotate_out, "")
        if logrotate_out:
            has_weekly = bool(re.search(r'weekly', logrotate_out, re.IGNORECASE))
            has_compress = bool(re.search(r'^\s*compress', logrotate_out, re.IGNORECASE | re.MULTILINE))
            has_minsize = bool(re.search(r'minsize\s+%d[mM]?' % LOGROTATE_MINSIZE, logrotate_out))
            if has_weekly and has_compress and has_minsize:
                log_msg("✅ Logrotate: weekly, compress, minsize %dM — корректно" % LOGROTATE_MINSIZE)
            else:
                if not has_weekly:
                    log_msg("❌ Logrotate: weekly не найдено")
                if not has_compress:
                    log_msg("❌ Logrotate: compress не найдено")
                if not has_minsize:
                    log_msg("❌ Logrotate: minsize %dM не найдено" % LOGROTATE_MINSIZE)
                syslog_ok = False
        else:
            log_msg("❌ Конфигурация logrotate для /opt не найдена")
            syslog_ok = False

    if syslog_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 6 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 6 не пройден")
    results["Пункт 6: Syslog"] = syslog_ok

    # --- Пункт 7: Мониторинг (Zabbix) на HQ-SRV ---
    log_msg("\n📌 Пункт 7: Мониторинг (Zabbix) на HQ-SRV")
    zabbix_ok = True

    if "HQ-SRV" not in vm_ports:
        zabbix_ok = False
    else:
        srv_out, _, _ = ssh_exec(vm_ports["HQ-SRV"],
            "docker ps --format '{{.Names}}' 2>/dev/null | grep -i zabbix; systemctl is-active zabbix-server 2>/dev/null",
            "root", "toor")
        log_msg("[HQ-SRV] Проверка сервера мониторинга")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", srv_out, "")
        if not (srv_out and re.search(r'(zabbix|active)', srv_out, re.IGNORECASE)):
            log_msg("❌ Сервер Zabbix не обнаружен")
            zabbix_ok = False

        if "HQ-CLI" in vm_ports:
            dns_out, _, _ = ssh_exec(vm_ports["HQ-CLI"], "nslookup mon.au-team.irpo", "root", "toor")
            log_msg("[HQ-CLI] Выполняется команда: nslookup mon.au-team.irpo")
            safe_log_output(log_lines, "[HQ-CLI] Вывод", dns_out, "")
            if not (dns_out and "Address" in dns_out and "NXDOMAIN" not in dns_out):
                log_msg("❌ DNS-запись mon.au-team.irpo не разрешается")
                zabbix_ok = False
            else:
                log_msg("✅ DNS-запись mon.au-team.irpo разрешается")

        # Агенты на HQ-SRV и BR-SRV
        for host in ["HQ-SRV", "BR-SRV"]:
            if host in vm_ports:
                agent_out, _, _ = ssh_exec(vm_ports[host],
                    "systemctl is-active zabbix-agent 2>/dev/null || systemctl is-active zabbix-agent2 2>/dev/null",
                    "root", "toor")
                log_msg("[%s] Проверка zabbix-agent" % host)
                safe_log_output(log_lines, "[%s] Вывод" % host, agent_out, "")
                if not (agent_out and "active" in agent_out):
                    log_msg("⚠️ Zabbix-agent на %s не активен" % host)

    if zabbix_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 7 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 7 не пройден")
    results["Пункт 7: Zabbix"] = zabbix_ok

    # --- Пункт 8: Ansible инвентаризация (PC-INFO) на BR-SRV ---
    log_msg("\n📌 Пункт 8: Ansible инвентаризация на BR-SRV")
    ansible_ok = True

    if "BR-SRV" not in vm_ports:
        ansible_ok = False
    else:
        playbook_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "ls /etc/ansible/*.yml /etc/ansible/*.yaml 2>/dev/null", "root", "toor")
        log_msg("[BR-SRV] Проверка наличия плейбука в /etc/ansible/")
        safe_log_output(log_lines, "[BR-SRV] Вывод", playbook_out, "")
        if not playbook_out:
            log_msg("❌ Плейбук не найден в /etc/ansible/")
            ansible_ok = False

        pcinfo_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "ls /etc/ansible/PC-INFO/ 2>/dev/null", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: ls /etc/ansible/PC-INFO/")
        safe_log_output(log_lines, "[BR-SRV] Вывод", pcinfo_out, "")
        if not pcinfo_out:
            log_msg("❌ Директория /etc/ansible/PC-INFO/ пуста или не существует")
            ansible_ok = False
        else:
            yml_files = [f.strip() for f in pcinfo_out.splitlines() if f.strip().endswith('.yml') or f.strip().endswith('.yaml')]
            if len(yml_files) < 1:
                log_msg("❌ Файлы отчётов .yml не найдены в PC-INFO")
                ansible_ok = False
            else:
                log_msg(f"✅ Найдено {len(yml_files)} файлов в PC-INFO: {', '.join(yml_files)}")

    if ansible_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 8 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 8 не пройден")
    results["Пункт 8: Ansible инвентаризация"] = ansible_ok

    # --- Пункт 9: fail2ban на HQ-SRV ---
    log_msg("\n📌 Пункт 9: fail2ban на HQ-SRV (бан на %d мин)" % FAIL2BAN_BANTIME_MIN)
    f2b_ok = True

    if "HQ-SRV" not in vm_ports:
        f2b_ok = False
    else:
        f2b_status, _, _ = ssh_exec(vm_ports["HQ-SRV"], "systemctl is-active fail2ban 2>/dev/null", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: systemctl is-active fail2ban")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", f2b_status, "")
        if not (f2b_status and "active" in f2b_status):
            log_msg("❌ Служба fail2ban не запущена")
            f2b_ok = False

        jail_conf, _, _ = ssh_exec(vm_ports["HQ-SRV"],
            "cat /etc/fail2ban/jail.local 2>/dev/null; cat /etc/fail2ban/jail.d/*.conf 2>/dev/null; cat /etc/fail2ban/jail.d/*.local 2>/dev/null",
            "root", "toor")
        log_msg("[HQ-SRV] Проверка fail2ban jail-конфигурации")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", jail_conf, "")

        sec = FAIL2BAN_BANTIME_MIN * 60
        if jail_conf:
            has_maxretry = bool(re.search(r'maxretry\s*=\s*3\b', jail_conf))
            has_bantime = bool(re.search(r'bantime\s*=\s*(?:%dm?|%dm|0?%d)\b' % (sec, FAIL2BAN_BANTIME_MIN, FAIL2BAN_BANTIME_MIN), jail_conf)) \
                or bool(re.search(r'bantime\s*=\s*%d\b' % sec, jail_conf)) \
                or bool(re.search(r'bantime\s*=\s*%dm\b' % FAIL2BAN_BANTIME_MIN, jail_conf))
            if not has_maxretry:
                log_msg("❌ fail2ban: maxretry=3 не найдено")
                f2b_ok = False
            if not has_bantime:
                log_msg("❌ fail2ban: bantime %d мин (%ds или %dm) не найдено" % (FAIL2BAN_BANTIME_MIN, sec, FAIL2BAN_BANTIME_MIN))
                f2b_ok = False
            if has_maxretry and has_bantime:
                log_msg("✅ fail2ban: maxretry=3, bantime=%d мин — корректно" % FAIL2BAN_BANTIME_MIN)
        else:
            log_msg("❌ Конфигурация jail для fail2ban не найдена")
            f2b_ok = False

    if f2b_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 9 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 9 не пройден")
    results["Пункт 9: fail2ban"] = f2b_ok

    # --- Пункт 10: Резервное копирование (Кибер Бэкап) ---
    log_msg("\n📌 Пункт 10: Резервное копирование (Кибер Бэкап)")
    backup_ok = True

    if "HQ-SRV" not in vm_ports:
        backup_ok = False
    else:
        acronis_out, _, _ = ssh_exec(vm_ports["HQ-SRV"],
            "systemctl is-active acronis_mms 2>/dev/null || systemctl is-active cyber-protect-agent 2>/dev/null || systemctl is-active acronis_agent 2>/dev/null",
            "root", "toor")
        log_msg("[HQ-SRV] Проверка сервиса резервного копирования")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", acronis_out, "")
        if not (acronis_out and "active" in acronis_out):
            log_msg("❌ Сервис резервного копирования на HQ-SRV не найден")
            backup_ok = False
        else:
            log_msg("✅ Сервис резервного копирования активен на HQ-SRV")

    if backup_ok and "HQ-CLI" in vm_ports:
        agent_out, _, _ = ssh_exec(vm_ports["HQ-CLI"],
            "systemctl is-active acronis_mms 2>/dev/null || systemctl is-active cyber-protect-agent 2>/dev/null || systemctl is-active acronis_agent 2>/dev/null",
            "root", "toor")
        log_msg("[HQ-CLI] Проверка агента резервного копирования")
        safe_log_output(log_lines, "[HQ-CLI] Вывод", agent_out, "")
        if not (agent_out and "active" in agent_out):
            log_msg("❌ Агент резервного копирования не найден на HQ-CLI")
            backup_ok = False
        else:
            log_msg("✅ Агент резервного копирования активен на HQ-CLI")

        backup_dir, _, _ = ssh_exec(vm_ports["HQ-CLI"], "test -d /backup && echo EXISTS", "root", "toor")
        log_msg("[HQ-CLI] Проверка директории /backup")
        safe_log_output(log_lines, "[HQ-CLI] Вывод", backup_dir, "")
        if not (backup_dir and "EXISTS" in backup_dir):
            log_msg("❌ Директория /backup не найдена на HQ-CLI")
            backup_ok = False
        else:
            log_msg("✅ Директория /backup существует на HQ-CLI")

    if backup_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 10 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 10 не пройден")
    results["Пункт 10: Резервное копирование"] = backup_ok

    # --- ИТОГОВЫЙ ОТЧЁТ ---
    log_msg("\n📊 ИТОГОВЫЙ ОТЧЁТ:")
    log_msg("=" * 60)
    total_passed = 0
    for item, passed in results.items():
        status = "✅ ПРОЙДЕН" if passed else "❌ НЕ ПРОЙДЕН"
        log_msg("%s: %s" % (item, status))
        if passed:
            total_passed += 1

    log_msg("\n📈 Выполнено %.2f из %.2f пунктов." % (POINTS, MAX_POINTS))
    return POINTS, log_lines


if __name__ == "__main__":
    pass
