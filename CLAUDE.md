# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated assessment checker scripts for the Russian professional exam "ДЭ 09.02.06 — Сетевое и системное администрирование" (Network and System Administration). Scripts validate infrastructure configurations across virtual machines via SSH.

## File Naming Convention

`assignment_checker_test_m{module}v{variant}[_mod].py`

- **m1/m2/m3** — Module 1 (networking), Module 2 (services), Module 3 (advanced services)
- **v1/v2/v3** — Variants with different parameter values (IPs, usernames, RAID levels, etc.)
- **_mod** — Scoring versions with `award()` function for graded evaluation per criteria (КО). MAX_POINTS=25 per module.

Base scripts (9) output pass/fail per checkpoint. Mod scripts (3) output numeric scores with partial credit.

## Running and Verifying

```bash
# Syntax check a single file
python3 -m py_compile assignment_checker_test_m2v1.py

# Syntax check all files
for f in assignment_checker_test_*.py; do python3 -m py_compile "$f" && echo "OK: $f"; done
```

Scripts are not run locally — they execute remotely via `ssh_exec()` against lab VMs. Entry point: `run_full_assignment_check(vm_ports)` where `vm_ports` is a `dict[str, str]` mapping device names to SSH port numbers.

## Architecture

Each script contains:

1. **Credentials** — `SRV_CREDENTIALS` (root/toor), `RTR_CREDENTIALS` (admin/admin)
2. **Execution functions:**
   - `ssh_exec(port, cmd, user, pass)` — Linux servers via sshpass+ssh
   - `rtr_exec(port, user, pass, cmd)` — EcoRouterOS routers via pexpect (interactive)
   - `rtr_exec_with_enable(...)` — Router privileged mode
3. **Check sections** — Each "Пункт" validates a specific service/config
4. **Scoring** (_mod only) — `award(name, score, max_score, details)` with partial credit

Devices: HQ-SRV, BR-SRV, HQ-CLI, BR-CLI, HQ-RTR, BR-RTR, ISP. Domain: `au-team.irpo`.

## Key Conventions

- **Prefer native Linux utilities** over cat/grep pipelines for remote commands:
  - `sshd -T` instead of `cat sshd_config`
  - `findmnt` instead of `mount | grep`
  - `mdadm --detail` instead of `cat /proc/mdstat`
  - `ss -tlnH sport = :PORT` instead of `ss -tuln | grep`
  - `systemctl is-active SERVICE` instead of `systemctl list-units | grep`
  - `htpasswd -vb` instead of `cat .htpasswd`
- **Domain user auth**: SSH as domain user first (creates profile), fallback to `wbinfo -a` — never use `su -l user -c 'whoami'`
- **cat/grep is acceptable** where no native alternative exists: compose.yaml, fail2ban jail.local, logrotate configs, nginx configs, PAM configs, ansible inventory
- All user-facing text is in Russian
- `pexpect` is optional — scripts degrade gracefully without it
- v2/v3 differ from v1 **only in parameter values** — propagate structural changes from v1 to v2/v3

## Criteria Reference

`критерии.docx` contains official scoring criteria. Each module has 13 КО (criteria) totaling 25 points. The "Отчёт ГОСТ" criterion (1 point) requires manual evaluation and is not automated.

For _mod scripts: verify `MAX_POINTS` equals the sum of unique `award()` max_score values plus the manual criterion.

## Dependencies

- Python 3 (stdlib only)
- `sshpass` (required at runtime on the checking machine)
- `pexpect` (optional, for EcoRouterOS router checks)
