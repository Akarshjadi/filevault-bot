"""Check Python syntax of all .py files in the project."""
import ast
import os
import sys

errors = []
for root, dirs, files in os.walk('.'):
    # Skip virtual environments and cache
    dirs[:] = [d for d in dirs if d not in ('venv', '.venv', '__pycache__', '.git')]
    for f in files:
        if f.endswith('.py'):
            path = os.path.join(root, f)
            try:
                with open(path) as fh:
                    ast.parse(fh.read())
            except SyntaxError as e:
                errors.append(f"{path}: {e}")

if errors:
    for e in errors:
        print(f"ERROR: {e}")
    sys.exit(1)
else:
    print("All Python files pass syntax check")