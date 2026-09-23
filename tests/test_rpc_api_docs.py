"""Keep runtime discovery and both human-readable RPC inventories identical."""

import ast
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def runtime_methods():
    tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8'))
    candidates = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (isinstance(key, ast.Constant) and key.value == 'methods'
                    and isinstance(value, (ast.List, ast.Tuple))
                    and all(isinstance(item, ast.Constant)
                            and isinstance(item.value, str)
                            for item in value.elts)):
                candidates.append(tuple(item.value for item in value.elts))
    assert len(candidates) == 1, 'main.py must expose one literal RPC method list'
    return candidates[0]


def documented_inventory(markdown):
    section = markdown.split('## Method inventory', 1)[1]
    section = section.split('## `agent.capabilities`', 1)[0]
    methods = []
    for line in section.splitlines():
        match = re.match(r'^\| `([^`]+)` \|', line)
        if match:
            methods.append(match.group(1))
    assert methods, 'method inventory table is missing or unreadable'
    return tuple(methods)


def documented_capabilities_example(markdown):
    section = markdown.split('## `agent.capabilities`', 1)[1]
    section = section.split('## `emulator.info`', 1)[0]
    blocks = re.findall(r'```json\s*\n(.*?)\n```', section, flags=re.DOTALL)
    examples = []
    for block in blocks:
        try:
            value = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get('methods'), list):
            examples.append(tuple(value['methods']))
    assert len(examples) == 1, 'capabilities section must contain one methods result'
    return examples[0]


def test_rpc_method_documentation_matches_runtime_capabilities():
    markdown = (ROOT / 'JSON_RPC_API.md').read_text(encoding='utf-8')
    runtime = runtime_methods()
    inventory = documented_inventory(markdown)
    example = documented_capabilities_example(markdown)

    assert inventory == runtime, (
        f'inventory differs from runtime; missing={sorted(set(runtime)-set(inventory))}, '
        f'extra={sorted(set(inventory)-set(runtime))}')
    assert example == runtime, (
        f'capabilities example differs from runtime; '
        f'missing={sorted(set(runtime)-set(example))}, '
        f'extra={sorted(set(example)-set(runtime))}')
    assert len(runtime) == len(set(runtime)), 'runtime method discovery has duplicates'
