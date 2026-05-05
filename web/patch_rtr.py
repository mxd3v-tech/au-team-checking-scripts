import os
import glob

def patch_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    lines = content.split('\n')
    new_lines = []
    i = 0
    modified = False
    while i < len(lines):
        line = lines[i]
        new_lines.append(line)
        if line.startswith('def rtr_exec_with_enable('):
            # check next lines to see if we already patched
            next_lines = '\n'.join(lines[i+1:i+5])
            if 'port == "N/A"' not in next_lines:
                # Need to find the right indentation
                # let's just insert it after the docstring or first line of body
                i += 1
                while i < len(lines) and (lines[i].strip() == '' or '"""' in lines[i]):
                    new_lines.append(lines[i])
                    if '"""' in lines[i] and lines[i].count('"""') < 2:
                        i+=1
                        while i < len(lines) and '"""' not in lines[i]:
                            new_lines.append(lines[i])
                            i+=1
                        new_lines.append(lines[i])
                    i += 1
                # now insert
                new_lines.append('    if not port or port == "N/A":')
                new_lines.append('        return None, "Node not running (N/A)", command')
                modified = True
                continue # we already incremented i, so don't increment at the end of loop
        i += 1

    if modified:
        with open(filepath, 'w') as f:
            f.write('\n'.join(new_lines))
        print(f"Patched {filepath}")

for f in glob.glob("*.py"):
    patch_file(f)
