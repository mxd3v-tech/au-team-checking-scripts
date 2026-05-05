from assignment_checker import run_full_assignment_check

# Замените порты на ваши реальные!
vm_ports = {
    "HQ-SRV": "40045",
    "BR-SRV": "40049",
    "HQ-CLI": "40046",
    "BR-CLI": "40050",
    "HQ-RTR": "40043",
    "BR-RTR": "40047",
    "HQ-SW": "40044",
    "ISP": "40042"
}

# Запуск проверки
percent, log_lines = run_full_assignment_check(vm_ports)

# Вывод результата в консоль
for line in log_lines:
    print(line, end='')

# Сохранение в файл (опционально)
with open("check_result.txt", "w") as f:
    f.writelines(log_lines)
