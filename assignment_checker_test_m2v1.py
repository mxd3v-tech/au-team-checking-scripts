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
            universal_newlines=True,
            bufsize=1
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            return None, "❌ Таймаут (%d сек)" % timeout, "Выполняется команда: %s" % command
        return stdout, stderr, "Выполняется команда: %s" % command
    except Exception as e:
        return None, "❌ Ошибка: %s" % str(e), "Выполняется команда: %s" % command

# ========== ФУНКЦИИ ДЛЯ МАРШРУТИЗАТОРОВ (EcoRouterOS) ==========
def rtr_exec(port, username, password, command, timeout=30):
    if not PEXPECT_AVAILABLE:
        return None, "pexpect не установлен", "Выполняется команда: %s" % command
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

def safe_log_output(log_lines, prefix, output, error=""):
    full_output = (output or "") + ("\n" + error if error else "")
    log_lines.append("%s:\n%s\n" % (prefix, full_output))

# ========== ГЛАВНАЯ ФУНКЦИЯ ==========
def run_full_assignment_check(vm_ports):
    POINTS = 0.0
    MAX_POINTS = 11.0
    log_lines = []
    results = {}

    def log_msg(msg):
        log_lines.append(msg)
        print(msg)

    log_msg("\n🔍 Доступные SSH-порты:")
    for device, port in sorted(vm_ports.items()):
        log_msg("  %s: %s" % (device, port))
    log_msg("")

    log_msg("🔍 Начало комплексной проверки Модуля 2")

    # --- Пункт 1: Samba DC на BR-SRV ---
    log_msg("\n📌 Пункт 1: Samba DC на BR-SRV")
    samba_ok = True

    if "BR-SRV" not in vm_ports:
        log_msg("⚠️ BR-SRV не найден")
        samba_ok = False
    else:
        # Проверка домена
        out, err, cmd = ssh_exec(vm_ports["BR-SRV"], "samba-tool domain info 127.0.0.1", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: samba-tool domain info 127.0.0.1")
        safe_log_output(log_lines, "[BR-SRV] Вывод", out, err)
        if not (out and re.search(r'Domain\s*:\s*au-team\.irpo', out)):
            log_msg("❌ Домен au-team.irpo не найден")
            samba_ok = False

        # Проверка пользователей hquser1-5
        users_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "samba-tool user list", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: samba-tool user list")
        safe_log_output(log_lines, "[BR-SRV] Вывод", users_out, "")

        hq_users = ["hquser%d" % i for i in range(1, 6)]
        if users_out:
            for user in hq_users:
                if user not in users_out:
                    log_msg("❌ Пользователь %s не найден" % user)
                    samba_ok = False
        else:
            samba_ok = False

        # Проверка группы hq
        hq_group, _, _ = ssh_exec(vm_ports["BR-SRV"], "samba-tool group show hq", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: samba-tool group show hq")
        safe_log_output(log_lines, "[BR-SRV] Вывод", hq_group, "")
        if hq_group:
            for user in hq_users:
                if user not in hq_group:
                    log_msg("❌ Пользователь %s не в группе hq" % user)
                    samba_ok = False
        else:
            log_msg("❌ Группа hq не найдена")
            samba_ok = False

        # Проверка аутентификации hquser1 на HQ-CLI
        if "HQ-CLI" in vm_ports:
            test_user = "hquser1"
            test_pass = "P@ssw0rd"

            auth_out, auth_err, _ = ssh_exec(vm_ports["HQ-CLI"], "whoami", test_user, test_pass)
            log_msg("[HQ-CLI] SSH-аутентификация %s" % test_user)
            safe_log_output(log_lines, "[HQ-CLI] Вывод", auth_out, auth_err)
            auth_success = auth_out and test_user in auth_out.strip().lower()
            if not auth_success:
                wb_out, wb_err, _ = ssh_exec(vm_ports["HQ-CLI"],
                    "wbinfo -a '%s%%\"%s\"'" % (test_user, test_pass), "root", "toor")
                log_msg("[HQ-CLI] Fallback: wbinfo -a %s" % test_user)
                safe_log_output(log_lines, "[HQ-CLI] Вывод", wb_out, wb_err)
                auth_success = wb_out and "succeeded" in wb_out.lower()
            if not auth_success:
                log_msg("❌ Аутентификация %s на HQ-CLI не удалась" % test_user)
                samba_ok = False
            else:
                # Проверка разрешённых команд (cat, grep, id)
                allowed_commands = ["cat /etc/passwd", "grep root /etc/passwd", "id"]
                for acmd in allowed_commands:
                    full_cmd = "echo '%s' | sudo -S %s" % (test_pass, acmd)
                    out_a, err_a, _ = ssh_exec(vm_ports["HQ-CLI"], full_cmd, test_user, test_pass)
                    log_msg("[HQ-CLI] Проверка разрешённой команды: %s" % acmd)
                    safe_log_output(log_lines, "[HQ-CLI] Вывод", out_a, err_a)
                    if not out_a or "Permission denied" in (err_a or "") or "password is required" in (err_a or ""):
                        log_msg("❌ %s не может выполнить: %s" % (test_user, acmd))
                        samba_ok = False

                # Проверка запрещённой команды (ps)
                forbidden_cmd = "ps aux"
                full_f = "echo '%s' | sudo -S %s" % (test_pass, forbidden_cmd)
                out_f, err_f, _ = ssh_exec(vm_ports["HQ-CLI"], full_f, test_user, test_pass)
                log_msg("[HQ-CLI] Проверка запрещённой команды: %s" % forbidden_cmd)
                safe_log_output(log_lines, "[HQ-CLI] Вывод", out_f, err_f)
                if out_f and "UID" in out_f:
                    log_msg("❌ %s смог выполнить запрещённую команду" % test_user)
                    samba_ok = False

    if samba_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 1 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 1 не пройден")
    results["Пункт 1: Samba DC"] = samba_ok

    # --- Пункт 2: RAID 0 на HQ-SRV ---
    log_msg("\n📌 Пункт 2: RAID 0 на HQ-SRV")
    raid_ok = True
    if "HQ-SRV" not in vm_ports:
        raid_ok = False
    else:
        mdstat, _, _ = ssh_exec(vm_ports["HQ-SRV"], "mdadm --detail /dev/md0 2>/dev/null", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: mdadm --detail /dev/md0")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", mdstat, "")
        if not (mdstat and "md0" in mdstat and "raid0" in mdstat.lower()):
            raid_ok = False

        mount_out, _, _ = ssh_exec(vm_ports["HQ-SRV"], "findmnt /raid --noheadings", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: findmnt /raid")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", mount_out, "")
        if not (mount_out and "/dev/md0" in mount_out and "ext4" in mount_out):
            raid_ok = False

        fstab, _, _ = ssh_exec(vm_ports["HQ-SRV"], "findmnt --fstab /raid --noheadings", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: findmnt --fstab /raid")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", fstab, "")
        if not (fstab and "md0" in fstab):
            log_msg("❌ Автомонтирование /raid не настроено")
            raid_ok = False

    if raid_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 2 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 2 не пройден")
    results["Пункт 2: RAID 0"] = raid_ok

    # --- Пункт 3: NFS на HQ-SRV ---
    log_msg("\n📌 Пункт 3: NFS на HQ-SRV")
    nfs_ok = True
    if "HQ-SRV" not in vm_ports or "HQ-CLI" not in vm_ports:
        nfs_ok = False
    else:
        exports, _, _ = ssh_exec(vm_ports["HQ-SRV"], "showmount -e localhost", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: showmount -e localhost")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", exports, "")
        if not (exports and "/raid/nfs" in exports):
            log_msg("❌ Экспорт /raid/nfs не найден")
            nfs_ok = False

        mount_cli, _, _ = ssh_exec(vm_ports["HQ-CLI"], "findmnt /mnt/nfs --noheadings", "root", "toor")
        log_msg("[HQ-CLI] Выполняется команда: findmnt /mnt/nfs")
        safe_log_output(log_lines, "[HQ-CLI] Вывод", mount_cli, "")
        if not (mount_cli and "/mnt/nfs" in mount_cli and "nfs" in mount_cli.lower()):
            log_msg("❌ /mnt/nfs не примонтирован на HQ-CLI")
            nfs_ok = False

    if nfs_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 3 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 3 не пройден")
    results["Пункт 3: NFS"] = nfs_ok

    # --- Пункт 4: Chrony на ISP ---
    log_msg("\n📌 Пункт 4: Chrony на ISP")
    chrony_ok = True
    clients = ["HQ-SRV", "HQ-CLI", "BR-RTR", "BR-SRV"]

    if "ISP" not in vm_ports:
        chrony_ok = False
    else:
        ntp_status, _, _ = ssh_exec(vm_ports["ISP"], "chronyc sources", "root", "toor")
        log_msg("[ISP] Выполняется команда: chronyc sources")
        safe_log_output(log_lines, "[ISP] Вывод", ntp_status, "")

        for client in clients:
            if client not in vm_ports:
                log_msg("⚠️ Клиент %s не найден" % client)
                chrony_ok = False
                continue

            if "RTR" in client:
                # Пробуем Linux chronyc, затем EcoRouter
                ntp_client, _, _ = ssh_exec(vm_ports[client], "chronyc sources", "root", "toor")
                if not ntp_client:
                    ntp_client, _, _ = rtr_exec_with_enable(
                        vm_ports[client], *get_rtr_creds(client), "show ntp status")
                log_msg("[%s] Проверка NTP" % client)
                safe_log_output(log_lines, "[%s] Вывод" % client, ntp_client, "")
                if not (ntp_client and ("*" in ntp_client or "^*" in ntp_client)):
                    log_msg("❌ %s не синхронизирован" % client)
                    chrony_ok = False
            else:
                chronyc_out, _, _ = ssh_exec(vm_ports[client], "chronyc sources", "root", "toor")
                log_msg("[%s] Выполняется команда: chronyc sources" % client)
                safe_log_output(log_lines, "[%s] Вывод" % client, chronyc_out, "")
                if not (chronyc_out and "^*" in chronyc_out):
                    log_msg("❌ %s не синхронизирован" % client)
                    chrony_ok = False

    if chrony_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 4 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 4 не пройден")
    results["Пункт 4: Chrony"] = chrony_ok

    # --- Пункт 5: Ansible на BR-SRV ---
    log_msg("\n📌 Пункт 5: Ansible на BR-SRV")
    ansible_ok = True
    if "BR-SRV" not in vm_ports:
        ansible_ok = False
    else:
        inventory, _, _ = ssh_exec(vm_ports["BR-SRV"], "cat /etc/ansible/hosts", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: cat /etc/ansible/hosts")
        safe_log_output(log_lines, "[BR-SRV] Вывод", inventory, "")
        required_hosts = ["hq-srv", "hq-cli", "hq-rtr", "br-rtr"]
        if inventory:
            for host in required_hosts:
                if host not in inventory.lower():
                    log_msg("❌ Хост %s не найден в инвентаре" % host)
                    ansible_ok = False
        else:
            ansible_ok = False

        ping_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "ansible all -m ping", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: ansible all -m ping")
        safe_log_output(log_lines, "[BR-SRV] Вывод", ping_out, "")
        if not (ping_out and '"pong"' in ping_out):
            log_msg("❌ ansible ping не прошёл")
            ansible_ok = False

    if ansible_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 5 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 5 не пройден")
    results["Пункт 5: Ansible"] = ansible_ok

    # --- Пункт 6: Docker на BR-SRV ---
    log_msg("\n📌 Пункт 6: Docker на BR-SRV")
    docker_ok = True

    if "BR-SRV" not in vm_ports:
        docker_ok = False
    else:
        compose_paths = [
            "/root/compose.yaml", "/opt/compose.yaml", "/etc/compose.yaml",
            "/root/docker-compose.yml", "/opt/docker-compose.yml",
            "/home/compose.yaml", "/compose.yaml",
        ]
        compose_content = None
        found_path = None
        for path in compose_paths:
            out, _, _ = ssh_exec(vm_ports["BR-SRV"], "cat %s" % path, "root", "toor")
            if out is not None and "container_name:" in out:
                compose_content = out
                found_path = path
                break

        if compose_content is None:
            log_msg("⚠️ compose-файл не найден")
        else:
            log_msg("✅ Найден: %s" % found_path)
            safe_log_output(log_lines, "[BR-SRV] Содержимое", compose_content, "")

            required_checks = {
                "container_name: db": bool(re.search(r'container_name:\s*db\b', compose_content, re.IGNORECASE)),
                "container_name: testapp": bool(re.search(r'container_name:\s*testapp', compose_content, re.IGNORECASE)),
                "MARIADB_DATABASE: testdb": bool(re.search(r'MARIADB_DATABASE:\s*testdb', compose_content)),
                "port 8080": bool(re.search(r'["\']?8080:', compose_content)),
            }
            for check_name, passed in required_checks.items():
                if not passed:
                    log_msg("❌ Отсутствует: %s" % check_name)
                    docker_ok = False

        ps_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "docker ps --format '{{.Names}}'", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: docker ps")
        safe_log_output(log_lines, "[BR-SRV] Вывод", ps_out, "")
        if ps_out:
            if "testapp" not in ps_out or "db" not in ps_out:
                log_msg("❌ Контейнеры не запущены")
                docker_ok = False
        else:
            docker_ok = False

        ss_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "ss -tlnH sport = :8080", "root", "toor")
        log_msg("[BR-SRV] Проверка порта 8080")
        safe_log_output(log_lines, "[BR-SRV] Вывод", ss_out, "")
        if not ss_out:
            log_msg("❌ Порт 8080 не слушается")
            docker_ok = False

    if docker_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 6 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 6 не пройден")
    results["Пункт 6: Docker"] = docker_ok

    # --- Пункт 7: Веб-приложение на HQ-SRV ---
    log_msg("\n📌 Пункт 7: Веб-приложение на HQ-SRV")
    web_ok = True
    if "HQ-SRV" not in vm_ports:
        web_ok = False
    else:
        httpd_out, _, _ = ssh_exec(vm_ports["HQ-SRV"],
            "systemctl is-active httpd 2>/dev/null || systemctl is-active apache2 2>/dev/null",
            "root", "toor")
        log_msg("[HQ-SRV] Проверка Apache")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", httpd_out, "")
        if not (httpd_out and httpd_out.strip() == "active"):
            log_msg("❌ Apache не активен")
            web_ok = False

        db_check, _, _ = ssh_exec(vm_ports["HQ-SRV"],
            "mysql -u web -pP@ssw0rd -BNe \"SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME='webdb'\"",
            "root", "toor")
        log_msg("[HQ-SRV] Проверка БД webdb")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", db_check, "")
        if not (db_check and "webdb" in db_check):
            log_msg("❌ БД webdb не найдена")
            web_ok = False

        curl_out, _, _ = ssh_exec(vm_ports["HQ-SRV"], "curl -s http://localhost", "root", "toor")
        log_msg("[HQ-SRV] Проверка доступности")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", curl_out, "")
        if not (curl_out and "<html" in curl_out.lower()):
            log_msg("❌ Веб-приложение недоступно")
            web_ok = False

    if web_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 7 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 7 не пройден")
    results["Пункт 7: Веб-приложение"] = web_ok

    # --- Пункт 8: Статическая трансляция портов ---
    log_msg("\n📌 Пункт 8: Статическая трансляция портов")
    portfwd_ok = True

    for rtr, checks in [("HQ-RTR", ["8080", "2026"]), ("BR-RTR", ["8080", "2026"])]:
        if rtr not in vm_ports:
            portfwd_ok = False
            continue

        nat_out, _, _ = ssh_exec(vm_ports[rtr],
            "iptables -t nat -L PREROUTING -n 2>/dev/null", "root", "toor")
        log_msg("[%s] Проверка iptables NAT PREROUTING" % rtr)
        safe_log_output(log_lines, "[%s] Вывод" % rtr, nat_out, "")

        if nat_out and "DNAT" in nat_out:
            for port in checks:
                if port in nat_out:
                    log_msg("✅ %s: проброс порта %s найден" % (rtr, port))
                else:
                    log_msg("❌ %s: проброс порта %s не найден" % (rtr, port))
                    portfwd_ok = False
        else:
            eco_out, _, _ = rtr_exec(vm_ports[rtr], *get_rtr_creds(rtr),
                "show running-config | include static")
            log_msg("[%s] EcoRouter: show running-config | include static" % rtr)
            safe_log_output(log_lines, "[%s] Вывод" % rtr, eco_out, "")
            if eco_out:
                for port in checks:
                    if port in eco_out:
                        log_msg("✅ %s: проброс порта %s найден" % (rtr, port))
                    else:
                        log_msg("❌ %s: проброс порта %s не найден" % (rtr, port))
                        portfwd_ok = False
            else:
                log_msg("❌ %s: проброс портов не найден" % rtr)
                portfwd_ok = False

    if portfwd_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 8 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 8 не пройден")
    results["Пункт 8: Проброс портов"] = portfwd_ok

    # --- Пункт 9: Nginx reverse proxy на ISP ---
    log_msg("\n📌 Пункт 9: Nginx reverse proxy на ISP")
    nginx_ok = True

    if "ISP" not in vm_ports:
        nginx_ok = False
    else:
        all_conf, _, _ = ssh_exec(vm_ports["ISP"],
            "cat /etc/nginx/sites-enabled.d/*.conf 2>/dev/null; cat /etc/nginx/conf.d/*.conf 2>/dev/null",
            "root", "toor")
        log_msg("[ISP] Конфигурация nginx")
        safe_log_output(log_lines, "[ISP] Вывод", all_conf, "")

        if all_conf:
            if "web.au-team.irpo" not in all_conf:
                log_msg("❌ Конфиг для web.au-team.irpo не найден")
                nginx_ok = False
            else:
                log_msg("✅ Конфиг для web.au-team.irpo найден")
            if "docker.au-team.irpo" not in all_conf:
                log_msg("❌ Конфиг для docker.au-team.irpo не найден")
                nginx_ok = False
            else:
                log_msg("✅ Конфиг для docker.au-team.irpo найден")
        else:
            log_msg("❌ Конфигурация nginx не найдена")
            nginx_ok = False

    if nginx_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 9 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 9 не пройден")
    results["Пункт 9: Nginx Reverse Proxy"] = nginx_ok

    # --- Пункт 10: Web-based auth на ISP ---
    log_msg("\n📌 Пункт 10: Web-based auth на ISP")
    auth_ok = True
    if "ISP" not in vm_ports:
        auth_ok = False
    else:
        htpasswd, _, _ = ssh_exec(vm_ports["ISP"],
            "htpasswd -vb /etc/nginx/.htpasswd WEB 'P@ssw0rd' 2>&1", "root", "toor")
        log_msg("[ISP] Проверка htpasswd: WEB / P@ssw0rd")
        safe_log_output(log_lines, "[ISP] Вывод", htpasswd, "")
        if not (htpasswd and (
            "correct" in htpasswd.lower() or
            "password verified" in htpasswd.lower() or
            "WEB:" in htpasswd)):
            log_msg("❌ Аутентификация WEB не настроена")
            auth_ok = False

    if auth_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 10 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 10 не пройден")
    results["Пункт 10: Web Auth"] = auth_ok

    # --- Пункт 11: Яндекс Браузер на HQ-CLI ---
    log_msg("\n📌 Пункт 11: Яндекс Браузер на HQ-CLI")
    browser_ok = True
    if "HQ-CLI" not in vm_ports:
        browser_ok = False
    else:
        which_out, _, _ = ssh_exec(vm_ports["HQ-CLI"],
            "which yandex-browser-stable 2>/dev/null || which yandex-browser 2>/dev/null",
            "root", "toor")
        log_msg("[HQ-CLI] Проверка Яндекс Браузера")
        safe_log_output(log_lines, "[HQ-CLI] Вывод", which_out, "")
        if not (which_out and "/" in which_out and "no " not in which_out):
            log_msg("❌ Яндекс Браузер не установлен")
            browser_ok = False

    if browser_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 11 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 11 не пройден")
    results["Пункт 11: Yandex Browser"] = browser_ok

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
