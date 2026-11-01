"""Installation paths live in local runtime.json or environment, never in workflows."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def discover():
    try:
        config = json.loads((ROOT / 'runtime.json').read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        config = {}
    def option(key, env, fallback):
        return os.environ.get(env) or config.get(key) or fallback
    install_base = ROOT.parent.parent if ROOT.parent.name == 'custom_nodes' else None
    if install_base is None:
        install_base = ROOT / '.local-runtime'
    base = Path(option('comfy_base', 'OPENPOSEGEN_COMFY_BASE', str(install_base))).resolve()
    core = Path(option('comfy_core', 'OPENPOSEGEN_COMFY_CORE', str(base)))
    assets = option('asset_root', 'OPENPOSEGEN_ASSET_ROOT', os.environ.get('BBAIGC_ASSET_ROOT') or str(base.parent / 'AssetLibrary' / 'RuiC-PoseAtlas'))
    return {'comfy_base': base, 'comfy_core': core.resolve(), 'asset_root': Path(assets).resolve(),
            'python': str(option('python', 'OPENPOSEGEN_PYTHON', sys.executable)),
            'comfy_url': str(option('comfy_url', 'OPENPOSEGEN_COMFY_URL', 'http://127.0.0.1:8000')).rstrip('/'),
            'port': int(option('port', 'OPENPOSEGEN_PORT', 8191)),
            'input': base / 'input', 'output': base / 'output', 'temp': base / 'temp'}
