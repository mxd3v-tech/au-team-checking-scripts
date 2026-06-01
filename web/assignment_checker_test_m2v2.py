import subprocess
import re
import sys

try:
    import pexpect
    PEXPECT_AVAILABLE = True
except ImportError:
    PEXPECT_AVAILABLE = False
    print("⚠️  Модуль pexpect не установлен. Проверка маршрутизаторов будет пропущена.", file=sys.stderr)

# ========== ПАРАМЕТРЫ ВАРИАНТА (В2) ==========
RAID_LEVEL = "raid5"        # уровень дискового массива
RAID_DEV = "md2"            # имя устройства массива
RAID_DISKS = 3              # количество дисков в массиве
NTP_STRATUM = 7             # стратум NTP-сервера на ISP
DOCKER_DB_IMAGE = "mariadb" # образ СУБД в docker
DOCKER_DB_NAME = "testdb2"  # имя БД в docker-стеке
DOCKER_DB_USER = "test2c"   # пользователь БД в docker-стеке
APP_PORT = "8082"           # порт веб-приложения (docker)
WEB_DB_USER = "web2c"       # пользователь БД веб-приложения на HQ-SRV
NGINX_AUTH_LOGIN = "Kozma"  # логин web-аутентификации на ISP

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

def safe_log_output(log_lines, prefix, output, error=""):
    full_output = (output or "") + ("\n" + error if error else "")
    log_lines.append("%s:\n%s\n" % (prefix, full_output))

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

    log_msg("🔍 Начало комплексной проверки Модуля 2 (M2-V2)")

    # --- Пункт 1: Samba DC на BR-SRV ---
    log_msg("\n📌 Пункт 1: Samba DC на BR-SRV")
    samba_ok = True

    if "BR-SRV" not in vm_ports:
        log_msg("⚠️ BR-SRV не найден")
        samba_ok = False
    else:
        out, err, cmd = ssh_exec(vm_ports["BR-SRV"], "samba-tool domain info 127.0.0.1", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: samba-tool domain info 127.0.0.1")
        safe_log_output(log_lines, "[BR-SRV] Вывод", out, err)
        if not (out and re.search(r'Domain\s*:\s*au-team\.irpo', out, re.IGNORECASE)):
            log_msg("❌ Домен au-team.irpo не найден")
            samba_ok = False

        users_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "samba-tool user list", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: samba-tool user list")
        safe_log_output(log_lines, "[BR-SRV] Вывод", users_out, "")

        hq_users = [f"hquser{i}" for i in range(1, 6)]
        if users_out:
            for user in hq_users:
                if user not in users_out:
                    log_msg(f"❌ Пользователь {user} не найден")
                    samba_ok = False
        else:
            samba_ok = False

        hq_group, _, _ = ssh_exec(vm_ports["BR-SRV"], "samba-tool group listmembers hq", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: samba-tool group listmembers hq")
        safe_log_output(log_lines, "[BR-SRV] Вывод", hq_group, "")
        if hq_group:
            for user in hq_users:
                if user not in hq_group:
                    log_msg(f"❌ Пользователь {user} не в группе hq")
                    samba_ok = False
        else:
            log_msg("❌ Группа hq не найдена или пуста")
            samba_ok = False

        # Аутентификация доменного пользователя и ограниченный sudo на HQ-CLI
        if "HQ-CLI" in vm_ports:
            test_user = "hquser1"
            test_pass = "P@ssw0rd"

            auth_out, auth_err, _ = ssh_exec(vm_ports["HQ-CLI"], "whoami", test_user, test_pass)
            log_msg(f"[HQ-CLI] SSH-аутентификация {test_user}")
            safe_log_output(log_lines, "[HQ-CLI] Вывод", auth_out, auth_err)
            auth_success = auth_out and test_user in auth_out.strip().lower()
            if not auth_success:
                wb_out, wb_err, _ = ssh_exec(vm_ports["HQ-CLI"], "wbinfo -a '%s%%\"%s\"'" % (test_user, test_pass), "root", "toor")
                log_msg(f"[HQ-CLI] Fallback: wbinfo -a {test_user}")
                safe_log_output(log_lines, "[HQ-CLI] Вывод", wb_out, wb_err)
                auth_success = wb_out and "succeeded" in wb_out.lower()
            if not auth_success:
                log_msg(f"❌ Аутентификация {test_user} на HQ-CLI не удалась")
                samba_ok = False
            else:
                # Разрешённые команды: cat, grep, id
                allowed_commands = ["cat /etc/passwd", "grep root /etc/passwd", "id"]
                for c in allowed_commands:
                    full_cmd = "echo '%s' | sudo -S %s" % (test_pass, c)
                    out, err, _ = ssh_exec(vm_ports["HQ-CLI"], full_cmd, test_user, test_pass)
                    log_msg(f"[HQ-CLI] Проверка разрешённой команды: {c}")
                    safe_log_output(log_lines, "[HQ-CLI] Вывод", out, err)
                    if not out or "Permission denied" in (err or "") or "password is required" in (err or ""):
                        log_msg(f"❌ {test_user} не может выполнить разрешённую команду: {c}")
                        samba_ok = False

                # Запрещённая команда (например ps) не должна выполняться через sudo
                forbidden_cmd = "ps aux"
                full_forbidden = "echo '%s' | sudo -S %s" % (test_pass, forbidden_cmd)
                out_f, err_f, _ = ssh_exec(vm_ports["HQ-CLI"], full_forbidden, test_user, test_pass)
                log_msg(f"[HQ-CLI] Проверка запрещённой команды: {forbidden_cmd}")
                safe_log_output(log_lines, "[HQ-CLI] Вывод", out_f, err_f)
                if out_f and "USER" in out_f and "PID" in out_f:
                    log_msg(f"❌ {test_user} смог выполнить запрещённую команду: {forbidden_cmd}")
                    samba_ok = False

    if samba_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 1 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 1 не пройден")
    results["Пункт 1: Samba DC"] = samba_ok

    # --- Пункт 2: RAID на HQ-SRV ---
    log_msg("\n📌 Пункт 2: RAID (%s, /dev/%s) на HQ-SRV" % (RAID_LEVEL, RAID_DEV))
    raid_ok = True
    if "HQ-SRV" not in vm_ports:
        raid_ok = False
    else:
        mdstat, _, _ = ssh_exec(vm_ports["HQ-SRV"], "mdadm --detail /dev/%s 2>/dev/null" % RAID_DEV, "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: mdadm --detail /dev/%s" % RAID_DEV)
        safe_log_output(log_lines, "[HQ-SRV] Вывод", mdstat, "")
        if not (mdstat and RAID_DEV in mdstat and RAID_LEVEL in mdstat.lower()):
            log_msg("❌ Массив %s уровня %s не найден" % (RAID_DEV, RAID_LEVEL))
            raid_ok = False
        else:
            # Проверка количества активных устройств
            m = re.search(r'Active Devices\s*:\s*(\d+)', mdstat)
            if m and int(m.group(1)) >= RAID_DISKS:
                log_msg("✅ В массиве %d активных устройств" % int(m.group(1)))
            else:
                log_msg("❌ Ожидалось %d дисков в массиве" % RAID_DISKS)
                raid_ok = False

        mdadm_conf, _, _ = ssh_exec(vm_ports["HQ-SRV"], "cat /etc/mdadm.conf 2>/dev/null; cat /etc/mdadm/mdadm.conf 2>/dev/null", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: cat /etc/mdadm.conf")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", mdadm_conf, "")
        if not (mdadm_conf and RAID_DEV in mdadm_conf):
            log_msg("⚠️ Конфигурация массива в /etc/mdadm.conf не найдена")

        mount_out, _, _ = ssh_exec(vm_ports["HQ-SRV"], "findmnt /raid --noheadings", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: findmnt /raid")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", mount_out, "")
        if not (mount_out and RAID_DEV in mount_out and "ext4" in mount_out):
            log_msg("❌ /raid не смонтирован с /dev/%s (ext4)" % RAID_DEV)
            raid_ok = False

        fstab, _, _ = ssh_exec(vm_ports["HQ-SRV"], "findmnt --fstab /raid --noheadings", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: findmnt --fstab /raid")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", fstab, "")
        if not fstab:
            log_msg("❌ Автомонтирование /raid не настроено в /etc/fstab")
            raid_ok = False

    if raid_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 2 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 2 не пройден")
    results["Пункт 2: RAID"] = raid_ok

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
            log_msg("❌ Каталог /raid/nfs не экспортируется")
            nfs_ok = False

        mount_cli, _, _ = ssh_exec(vm_ports["HQ-CLI"], "findmnt /mnt/nfs --noheadings", "root", "toor")
        log_msg("[HQ-CLI] Выполняется команда: findmnt /mnt/nfs")
        safe_log_output(log_lines, "[HQ-CLI] Вывод", mount_cli, "")
        if not (mount_cli and "/mnt/nfs" in mount_cli and "nfs" in mount_cli.lower()):
            log_msg("❌ /mnt/nfs не примонтирован на HQ-CLI")
            nfs_ok = False

        fstab_cli, _, _ = ssh_exec(vm_ports["HQ-CLI"], "findmnt --fstab /mnt/nfs --noheadings", "root", "toor")
        log_msg("[HQ-CLI] Выполняется команда: findmnt --fstab /mnt/nfs")
        safe_log_output(log_lines, "[HQ-CLI] Вывод", fstab_cli, "")
        if fstab_cli and "/mnt/nfs" in fstab_cli:
            log_msg("✅ Автомонтирование /mnt/nfs настроено на HQ-CLI")
        else:
            log_msg("ℹ️ Автомонтирование /mnt/nfs в fstab не найдено (монтирование может быть через autofs)")

    if nfs_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 3 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 3 не пройден")
    results["Пункт 3: NFS"] = nfs_ok

    # --- Пункт 4: Chrony на ISP ---
    log_msg("\n📌 Пункт 4: Chrony на ISP (стратум %d)" % NTP_STRATUM)
    chrony_ok = True
    clients = ["HQ-SRV", "HQ-CLI", "BR-RTR", "BR-SRV"]

    if "ISP" not in vm_ports:
        chrony_ok = False
    else:
        ntp_status, _, _ = ssh_exec(vm_ports["ISP"], "chronyc sources", "root", "toor")
        log_msg("[ISP] Выполняется команда: chronyc sources")
        safe_log_output(log_lines, "[ISP] Вывод", ntp_status, "")

        # Стратум сервера задаётся директивой 'local stratum N'
        strat_conf, _, _ = ssh_exec(vm_ports["ISP"], "grep -ri 'local stratum' /etc/chrony*", "root", "toor")
        log_msg("[ISP] Выполняется команда: grep -ri 'local stratum' /etc/chrony*")
        safe_log_output(log_lines, "[ISP] Вывод", strat_conf, "")
        if not (strat_conf and re.search(r'local\s+stratum\s+%d\b' % NTP_STRATUM, strat_conf)):
            log_msg("❌ ISP: стратум %d (local stratum %d) не настроен" % (NTP_STRATUM, NTP_STRATUM))
            chrony_ok = False

        for client in clients:
            if client not in vm_ports:
                log_msg(f"⚠️ Клиент {client} не найден")
                chrony_ok = False
                continue

            if "RTR" in client:
                ntp_client, _, _ = rtr_exec_with_enable(vm_ports[client], *get_rtr_creds(client), "show ntp status")
                log_msg("[%s] Выполняется команда: show ntp status" % client)
                safe_log_output(log_lines, "[%s] Вывод" % client, ntp_client, "")
                if not (ntp_client and ("*" in ntp_client or "+" in ntp_client or "synchron" in ntp_client.lower())):
                    log_msg(f"❌ Маршрутизатор {client} не синхронизирован")
                    chrony_ok = False
            else:
                chronyc_out, _, _ = ssh_exec(vm_ports[client], "chronyc sources", "root", "toor")
                log_msg("[%s] Выполняется команда: chronyc sources" % client)
                safe_log_output(log_lines, "[%s] Вывод" % client, chronyc_out, "")
                if not (chronyc_out and "^*" in chronyc_out):
                    log_msg(f"❌ Клиент {client} не синхронизирован")
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
        inventory, _, _ = ssh_exec(vm_ports["BR-SRV"], "cat /etc/ansible/hosts 2>/dev/null; cat /etc/ansible/inventory 2>/dev/null", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: cat /etc/ansible/hosts")
        safe_log_output(log_lines, "[BR-SRV] Вывод", inventory, "")
        required_hosts = ["hq-srv", "hq-cli", "hq-rtr", "br-rtr"]
        if inventory:
            for host in required_hosts:
                if host not in inventory.lower():
                    log_msg(f"❌ В инвентаре отсутствует {host}")
                    ansible_ok = False
        else:
            ansible_ok = False

        ping_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "ansible all -m ping", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: ansible all -m ping")
        safe_log_output(log_lines, "[BR-SRV] Вывод", ping_out, "")
        if not (ping_out and '"pong"' in ping_out):
            log_msg("❌ Не все узлы отвечают pong")
            ansible_ok = False
        # Проверяем отсутствие FAILED/UNREACHABLE
        if ping_out and ("UNREACHABLE" in ping_out or "FAILED" in ping_out):
            log_msg("❌ Есть недоступные узлы (UNREACHABLE/FAILED)")
            ansible_ok = False

    if ansible_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 5 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 5 не пройден")
    results["Пункт 5: Ansible"] = ansible_ok

    # --- Пункт 6: Docker-стек на BR-SRV ---
    log_msg("\n📌 Пункт 6: Docker (site + db) на BR-SRV")
    docker_ok = True

    if "BR-SRV" not in vm_ports:
        log_msg("⚠️ BR-SRV не найден")
        docker_ok = False
    else:
        # Поиск compose-файла
        find_out, _, _ = ssh_exec(vm_ports["BR-SRV"],
            "find / -maxdepth 4 \\( -name 'compose.y*ml' -o -name 'docker-compose.y*ml' \\) 2>/dev/null | head -5",
            "root", "toor")
        log_msg("[BR-SRV] Поиск compose-файла")
        safe_log_output(log_lines, "[BR-SRV] Вывод", find_out, "")

        compose_content = None
        if find_out:
            for path in [p.strip() for p in find_out.splitlines() if p.strip()]:
                out, _, _ = ssh_exec(vm_ports["BR-SRV"], f"cat {path}", "root", "toor")
                if out and ("container_name" in out or "services" in out):
                    compose_content = out
                    log_msg(f"✅ Найден compose-файл: {path}")
                    safe_log_output(log_lines, "[BR-SRV] Содержимое", compose_content, "")
                    break

        if compose_content:
            checks = {
                "контейнер site": bool(re.search(r'container_name:\s*site\b', compose_content, re.IGNORECASE)),
                "контейнер db": bool(re.search(r'container_name:\s*db\b', compose_content, re.IGNORECASE)),
                "образ %s" % DOCKER_DB_IMAGE: bool(re.search(r'image:\s*\S*%s' % DOCKER_DB_IMAGE, compose_content, re.IGNORECASE)),
                "БД %s" % DOCKER_DB_NAME: DOCKER_DB_NAME in compose_content,
                "пользователь %s" % DOCKER_DB_USER: DOCKER_DB_USER in compose_content,
                "порт %s" % APP_PORT: bool(re.search(r'["\']?%s:' % APP_PORT, compose_content)),
            }
            for name, ok in checks.items():
                if not ok:
                    log_msg(f"❌ В compose отсутствует: {name}")
                    docker_ok = False
        else:
            log_msg("⚠️ compose-файл не найден — проверка по runtime")

        ps_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "docker ps --format '{{.Names}} {{.Image}}'", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: docker ps --format '{{.Names}} {{.Image}}'")
        safe_log_output(log_lines, "[BR-SRV] Вывод", ps_out, "")
        if ps_out:
            if not re.search(r'^site\b', ps_out, re.MULTILINE):
                log_msg("❌ Контейнер site не запущен")
                docker_ok = False
            if not re.search(r'^db\b', ps_out, re.MULTILINE):
                log_msg("❌ Контейнер db не запущен")
                docker_ok = False
            if DOCKER_DB_IMAGE not in ps_out.lower():
                log_msg("❌ Образ %s среди запущенных контейнеров не найден" % DOCKER_DB_IMAGE)
                docker_ok = False
        else:
            docker_ok = False

        netstat_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "ss -tlnH sport = :%s" % APP_PORT, "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: ss -tlnH sport = :%s" % APP_PORT)
        safe_log_output(log_lines, "[BR-SRV] Вывод", netstat_out, "")
        if not netstat_out:
            log_msg("❌ Порт %s не слушается" % APP_PORT)
            docker_ok = False

        curl_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "curl -s http://localhost:%s" % APP_PORT, "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: curl -s http://localhost:%s" % APP_PORT)
        safe_log_output(log_lines, "[BR-SRV] Вывод", curl_out, "")
        if not (curl_out and ("<html" in curl_out.lower() or "site" in curl_out.lower())):
            log_msg("❌ Приложение недоступно на порту %s" % APP_PORT)
            docker_ok = False

    if docker_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 6 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 6 не пройден")
    results["Пункт 6: Docker"] = docker_ok

    # --- Пункт 7: Веб-приложение (apache + mariadb) на HQ-SRV ---
    log_msg("\n📌 Пункт 7: Веб-приложение на HQ-SRV")
    web_ok = True
    if "HQ-SRV" not in vm_ports:
        web_ok = False
    else:
        httpd_out, _, _ = ssh_exec(vm_ports["HQ-SRV"], "systemctl is-active httpd 2>/dev/null || systemctl is-active apache2 2>/dev/null", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: systemctl is-active httpd")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", httpd_out, "")
        if not (httpd_out and "active" in httpd_out):
            log_msg("❌ Веб-сервер apache не запущен")
            web_ok = False

        db_check, _, _ = ssh_exec(vm_ports["HQ-SRV"],
            "mysql -u %s -pP@ssw0rd -BNe \"SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME='webdb'\"" % WEB_DB_USER,
            "root", "toor")
        log_msg("[HQ-SRV] Проверка доступа пользователя %s к БД webdb" % WEB_DB_USER)
        safe_log_output(log_lines, "[HQ-SRV] Вывод", db_check, "")
        if not (db_check and "webdb" in db_check):
            log_msg("❌ Пользователь %s не имеет доступа к БД webdb" % WEB_DB_USER)
            web_ok = False

        php_check, _, _ = ssh_exec(vm_ports["HQ-SRV"], "grep -l webdb /var/www/html/index.php 2>/dev/null", "root", "toor")
        log_msg("[HQ-SRV] Проверка index.php на наличие webdb")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", php_check, "")
        if not php_check:
            log_msg("❌ index.php не содержит подключение к webdb")
            web_ok = False

        curl_out, _, _ = ssh_exec(vm_ports["HQ-SRV"], "curl -s http://localhost", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: curl -s http://localhost")
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

    # --- Пункт 8: Nginx reverse proxy на ISP ---
    log_msg("\n📌 Пункт 8: Nginx reverse proxy на ISP")
    nginx_ok = True

    if "ISP" not in vm_ports:
        nginx_ok = False
    else:
        conf_out, _, _ = ssh_exec(vm_ports["ISP"],
            "cat /etc/nginx/sites-enabled.d/* 2>/dev/null; cat /etc/nginx/conf.d/* 2>/dev/null; cat /etc/nginx/sites-enabled/* 2>/dev/null",
            "root", "toor")
        log_msg("[ISP] Чтение конфигурации nginx")
        safe_log_output(log_lines, "[ISP] Вывод", conf_out, "")

        if conf_out:
            web_block = re.search(r'server\s*{[^}]*web\.au-team\.irpo[^}]*}', conf_out, re.DOTALL | re.IGNORECASE)
            docker_block = re.search(r'server\s*{[^}]*docker\.au-team\.irpo[^}]*}', conf_out, re.DOTALL | re.IGNORECASE)
            # допускаем, что server_name может стоять до proxy_pass — ищем оба признака в конфиге
            has_web = "web.au-team.irpo" in conf_out
            has_docker = "docker.au-team.irpo" in conf_out
            has_proxy = "proxy_pass" in conf_out

            if not has_web:
                log_msg("❌ server_name web.au-team.irpo не найден")
                nginx_ok = False
            else:
                log_msg("✅ Найден server_name web.au-team.irpo")
            if not has_docker:
                log_msg("❌ server_name docker.au-team.irpo не найден")
                nginx_ok = False
            else:
                log_msg("✅ Найден server_name docker.au-team.irpo")
            if not has_proxy:
                log_msg("❌ Директива proxy_pass не найдена")
                nginx_ok = False
        else:
            log_msg("❌ Конфигурация nginx не найдена")
            nginx_ok = False

    if nginx_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 8 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 8 не пройден")
    results["Пункт 8: Nginx Reverse Proxy"] = nginx_ok

    # --- Пункт 9: Web-based auth на ISP ---
    log_msg("\n📌 Пункт 9: Web-based аутентификация на ISP (логин %s)" % NGINX_AUTH_LOGIN)
    auth_ok = True
    if "ISP" not in vm_ports:
        auth_ok = False
    else:
        htpasswd, _, _ = ssh_exec(vm_ports["ISP"],
            "htpasswd -vb /etc/nginx/.htpasswd %s 'P@ssw0rd' 2>&1" % NGINX_AUTH_LOGIN, "root", "toor")
        log_msg("[ISP] Проверка htpasswd: %s / P@ssw0rd" % NGINX_AUTH_LOGIN)
        safe_log_output(log_lines, "[ISP] Вывод", htpasswd, "")
        if not (htpasswd and ("correct" in htpasswd.lower() or "password verified" in htpasswd.lower())):
            log_msg("❌ Учётная запись %s в /etc/nginx/.htpasswd не подтверждена" % NGINX_AUTH_LOGIN)
            auth_ok = False

        # Проверка, что auth_basic подключён в конфиге
        ab_out, _, _ = ssh_exec(vm_ports["ISP"], "grep -r 'auth_basic' /etc/nginx/ 2>/dev/null", "root", "toor")
        log_msg("[ISP] Выполняется команда: grep -r 'auth_basic' /etc/nginx/")
        safe_log_output(log_lines, "[ISP] Вывод", ab_out, "")
        if not (ab_out and "auth_basic" in ab_out):
            log_msg("❌ auth_basic не подключён в конфигурации nginx")
            auth_ok = False

    if auth_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 9 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 9 не пройден")
    results["Пункт 9: Web Auth"] = auth_ok

    # --- Пункт 10: Яндекс Браузер на HQ-CLI ---
    log_msg("\n📌 Пункт 10: Яндекс Браузер на HQ-CLI")
    browser_ok = True
    if "HQ-CLI" not in vm_ports:
        browser_ok = False
    else:
        which_out, _, _ = ssh_exec(vm_ports["HQ-CLI"],
            "which yandex-browser-stable 2>/dev/null || which yandex-browser 2>/dev/null || rpm -q yandex-browser-stable 2>/dev/null",
            "root", "toor")
        log_msg("[HQ-CLI] Проверка установки Яндекс Браузера")
        safe_log_output(log_lines, "[HQ-CLI] Вывод", which_out, "")
        if not (which_out and "/" in which_out and "no " not in which_out.lower()):
            log_msg("❌ Яндекс Браузер не установлен")
            browser_ok = False

    if browser_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 10 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 10 не пройден")
    results["Пункт 10: Yandex Browser"] = browser_ok

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
