"""Syntax check for all Python files in the project."""
import ast
import os
import sys
import glob

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_DIR)

# Discover all .py files (excluding .venv and hidden dirs)
exclude_dirs = {'.venv', '.git', '__pycache__', '.hermes'}

files = []
for root, dirs, filenames in os.walk(PROJECT_DIR):
    # Prune excluded dirs in-place so os.walk skips them
    dirs[:] = [d for d in dirs if d not in exclude_dirs]
    for fn in filenames:
        if fn.endswith('.py'):
            rel = os.path.relpath(os.path.join(root, fn), PROJECT_DIR)
            files.append(rel)

files.sort()
errors = []

for f in files:
    try:
        with open(f, encoding='utf-8') as fh:
            tree = ast.parse(fh.read())
        # Verify all imports are syntactically valid (no bare ast.parse needed beyond this)
        print(f'  ✓ {f}')
    except SyntaxError as e:
        errors.append((f, str(e)))
        print(f'  ✗ {f}: {e}')
    except Exception as e:
        print(f'  ? {f}: {e}')

print(f'\nTotal: {len(files)} files')
if errors:
    print(f'Failed: {len(errors)}')
    for f, e in errors:
        print(f'  - {f}: {e}')
    sys.exit(1)
else:
    print('All files OK')
