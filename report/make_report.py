#!/usr/bin/env python3
"""
Генератор Excel-отчёта из ZIP-архивов результатов проверки.

Использование:
    python3 make_report.py results_m1.zip results_m1_mod.zip results_m2.zip ...
    python3 make_report.py *.zip            # все архивы в текущей папке
    python3 make_report.py results/         # все ZIP из папки

Поддерживаемые форматы имён архивов:
    results_{вариант}_{ip}_{дд_мм_гггг_чч:мм}.zip
    results_{вариант}_{ip}.zip
    results_{вариант}.zip

Имена лог-файлов внутри ZIP:
    outstend_{вариант}_{студент}-{ip}.txt

Итоговый файл: report_{дата}.xlsx
"""

import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
except ImportError:
    print("❌ Установите openpyxl: pip install openpyxl")
    sys.exit(1)


# ─── Парсинг лог-файлов ──────────────────────────────────────────────────────

def parse_log(text: str) -> dict:
    """
    Извлекает из лога:
    - score_points: балл по пунктам (число из 'Выполнено X из Y')
    - score_ko:     балл по КО (число из 'Набрано X из Y')
    - report_block: итоговый блок (с 📊 до 📈 включительно)
    - is_mod:       True если это _mod-чекер (имеет КО-баллы)
    """
    result = {
        "score_points": None,
        "score_ko": None,
        "report_block": None,
        "is_mod": False,
    }

    # Извлекаем итоговый блок: от строки с 📊 до строки с 📈
    lines = text.splitlines()
    start = -1
    end = -1
    for i in range(len(lines) - 1, -1, -1):
        if "📈" in lines[i] and end == -1:
            end = i
        if "📊" in lines[i] and end != -1:
            start = i
            break

    if start != -1 and end != -1:
        result["report_block"] = "\n".join(lines[start:end + 1])

    # Балл по КО (_mod): "Набрано X из Y баллов"
    m = re.findall(r"Набрано\s+([\d.]+)\s+из", text)
    if m:
        val = float(m[-1])
        result["score_ko"] = int(val) if val == int(val) else val
        result["is_mod"] = True

    # Балл по пунктам (базовый): "Выполнено X из Y пунктов"
    m = re.findall(r"Выполнено\s+([\d.]+)\s+из", text)
    if m:
        val = float(m[-1])
        result["score_points"] = int(val) if val == int(val) else val

    return result


# ─── Парсинг имён файлов ─────────────────────────────────────────────────────

def parse_log_filename(filename: str) -> dict:
    """
    Парсит имя файла лога: outstend_{вариант}_{студент}-{ip}.txt
    Возвращает: {variant, student, node_ip}

    Пример: outstend_test_m2v1_Student_m2_11-192-168-103-43.txt
    """
    name = Path(filename).stem  # без .txt
    # Убираем префикс outstend_
    if name.startswith("outstend_"):
        name = name[len("outstend_"):]

    # IP в конце: -NNN-NNN-NNN-NNN (4 группы цифр через тире)
    ip_match = re.search(r"-(\d{1,3}-\d{1,3}-\d{1,3}-\d{1,3})$", name)
    node_ip = ""
    if ip_match:
        node_ip = ip_match.group(1).replace("-", ".")
        name = name[:ip_match.start()]

    # Вариант — первая часть до имени студента.
    # Паттерн варианта: test_mXvY или test_federal_code_mXvY
    variant_match = re.match(r"(test_(?:federal_code_)?m\dv\d(?:_mod)?)", name)
    if variant_match:
        variant = variant_match.group(1)
        student = name[len(variant):].lstrip("_")
    else:
        # Fallback: берём первые два сегмента как вариант
        parts = name.split("_")
        variant = "_".join(parts[:3]) if len(parts) >= 3 else name
        student = "_".join(parts[3:]) if len(parts) > 3 else ""

    return {
        "variant": variant,
        "student": student,
        "node_ip": node_ip,
    }


def parse_zip_filename(filename: str) -> dict:
    """
    Парсит имя ZIP-архива: results_{вариант}_{ip}_{дата}.zip
    Возвращает: {variant, node_ip, timestamp}

    Пример: results_test_m2v1_192-168-103-43_05_05_2025_14:32.zip
    """
    name = Path(filename).stem
    if name.startswith("results_"):
        name = name[len("results_"):]

    # Дата в конце: _ДД_ММ_ГГГГ_ЧЧ:ММ
    date_match = re.search(r"_(\d{2}_\d{2}_\d{4}_\d{2}[:\-]\d{2})$", name)
    timestamp = ""
    if date_match:
        timestamp = date_match.group(1)
        name = name[:date_match.start()]

    # IP: NNN-NNN-NNN-NNN
    ip_match = re.search(r"_(\d{1,3}-\d{1,3}-\d{1,3}-\d{1,3})$", name)
    node_ip = ""
    if ip_match:
        node_ip = ip_match.group(1).replace("-", ".")
        name = name[:ip_match.start()]

    return {"variant": name, "node_ip": node_ip, "timestamp": timestamp}


