import subprocess
import re
import sys

try:
    import pexpect
    PEXPECT_AVAILABLE = True
except ImportError:
    PEXPECT_AVAILABLE = False
    print("⚠️  Модуль pexpect не установлен. Проверка маршрутизаторов будет пропущена.", file=sys.stderr)

# ========== УЧЁТНЫЕ ДАННЫЕ ==========
SRV_CREDENTIALS = {
    "BR-SRV": ("root", "toor"),
    "HQ-SRV": ("root", "toor"),
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

# ========== ГЛАВНАЯ ФУНКЦИЯ ==========
def run_full_assignment_check(vm_ports):
    POINTS = 0.0
    MAX_POINTS = 25.0
    log_lines = []
    results = {}

    def log_msg(msg):
        log_lines.append(msg)
        print(msg)

    def award(name, score, max_score, details=""):
        nonlocal POINTS; POINTS += score
        results[name] = {"score": score, "max": max_score}
        icon = "✅" if score == max_score else ("⚠️" if score > 0 else "❌")
        log_msg("%s %s: %d/%d%s" % (icon, name, score, max_score, " — " + details if details else ""))

    # --- Вывод портов SSH ---
    log_msg("\n🔍 Доступные SSH-порты:")
    for device, port in sorted(vm_ports.items()):
        log_msg(f"  {device}: {port}")
    log_msg("")

    log_msg("🔍 Модуль 3 (M3-V1), КО 09.02.06-1-2026")

    DEVICE_NAMES = {
        "BR-SRV": "br-srv.au-team.irpo",
        "HQ-SRV": "hq-srv.au-team.irpo",
        "HQ-CLI": "hq-cli.au-team.irpo",
        "BR-CLI": "br-cli.au-team.irpo",
        "HQ-RTR": "hq-rtr",
        "BR-RTR": "br-rtr",
        "ISP": "isp",
    }

    # --- Пункт 1: Импорт пользователей (задание п.11) ---
    log_msg("\n📌 Пункт 1: Импорт пользователей из users.csv")
    import_ok = True

    if "BR-SRV" not in vm_ports:
        log_msg("⚠️ BR-SRV не найден")
        import_ok = False
    else:
        # Проверяем список пользователей в домене
        users_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "samba-tool user list", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: samba-tool user list")
        safe_log_output(log_lines, "[BR-SRV] Вывод", users_out, "")

        if not users_out:
            log_msg("❌ Не удалось получить список пользователей")
            import_ok = False
        else:
            # Считаем количество пользователей (исключаем стандартных: Administrator, Guest, krbtgt и т.д.)
            standard_users = {"administrator", "guest", "krbtgt", "dns-hq-srv"}
            user_list = [u.strip().lower() for u in users_out.splitlines() if u.strip()]
            custom_users = [u for u in user_list if u not in standard_users]
            if len(custom_users) < 3:
                log_msg(f"❌ Найдено слишком мало импортированных пользователей: {len(custom_users)}")
                import_ok = False
            else:
                log_msg(f"✅ Найдено {len(custom_users)} пользователей в домене")

        # Проверка входа на HQ-CLI — SSH как доменный пользователь + fallback wbinfo
        if import_ok and "HQ-CLI" in vm_ports and custom_users:
            test_user = custom_users[0]
            test_pass = "P@ssw0rd"

            # Попытка SSH как доменный пользователь (создаёт профиль при первом входе)
            auth_out, auth_err, _ = ssh_exec(vm_ports["HQ-CLI"], "whoami", test_user, test_pass)
            auth_success = auth_out and test_user in auth_out.strip().lower()
            log_msg("[HQ-CLI] SSH вход как %s: %s" % (test_user, "OK" if auth_success else "FAIL"))
            safe_log_output(log_lines, "[HQ-CLI] Вывод", auth_out, auth_err)

            if not auth_success:
                # Fallback: wbinfo
                wb_out, _, _ = ssh_exec(vm_ports["HQ-CLI"],
                    "wbinfo -a '%s%%\"%s\"'" % (test_user, test_pass), "root", "toor")
                log_msg("[HQ-CLI] wbinfo -a %s" % test_user)
                safe_log_output(log_lines, "[HQ-CLI] Вывод", wb_out, "")
                if wb_out and "succeeded" in wb_out.lower():
                    auth_success = True
                    log_msg("✅ wbinfo аутентификация %s успешна" % test_user)

            if not auth_success:
                log_msg("❌ Доменные пользователи не доступны на HQ-CLI")
                import_ok = False
            else:
                log_msg("✅ Доменные пользователи доступны на HQ-CLI")

    if import_ok: award("КО: Импорт пользователей", 2, 2)
    else: award("КО: Импорт пользователей", 0, 2)

    # --- Пункт 2: Центр сертификации (задание п.12) ---
    log_msg("\n📌 Пункт 2: Центр сертификации на HQ-SRV")
    ca_ok = True

    if "HQ-SRV" not in vm_ports:
        log_msg("⚠️ HQ-SRV не найден")
        ca_ok = False
    else:
        # Проверяем наличие CA-сертификата
        ca_cert, _, _ = ssh_exec(vm_ports["HQ-SRV"], "ls /etc/pki/CA/certs/ 2>/dev/null || ls /etc/ssl/certs/ca-* 2>/dev/null || ls /root/ca* 2>/dev/null || find / -maxdepth 3 -name 'ca*.pem' -o -name 'ca*.crt' 2>/dev/null | head -5", "root", "toor")
        log_msg("[HQ-SRV] Поиск CA-сертификата")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", ca_cert, "")
        if not ca_cert:
            log_msg("❌ CA-сертификат не найден")
            ca_ok = False

        # Проверка HTTPS на nginx (ISP)
        if "ISP" in vm_ports:
            nginx_conf, _, _ = ssh_exec(vm_ports["ISP"], "cat /etc/nginx/sites-enabled.d/*.conf 2>/dev/null || cat /etc/nginx/conf.d/*.conf 2>/dev/null", "root", "toor")
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

        # Проверяем доверие на HQ-CLI
        if "HQ-CLI" in vm_ports:
            trust_out, _, _ = ssh_exec(vm_ports["HQ-CLI"], "trust list | head -30", "root", "toor")
            log_msg("[HQ-CLI] Выполняется команда: trust list | head -30")
            safe_log_output(log_lines, "[HQ-CLI] Вывод", trust_out, "")
            # Информационно — не фейлим, т.к. trust может быть настроен по-разному

    if ca_ok: award("КО: Центр сертификации", 3, 3)
    else: award("КО: Центр сертификации", 0, 3)

    # --- Пункт 3: IPsec туннель (задание п.13) ---
    log_msg("\n📌 Пункт 3: IPsec туннель")
    ipsec_ok = True

    for rtr in ["HQ-RTR", "BR-RTR"]:
        if rtr not in vm_ports:
            log_msg(f"⚠️ {rtr} не найден")
            ipsec_ok = False
            continue

        # Проверяем наличие ipsec в конфигурации
        ipsec_out, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show running-config | include ipsec")
        log_msg(f"[{rtr}] Выполняется команда: show running-config | include ipsec")
        safe_log_output(log_lines, f"[{rtr}] Вывод", ipsec_out, "")
        if not (ipsec_out and "ipsec" in ipsec_out.lower()):
            log_msg(f"❌ {rtr}: IPsec не настроен")
            ipsec_ok = False

        # Проверяем OSPF всё ещё работает
        ospf_out, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr), "show ip ospf neighbor")
        log_msg(f"[{rtr}] Выполняется команда: show ip ospf neighbor")
        safe_log_output(log_lines, f"[{rtr}] Вывод", ospf_out, "")
        if not (ospf_out and ("Full" in ospf_out)):
            log_msg(f"❌ {rtr}: OSPF neighbor не в состоянии Full")
            ipsec_ok = False
        else:
            log_msg(f"✅ {rtr}: OSPF neighbor активен")

    if ipsec_ok: award("КО: IPsec туннель", 2, 2)
    else: award("КО: IPsec туннель", 0, 2)

    # --- Пункт 4: Межсетевой экран (задание п.14) ---
    log_msg("\n📌 Пункт 4: Межсетевой экран")
    fw_ok = True

    if "HQ-RTR" in vm_ports:
        fw_out, _, _ = rtr_exec(vm_ports["HQ-RTR"], *get_rtr_creds("HQ-RTR"), "show running-config | include access-list")
        log_msg("[HQ-RTR] Выполняется команда: show running-config | include access-list")
        safe_log_output(log_lines, "[HQ-RTR] Вывод", fw_out, "")
        if not (fw_out and "access-list" in fw_out.lower()):
            log_msg("❌ HQ-RTR: ACL не настроен")
            fw_ok = False
        else:
            log_msg("✅ HQ-RTR: ACL найден")
    else:
        fw_ok = False

    # Проверяем что ICMP работает (базовый тест)
    if "HQ-CLI" in vm_ports and "ISP" in vm_ports:
        # Пинг с ISP на HQ-RTR внешний интерфейс (должен работать — icmp разрешён)
        ping_out, _, _ = ssh_exec(vm_ports["ISP"], "ping -c 2 172.16.1.2", "root", "toor")
        log_msg("[ISP] Выполняется команда: ping -c 2 172.16.1.2")
        safe_log_output(log_lines, "[ISP] Вывод", ping_out, "")
        if ping_out and "2 received" in ping_out:
            log_msg("✅ ICMP через межсетевой экран работает")
        else:
            log_msg("⚠️ ICMP пинг не прошёл (может быть нормально если блокируется)")

    if fw_ok: award("КО: Межсетевой экран", 2, 2)
    else: award("КО: Межсетевой экран", 0, 2)

    # --- Пункт 5: CUPS принт-сервер (задание п.15) ---
    log_msg("\n📌 Пункт 5: CUPS принт-сервер на HQ-SRV")
    cups_ok = True

    if "HQ-SRV" not in vm_ports:
        cups_ok = False
    else:
        # Проверяем что cups работает
        cups_status, _, _ = ssh_exec(vm_ports["HQ-SRV"], "systemctl is-active cups", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: systemctl is-active cups")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", cups_status, "")
        if not (cups_status and cups_status.strip() == "active"):
            log_msg("❌ CUPS не запущен")
            cups_ok = False

        # Проверяем наличие PDF-принтера
        printers_out, _, _ = ssh_exec(vm_ports["HQ-SRV"], "lpstat -p", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: lpstat -p")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", printers_out, "")
        if not (printers_out and re.search(r'(pdf|PDF|virtual)', printers_out, re.IGNORECASE)):
            log_msg("❌ PDF-принтер не найден")
            cups_ok = False
        else:
            log_msg("✅ PDF-принтер найден")

    # Проверяем принтер по умолчанию на HQ-CLI
    if cups_ok and "HQ-CLI" in vm_ports:
        default_printer, _, _ = ssh_exec(vm_ports["HQ-CLI"], "lpstat -d", "root", "toor")
        log_msg("[HQ-CLI] Выполняется команда: lpstat -d")
        safe_log_output(log_lines, "[HQ-CLI] Вывод", default_printer, "")
        if not (default_printer and "system default destination" in default_printer.lower()):
            log_msg("❌ Принтер по умолчанию не настроен на HQ-CLI")
            cups_ok = False
        else:
            log_msg("✅ Принтер по умолчанию настроен на HQ-CLI")

    if cups_ok: award("КО: CUPS", 1, 1)
    else: award("КО: CUPS", 0, 1)

    # --- Пункт 6a: Syslog (задание п.16) ---
    log_msg("\n📌 КО: Syslog на HQ-SRV")
    syslog_ok = True
    logrotate_ok = True

    if "HQ-SRV" not in vm_ports:
        syslog_ok = False
        logrotate_ok = False
    else:
        # Проверяем rsyslog конфигурацию
        rsyslog_conf, _, _ = ssh_exec(vm_ports["HQ-SRV"], "cat /etc/rsyslog.conf 2>/dev/null; ls /etc/rsyslog.d/ 2>/dev/null", "root", "toor")
        log_msg("[HQ-SRV] Проверка конфигурации rsyslog")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", rsyslog_conf, "")

        # Проверяем что rsyslog слушает сеть (UDP 514 или TCP 514)
        listen_out, _, _ = ssh_exec(vm_ports["HQ-SRV"], "ss -tulnH sport = :514", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: ss -tulnH sport = :514")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", listen_out, "")
        if not listen_out:
            log_msg("❌ rsyslog не слушает порт 514")
            syslog_ok = False

        # Проверяем наличие директорий в /opt
        opt_dirs, _, _ = ssh_exec(vm_ports["HQ-SRV"], "ls -la /opt/", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: ls -la /opt/")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", opt_dirs, "")

        # Проверяем ротацию логов (отдельный критерий КО)
        logrotate_out, _, _ = ssh_exec(vm_ports["HQ-SRV"], "cat /etc/logrotate.d/*opt* 2>/dev/null || grep -r '/opt' /etc/logrotate.d/ 2>/dev/null || grep -r '/opt' /etc/logrotate.conf 2>/dev/null", "root", "toor")
        log_msg("[HQ-SRV] Проверка logrotate для /opt")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", logrotate_out, "")
        if logrotate_out:
            has_weekly = bool(re.search(r'weekly', logrotate_out, re.IGNORECASE))
            has_compress = bool(re.search(r'compress', logrotate_out, re.IGNORECASE))
            has_minsize = bool(re.search(r'minsize\s+10[mM]', logrotate_out))
            if has_weekly and has_compress and has_minsize:
                log_msg("✅ Logrotate: weekly, compress, minsize 10M — корректно")
            else:
                if not has_weekly: log_msg("❌ Logrotate: weekly не найдено")
                if not has_compress: log_msg("❌ Logrotate: compress не найдено")
                if not has_minsize: log_msg("❌ Logrotate: minsize 10M не найдено")
                logrotate_ok = False
        else:
            log_msg("❌ Конфигурация logrotate для /opt не найдена")
            logrotate_ok = False

    if syslog_ok: award("КО: Syslog", 2, 2)
    else: award("КО: Syslog", 0, 2)

    if logrotate_ok: award("КО: Ротация логов", 1, 1)
    else: award("КО: Ротация логов", 0, 1)

    # --- Пункт 7: Zabbix мониторинг (задание п.17) ---
    log_msg("\n📌 Пункт 7: Zabbix мониторинг на HQ-SRV")
    zabbix_ok = True

    if "HQ-SRV" not in vm_ports:
        zabbix_ok = False
    else:
        # Проверяем контейнер Zabbix
        docker_out, _, _ = ssh_exec(vm_ports["HQ-SRV"], "docker ps --format '{{.Names}}' 2>/dev/null || podman ps --format '{{.Names}}' 2>/dev/null", "root", "toor")
        log_msg("[HQ-SRV] Проверка контейнеров мониторинга")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", docker_out, "")
        if not (docker_out and re.search(r'zabbix', docker_out, re.IGNORECASE)):
            log_msg("❌ Контейнер Zabbix не найден")
            zabbix_ok = False

        # Проверяем доступность веб-интерфейса
        curl_out, _, _ = ssh_exec(vm_ports["HQ-SRV"], "curl -s -o /dev/null -w '%{http_code}' http://localhost:8080 2>/dev/null || curl -s -o /dev/null -w '%{http_code}' http://localhost:80 2>/dev/null", "root", "toor")
        log_msg("[HQ-SRV] Проверка доступности веб-интерфейса Zabbix")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", curl_out, "")
        if not (curl_out and ("200" in curl_out or "302" in curl_out)):
            log_msg("⚠️ Веб-интерфейс мониторинга недоступен локально")

        # Проверяем DNS-запись mon.au-team.irpo
        if "HQ-CLI" in vm_ports:
            dns_out, _, _ = ssh_exec(vm_ports["HQ-CLI"], "nslookup mon.au-team.irpo", "root", "toor")
            log_msg("[HQ-CLI] Выполняется команда: nslookup mon.au-team.irpo")
            safe_log_output(log_lines, "[HQ-CLI] Вывод", dns_out, "")
            if not (dns_out and "Address" in dns_out):
                log_msg("❌ DNS-запись mon.au-team.irpo не разрешается")
                zabbix_ok = False
            else:
                log_msg("✅ DNS-запись mon.au-team.irpo разрешается")

        # Проверяем zabbix-agent на BR-SRV
        if "BR-SRV" in vm_ports:
            agent_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "systemctl is-active zabbix-agent 2>/dev/null || systemctl is-active zabbix-agent2 2>/dev/null", "root", "toor")
            log_msg("[BR-SRV] Проверка zabbix-agent")
            safe_log_output(log_lines, "[BR-SRV] Вывод", agent_out, "")
            if not (agent_out and "active" in agent_out):
                log_msg("⚠️ Zabbix-agent на BR-SRV не активен")

    if zabbix_ok: award("КО: Zabbix мониторинг", 3, 3)
    else: award("КО: Zabbix мониторинг", 0, 3)

    # --- Пункт 8: Ansible инвентаризация (задание п.18) ---
    log_msg("\n📌 Пункт 8: Ansible инвентаризация на BR-SRV")
    ansible_ok = True

    if "BR-SRV" not in vm_ports:
        ansible_ok = False
    else:
        # Проверяем наличие плейбука
        playbook_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "ls /etc/ansible/*.yml /etc/ansible/*.yaml 2>/dev/null", "root", "toor")
        log_msg("[BR-SRV] Проверка наличия плейбука в /etc/ansible/")
        safe_log_output(log_lines, "[BR-SRV] Вывод", playbook_out, "")
        if not playbook_out:
            log_msg("❌ Плейбук не найден в /etc/ansible/")
            ansible_ok = False

        # Проверяем директорию PC-INFO
        pcinfo_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "ls /etc/ansible/PC-INFO/ 2>/dev/null", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: ls /etc/ansible/PC-INFO/")
        safe_log_output(log_lines, "[BR-SRV] Вывод", pcinfo_out, "")
        if not pcinfo_out:
            log_msg("❌ Директория /etc/ansible/PC-INFO/ пуста или не существует")
            ansible_ok = False
        else:
            # Проверяем наличие файлов .yml
            yml_files = [f.strip() for f in pcinfo_out.splitlines() if f.strip().endswith('.yml') or f.strip().endswith('.yaml')]
            if len(yml_files) < 1:
                log_msg("❌ Файлы отчётов .yml не найдены в PC-INFO")
                ansible_ok = False
            else:
                log_msg(f"✅ Найдено {len(yml_files)} файлов в PC-INFO: {', '.join(yml_files)}")

    if ansible_ok: award("КО: Ansible инвентаризация", 2, 2)
    else: award("КО: Ansible инвентаризация", 0, 2)

    # --- Пункт 9: PAM / fail2ban (задание п.19) ---
    log_msg("\n📌 Пункт 9: PAM защита SSH на HQ-SRV")
    pam_ok = True

    if "HQ-SRV" not in vm_ports:
        pam_ok = False
    else:
        # Проверяем fail2ban
        f2b_status, _, _ = ssh_exec(vm_ports["HQ-SRV"], "systemctl is-active fail2ban 2>/dev/null", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: systemctl is-active fail2ban")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", f2b_status, "")

        # Проверяем pam_faillock или pam_tally2
        pam_conf, _, _ = ssh_exec(vm_ports["HQ-SRV"], "grep -r 'pam_faillock\\|pam_tally\\|fail2ban' /etc/pam.d/sshd /etc/pam.d/system-auth /etc/pam.d/common-auth 2>/dev/null", "root", "toor")
        log_msg("[HQ-SRV] Проверка PAM конфигурации для SSH")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", pam_conf, "")

        # Также проверяем fail2ban jail
        jail_conf, _, _ = ssh_exec(vm_ports["HQ-SRV"], "cat /etc/fail2ban/jail.local 2>/dev/null || cat /etc/fail2ban/jail.d/*.conf 2>/dev/null", "root", "toor")
        log_msg("[HQ-SRV] Проверка fail2ban jail конфигурации")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", jail_conf, "")

        has_protection = False

        # Вариант 1: fail2ban
        if f2b_status and "active" in f2b_status:
            if jail_conf:
                has_maxretry = bool(re.search(r'maxretry\s*=\s*3', jail_conf))
                has_bantime = bool(re.search(r'bantime\s*=\s*(60|1m)', jail_conf))
                if has_maxretry and has_bantime:
                    log_msg("✅ fail2ban: maxretry=3, bantime=60s — корректно")
                    has_protection = True
                else:
                    if not has_maxretry:
                        log_msg("❌ fail2ban: maxretry=3 не найдено")
                    if not has_bantime:
                        log_msg("❌ fail2ban: bantime=60 не найдено")

        # Вариант 2: pam_faillock
        if not has_protection and pam_conf:
            has_deny = bool(re.search(r'deny\s*=\s*3', pam_conf))
            has_unlock = bool(re.search(r'unlock_time\s*=\s*60', pam_conf))
            if has_deny and has_unlock:
                log_msg("✅ pam_faillock: deny=3, unlock_time=60 — корректно")
                has_protection = True

        if not has_protection:
            log_msg("❌ Защита SSH (fail2ban или pam_faillock) не настроена корректно")
            pam_ok = False

    if pam_ok: award("КО: fail2ban", 1, 1)
    else: award("КО: fail2ban", 0, 1)

    # --- Пункт 10: Резервное копирование (задание п.20) ---
    log_msg("\n📌 Пункт 10: Резервное копирование (Кибер Бэкап)")
    backup_ok = True

    if "HQ-SRV" not in vm_ports:
        backup_ok = False
    else:
        # Проверяем что сервис бэкапа запущен
        acronis_out, _, _ = ssh_exec(vm_ports["HQ-SRV"],
            "systemctl is-active acronis_mms 2>/dev/null || systemctl is-active cyber-protect 2>/dev/null || systemctl is-active acronis_service 2>/dev/null",
            "root", "toor")
        log_msg("[HQ-SRV] Проверка сервисов резервного копирования")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", acronis_out, "")
        if not (acronis_out and "active" in acronis_out):
            log_msg("❌ Сервисы резервного копирования не найдены")
            backup_ok = False
        else:
            log_msg("✅ Сервисы резервного копирования активны")

    # Проверяем агент на HQ-CLI
    if backup_ok and "HQ-CLI" in vm_ports:
        agent_out, _, _ = ssh_exec(vm_ports["HQ-CLI"],
            "systemctl is-active acronis_mms 2>/dev/null || systemctl is-active cyber-protect 2>/dev/null || systemctl is-active acronis_service 2>/dev/null",
            "root", "toor")
        log_msg("[HQ-CLI] Проверка агента резервного копирования")
        safe_log_output(log_lines, "[HQ-CLI] Вывод", agent_out, "")
        if not (agent_out and "active" in agent_out):
            log_msg("❌ Агент резервного копирования не найден на HQ-CLI")
            backup_ok = False
        else:
            log_msg("✅ Агент резервного копирования активен на HQ-CLI")

        # Проверяем директорию /backup
        backup_dir, _, _ = ssh_exec(vm_ports["HQ-CLI"], "test -d /backup && ls /backup/", "root", "toor")
        log_msg("[HQ-CLI] Выполняется команда: test -d /backup && ls /backup/")
        safe_log_output(log_lines, "[HQ-CLI] Вывод", backup_dir, "")
        if not backup_dir:
            log_msg("❌ Директория /backup не найдена на HQ-CLI")
            backup_ok = False
        else:
            log_msg("✅ Директория /backup существует на HQ-CLI")

    if backup_ok: award("КО: Резервное копирование", 3, 3)
    else: award("КО: Резервное копирование", 0, 3)

    # --- Ansible бэкап RTR (КО требует, max 2) ---
    log_msg("\n📌 КО: Ansible бэкап RTR на BR-SRV")
    ansible_bak_ok = True
    if "BR-SRV" not in vm_ports:
        ansible_bak_ok = False
    else:
        bak_pb, _, _ = ssh_exec(vm_ports["BR-SRV"], "find /etc/ansible -name '*.yml' -o -name '*.yaml' 2>/dev/null | xargs grep -l -i 'backup\\|config\\|running' 2>/dev/null", "root", "toor")
        log_msg("[BR-SRV] Поиск плейбука бэкапа конфигурации")
        safe_log_output(log_lines, "[BR-SRV] Вывод", bak_pb, "")
        bak_files, _, _ = ssh_exec(vm_ports["BR-SRV"], "find /etc/ansible -name '*rtr*' -o -name '*backup*' -o -name '*config*' 2>/dev/null | head -10", "root", "toor")
        safe_log_output(log_lines, "[BR-SRV] Вывод", bak_files, "")
        if not bak_pb and not bak_files:
            ansible_bak_ok = False
    if ansible_bak_ok: award("КО: Ansible бэкап RTR", 2, 2)
    else: award("КО: Ansible бэкап RTR", 0, 2)

    # --- ИТОГОВЫЙ ОТЧЁТ ---
    # Добавляем ротацию логов (max 1) — из syslog проверки
    # (уже включена в syslog_ok, но КО считает отдельно)

    log_msg("\n📊 ИТОГО (M3-V1, КО 09.02.06-1-2026):")
    log_msg("=" * 60)
    for item, d in results.items():
        icon = "✅" if d["score"] == d["max"] else ("⚠️" if d["score"] > 0 else "❌")
        log_msg("%s %s: %d/%d" % (icon, item, d["score"], d["max"]))
    log_msg("\n📈 Набрано %.1f из %.1f баллов" % (POINTS, MAX_POINTS))
    return POINTS, log_lines


if __name__ == "__main__":
    pass
