#!/usr/bin/env python3
"""
Извлекает набранные баллы из логов проверки.
Использование:
    python3 extract_scores.py /path/to/logs/
    python3 extract_scores.py log1.txt log2.txt
"""

import sys
import re
from pathlib import Path


def extract_score(filepath):
    try:
        text = open(filepath, 'r', encoding='utf-8', errors='replace').read()
    except:
        return None
    m = re.findall(r'Набрано\s+([\d.]+)\s+из', text)
    if m:
        score = float(m[-1])
        return int(score) if score == int(score) else score
    # Фоллбэк: старый формат "Выполнено X из Y"
    m = re.findall(r'Выполнено\s+([\d.]+)\s+из', text)
    if m:
        score = float(m[-1])
        return int(score) if score == int(score) else score
    return None


files = []
for arg in sys.argv[1:]:
    p = Path(arg)
    if p.is_dir():
        files.extend(sorted(f for f in p.iterdir() if f.is_file()))
    elif p.is_file():
        files.append(p)

for f in files:
    score = extract_score(f)
    if score is not None:
        print(score)