# ─── Определение модуля из варианта ──────────────────────────────────────────

def variant_to_module(variant: str) -> str:
    """test_m2v1 → 'MOD-2', test_m2v1_mod → 'MOD-2'"""
    m = re.search(r"m(\d)", variant)
    return f"MOD-{m.group(1)}" if m else "OTHER"


def is_mod_variant(variant: str) -> bool:
    return "_mod" in variant


# ─── Сбор данных из ZIP-файлов ───────────────────────────────────────────────

def collect_data(zip_paths: list) -> dict:
    """
    Возвращает:
    {
      "MOD-1": {
        student: {
          "base": {variant, node_ip, score_points, score_ko, report_block, is_mod},
          "mod":  {...}  # если есть _mod
        }
      },
      "MOD-2": {...},
      "MOD-3": {...},
    }
    """
    data = {"MOD-1": {}, "MOD-2": {}, "MOD-3": {}}

    for zip_path in zip_paths:
        try:
            zf = zipfile.ZipFile(zip_path)
        except Exception as e:
            print(f"⚠️  Не удалось открыть {zip_path}: {e}")
            continue

        zip_meta = parse_zip_filename(Path(zip_path).name)

        for entry in zf.namelist():
            if not entry.endswith(".txt") or not entry.startswith("outstend_"):
                continue
            try:
                text = zf.read(entry).decode("utf-8", errors="replace")
            except Exception:
                continue

            file_meta = parse_log_filename(entry)
            variant = file_meta["variant"]
            student = file_meta["student"]
            node_ip = file_meta["node_ip"] or zip_meta["node_ip"]
            module = variant_to_module(variant)

            if module not in data:
                continue

            parsed = parse_log(text)
            record = {
                "variant": variant,
                "node_ip": node_ip,
                "score_points": parsed["score_points"],
                "score_ko": parsed["score_ko"],
                "report_block": parsed["report_block"],
                "is_mod": parsed["is_mod"],
            }

            if student not in data[module]:
                data[module][student] = {}

            key = "mod" if is_mod_variant(variant) else "base"
            # Если уже есть запись — обновляем только если новая полнее
            if key not in data[module][student]:
                data[module][student][key] = record
            else:
                existing = data[module][student][key]
                if record["score_ko"] is not None and existing["score_ko"] is None:
                    data[module][student][key] = record

        zf.close()

    return data


# ─── Стили Excel ─────────────────────────────────────────────────────────────

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
SUBHEADER_FILL = PatternFill("solid", fgColor="2E75B6")
SUBHEADER_FONT = Font(bold=True, color="FFFFFF", size=10)
ALT_FILL = PatternFill("solid", fgColor="DEEAF1")
TOTAL_FILL = PatternFill("solid", fgColor="E2EFDA")
TOTAL_FONT = Font(bold=True, size=11)
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)


def style_header(cell, sub=False):
    cell.fill = SUBHEADER_FILL if sub else HEADER_FILL
    cell.font = SUBHEADER_FONT if sub else HEADER_FONT
    cell.alignment = CENTER
    cell.border = BORDER


def style_cell(cell, alt=False, wrap=True):
    if alt:
        cell.fill = ALT_FILL
    cell.border = BORDER
    cell.alignment = WRAP if wrap else CENTER


# ─── Построение листа модуля ─────────────────────────────────────────────────

MOD_HEADERS = [
    "Имя пользователя",
    "Узел",
    "Вариант",
    "Пункты задания",
    "Балл по КО",
    "Итог по логам",
]

COL_WIDTHS = [22, 16, 18, 14, 11, 80]


