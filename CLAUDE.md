# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Structure

```
au-team-checking-scripts/
├── checkers/         — скрипты автоматической проверки заданий
├── web/              — веб-интерфейс для запуска проверок (Flask)
├── report/           — обработчик результатов: генерация Excel-таблицы
└── tasks/
    ├── criteria/     — PDF с заданиями по модулям + критерии оценки (docx)
    └── variants/     — варианты заданий (docx) для раздачи участникам
```

## Checkers (`checkers/`)

### Naming Convention

`assignment_checker_test_m{module}v{variant}[_mod].py`

- **m1/m2/m3** — Модуль 1 (сеть), Модуль 2 (сервисы), Модуль 3 (продвинутые сервисы)
- **v1/v2/v3** — Варианты с разными параметрами (IP, имена пользователей, уровни RAID и т.д.)
- **_mod** — Версия с баллами: функция `award()` для дробной оценки по КО. MAX_POINTS=25 на модуль.
- **federal_code_** — Скрипты для федерального демонстрационного экзамена (другая топология).

Базовые скрипты (без `_mod`) выводят пройдено/не пройдено по каждому пункту.
`_mod`-скрипты выводят числовой балл с частичным зачётом.

### Entry Point

```python
def run_full_assignment_check(vm_ports: dict[str, str]) -> tuple[int|float, list[str]]:
    return score, log_lines
```

`vm_ports` — словарь `{"имя_ВМ": "ssh_порт"}`. Порт `"N/A"` означает ВМ не найдена.

### Devices

`HQ-SRV`, `BR-SRV`, `HQ-CLI`, `BR-CLI`, `HQ-RTR`, `BR-RTR`, `ISP`. Domain: `au-team.irpo`.
`BR-FW` присутствует в топологии, но скриптами **не проверяется**.

### Key Conventions

- Предпочитать нативные утилиты Linux вместо cat/grep-пайплайнов:
  - `sshd -T` вместо `cat sshd_config`
  - `findmnt` вместо `mount | grep`
  - `mdadm --detail` вместо `cat /proc/mdstat`
  - `ss -tlnH sport = :PORT` вместо `ss -tuln | grep`
  - `systemctl is-active SERVICE` вместо `systemctl list-units | grep`
  - `htpasswd -vb` вместо `cat .htpasswd`
- `cat/grep` допустимы там, где нет нативной альтернативы: compose.yaml, jail.local, logrotate, nginx-конфиги, PAM, ansible inventory
- v2/v3 отличаются от v1 **только значениями параметров** — структурные изменения из v1 переносить во все варианты
- Весь пользовательский текст на русском языке

### Running and Verifying

```bash
# Проверка синтаксиса одного файла
python3 -m py_compile checkers/assignment_checker_test_m2v1.py

# Проверка синтаксиса всех файлов
for f in checkers/assignment_checker_test_*.py; do python3 -m py_compile "$f" && echo "OK: $f"; done
```

## Web Interface (`web/`)

Flask-приложение для запуска проверок через браузер.

```bash
cd web/
python3 main.py              # веб-сервер на 0.0.0.0:5000
python3 main.py --cli        # curses TUI
```

Логин: `admin` / `password`.

**Зависимости:** `flask>=2.2`, `requests`, `pexpect` (опционально), `sshpass` (системный пакет).

```bash
pip install -r web/requirements.txt
apt-get install sshpass
```

Приложение запускается **на самом сервере PNETLab/EVE-NG**. SSH-соединения направлены на `localhost` — QEMU-машины экспортируют порты локально.

## Report Generator (`report/`)

Генерирует Excel-таблицу из результатов проверок.

```bash
# Из веб-интерфейса: вкладка «Результаты» → загрузить ZIP-файлы → скачать Excel
# Или локально:
python3 report/make_report.py results_m1.zip results_m1_mod.zip results_m2.zip ...
```

**Формат ZIP-файлов** (скачиваются из веб-интерфейса после проверки):
`results_{вариант}_{ip}_{дд_мм_гггг_чч:мм}.zip`

Внутри ZIP: `outstend_{вариант}_{студент}-{ip}.txt` — лог проверки каждого студента.

**Итоговая Excel** содержит листы MOD-1, MOD-2, MOD-3, ИТОГ.
Колонки: `Имя пользователя | Узел | Вариант | Пункты | Балл по КО | Итог по логам`.

### Вспомогательные скрипты

- `report/points_extractor.py` — извлекает баллы из лог-файлов
- `report/log_extractor.py` — извлекает итоговый блок из лог-файлов для вставки в Excel

## Tasks (`tasks/`)

- `tasks/criteria/` — PDF с заданиями (МОДУЛЬ-1/2/3.pdf) и критерии оценки (критерии.docx)
- `tasks/variants/` — варианты заданий для раздачи участникам (М1-В1...М3-В3)

## Dependencies

- Python 3 (stdlib)
- `sshpass` — системный пакет (не pip)
- `pexpect` — опционально, для проверки EcoRouterOS-роутеров
- `openpyxl` — для генерации Excel (`pip install openpyxl`)
