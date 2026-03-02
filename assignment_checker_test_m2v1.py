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
    "ISP": ("root", "toor"),  # Linux
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
    """
    Выполняет команду на удалённой машине через SSH.
    Возвращает: (stdout, stderr, log_message)
    """
    if not ssh_port or ssh_port == "N/A":
        return None, "❌ Порт SSH недоступен: %s" % ssh_port, "Выполняется команда: %s" % command

    try:
        # Формируем команду
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

        # Запускаем процесс
        process = subprocess.Popen(
            ssh_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1  # построчный вывод
        )

        # Ждём завершения с таймаутом
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()  # очистка буферов
            return None, "❌ Таймаут выполнения команды (%d сек)" % timeout, "Выполняется команда: %s" % command

        # Возвращаем результат
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

def safe_log_output(log_lines, prefix, output, error=""):
    full_output = (output or "") + ("\n" + error if error else "")
    log_lines.append("%s:\n%s\n" % (prefix, full_output))


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


# ========== ГЛАВНАЯ ФУНКЦИЯ ==========
def run_full_assignment_check(vm_ports):
    POINTS = 0.0
    MAX_POINTS = 11.0
    log_lines = []
    results = {}

    def log_msg(msg):
        log_lines.append(msg)
        print(msg)
    # --- Вывод портов SSH ---
    log_msg("\n🔍 Доступные SSH-порты:")
    for device, port in sorted(vm_ports.items()):
        log_msg(f"  {device}: {port}")
    log_msg("")

    log_msg("🔍 Начало комплексной проверки Модуля 2")

    DEVICE_NAMES = {
        "BR-SRV": "br-srv.au-team.irpo",
        "HQ-SRV": "hq-srv.au-team.irpo",
        "HQ-CLI": "hq-cli.au-team.irpo",
        "BR-CLI": "br-cli.au-team.irpo",
        "HQ-RTR": "hq-rtr",
        "BR-RTR": "br-rtr",
        "ISP": "isp",
    }

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

        # Проверка пользователей
        users_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "samba-tool user list", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: samba-tool user list")
        safe_log_output(log_lines, "[BR-SRV] Вывод", users_out, "")

        hq_users = [f"hquser{i}" for i in range(1, 6)]
        br_users = [f"bruser{i}" for i in range(1, 6)]
        all_users = hq_users + br_users

        if users_out:
            for user in all_users:
                if user not in users_out:
                    log_msg(f"❌ Пользователь {user} не найден")
                    samba_ok = False
        else:
            samba_ok = False

        # Проверка групп
        hq_group, _, _ = ssh_exec(vm_ports["BR-SRV"], "samba-tool group show hq", "root", "toor")
        br_group, _, _ = ssh_exec(vm_ports["BR-SRV"], "samba-tool group show br", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: samba-tool group show hq")
        safe_log_output(log_lines, "[BR-SRV] Вывод", hq_group, "")
        log_msg("[BR-SRV] Выполняется команда: samba-tool group show br")
        safe_log_output(log_lines, "[BR-SRV] Вывод", br_group, "")

        if hq_group and br_group:
            for user in hq_users:
                if user not in hq_group:
                    log_msg(f"❌ Пользователь {user} не в группе hq")
                    samba_ok = False
            for user in br_users:
                if user not in br_group:
                    log_msg(f"❌ Пользователь {user} не в группе br")
                    samba_ok = False
        else:
            samba_ok = False

        # === Проверка аутентификации и sudo для hquser1 ===
        if "HQ-CLI" in vm_ports:
            test_user = "hquser1"
            test_pass = "P@ssw0rd"

            # Аутентификация
            auth_cmd = f"su -l {test_user} -c 'whoami'"
            auth_out, auth_err, _ = ssh_exec(vm_ports["HQ-CLI"], auth_cmd, "root", "toor")
            log_msg(f"[HQ-CLI] Проверка аутентификации {test_user}")
            safe_log_output(log_lines, "[HQ-CLI] Вывод", auth_out, auth_err)
            if not (auth_out and test_user in auth_out):
                log_msg(f"❌ Аутентификация {test_user} на HQ-CLI не удалась")
                samba_ok = False
            else:
                # Проверка разрешённых команд (cat, grep, id)
                allowed_commands = ["cat /etc/passwd", "grep root /etc/passwd", "id"]
                for cmd in allowed_commands:
                    full_cmd = f"su -l {test_user} -c 'echo \"{test_pass}\" | sudo -S {cmd}'"
                    out, err, _ = ssh_exec(vm_ports["HQ-CLI"], full_cmd, "root", "toor")
                    log_msg(f"[HQ-CLI] Проверка разрешённой команды: {cmd}")
                    safe_log_output(log_lines, "[HQ-CLI] Вывод", out, err)
                    if not out or "Permission denied" in err or "password is required" in err:
                        log_msg(f"❌ Пользователь {test_user} не может выполнить разрешённую команду: {cmd}")
                        samba_ok = False

                # Проверка запрещённой команды (ps)
                forbidden_cmd = "ps aux"
                full_forbidden = f"su -l {test_user} -c 'echo \"{test_pass}\" | sudo -S {forbidden_cmd}'"
                out_f, err_f, _ = ssh_exec(vm_ports["HQ-CLI"], full_forbidden, "root", "toor")
                log_msg(f"[HQ-CLI] Проверка запрещённой команды: {forbidden_cmd}")
                safe_log_output(log_lines, "[HQ-CLI] Вывод", out_f, err_f)
                if out_f and "UID" in out_f:
                    log_msg(f"❌ Пользователь {test_user} смог выполнить запрещённую команду: {forbidden_cmd}")
                    samba_ok = False

        # === Проверка, что bruser1 НЕ может использовать sudo ===
        if "BR-CLI" in vm_ports:
            test_user_br = "bruser1"
            test_pass_br = "P@ssw0rd"

            # Аутентификация
            auth_cmd_br = f"su -l {test_user_br} -c 'whoami'"
            auth_out_br, auth_err_br, _ = ssh_exec(vm_ports["BR-CLI"], auth_cmd_br, "root", "toor")
            log_msg(f"[BR-CLI] Проверка аутентификации {test_user_br}")
            safe_log_output(log_lines, "[BR-CLI] Вывод", auth_out_br, auth_err_br)
            if not (auth_out_br and test_user_br in auth_out_br):
                log_msg(f"❌ Аутентификация {test_user_br} на BR-CLI не удалась")
                samba_ok = False
            else:
                # Попытка выполнить sudo (должна завершиться ошибкой)
                sudo_test = f"su -l {test_user_br} -c 'echo \"{test_pass_br}\" | sudo -S cat /etc/passwd'"
                out_br, err_br, _ = ssh_exec(vm_ports["BR-CLI"], sudo_test, "root", "toor")
                log_msg(f"[BR-CLI] Проверка запрета sudo для {test_user_br}")
                safe_log_output(log_lines, "[BR-CLI] Вывод", out_br, err_br)
                if out_br and "root:" in out_br:
                    log_msg(f"❌ Пользователь {test_user_br} смог выполнить sudo — это запрещено!")
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
        # Проверка массива
        mdstat, _, _ = ssh_exec(vm_ports["HQ-SRV"], "cat /proc/mdstat", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: cat /proc/mdstat")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", mdstat, "")
        if not (mdstat and "md0" in mdstat and "active raid0" in mdstat):
            raid_ok = False

        # Проверка конфигурации
        mdadm_conf, _, _ = ssh_exec(vm_ports["HQ-SRV"], "cat /etc/mdadm.conf", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: cat /etc/mdadm.conf")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", mdadm_conf, "")
        if not (mdadm_conf and "md0" in mdadm_conf):
            raid_ok = False

        # Проверка монтирования
        mount_out, _, _ = ssh_exec(vm_ports["HQ-SRV"], "mount | grep /raid", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: mount | grep /raid")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", mount_out, "")
        if not (mount_out and "/dev/md0" in mount_out and "ext4" in mount_out):
            raid_ok = False

        # Проверка автомонтирования (через /etc/fstab)
        fstab, _, _ = ssh_exec(vm_ports["HQ-SRV"], "grep '/raid' /etc/fstab", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: grep '/raid' /etc/fstab")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", fstab, "")
        if not (fstab and "/dev/md0" in fstab and "ext4" in fstab):
            log_msg("❌ Автомонтирование /raid не настроено в /etc/fstab")
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
    if "HQ-SRV" not in vm_ports or "HQ-CLI" not in vm_ports or "BR-CLI" not in vm_ports:
        nfs_ok = False
    else:
        # Проверка экспорта
        exports, _, _ = ssh_exec(vm_ports["HQ-SRV"], "showmount -e localhost", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: showmount -e localhost")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", exports, "")
        if not (exports and "/raid/nfs" in exports):
            nfs_ok = False

        # Проверка монтирования на клиентах
        for cli in ["HQ-CLI", "BR-CLI"]:
            mount_cli, _, _ = ssh_exec(vm_ports[cli], "mount | grep /mnt/nfs", "root", "toor")
            log_msg("[%s] Выполняется команда: mount | grep /mnt/nfs" % cli)
            safe_log_output(log_lines, "[%s] Вывод" % cli, mount_cli, "")
            if not (mount_cli and "/mnt/nfs" in mount_cli and ":/raid/nfs" in mount_cli):
                log_msg(f"❌ /mnt/nfs не примонтирован на {cli}")
                nfs_ok = False

        # Проверка автомонтирования через /etc/fstab
        for cli in ["HQ-CLI", "BR-CLI"]:
            fstab_cli, _, _ = ssh_exec(vm_ports[cli], "grep '/mnt/nfs' /etc/fstab", "root", "toor")
            log_msg("[%s] Выполняется команда: grep '/mnt/nfs' /etc/fstab" % cli)
            safe_log_output(log_lines, "[%s] Вывод" % cli, fstab_cli, "")
            # Пропускаем ошибку, если монтирование есть, но fstab не содержит запись
            if fstab_cli:
                if "/mnt/nfs" in fstab_cli and ":/raid/nfs" in fstab_cli and "nfs" in fstab_cli:
                    log_msg(f"✅ Автомонтирование /mnt/nfs настроено в /etc/fstab на {cli}")
                else:
                    log_msg(f"⚠️ Строка /etc/fstab для /mnt/nfs найдена, но не соответствует ожидаемому формату — пропускаем (монтирование существует)")
            else:
                log_msg(f"ℹ️ Автомонтирование /mnt/nfs не настроено в /etc/fstab на {cli}, но монтирование существует — считаем OK")

    if nfs_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 3 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 3 не пройден")
    results["Пункт 3: NFS"] = nfs_ok


    # --- Пункт 4: Chrony на ISP ---
    log_msg("\n📌 Пункт 4: Chrony на ISP")
    chrony_ok = True
    clients = ["HQ-SRV", "HQ-CLI", "BR-RTR", "BR-SRV", "BR-CLI"]

    if "ISP" not in vm_ports:
        chrony_ok = False
    else:
        # Проверка ISP (Linux)
        ntp_status, _, _ = ssh_exec(vm_ports["ISP"], "chronyc sources", "root", "toor")
        log_msg("[ISP] Выполняется команда: chronyc sources")
        safe_log_output(log_lines, "[ISP] Вывод", ntp_status, "")
        if not (ntp_status and "^*" in ntp_status):
            log_msg("❌ ISP не синхронизирован с NTP-сервером")
            chrony_ok = False

        # Проверка клиентов
        for client in clients:
            if client not in vm_ports:
                log_msg(f"⚠️ Клиент {client} не найден")
                chrony_ok = False
                continue

            if "RTR" in client:
                # Для EcoRouterOS: используем rtr_exec_with_enable и команду 'ntp status'
                ntp_client, _, _ = rtr_exec_with_enable(vm_ports[client], *get_rtr_creds(client), "show ntp status")
                log_msg("[%s] Выполняется команда: ntp status" % client)
                safe_log_output(log_lines, "[%s] Вывод" % client, ntp_client, "")
                if not (ntp_client and ("*" in ntp_client or "+" in ntp_client)):
                    log_msg(f"❌ Маршрутизатор {client} не синхронизирован (нет 'best' или 'sync')")
                    chrony_ok = False
            else:
                # Для серверов — chronyc sources
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
        inventory, _, _ = ssh_exec(vm_ports["BR-SRV"], "cat /etc/ansible/hosts", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: cat /etc/ansible/hosts")
        safe_log_output(log_lines, "[BR-SRV] Вывод", inventory, "")
        required_hosts = ["hq-srv", "hq-cli", "hq-rtr", "br-rtr", "br-cli"]
        if inventory:
            for host in required_hosts:
                if host not in inventory.lower():
                    ansible_ok = False
        else:
            ansible_ok = False

        ping_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "ansible all -m ping", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: ansible all -m ping")
        safe_log_output(log_lines, "[BR-SRV] Вывод", ping_out, "")
        if not (ping_out and '"pong"' in ping_out):
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
        log_msg("⚠️ BR-SRV не найден")
        docker_ok = False
    else:
        # === Шаг 1: Поиск compose-файла ===
        compose_paths = [
            "/root/compose.yaml",
            "/opt/compose.yaml",
            "/etc/compose.yaml",
            "/home/bruser/compose.yaml",
            "/compose.yaml",
            "/root/docker-compose.yml",
            "/opt/docker-compose.yml"
        ]
        compose_content = None
        found_path = None

        for path in compose_paths:
            out, _, _ = ssh_exec(vm_ports["BR-SRV"], f"cat {path}", "root", "toor")
            if out is not None and "container_name:" in out:
                compose_content = out
                found_path = path
                break

        if compose_content is None:
            log_msg("❌ compose-файл не найден")
            docker_ok = False
        else:
            log_msg(f"✅ Найден: {found_path}")
            safe_log_output(log_lines, "[BR-SRV] Содержимое", compose_content, "")

            # === Шаг 2: Простая проверка по ключевым строкам ===
            required_lines = [
                "container_name: db",
                "image: mariadb",
                "MARIADB_USER: test",
                "MARIADB_PASSWORD: P@ssw0rd",
                "MARIADB_DATABASE: testdb",
                'ports:\n      - "3306:3306"',
                "container_name: testapp",
                "image: site",
                'ports:\n      - "8080:',
                "DB_USER: test",
                "DB_PASS: P@ssw0rd",
                "DB_NAME: testdb",
                "DB_TYPE: maria",
                "depends_on:\n      - database"
            ]

            for line in required_lines:
                if line not in compose_content:
                    log_msg(f"❌ Отсутствует: {repr(line)}")
                    docker_ok = False

        # === Шаг 3: Контейнеры ===
        ps_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "docker ps --format '{{.Names}}'", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: docker ps --format '{{.Names}}'")
        safe_log_output(log_lines, "[BR-SRV] Вывод", ps_out, "")
        if ps_out:
            if "testapp" not in ps_out or "db" not in ps_out:
                log_msg("❌ Один или оба контейнера не запущены")
                docker_ok = False
        else:
            docker_ok = False

        # === Шаг 4: Порт 8080 ===
        netstat_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "ss -tuln | grep ':8080'", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: ss -tuln | grep ':8080'")
        safe_log_output(log_lines, "[BR-SRV] Вывод", netstat_out, "")
        if not netstat_out:
            log_msg("❌ Порт 8080 не слушается")
            docker_ok = False

        # === Шаг 5: Доступность ===
        curl_out, _, _ = ssh_exec(vm_ports["BR-SRV"], "curl -s http://localhost:8080", "root", "toor")
        log_msg("[BR-SRV] Выполняется команда: curl -s http://localhost:8080")
        safe_log_output(log_lines, "[BR-SRV] Вывод", curl_out, "")
        if not (curl_out and ("<html" in curl_out.lower() or "testapp" in curl_out.lower())):
            log_msg("❌ Приложение недоступно")
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
        # Проверка сервиса httpd (не apache2)
        httpd_out, _, _ = ssh_exec(vm_ports["HQ-SRV"], "systemctl is-active httpd", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: systemctl is-active httpd")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", httpd_out, "")
        if not (httpd_out and "active" in httpd_out):
            web_ok = False

        # Проверка БД
        db_check, _, _ = ssh_exec(vm_ports["HQ-SRV"], "mysql -u web -pP@ssw0rd -e 'show databases;' | grep webdb", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: mysql -u web -pP@ssw0rd -e 'show databases;' | grep webdb")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", db_check, "")
        if not db_check:
            web_ok = False

        # Проверка файла index.php
        php_check, _, _ = ssh_exec(vm_ports["HQ-SRV"], "cat /var/www/html/index.php | grep 'webdb'", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: cat /var/www/html/index.php | grep 'webdb'")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", php_check, "")
        if not php_check:
            web_ok = False

        # Проверка доступности
        curl_out, _, _ = ssh_exec(vm_ports["HQ-SRV"], "curl -s http://localhost", "root", "toor")
        log_msg("[HQ-SRV] Выполняется команда: curl -s http://localhost")
        safe_log_output(log_lines, "[HQ-SRV] Вывод", curl_out, "")
        if not (curl_out and "<html" in curl_out.lower()):
            web_ok = False

    if web_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 7 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 7 не пройден")
    results["Пункт 7: Веб-приложение"] = web_ok

    # --- Вывод портов SSH ---
    log_msg("\n🔍 Доступные SSH-порты:")
    for device, port in sorted(vm_ports.items()):
        log_msg(f"  {device}: {port}")
    log_msg("")

# --- Пункт 9: Nginx reverse proxy on ISP ---
    log_msg("\n📌 Пункт 9: Nginx reverse proxy на ISP")
    nginx_ok = True

    if "ISP" not in vm_ports:
        nginx_ok = False
    else:
        # === Шаг 1: Получаем список файлов ===
        ls_out, _, _ = ssh_exec(vm_ports["ISP"], "ls /etc/nginx/sites-enabled.d/", "root", "toor")
        log_msg("[ISP] Выполняется команда: ls /etc/nginx/sites-enabled.d/")
        safe_log_output(log_lines, "[ISP] Вывод", ls_out, "")

        files = []  # ← ГАРАНТИРОВАННОЕ объявление
        if ls_out is not None:
            files = [f.strip() for f in ls_out.splitlines() if f.strip()]

        web_found = False
        docker_found = False

        for fname in files:
            path = f"/etc/nginx/sites-enabled.d/{fname}"
            content, _, _ = ssh_exec(vm_ports["ISP"], f"cat {path}", "root", "toor")
            if not content:
                continue

            log_msg(f"[ISP] Содержимое {fname}:")
            safe_log_output(log_lines, f"[ISP] {fname}", content, "")

            # Разделяем на блоки server { ... }
            server_blocks = re.findall(r'(server\s*{[^}]*})', content, re.DOTALL)
            for block in server_blocks:
                if "web.au-team.irpo" in block:
                    web_found = True
                    match = re.search(r'proxy_pass\s+http://(\d+\.\d+\.\d+\.\d+):8080;', block)
                    if match:
                        ip = match.group(1)
                        if ip == "172.16.1.2":
                            log_msg("✅ web.au-team.irpo → 172.16.1.2")
                        else:
                            log_msg(f"❌ web.au-team.irpo: ожидается 172.16.1.2, получено {ip}")
                            nginx_ok = False
                    else:
                        log_msg("❌ web.au-team.irpo: нет proxy_pass")
                        nginx_ok = False

                if "docker.au-team.irpo" in block:
                    docker_found = True
                    match = re.search(r'proxy_pass\s+http://(\d+\.\d+\.\d+\.\d+):8080;', block)
                    if match:
                        ip = match.group(1)
                        if ip == "172.16.2.2":
                            log_msg("✅ docker.au-team.irpo → 172.16.2.2")
                        else:
                            log_msg(f"❌ docker.au-team.irpo: ожидается 172.16.2.2, получено {ip}")
                            nginx_ok = False
                    else:
                        log_msg("❌ docker.au-team.irpo: нет proxy_pass")
                        nginx_ok = False

        if not web_found:
            log_msg("❌ Конфиг для web.au-team.irpo не найден")
            nginx_ok = False
        if not docker_found:
            log_msg("❌ Конфиг для docker.au-team.irpo не найден")
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
        htpasswd, _, _ = ssh_exec(vm_ports["ISP"], "cat /etc/nginx/.htpasswd", "root", "toor")
        log_msg("[ISP] Выполняется команда: cat /etc/nginx/.htpasswd")
        safe_log_output(log_lines, "[ISP] Вывод", htpasswd, "")
        if not (htpasswd and "WEB:" in htpasswd):
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
        which_out, _, _ = ssh_exec(vm_ports["HQ-CLI"], "which yandex-browser", "root", "toor")
        log_msg("[HQ-CLI] Выполняется команда: which yandex-browser")
        safe_log_output(log_lines, "[HQ-CLI] Вывод", which_out, "")
        if not which_out:
            browser_ok = False

    if browser_ok:
        POINTS += 1.0
        log_msg("✅ Пункт 11 пройден (+1 балл)")
    else:
        log_msg("❌ Пункт 11 не пройден")
    results["Пункт 11: Yandex Browser"] = browser_ok

    # --- ИТОГОВЫЙ ОТЧЁТ ---
    log_msg("\n📊 ИТОГОВЫЙ ОТЧЁТ:")
    log_msg("="*60)
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