def build_module_sheet(ws, module_data: dict, module_name: str):
    ws.title = module_name

    # Заголовок листа
    ws.merge_cells("A1:F1")
    title_cell = ws["A1"]
    title_cell.value = f"Результаты проверки — {module_name}"
    title_cell.fill = HEADER_FILL
    title_cell.font = Font(bold=True, color="FFFFFF", size=13)
    title_cell.alignment = CENTER
    title_cell.border = BORDER
    ws.row_dimensions[1].height = 24

    # Заголовки колонок
    for col, (header, width) in enumerate(zip(MOD_HEADERS, COL_WIDTHS), start=1):
        cell = ws.cell(row=2, column=col, value=header)
        style_header(cell, sub=True)
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.row_dimensions[2].height = 20

    # Сортируем студентов
    students = sorted(module_data.keys())

    for i, student in enumerate(students):
        row = i + 3
        alt = (i % 2 == 1)
        records = module_data[student]

        # Берём base для пунктов, mod для КО-балла
        base = records.get("base", {})
        mod = records.get("mod", {})

        variant = (base or mod).get("variant", "")
        node_ip = (base or mod).get("node_ip", "")
        score_points = base.get("score_points")
        score_ko = mod.get("score_ko") if mod else base.get("score_ko")
        report_block = (mod or base).get("report_block", "")

        values = [student, node_ip, variant, score_points, score_ko, report_block]
        for col, val in enumerate(values, start=1):
            cell = ws.cell(row=row, column=col, value=val)
            style_cell(cell, alt=alt, wrap=(col == 6))
            if col in (4, 5):
                cell.alignment = CENTER

        # Высота строки зависит от длины блока
        if report_block:
            line_count = report_block.count("\n") + 1
            ws.row_dimensions[row].height = max(15, min(line_count * 14, 400))

    ws.freeze_panes = "A3"


# ─── Построение листа ИТОГ ───────────────────────────────────────────────────

ИТОГ_HEADERS = ["Имя пользователя", "MOD-1 (25)", "MOD-2 (25)", "MOD-3 (25)", "Итого (75)", "% выполнения"]
ИТОГ_WIDTHS  = [22, 12, 12, 12, 12, 14]


def build_summary_sheet(ws, all_data: dict):
    ws.title = "ИТОГ"

    ws.merge_cells("A1:F1")
    t = ws["A1"]
    t.value = "Итоговая сводка по всем модулям"
    t.fill = HEADER_FILL
    t.font = Font(bold=True, color="FFFFFF", size=13)
    t.alignment = CENTER
    t.border = BORDER
    ws.row_dimensions[1].height = 24

    for col, (header, width) in enumerate(zip(ИТОГ_HEADERS, ИТОГ_WIDTHS), start=1):
        cell = ws.cell(row=2, column=col, value=header)
        style_header(cell, sub=True)
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.row_dimensions[2].height = 20

    # Собираем всех студентов
    all_students = set()
    for module_data in all_data.values():
        all_students.update(module_data.keys())

    for i, student in enumerate(sorted(all_students)):
        row = i + 3
        alt = (i % 2 == 1)

        scores = []
        for mod in ("MOD-1", "MOD-2", "MOD-3"):
            records = all_data.get(mod, {}).get(student, {})
            mod_rec = records.get("mod", {})
            base_rec = records.get("base", {})
            score = mod_rec.get("score_ko") if mod_rec else base_rec.get("score_ko")
            scores.append(score)

        total = sum(s for s in scores if s is not None)
        pct = round(total / 75 * 100, 1) if total else 0

        values = [student] + scores + [total, pct]
        for col, val in enumerate(values, start=1):
            cell = ws.cell(row=row, column=col, value=val)
            style_cell(cell, alt=alt, wrap=False)
            cell.alignment = CENTER
            if col == 1:
                cell.alignment = Alignment(vertical="center")

    ws.freeze_panes = "A3"


# ─── Основная функция ────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    # Собираем список ZIP-файлов
    zip_paths = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        if p.is_dir():
            zip_paths.extend(sorted(p.glob("*.zip")))
        elif p.suffix == ".zip" and p.exists():
            zip_paths.append(p)
        else:
            print(f"⚠️  Пропускаем: {arg}")

    if not zip_paths:
        print("❌ ZIP-файлы не найдены")
        sys.exit(1)

    print(f"📂 Обрабатываем {len(zip_paths)} архив(ов):")
    for z in zip_paths:
        print(f"   {z.name}")

    data = collect_data(zip_paths)

    # Статистика
    for mod, mod_data in data.items():
        print(f"   {mod}: {len(mod_data)} студентов")

    # Генерируем Excel
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # удаляем пустой лист по умолчанию

    for module in ("MOD-1", "MOD-2", "MOD-3"):
        ws = wb.create_sheet(module)
        build_module_sheet(ws, data.get(module, {}), module)

    ws_total = wb.create_sheet("ИТОГ")
    build_summary_sheet(ws_total, data)

    ts = datetime.now().strftime("%d_%m_%Y_%H-%M")
    out_path = Path(f"report_{ts}.xlsx")
    wb.save(out_path)
    print(f"\n✅ Отчёт сохранён: {out_path.resolve()}")


if __name__ == "__main__":
    main()
