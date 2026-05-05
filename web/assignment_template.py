# assignment_template.py
"""
Шаблон проверки задания L2 КР
Плейсхолдеры:
- {VLAN_PC1_2_5_BASE}: Базовый номер VLAN для PC1, PC2, PC5 (например, 85 -> VLAN 85, 86 -> VLAN 86)
- {VLAN_PC3_4_BASE}: Базовый номер VLAN для PC3, PC4 (например, 90 -> VLAN 90, 91 -> VLAN 91)
- {IP_OCTET_PC1_2_5_BASE}: Базовый октет IP для сети VLAN PC1, PC2, PC5 (например, 85 -> 85.85.85.0/24, 86 -> 86.86.86.0/24)
- {IP_OCTET_PC3_4_BASE}: Базовый октет IP для сети VLAN PC3, PC4 (например, 90 -> 90.90.90.0/24, 91 -> 91.91.91.0/24)
- {ROOT_BRIDGE_HQ}: Имя корневого моста RSTP для офиса HQ ("SW-HQ1" или "SW-HQ2")
- {QINQ_OUTER_VLAN}: Номер внешнего VLAN для QinQ (например, 180, 181)
"""

import subprocess
import re
import time

def ssh_exec(ssh_port, command, username='root', password='root', timeout=10):
    """
    Выполняет команду на удаленной машине через SSH с использованием sshpass и subprocess.
    Совместимо с Python < 3.7.
    """
    if not ssh_port or ssh_port == "N/A":
        return None, f"❌ Порт SSH недоступен: {ssh_port}"

    process = None
    try:
        ssh_cmd = [
            "sshpass", "-p", password,
            "ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-p", ssh_port, f"{username}@localhost", command
        ]

        process = subprocess.Popen(
            ssh_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )

        stdout, stderr = process.communicate(timeout=timeout)

        return stdout, stderr

    except subprocess.TimeoutExpired:
        # Без kill процесс sshpass/ssh продолжает работать в фоне и держит
        # сетевое соединение, что в массовых проверках утекает в зомби-процессы.
        if process is not None:
            try:
                process.kill()
                process.communicate()
            except Exception:
                pass
        return None, f"❌ Таймаут выполнения команды для порта {ssh_port}"
    except FileNotFoundError:
        return None, f"❌ Команда sshpass не найдена в системе"
    except Exception as e:
        if process is not None and process.poll() is None:
            try:
                process.kill()
                process.communicate()
            except Exception:
                pass
        return None, f"❌ Неизвестная ошибка при выполнении команды для порта {ssh_port}: {str(e)}"


