#!/usr/bin/env python3
"""
Извлекает итоговые блоки из логов → одна строка на студента.
При вставке в Excel каждый блок попадает в одну ячейку.

Использование:
    python3 log_extractor.py /path/to/logs/
"""

import sys
import re
from pathlib import Path


def extract_report(text):
    lines = text.splitlines()

    start = -1
    for i in range(len(lines) - 1, -1, -1):
        if '📊' in lines[i]:
            start = i
            break

    if start == -1:
        return None

    result = []
    for line in lines[start:]:
        result.append(line)
        if '📈' in line:
            break

    return '\n'.join(result)


def main():
    if len(sys.argv) < 2:
        print("Использование:")
        print("  python3 %s <папка_с_логами>" % sys.argv[0])
        sys.exit(1)

    files = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        if p.is_dir():
            for ext in ['*.txt', '*.log', '*.out']:
                files.extend(sorted(p.glob(ext)))
            if not files:
                files = sorted(f for f in p.iterdir() if f.is_file())
        elif p.is_file():
            files.append(p)

    if not files:
        print("❌ Файлы не найдены")
        sys.exit(1)

    for fp in files:
        try:
            text = open(fp, 'r', encoding='utf-8', errors='replace').read()
        except:
            continue

        report = extract_report(text)
        if report:
            # Оборачиваем в кавычки — Excel воспримет \n внутри как перенос в ячейке
            escaped = report.replace('"', '""')
            print('"%s"' % escaped)


if __name__ == "__main__":
    main()
