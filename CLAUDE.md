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
- **vch** — Вариативная часть (раздел 4 ведомости): узел управления Ansible на BR-SRV. `vch_mod` использует взвешенную оценку (сырой балл 0/1/2 × вес 1 или 1.5), вариант-независим (одна версия на все варианты).

Базовые скрипты (без `_mod`) выводят пройдено/не пройдено по каждому пункту.
`_mod`-скрипты выводят числовой балл с частичным зачётом.

### Output contract (📊/📈) — критично

`report/` и `web/main.py` парсят лог **по маркерам**, не по структуре кода. Этот контракт нельзя ломать:

- Итоговый блок — строки от первой `📊` до строки с `📈` (см. `report/log_extractor.py`, `make_report.py`).
- `_mod`/`vch`: последняя строка `📈 Набрано X из Y баллов` → колонка «Балл по КО».
- base: последняя строка `📈 Выполнено X из Y пунктов` → колонка «Пункты задания».
- Внутри блока каждая строка КО: `<иконка> <код> <краткое название>: <балл>/<макс>` (например `✅ 1.1 Маршрутизация и NAT на ISP: 2/2`). Эту строку разбирает `parse_vedomost()` в `web/main.py` для таблицы-ведомости.

`run_full_assignment_check` печатает лог И через `print()`, И накапливает в `log_lines` (веб стримит построчно). Менять сигнатуру `(score, log_lines)` нельзя.

### Оценочная ведомость (VEDOMOST)

`tasks/Ведомость.pdf` задаёт официальный порядок, коды и названия КО (4 раздела: m1→раздел 1, m2→раздел 2, m3→раздел 3, vch→раздел 4). Каждый `_mod`/`vch` несёт список `VEDOMOST` (`(код, краткое_название, внутреннее_имя_КО)`).

**Логику проверки (`award()`) при этом не трогают** — переименование и сортировка применяются **только** при формировании итогового блока `📊…📈`. Внутренние имена КО в `award()` остаются прежними; `VEDOMOST` отображает их в порядок/имена ведомости. Маппинг m1/m2/m3 — один-в-один по макс. баллам; vch идёт по позиции (КО1…КО10 = 4.1…4.10).

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

⚠️ **`web/` содержит собственные копии чекеров.** Приложение грузит их из своего каталога (`CHECKER_DIR = web/`, через `__import__`), а **не** из `checkers/`. Источник правды — `checkers/`; после правки чекера копию нужно скопировать в `web/`, иначе веб запускает старую версию. (`VARIANTS_DIR = web/generated_variants` — для динамически созданных вариантов `assignment_checker_task*.py`, обычно отсутствует.)

Результаты чекеров для веба собирает `run_check_in_thread` → `last_results` (отдаётся через `/get_results`). Путь `run_check_for_users` обслуживает curses-TUI (`--cli`), а не веб-таблицу.

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
- `tasks/variants/` — варианты заданий участникам: `В{N}_КОД 09.02.06-1-2026-ПУ.pdf` (один PDF = все модули варианта)
- `tasks/Ведомость.pdf` — оценочная ведомость: эталонные коды/порядок/названия КО (источник для `VEDOMOST`)

## Dependencies

- Python 3 (stdlib)
- `sshpass` — системный пакет (не pip)
- `pexpect` — опционально, для проверки EcoRouterOS-роутеров
- `openpyxl` — для генерации Excel (`pip install openpyxl`)
