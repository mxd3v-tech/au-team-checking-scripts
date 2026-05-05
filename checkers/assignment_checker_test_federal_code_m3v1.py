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
    MAX_POINTS = 10.0
    log_lines = []
    results = {}

    def log_msg(msg):
        log_lines.append(msg)
        print(msg)

    # --- Вывод портов SSH ---
    log_msg("\n🔍 Доступные SSH-порты:")
    for device, port in sorted(vm_ports.items()):
        log_msg("  %s: %s" % (device, port))
    log_msg("")

    log_msg("🔍 Начало комплексной проверки Модуля 3 (M3-V1)")

    # Определяем тип маршрутизаторов
    hq_rtr_linux = is_rtr_linux(vm_ports, "HQ-RTR") if "HQ-RTR" in vm_ports else False
    br_rtr_linux = is_rtr_linux(vm_ports, "BR-RTR") if "BR-RTR" in vm_ports else False
    if hq_rtr_linux:
        log_msg("ℹ️  HQ-RTR определён как Linux-маршрутизатор")
    if br_rtr_linux:
        log_msg("ℹ️  BR-RTR определён как Linux-маршрутизатор")

    # --- Пункт 1: Импорт пользователей (задание п.1) ---
    log_msg("\n📌 Пункт 1: Импорт пользователей из users.csv")
    import_ok = True

    if "BR-SRV" not in vm_ports:
        log_msg("⚠️ BR-SRV не найден")
        import_ok = False
    else:
        # Проверяем список пользователей в домене
        users_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "samba-tool user list", "root", "toor")
        log_msg("[BR-SRV] samba-tool user list")
        safe_log_output(log_lines, "[BR-SRV] Вывод", users_out, "")

        custom_users = []
        if not users_out:
            log_msg("❌ Не удалось получить список пользователей")
            import_ok = False
        else:
            standard_users = {"administrator", "guest", "krbtgt", "dns-hq-srv"}
            user_list = [u.strip().lower() for u in users_out.splitlines() if u.strip()]
            custom_users = [u for u in user_list if u not in standard_users]
            if len(custom_users) < 3:
                log_msg("❌ Найдено слишком мало импортированных пользователей: %d" % len(custom_users))
                import_ok = False
            else:
                log_msg("✅ Найдено %d пользователей в домене" % len(custom_users))

        # Проверка входа на HQ-CLI — SSH как доменный пользователь + fallback wbinfo
        if import_ok and "HQ-CLI" in vm_ports and custom_users:
            test_user = custom_users[0]
            test_pass = "P@ssw0rd"

            auth_out, auth_err, _ = ssh_exec(vm_ports["HQ-CLI"], "whoami", test_user, test_pass)
            auth_success = auth_out and test_user in auth_out.strip().lower()
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
                log_msg("❌ Доменные пользователи не доступны на HQ-CLI")
                import_ok = False
            else:
                log_msg("✅ Доменные пользователи доступны на HQ-CLI")

    if import_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 1 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 1 не пройден")
    results["Пункт 1: Импорт пользователей"] = import_ok

    # --- Пункт 2: Центр сертификации (задание п.2) ---
    log_msg("\n📌 Пункт 2: Центр сертификации на HQ-SRV")
    ca_ok = True

    if "HQ-SRV" not in vm_ports:
        log_msg("⚠️ HQ-SRV не найден")
        ca_ok = False
    else:
        # Проверяем наличие CA-сертификата (ГОСТ алгоритмы)
        ca_cert, _, _ = ssh_exec(vm_ports["HQ-SRV"],
            "find /etc/pki /etc/ssl /root /opt -maxdepth 3 "
            "\\( -name 'ca*.pem' -o -name 'ca*.crt' -o -name 'rootCA*' \\) "
            "2>/dev/null | head -5", "root", "toor")
        log_msg("[HQ-SRV] Поиск CA-сертификата")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", ca_cert, "")
        if not ca_cert or not ca_cert.strip():
            log_msg("❌ CA-сертификат не найден")
            ca_ok = False
        else:
            # Проверяем срок действия (30 дней) и алгоритм
            cert_path = ca_cert.strip().splitlines()[0].strip()
            cert_info, _, _ = ssh_exec(vm_ports["HQ-SRV"],
                "openssl x509 -in '%s' -noout -text 2>/dev/null | head -20" % cert_path,
                "root", "toor")
            log_msg("[HQ-SRV] Информация о CA-сертификате")
            safe_log_output(log_lines, "[HQ-SRV] Вывод", cert_info, "")
            if cert_info:
                # Проверяем отечественные алгоритмы (ГОСТ)
                has_gost = bool(re.search(r'gost|GOST|grasshopper|magma|id-tc26', cert_info, re.IGNORECASE))
                if has_gost:
                    log_msg("✅ Используются отечественные алгоритмы (ГОСТ)")
                else:
                    log_msg("⚠️ Отечественные алгоритмы (ГОСТ) не обнаружены в сертификате")

        # Проверка HTTPS на nginx (ISP — реверсивный прокси)
        if "ISP" in vm_ports:
            nginx_conf, _, _ = ssh_exec(vm_ports["ISP"],
                "cat /etc/nginx/sites-enabled/*.conf /etc/nginx/sites-enabled.d/*.conf "
                "/etc/nginx/conf.d/*.conf 2>/dev/null", "root", "toor")
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

        # Проверяем HTTPS доступность web.au-team.irpo и docker.au-team.irpo с HQ-CLI
        if "HQ-CLI" in vm_ports:
            for domain in ["web.au-team.irpo", "docker.au-team.irpo"]:
                curl_out, _, _ = ssh_exec(vm_ports["HQ-CLI"],
                    "curl -sk -o /dev/null -w '%%{http_code}' https://%s 2>/dev/null" % domain,
                    "root", "toor")
                log_msg("[HQ-CLI] curl https://%s → %s" % (domain, (curl_out or "").strip()))
                if curl_out and curl_out.strip() in ("200", "301", "302"):
                    log_msg("✅ https://%s доступен" % domain)
                else:
                    log_msg("❌ https://%s недоступен" % domain)
                    ca_ok = False

    if ca_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 2 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 2 не пройден")
    results["Пункт 2: Центр сертификации"] = ca_ok

    # --- Пункт 3: Шифрованный туннель (задание п.3) ---
    log_msg("\n📌 Пункт 3: Шифрованный туннель между HQ-RTR и BR-RTR")
    tunnel_ok = True

    for rtr_name, rtr_is_linux in [("HQ-RTR", hq_rtr_linux), ("BR-RTR", br_rtr_linux)]:
        if rtr_name not in vm_ports:
            log_msg("⚠️ %s не найден" % rtr_name)
            tunnel_ok = False
            continue

        if rtr_is_linux:
            # Проверяем WireGuard или IPsec на Linux
            wg_out, _, _ = ssh_exec(vm_ports[rtr_name], "wg show 2>/dev/null", "root", "toor")
            ipsec_out, _, _ = ssh_exec(vm_ports[rtr_name],
                "ipsec status 2>/dev/null || strongswan status 2>/dev/null || "
                "ip xfrm state 2>/dev/null", "root", "toor")
            log_msg("[%s] Проверка шифрованного туннеля (Linux)" % rtr_name)
            safe_log_output(log_lines, "[%s] wg show" % rtr_name, wg_out, "")
            safe_log_output(log_lines, "[%s] ipsec/xfrm" % rtr_name, ipsec_out, "")

            has_wg = wg_out and ("interface" in wg_out.lower() or "peer" in wg_out.lower())
            has_ipsec = ipsec_out and (
                "established" in ipsec_out.lower() or
                "INSTALLED" in (ipsec_out or "") or
                "proto esp" in (ipsec_out or "").lower() or
                "src" in (ipsec_out or ""))
            if has_wg:
                log_msg("✅ %s: WireGuard туннель активен" % rtr_name)
            elif has_ipsec:
                log_msg("✅ %s: IPsec туннель активен" % rtr_name)
            else:
                log_msg("❌ %s: Шифрованный туннель не обнаружен" % rtr_name)
                tunnel_ok = False

            # Проверяем OSPF
            ospf_out, _, _ = ssh_exec(vm_ports[rtr_name],
                "vtysh -c 'show ip ospf neighbor' 2>/dev/null", "root", "toor")
            log_msg("[%s] OSPF neighbor (Linux)" % rtr_name)
            safe_log_output(log_lines, "[%s] Вывод" % rtr_name, ospf_out, "")
            if ospf_out and "Full" in ospf_out:
                log_msg("✅ %s: OSPF neighbor активен" % rtr_name)
            else:
                log_msg("❌ %s: OSPF neighbor не в состоянии Full" % rtr_name)
                tunnel_ok = False
        else:
            # EcoRouter — проверяем ipsec в running-config
            u, p = get_rtr_creds(rtr_name)
            ipsec_out, _, _ = rtr_exec_with_enable(vm_ports[rtr_name], u, p,
                "show running-config")
            log_msg("[%s] show running-config (EcoRouter)" % rtr_name)
            safe_log_output(log_lines, "[%s] Вывод" % rtr_name, ipsec_out, "")
            if ipsec_out and re.search(r'ipsec|wireguard|crypto', ipsec_out, re.IGNORECASE):
                log_msg("✅ %s: Шифрованный туннель настроен" % rtr_name)
            else:
                log_msg("❌ %s: Шифрованный туннель не настроен" % rtr_name)
                tunnel_ok = False

            # OSPF
            ospf_out, _, _ = rtr_exec(vm_ports[rtr_name], u, p, "show ip ospf neighbor")
            log_msg("[%s] show ip ospf neighbor" % rtr_name)
            safe_log_output(log_lines, "[%s] Вывод" % rtr_name, ospf_out, "")
            if ospf_out and "Full" in ospf_out:
                log_msg("✅ %s: OSPF neighbor активен" % rtr_name)
            else:
                log_msg("❌ %s: OSPF neighbor не в состоянии Full" % rtr_name)
                tunnel_ok = False

    if tunnel_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 3 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 3 не пройден")
    results["Пункт 3: Шифрованный туннель"] = tunnel_ok

    # --- Пункт 4: Межсетевой экран (задание п.4) ---
    log_msg("\n📌 Пункт 4: Межсетевой экран на HQ-RTR и BR-RTR")
    fw_ok = True

    for rtr_name, rtr_is_linux in [("HQ-RTR", hq_rtr_linux), ("BR-RTR", br_rtr_linux)]:
        if rtr_name not in vm_ports:
            log_msg("⚠️ %s не найден" % rtr_name)
            fw_ok = False
            continue

        if rtr_is_linux:
            # Проверяем iptables/nftables
            ipt_out, _, _ = ssh_exec(vm_ports[rtr_name],
                "iptables -L -n 2>/dev/null | head -40", "root", "toor")
            nft_out, _, _ = ssh_exec(vm_ports[rtr_name],
                "nft list ruleset 2>/dev/null | head -40", "root", "toor")
            log_msg("[%s] Проверка межсетевого экрана (Linux)" % rtr_name)
            safe_log_output(log_lines, "[%s] iptables" % rtr_name, ipt_out, "")
            safe_log_output(log_lines, "[%s] nftables" % rtr_name, nft_out, "")

            has_fw = False
            if ipt_out and ("DROP" in ipt_out or "REJECT" in ipt_out):
                has_fw = True
            if nft_out and ("drop" in nft_out or "reject" in nft_out):
                has_fw = True
            if ipt_out and "policy DROP" in ipt_out:
                has_fw = True

            if has_fw:
                log_msg("✅ %s: Межсетевой экран настроен" % rtr_name)
            else:
                log_msg("❌ %s: Межсетевой экран не настроен (нет правил блокировки)" % rtr_name)
                fw_ok = False
        else:
            # EcoRouter — проверяем ACL
            u, p = get_rtr_creds(rtr_name)
            acl_out, _, _ = rtr_exec(vm_ports[rtr_name], u, p,
                "show running-config | include access-list")
            log_msg("[%s] show running-config | include access-list" % rtr_name)
            safe_log_output(log_lines, "[%s] Вывод" % rtr_name, acl_out, "")
            if acl_out and "access-list" in acl_out.lower():
                log_msg("✅ %s: ACL найден" % rtr_name)
            else:
                log_msg("❌ %s: ACL не настроен" % rtr_name)
                fw_ok = False

    # Проверяем что ICMP работает через фаервол (базовый тест)
    if "ISP" in vm_ports:
        ping_out, _, _ = ssh_exec(vm_ports["ISP"], "ping -c 2 -W 3 172.16.1.2", "root", "toor")
        log_msg("[ISP] ping -c 2 172.16.1.2")
        safe_log_output(log_lines, "[ISP] Вывод", ping_out, "")
        if ping_out and ("1 received" in ping_out or "2 received" in ping_out):
            log_msg("✅ ICMP через межсетевой экран работает")
        else:
            log_msg("⚠️ ICMP пинг не прошёл (информационно)")

    if fw_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 4 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 4 не пройден")
    results["Пункт 4: Межсетевой экран"] = fw_ok

    # --- Пункт 5: CUPS принт-сервер (задание п.5) ---
    log_msg("\n📌 Пункт 5: CUPS принт-сервер на HQ-SRV")
    cups_ok = True

    if "HQ-SRV" not in vm_ports:
        log_msg("⚠️ HQ-SRV не найден")
        cups_ok = False
    else:
        cups_status, _, _ = ssh_exec(vm_ports["HQ-SRV"], "systemctl is-active cups", "root", "toor")
        log_msg("[HQ-SRV] systemctl is-active cups")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", cups_status, "")
        if not (cups_status and cups_status.strip() == "active"):
            log_msg("❌ CUPS не запущен")
            cups_ok = False

        # Проверяем наличие PDF-принтера
        printers_out, _, _ = ssh_exec(vm_ports["HQ-SRV"], "lpstat -p 2>/dev/null", "root", "toor")
        log_msg("[HQ-SRV] lpstat -p")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", printers_out, "")
        if not (printers_out and re.search(r'(pdf|PDF|virtual|cups-pdf)', printers_out, re.IGNORECASE)):
            log_msg("❌ PDF-принтер не найден")
            cups_ok = False
        else:
            log_msg("✅ PDF-принтер найден на HQ-SRV")

    # Проверяем принтер по умолчанию на HQ-CLI
    if cups_ok and "HQ-CLI" in vm_ports:
        default_printer, _, _ = ssh_exec(vm_ports["HQ-CLI"], "lpstat -d 2>/dev/null", "root", "toor")
        log_msg("[HQ-CLI] lpstat -d")
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

    # --- Пункт 6: rsyslog (задание п.6) ---
    log_msg("\n📌 Пункт 6: rsyslog логирование на HQ-SRV")
    syslog_ok = True

    if "HQ-SRV" not in vm_ports:
        log_msg("⚠️ HQ-SRV не найден")
        syslog_ok = False
    else:
        # Проверяем что rsyslog слушает сеть (порт 514)
        listen_out, _, _ = ssh_exec(vm_ports["HQ-SRV"], "ss -tulnH sport = :514", "root", "toor")
        log_msg("[HQ-SRV] ss -tulnH sport = :514")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", listen_out, "")
        if not (listen_out and listen_out.strip()):
            log_msg("❌ rsyslog не слушает порт 514")
            syslog_ok = False
        else:
            log_msg("✅ rsyslog слушает порт 514")

        # Проверяем наличие директорий /opt для каждого устройства
        expected_dirs = ["hq-rtr", "br-rtr", "br-srv"]
        opt_dirs, _, _ = ssh_exec(vm_ports["HQ-SRV"], "ls -1 /opt/", "root", "toor")
        log_msg("[HQ-SRV] ls /opt/")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", opt_dirs, "")
        if opt_dirs:
            existing = [d.strip().lower() for d in opt_dirs.splitlines() if d.strip()]
            for edir in expected_dirs:
                if edir in existing:
                    log_msg("✅ Директория /opt/%s найдена" % edir)
                else:
                    log_msg("❌ Директория /opt/%s не найдена" % edir)
                    syslog_ok = False
        else:
            log_msg("❌ Директория /opt пуста или не доступна")
            syslog_ok = False

        # Проверяем что HQ-SRV не отправляет логи самому себе
        rsyslog_conf, _, _ = ssh_exec(vm_ports["HQ-SRV"],
            "cat /etc/rsyslog.conf /etc/rsyslog.d/*.conf 2>/dev/null", "root", "toor")
        log_msg("[HQ-SRV] Проверка конфигурации rsyslog")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", rsyslog_conf, "")
        if rsyslog_conf and re.search(r'@@?(127\.0\.0\.1|localhost|hq-srv)', rsyslog_conf, re.IGNORECASE):
            log_msg("⚠️ HQ-SRV может отправлять логи самому себе")

        # Проверяем logrotate для /opt
        logrotate_out, _, _ = ssh_exec(vm_ports["HQ-SRV"],
            "cat /etc/logrotate.d/*opt* /etc/logrotate.d/*syslog* 2>/dev/null; "
            "grep -rl '/opt' /etc/logrotate.d/ 2>/dev/null | xargs cat 2>/dev/null", "root", "toor")
        log_msg("[HQ-SRV] Проверка logrotate для /opt")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", logrotate_out, "")
        if logrotate_out:
            has_weekly = bool(re.search(r'weekly', logrotate_out, re.IGNORECASE))
            has_compress = bool(re.search(r'compress', logrotate_out, re.IGNORECASE))
            has_minsize = bool(re.search(r'minsize\s+10[mM]', logrotate_out))
            if has_weekly and has_compress and has_minsize:
                log_msg("✅ Logrotate: weekly, compress, minsize 10M — корректно")
            else:
                if not has_weekly:
                    log_msg("❌ Logrotate: weekly не найдено")
                if not has_compress:
                    log_msg("❌ Logrotate: compress не найдено")
                if not has_minsize:
                    log_msg("❌ Logrotate: minsize 10M не найдено")
                syslog_ok = False
        else:
            log_msg("❌ Конфигурация logrotate для /opt не найдена")
            syslog_ok = False

    if syslog_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 6 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 6 не пройден")
    results["Пункт 6: rsyslog"] = syslog_ok

    # --- Пункт 7: Мониторинг (задание п.7) ---
    log_msg("\n📌 Пункт 7: Мониторинг на HQ-SRV (mon.au-team.irpo)")
    mon_ok = True

    if "HQ-SRV" not in vm_ports:
        log_msg("⚠️ HQ-SRV не найден")
        mon_ok = False
    else:
        # Проверяем запущенные сервисы мониторинга (zabbix, prometheus, grafana, nagios и т.д.)
        docker_out, _, _ = ssh_exec(vm_ports["HQ-SRV"],
            "docker ps --format '{{.Names}}' 2>/dev/null; "
            "podman ps --format '{{.Names}}' 2>/dev/null", "root", "toor")
        log_msg("[HQ-SRV] Проверка контейнеров мониторинга")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", docker_out, "")

        systemd_out, _, _ = ssh_exec(vm_ports["HQ-SRV"],
            "systemctl is-active zabbix-server prometheus grafana-server nagios 2>/dev/null",
            "root", "toor")
        log_msg("[HQ-SRV] Проверка сервисов мониторинга")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", systemd_out, "")

        has_monitoring = False
        if docker_out and re.search(r'zabbix|prometheus|grafana|nagios', docker_out, re.IGNORECASE):
            has_monitoring = True
            log_msg("✅ Контейнер мониторинга найден")
        if systemd_out and "active" in systemd_out:
            has_monitoring = True
            log_msg("✅ Сервис мониторинга активен")
        if not has_monitoring:
            log_msg("❌ Сервисы мониторинга не найдены")
            mon_ok = False

        # Проверяем доступность по mon.au-team.irpo с HQ-CLI
        if "HQ-CLI" in vm_ports:
            dns_out, _, _ = ssh_exec(vm_ports["HQ-CLI"],
                "host mon.au-team.irpo 2>/dev/null || nslookup mon.au-team.irpo 2>/dev/null",
                "root", "toor")
            log_msg("[HQ-CLI] DNS mon.au-team.irpo")
            safe_log_output(log_lines, "[HQ-CLI] Вывод", dns_out, "")
            if dns_out and ("address" in dns_out.lower() or "Address" in dns_out):
                log_msg("✅ DNS-запись mon.au-team.irpo разрешается")
            else:
                log_msg("❌ DNS-запись mon.au-team.irpo не разрешается")
                mon_ok = False

            # Проверяем HTTP-доступность
            curl_out, _, _ = ssh_exec(vm_ports["HQ-CLI"],
                "curl -s -o /dev/null -w '%%{http_code}' http://mon.au-team.irpo 2>/dev/null",
                "root", "toor")
            log_msg("[HQ-CLI] curl http://mon.au-team.irpo → %s" % (curl_out or "").strip())
            if curl_out and curl_out.strip() in ("200", "302", "301", "303"):
                log_msg("✅ Мониторинг доступен по HTTP")
            else:
                log_msg("❌ Мониторинг недоступен по http://mon.au-team.irpo")
                mon_ok = False

        # Проверяем агент мониторинга на BR-SRV
        if "BR-SRV" in vm_ports:
            agent_out, _, _ = ssh_exec(vm_ports["BR-SRV"],
                "systemctl is-active zabbix-agent 2>/dev/null || "
                "systemctl is-active zabbix-agent2 2>/dev/null || "
                "systemctl is-active prometheus-node-exporter 2>/dev/null || "
                "systemctl is-active node_exporter 2>/dev/null",
                "root", "toor")
            log_msg("[BR-SRV] Проверка агента мониторинга")
            safe_log_output(log_lines, "[BR-SRV] Вывод", agent_out, "")
            if agent_out and "active" in agent_out:
                log_msg("✅ Агент мониторинга на BR-SRV активен")
            else:
                log_msg("⚠️ Агент мониторинга на BR-SRV не обнаружен")

    if mon_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 7 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 7 не пройден")
    results["Пункт 7: Мониторинг"] = mon_ok

    # --- Пункт 8: Ansible инвентаризация (задание п.8) ---
    log_msg("\n📌 Пункт 8: Ansible инвентаризация на BR-SRV")
    ansible_ok = True

    if "BR-SRV" not in vm_ports:
        log_msg("⚠️ BR-SRV не найден")
        ansible_ok = False
    else:
        # Проверяем наличие плейбука
        playbook_out, _, _ = ssh_exec(vm_ports["BR-SRV"],
            "ls /etc/ansible/*.yml /etc/ansible/*.yaml 2>/dev/null", "root", "toor")
        log_msg("[BR-SRV] Проверка наличия плейбука в /etc/ansible/")
        safe_log_output(log_lines, "[BR-SRV] Вывод", playbook_out, "")
        if not playbook_out or not playbook_out.strip():
            log_msg("❌ Плейбук не найден в /etc/ansible/")
            ansible_ok = False

        # Проверяем директорию PC-INFO
        pcinfo_out, _, _ = ssh_exec(vm_ports["BR-SRV"],
            "ls /etc/ansible/PC-INFO/ 2>/dev/null", "root", "toor")
        log_msg("[BR-SRV] ls /etc/ansible/PC-INFO/")
        safe_log_output(log_lines, "[BR-SRV] Вывод", pcinfo_out, "")
        if not pcinfo_out or not pcinfo_out.strip():
            log_msg("❌ Директория /etc/ansible/PC-INFO/ пуста или не существует")
            ansible_ok = False
        else:
            yml_files = [f.strip() for f in pcinfo_out.splitlines()
                         if f.strip().endswith('.yml') or f.strip().endswith('.yaml')]
            if len(yml_files) < 1:
                log_msg("❌ Файлы отчётов .yml не найдены в PC-INFO")
                ansible_ok = False
            else:
                log_msg("✅ Найдено %d файлов в PC-INFO: %s" % (len(yml_files), ', '.join(yml_files)))
                # Проверяем что файлы содержат имя компьютера и IP
                for yf in yml_files[:2]:
                    content, _, _ = ssh_exec(vm_ports["BR-SRV"],
                        "cat '/etc/ansible/PC-INFO/%s' 2>/dev/null" % yf, "root", "toor")
                    log_msg("[BR-SRV] Содержимое %s" % yf)
                    safe_log_output(log_lines, "[BR-SRV] Вывод", content, "")
                    if content and ("hostname" in content.lower() or "ip" in content.lower()
                                    or "ansible_host" in content.lower() or "address" in content.lower()):
                        log_msg("✅ Файл %s содержит информацию о машине" % yf)
                    else:
                        log_msg("⚠️ Файл %s может не содержать нужную информацию" % yf)

    if ansible_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 8 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 8 не пройден")
    results["Пункт 8: Ansible инвентаризация"] = ansible_ok

    # --- Пункт 9: fail2ban (задание п.9) ---
    log_msg("\n📌 Пункт 9: fail2ban на HQ-SRV")
    f2b_ok = True

    if "HQ-SRV" not in vm_ports:
        log_msg("⚠️ HQ-SRV не найден")
        f2b_ok = False
    else:
        # Проверяем fail2ban активен
        f2b_status, _, _ = ssh_exec(vm_ports["HQ-SRV"],
            "systemctl is-active fail2ban", "root", "toor")
        log_msg("[HQ-SRV] systemctl is-active fail2ban")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", f2b_status, "")
        if not (f2b_status and f2b_status.strip() == "active"):
            log_msg("❌ fail2ban не запущен")
            f2b_ok = False

        # Проверяем конфигурацию jail
        jail_conf, _, _ = ssh_exec(vm_ports["HQ-SRV"],
            "cat /etc/fail2ban/jail.local 2>/dev/null; "
            "cat /etc/fail2ban/jail.d/*.conf 2>/dev/null; "
            "cat /etc/fail2ban/jail.d/*.local 2>/dev/null", "root", "toor")
        log_msg("[HQ-SRV] Проверка fail2ban jail конфигурации")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", jail_conf, "")

        if jail_conf:
            # Проверяем порт ssh
            has_ssh_port = bool(re.search(r'port\s*=\s*(ssh|2026|22)', jail_conf))
            # maxretry = 3
            has_maxretry = bool(re.search(r'maxretry\s*=\s*3', jail_conf))
            # bantime = 60 или 1m
            has_bantime = bool(re.search(r'bantime\s*=\s*(60|1m)', jail_conf))

            if has_ssh_port:
                log_msg("✅ fail2ban: порт SSH указан")
            else:
                log_msg("❌ fail2ban: порт SSH не указан")
                f2b_ok = False

            if has_maxretry:
                log_msg("✅ fail2ban: maxretry=3")
            else:
                log_msg("❌ fail2ban: maxretry=3 не найдено")
                f2b_ok = False

            if has_bantime:
                log_msg("✅ fail2ban: bantime=60s/1m")
            else:
                log_msg("❌ fail2ban: bantime=60/1m не найдено")
                f2b_ok = False
        else:
            log_msg("❌ Конфигурация fail2ban jail не найдена")
            f2b_ok = False

    if f2b_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 9 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 9 не пройден")
    results["Пункт 9: fail2ban"] = f2b_ok

    # --- Пункт 10: Резервное копирование (задание п.10) ---
    log_msg("\n📌 Пункт 10: Резервное копирование (Кибер Бэкап)")
    backup_ok = True

    if "HQ-SRV" not in vm_ports:
        log_msg("⚠️ HQ-SRV не найден")
        backup_ok = False
    else:
        # Проверяем что сервис бэкапа запущен на HQ-SRV
        acronis_out, _, _ = ssh_exec(vm_ports["HQ-SRV"],
            "systemctl is-active acronis_mms 2>/dev/null || "
            "systemctl is-active cyber-protect 2>/dev/null || "
            "systemctl is-active acronis_service 2>/dev/null || "
            "systemctl is-active cyber-backup 2>/dev/null",
            "root", "toor")
        log_msg("[HQ-SRV] Проверка сервисов резервного копирования")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", acronis_out, "")
        if not (acronis_out and "active" in acronis_out):
            log_msg("❌ Сервисы резервного копирования не найдены на HQ-SRV")
            backup_ok = False
        else:
            log_msg("✅ Сервисы резервного копирования активны на HQ-SRV")

    # Проверяем агент на HQ-CLI
    if backup_ok and "HQ-CLI" in vm_ports:
        agent_out, _, _ = ssh_exec(vm_ports["HQ-CLI"],
            "systemctl is-active acronis_mms 2>/dev/null || "
            "systemctl is-active cyber-protect 2>/dev/null || "
            "systemctl is-active acronis_service 2>/dev/null || "
            "systemctl is-active cyber-backup 2>/dev/null",
            "root", "toor")
        log_msg("[HQ-CLI] Проверка агента резервного копирования")
        safe_log_output(log_lines, "[HQ-CLI] Вывод", agent_out, "")
        if not (agent_out and "active" in agent_out):
            log_msg("❌ Агент резервного копирования не найден на HQ-CLI")
            backup_ok = False
        else:
            log_msg("✅ Агент резервного копирования активен на HQ-CLI")

        # Проверяем директорию /backup
        backup_dir, _, _ = ssh_exec(vm_ports["HQ-CLI"],
            "test -d /backup && echo 'EXISTS' || echo 'MISSING'", "root", "toor")
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

    log_msg("\n📈 Выполнено %.1f из %.1f пунктов." % (POINTS, MAX_POINTS))
    return POINTS, log_lines


if __name__ == "__main__":
    pass
