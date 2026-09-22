import typing

if not hasattr(typing, 'override'):
    typing.override = lambda function: function

exec(compile(open('main.py', encoding='utf-8').read(), 'main.py', 'exec'))
