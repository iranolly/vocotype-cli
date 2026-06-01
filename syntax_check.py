"""Syntax check for all modified files"""
import ast, os

files = [
    'app/funasr_config.py',
    'app/funasr_server.py',
    'app/proper_nouns.py',
    'app/post_processor.py',
    'main.py',
]

for f in files:
    with open(f, encoding='utf-8') as fh:
        ast.parse(fh.read())
    print(f'{f}: OK')

print('All files OK')