def run_full_assignment_check(vm_ports):
    PERCENT = 0
    MAX_POSSIBLE_PERCENT = 100 # Максимальный процент для задания
    log = []

    def log_msg(msg):
        log.append(msg)
        print(msg)

    # --- Настройки для задания (подставляемые параметры) ---
    vlan_pc1_2_5_base = {VLAN_PC1_2_5_BASE}
    vlan_pc3_4_base = {VLAN_PC3_4_BASE}
    ip_octet_85 = {IP_OCTET_PC1_2_5_BASE}
    ip_octet_90 = {IP_OCTET_PC3_4_BASE}
    root_bridge_hq = "{ROOT_BRIDGE_HQ}"
    qinq_outer_vlan = {QINQ_OUTER_VLAN}

    # --- Учетные данные ---
    CREDENTIALS = {
        "PC1": {"username": "root", "password": "toor"},
        "PC2": {"username": "root", "password": "toor"},
        "PC3": {"username": "root", "password": "toor"},
        "PC4": {"username": "root", "password": "toor"},
        "PC5": {"username": "root", "password": "toor"},
    }
    DEFAULT_CREDENTIALS = {'username': 'root', 'password': 'toor'}

    def get_credentials(vm_name):
        creds = CREDENTIALS.get(vm_name, DEFAULT_CREDENTIALS)
        return creds.get('username', DEFAULT_CREDENTIALS['username']), creds.get('password', DEFAULT_CREDENTIALS['password'])

    def has_ip(output, expected_ip):
        if not output:
            return False
        found_ips = re.findall(r'\d+\.\d+\.\d+\.\d+', output)
        return expected_ip in found_ips

    # --- Проверка IP-адресации на ПК (10%) ---
    log_msg("\n-------------IP-АДРЕСАЦИЯ------------")
    pc_checks = [
        ("PC1", f"{ip_octet_85}.{ip_octet_85}.{ip_octet_85}.1", 2), # 2%
        ("PC2", f"{ip_octet_85}.{ip_octet_85}.{ip_octet_85}.2", 2), # 2%
        ("PC3", f"{ip_octet_90}.{ip_octet_90}.{ip_octet_90}.3", 2), # 2%
        ("PC4", f"{ip_octet_90}.{ip_octet_90}.{ip_octet_90}.4", 2), # 2%
        ("PC5", f"{ip_octet_85}.{ip_octet_85}.{ip_octet_85}.5", 2), # 2%
    ]
    for name, expected_ip, points_per_check in pc_checks:
        username, password = get_credentials(name)
        out, err = ssh_exec(vm_ports.get(name, "N/A"), "ip -br a | grep -E 'ens3|eth0'", username=username, password=password) # Проверяем ens3 или eth0
        full_output = (out or "") + ("\n" + err if err else "")
        log_msg(f"[{name}] Вывод команды:\n{full_output}\n")
        if has_ip(out, expected_ip):
            PERCENT += points_per_check
            log_msg(f"✅ {name}: {expected_ip} (Получено +{points_per_check}%)")
        else:
            log_msg(f"❌ {name}: не найден {expected_ip} (0%)")

    # --- Проверка названий мостов (10%) ---
    log_msg("\n-------------ИМЕНА МОСТОВ-----------")
    switches = [
        ("SW-HQ1", "SW-HQ1", 1.67),
        ("SW-HQ2", "SW-HQ2", 1.67),
        ("SW-HQ3", "SW-HQ3", 1.67),
        ("SW-BR1", "SW-BR1", 1.67),
        ("SW-CORE1", "SW-CORE1", 1.67),
        ("SW-CORE2", "SW-CORE2", 1.67),
    ]
    for vm_name, expected_name, points_per_check in switches:
        username_sw, password_sw = get_credentials(vm_name)
        out, err = ssh_exec(vm_ports.get(vm_name, "N/A"), "ovs-vsctl show", username=username_sw, password=password_sw)
        full_output = (out or "") + ("\n" + err if err else "")
        log_msg(f"[{vm_name}] Вывод команды:\n{full_output}\n")
        if out and f"Bridge {expected_name}" in out:
            PERCENT += points_per_check
            log_msg(f"✅ {vm_name}: имя {expected_name} OK (Получено +{points_per_check:.2f}%)")
        else:
            log_msg(f"❌ {vm_name}: имя {expected_name} не найдено (0%)")

    # --- Проверка VLAN на коммутаторах HQ (20%) ---
    log_msg("\n-------------VLAN НА КОММУТАТОРАХ HQ-----------")
    # SW-HQ1 (6.67%)
    switch_name = "SW-HQ1"
    username_sw, password_sw = get_credentials(switch_name)
    sw_out, sw_err = ssh_exec(vm_ports.get(switch_name, "N/A"), "ovs-vsctl show", username=username_sw, password=password_sw)
    full_output = (sw_out or "") + ("\n" + sw_err if sw_err else "")
    log_msg(f"[{switch_name}] Вывод команды:\n{full_output}\n")
    if sw_out:
        sw_hq1_points = 0
        # Проверяем имя моста (уже делалось, но для полноты)
        if "Bridge SW-HQ1" in sw_out:
            sw_hq1_points += 0.5 # 0.5%
        # Проверяем порт с tag vlan_pc1_2_5_base (для PC1 - предположим ens4)
        if f'Port ens4' in sw_out and f'tag: {vlan_pc1_2_5_base}' in sw_out.split('Port ens4')[1].split('Port ')[0]:
            sw_hq1_points += 1 # 1%
        # Проверяем trunk порты с [vlan_pc1_2_5_base, vlan_pc3_4_base] (ens3, ens5, ens6)
        trunk_ports_to_check = ['ens3', 'ens5', 'ens6']
        for port in trunk_ports_to_check:
            port_section = ''
            if f'Port {port}' in sw_out:
                start_idx = sw_out.find(f'Port {port}')
                next_port_idx = sw_out.find('Port ', start_idx + len(f'Port {port}'))
                if next_port_idx == -1:
                    port_section = sw_out[start_idx:]
                else:
                    port_section = sw_out[start_idx:next_port_idx]
                # Регулярное выражение для поиска trunks с подставляемыми VLAN ID
                if re.search(rf'trunks:\s*\[\s*({vlan_pc1_2_5_base}|{vlan_pc3_4_base})\s*,\s*({vlan_pc1_2_5_base}|{vlan_pc3_4_base})\s*\]', port_section):
                    sw_hq1_points += 1.72 # ~1.72% за каждый (всего ~5.16% за 3 порта)
        PERCENT += sw_hq1_points
        log_msg(f"✅ [{switch_name}] Проверка VLAN завершена, процентов: {sw_hq1_points:.2f}%")
    else:
        log_msg(f"❌ [{switch_name}]: недоступен")

    # SW-HQ2 (6.67%)
    switch_name = "SW-HQ2"
    username_sw, password_sw = get_credentials(switch_name)
    sw_out, sw_err = ssh_exec(vm_ports.get(switch_name, "N/A"), "ovs-vsctl show", username=username_sw, password=password_sw)
    full_output = (sw_out or "") + ("\n" + sw_err if sw_err else "")
    log_msg(f"[{switch_name}] Вывод команды:\n{full_output}\n")
    if sw_out:
        sw_hq2_points = 0
        if "Bridge SW-HQ2" in sw_out:
            sw_hq2_points += 0.5 # 0.5%
        # Проверяем порт с tag vlan_pc1_2_5_base (для PC2 - предположим ens5)
        if f'Port ens5' in sw_out and f'tag: {vlan_pc1_2_5_base}' in sw_out.split('Port ens5')[1].split('Port ')[0]:
            sw_hq2_points += 1 # 1%
        # Проверяем trunk порты с [vlan_pc1_2_5_base, vlan_pc3_4_base] (ens3, ens4)
        trunk_ports_to_check = ['ens3', 'ens4']
        for port in trunk_ports_to_check:
            port_section = ''
            if f'Port {port}' in sw_out:
                start_idx = sw_out.find(f'Port {port}')
                next_port_idx = sw_out.find('Port ', start_idx + len(f'Port {port}'))
                if next_port_idx == -1:
                    port_section = sw_out[start_idx:]
                else:
                    port_section = sw_out[start_idx:next_port_idx]
                if re.search(rf'trunks:\s*\[\s*({vlan_pc1_2_5_base}|{vlan_pc3_4_base})\s*,\s*({vlan_pc1_2_5_base}|{vlan_pc3_4_base})\s*\]', port_section):
                    sw_hq2_points += 2.58 # ~2.58% за каждый (всего ~5.16% за 2 порта)
        PERCENT += sw_hq2_points
        log_msg(f"✅ [{switch_name}] Проверка VLAN завершена, процентов: {sw_hq2_points:.2f}%")
    else:
        log_msg(f"❌ [{switch_name}]: недоступен")

    # SW-HQ3 (6.66%)
    switch_name = "SW-HQ3"
    username_sw, password_sw = get_credentials(switch_name)
    sw_out, sw_err = ssh_exec(vm_ports.get(switch_name, "N/A"), "ovs-vsctl show", username=username_sw, password=password_sw)
    full_output = (sw_out or "") + ("\n" + sw_err if sw_err else "")
    log_msg(f"[{switch_name}] Вывод команды:\n{full_output}\n")
    if sw_out:
        sw_hq3_points = 0
        if "Bridge SW-HQ3" in sw_out:
            sw_hq3_points += 0.5 # 0.5%
        # Проверяем порт с tag vlan_pc3_4_base (для PC3 - предположим ens5)
        if f'Port ens5' in sw_out and f'tag: {vlan_pc3_4_base}' in sw_out.split('Port ens5')[1].split('Port ')[0]:
            sw_hq3_points += 1 # 1%
        # Проверяем trunk порты с [vlan_pc1_2_5_base, vlan_pc3_4_base] (ens3, ens4)
        trunk_ports_to_check = ['ens3', 'ens4']
        for port in trunk_ports_to_check:
            port_section = ''
            if f'Port {port}' in sw_out:
                start_idx = sw_out.find(f'Port {port}')
                next_port_idx = sw_out.find('Port ', start_idx + len(f'Port {port}'))
                if next_port_idx == -1:
                    port_section = sw_out[start_idx:]
                else:
                    port_section = sw_out[start_idx:next_port_idx]
                if re.search(rf'trunks:\s*\[\s*({vlan_pc1_2_5_base}|{vlan_pc3_4_base})\s*,\s*({vlan_pc1_2_5_base}|{vlan_pc3_4_base})\s*\]', port_section):
                    sw_hq3_points += 2.58 # ~2.58% за каждый (всего ~5.16% за 2 порта)
        PERCENT += sw_hq3_points
        log_msg(f"✅ [{switch_name}] Проверка VLAN завершена, процентов: {sw_hq3_points:.2f}%")
    else:
        log_msg(f"❌ [{switch_name}]: недоступен")

    # --- Проверка RSTP (15%) ---
    log_msg("\n-------------RSTP-----------")
    # Корневой мост (7%)
    username_sw, password_sw = get_credentials(root_bridge_hq)
    rstp_root, rstp_root_err = ssh_exec(vm_ports.get(root_bridge_hq, "N/A"), "ovs-appctl rstp/show", username=username_sw, password=password_sw)
    full_output = (rstp_root or "") + ("\n" + rstp_root_err if rstp_root_err else "")
    log_msg(f"[{root_bridge_hq}] Вывод команды:\n{full_output}\n")
    if rstp_root and "This bridge is the root" in rstp_root:
        PERCENT += 7
        log_msg(f"✅ {root_bridge_hq}: корень RSTP (Получено +7%)")
    else:
        log_msg(f"❌ {root_bridge_hq}: не корень RSTP (0%)")

    # Не корневые мосты (4% + 4%)
    non_root_switches = ["SW-HQ1", "SW-HQ2", "SW-HQ3"]
    non_root_switches.remove(root_bridge_hq) # Убираем корневой из списка
    for sw in non_root_switches[:2]: # Берём только 2, чтобы сумма была 4% + 4% = 8%, итого 7+8=15
        username_sw, password_sw = get_credentials(sw)
        rstp_out, rstp_err = ssh_exec(vm_ports.get(sw, "N/A"), "ovs-appctl rstp/show", username=username_sw, password=password_sw)
        full_output = (rstp_out or "") + ("\n" + rstp_err if rstp_err else "")
        log_msg(f"[{sw}] Вывод команды:\n{full_output}\n")
        if rstp_out and "root-port" in rstp_out:
            PERCENT += 4
            log_msg(f"✅ {sw}: root-port найден (Получено +4%)")
        else:
            log_msg(f"❌ {sw}: root-port не найден (0%)")

    # --- Проверка QinQ (30%) ---
    log_msg("\n-------------QinQ-----------")
    qinq_success_count = 0
    for sw in ["SW-CORE1", "SW-CORE2"]:
        port = vm_ports.get(sw, "N/A")
        username_sw, password_sw = get_credentials(sw)
        mode_out, mode_err = ssh_exec(port, "timeout 5 ovs-vsctl get port ens4 vlan_mode", username=username_sw, password=password_sw)
        tag_out, tag_err = ssh_exec(port, "timeout 5 ovs-vsctl get port ens4 tag", username=username_sw, password=password_sw)
        log_msg(f"[{sw}] vlan_mode (ens4): {mode_out or 'N/A'}")
        log_msg(f"[{sw}] tag (ens4): {tag_out or 'N/A'}")
        points_awarded = 0
        if mode_out and "dot1q-tunnel" in mode_out:
            points_awarded += 10 # 10% за mode
        if tag_out and str(qinq_outer_vlan) in tag_out:
            points_awarded += 10 # 10% за tag
        PERCENT += points_awarded
        log_msg(f"✅ {sw}: QinQ баллов: {points_awarded}%")
        if points_awarded == 20: # mode и tag OK
            qinq_success_count += 1

    if qinq_success_count == 2: # Оба магистральных коммутатора настроены
        PERCENT += 10 # 10% бонус за полную настройку QinQ
        log_msg("✅ QinQ: Бонус за настройку на обоих магистральных коммутаторах (Получено +10%)")

    # --- Проверка пинга (15%) ---
    log_msg("\n-------------PING-----------")
    # Используем подставляемые IP-адреса
    ping_tests = [
        (f"{ip_octet_85}.{ip_octet_85}.{ip_octet_85}.2", "PC1", f"{ip_octet_85}.{ip_octet_85}.{ip_octet_85}.1", 5), # PC2 ping PC1
        (f"{ip_octet_85}.{ip_octet_85}.{ip_octet_85}.5", "PC2", f"{ip_octet_85}.{ip_octet_85}.{ip_octet_85}.2", 5), # PC5 ping PC2
        (f"{ip_octet_90}.{ip_octet_90}.{ip_octet_90}.4", "PC3", f"{ip_octet_90}.{ip_octet_90}.{ip_octet_90}.3", 5), # PC4 ping PC3
    ]
    for target_ip, source_vm, source_ip, points_per_ping in ping_tests:
        username_pc, password_pc = get_credentials(source_vm)
        ping_out, ping_err = ssh_exec(vm_ports.get(source_vm, "N/A"), f"ping -c 1 -w 2 {target_ip}", username=username_pc, password=password_pc)
        full_output = (ping_out or "") + ("\n" + ping_err if ping_err else "")
        log_msg(f"[{source_vm}] Вывод ping {target_ip}:\n{full_output}\n")
        if ping_out and "1 received" in ping_out:
            PERCENT += points_per_ping
            log_msg(f"✅ Ping {source_ip} → {target_ip} (Получено +{points_per_ping}%)")
        else:
            log_msg(f"❌ Ping {source_ip} → {target_ip} (0%)")

    # --- Округление общего процента ---
    PERCENT = round(PERCENT, 2)

    # Убедимся, что процент не превышает 100
    if PERCENT > MAX_POSSIBLE_PERCENT:
        PERCENT = MAX_POSSIBLE_PERCENT
        log_msg(f"⚠️ Процент превысил 100 и был ограничен до {MAX_POSSIBLE_PERCENT}%")

    log_msg(f"\n📊 Итоговый процент: {PERCENT} из {MAX_POSSIBLE_PERCENT}")
    return PERCENT, log

