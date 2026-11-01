"""Install OpenPoseGen without replacing user data or the working PyTorch stack."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'ComfyUI-BBAIGC-AssetLibrary'
IGNORED = {'.git', '__pycache__', '.pytest_cache', '.playwright-mcp', 'node_modules', 'verification', 'sources', 'tests', '.sync-backups', 'release'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def run(arguments, capture=False):
    return subprocess.run([str(value) for value in arguments], check=True, text=True, encoding='utf-8',
                          stdout=subprocess.PIPE if capture else None).stdout


def windows_installations():
    path = Path(os.environ.get('APPDATA', '')) / 'Comfy Desktop' / 'installations.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    found = []
    def walk(item):
        if isinstance(item, dict):
            paths = {key: Path(val) for key, val in item.items() if isinstance(val, str) and ('path' in key.lower() or 'directory' in key.lower() or key.lower().endswith('dir')) and len(val) < 2000}
            bases = [path for path in paths.values() if (path / 'custom_nodes').is_dir() and (path / 'user').is_dir()]
            cores = [candidate for path in paths.values() for candidate in (path, path / 'ComfyUI') if (candidate / 'folder_paths.py').is_file()]
            if bases:
                found.append((bases[0], cores[0] if cores else None))
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
    walk(value)
    return found


def discover(args):
    base = args.comfy_base.resolve() if args.comfy_base else None
    core = args.comfy_core.resolve() if args.comfy_core else None
    if base is None and ROOT.parent.name == 'custom_nodes':
        base = ROOT.parent.parent.resolve()
    candidates = windows_installations() if os.name == 'nt' else []
    if base is None and len(candidates) == 1:
        base, detected_core = candidates[0]
        core = core or detected_core
    if base is None:
        base = next((path.resolve() for path in (Path.cwd(), Path.cwd() / 'ComfyUI') if (path / 'custom_nodes').is_dir() and ((path / 'main.py').is_file() or (path / 'user').is_dir())), None)
    if base is None:
        raise ValueError('无法唯一识别ComfyUI实例，请明确指定 --comfy-base、--comfy-core 和 --python。')
    if core is None:
        options = [base, base / 'ComfyUI', *[candidate for detected, candidate in candidates if detected.resolve() == base and candidate]]
        core = next((path.resolve() for path in options if (path / 'folder_paths.py').is_file()), None)
    if core is None:
        raise ValueError('没有找到含folder_paths.py的ComfyUI核心，请指定 --comfy-core。')
    python = args.python.resolve() if args.python else next((path for path in (base / '.venv/Scripts/python.exe', base / 'python_embeded/python.exe', base.parent / 'python_embeded/python.exe', base / '.venv/bin/python') if path.is_file()), None)
    if python is None or not python.is_file():
        raise ValueError('没有找到当前实例的Python，请指定 --python；不要使用另一个ComfyUI的环境。')
    if not (base / 'custom_nodes').is_dir() or not (core / 'folder_paths.py').is_file():
        raise ValueError('ComfyUI实例或核心路径不完整。')
    return base, core, python


def files(root, exclusions=()):
    for current, directories, names in os.walk(root):
        directories[:] = [name for name in directories if name not in IGNORED and name not in exclusions and not (Path(current) / name).is_symlink()]
        for name in names:
            path = Path(current) / name
            if path.is_symlink() or name.endswith(('.pyc', '.log', '.tmp')) or name in ('runtime.json', 'provider.json', 'settings.json', 'jobs.json'):
                continue
            yield path, path.relative_to(root)


def copy_preserving(source, target, backup_root):
    copied = []
    for path, relative in files(source):
        destination = target / relative
        if destination.is_file() and digest(path) == digest(destination):
            continue
        if destination.exists():
            backup = backup_root / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, backup)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        if digest(path) != digest(destination):
            raise RuntimeError(f'安装文件校验失败：{relative}')
        copied.append(relative.as_posix())
    return copied


def dependency_targets(base):
    specifications = json.loads((ROOT / 'install/dependencies.json').read_text(encoding='utf-8'))
    targets = []
    custom = base / 'custom_nodes'
    for item in specifications['bundled']:
        matches = []
        for candidate in custom.iterdir():
            if not candidate.is_dir() or candidate.name.startswith('.'):
                continue
            marker = candidate / item['marker']
            if candidate.name in item['aliases'] or marker.is_file() and item['signature'] in marker.read_text(encoding='utf-8', errors='replace'):
                matches.append(candidate)
        if len(matches) > 1:
            raise ValueError('发现重复节点安装，请先保留一份：' + ', '.join(map(str, matches)))
        target = matches[0] if matches else custom / item['directory']
        targets.append((item, target))
    return specifications, targets


def install_dependency(item, target, backup_root):
    source = ROOT / 'release/dependencies' / item['directory']
    if not source.is_dir():
        raise ValueError(f'发布包缺少共享运行源码：{source.name}。请使用完整release ZIP。')
    if target.exists():
        different = [relative for path, relative in files(source) if not (target / relative).is_file() or digest(path) != digest(target / relative)]
        if not different:
            return {'directory': target.name, 'status': 'reused'}
        installed_manifest = target / '.openposegen-bundle.json'
        if not installed_manifest.is_file():
            if not (target / '.git').exists():
                raise ValueError(f'{target.name}已存在且与发布快照不同，已保留；不能覆盖无法识别的本地修改。')
            remote = run(['git', '-C', target, 'remote', 'get-url', 'origin'], True).strip().removesuffix('.git')
            if remote != item['repository'] or run(['git', '-C', target, 'status', '--porcelain'], True).strip():
                raise ValueError(f'{target.name}包含本地修改或来自其他来源，已保留；不能注册第二份共享节点。')
        else:
            previous = json.loads(installed_manifest.read_text(encoding='utf-8')).get('files', {})
            changed = [relative for relative, expected in previous.items() if (target / relative).is_file() and digest(target / relative) != expected]
            if changed:
                raise ValueError(f'{target.name}含用户本地修改，已保留：' + ', '.join(changed[:5]))
    copied = copy_preserving(source, target, backup_root / target.name)
    atomic_json(target / '.openposegen-bundle.json', {'version': item['version'], 'repository': item['repository'], 'files': {relative.as_posix(): digest(path) for path, relative in files(source)}})
    return {'directory': target.name, 'status': 'installed', 'files': len(copied)}


def protected_pip(python, specs):
    code = "import json,importlib.metadata as m;d={};\nfor n in ['torch','torchvision','torchaudio']:\n try:d[n]=m.version(n)\n except m.PackageNotFoundError:pass\nprint(json.dumps(d))"
    protected = json.loads(run([python, '-c', code], True))
    with tempfile.TemporaryDirectory(prefix='openposegen-pip-') as temp:
        constraints, report = Path(temp) / 'constraints.txt', Path(temp) / 'plan.json'
        constraints.write_text('\n'.join(f'{name}==={version}' for name, version in protected.items()), encoding='utf-8')
        command = [python, '-m', 'pip', 'install', '--upgrade-strategy', 'only-if-needed', '-c', constraints, *specs]
        run([*command, '--dry-run', '--report', report])
        planned = json.loads(report.read_text(encoding='utf-8'))
        if any(item.get('metadata', {}).get('name', '').lower() in ('torch', 'torchvision', 'torchaudio') for item in planned.get('install', [])):
            raise ValueError('依赖计划会替换PyTorch组件，已停止，原环境未改动。')
        run(command)
    if json.loads(run([python, '-c', code], True)) != protected:
        raise RuntimeError('安装后PyTorch版本变化，停止后续步骤。')


def patch_gguf(target, backup_root):
    """Apply only the two verified Qwen2.1 compatibility expressions."""
    path = target / 'loader.py'
    text = path.read_text(encoding='utf-8')
    original = text
    import re
    match = re.search(r'IMG_ARCH_LIST\s*=\s*\{([^\n]+)\}', text)
    if not match:
        raise ValueError('GGUF架构列表已变化，请先核对Qwen2.1兼容性，未覆盖上游文件。')
    if 'qwen_image21' not in match.group(0):
        text = text[:match.end() - 1] + ', "qwen_image21"' + text[match.end() - 1:]
    old = 'if len(shape) <= 1 and tensor.tensor_type == gguf.GGMLQuantizationType.BF16:'
    fixed = 'if len(shape) <= 1 and is_quantized(state_dict[sd_key]):'
    if old in text:
        text = text.replace(old, fixed)
    elif fixed not in text:
        anchor = '        state_dict[sd_key] = GGMLTensor(torch_tensor, tensor_type=tensor.tensor_type, tensor_shape=shape)'
        if text.count(anchor) != 1:
            raise ValueError('GGUF张量加载实现已变化，未自动重写，请核对当前上游支持。')
        text = text.replace(anchor, anchor + '\n        ' + fixed + '\n            state_dict[sd_key] = dequantize_tensor(state_dict[sd_key], dtype=torch.float32)')
    if 'from .dequant import is_quantized' not in text:
        raise ValueError('GGUF反量化接口已变化，未覆盖文件。')
    if text != original:
        backup = backup_root / 'ComfyUI-GGUF/loader.py'
        backup.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, backup)
        path.write_text(text, encoding='utf-8')


def download_model(item, models_root):
    destination = (models_root / item['destination']).resolve()
    if not destination.is_relative_to(models_root.resolve()):
        raise ValueError('模型清单路径越界。')
    if destination.is_file():
        if destination.stat().st_size != item['bytes'] or digest(destination) != item['sha256']:
            raise ValueError(f'已有模型与指定版本不同，未覆盖：{destination.name}')
        return {'file': item['destination'], 'status': 'verified-existing'}
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + '.part')
    offset = partial.stat().st_size if partial.is_file() else 0
    if offset > item['bytes']:
        raise ValueError(f'已有断点文件大小不正确，已保留：{partial.name}')
    headers = {'User-Agent': 'OpenPoseGen/1.2.0', 'Accept-Encoding': 'identity'}
    if offset:
        headers['Range'] = f'bytes={offset}-'
    credential = os.environ.get('CIVITAI_API_KEY') if 'civitai.com' in item['url'] else os.environ.get('HF_TOKEN')
    if credential:
        headers['Authorization'] = 'Bearer ' + credential
    class PublicRedirect(HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, response_headers, new_url):
            result = super().redirect_request(request, fp, code, msg, response_headers, new_url)
            if result and urlsplit(request.full_url).netloc != urlsplit(new_url).netloc:
                result.remove_header('Authorization')
            return result
    try:
        with build_opener(PublicRedirect()).open(Request(item['url'], headers=headers), timeout=120) as response:
            resumed = offset > 0 and response.status == 206
            if resumed and not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
                raise ValueError('模型下载断点响应不匹配。')
            with partial.open('ab' if resumed else 'wb') as output:
                shutil.copyfileobj(response, output, 1024 * 1024)
    except Exception as error:
        raise RuntimeError(f'模型下载未完成：{item["destination"]}。断点已保留；需要登录时请先接受上游许可并设置本机HF_TOKEN/CIVITAI_API_KEY。') from error
    if partial.stat().st_size != item['bytes'] or digest(partial) != item['sha256']:
        raise ValueError(f'模型校验失败，断点已保留：{partial.name}')
    os.replace(partial, destination)
    return {'file': item['destination'], 'status': 'downloaded-verified'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comfy-base', type=Path)
    parser.add_argument('--comfy-core', type=Path)
    parser.add_argument('--python', type=Path)
    parser.add_argument('--asset-root', type=Path)
    parser.add_argument('--models-root', type=Path)
    parser.add_argument('--comfy-url', default='http://127.0.0.1:8000')
    parser.add_argument('--models', default='none', help='none or qwen,qwen-turbo,moss,moss-official,speech')
    parser.add_argument('--with-speech', action='store_true')
    parser.add_argument('--text-model', choices=('none', 'qwen3.5:9b'), default='none', help='optional Ollama optimizer model; requires an installed Ollama CLI')
    parser.add_argument('--skip-pip', action='store_true')
    parser.add_argument('--plan', action='store_true', help='read-only path and dependency plan')
    args = parser.parse_args()
    base, core, python = discover(args)
    target = base / 'custom_nodes' / PACKAGE
    asset_root = args.asset_root.resolve() if args.asset_root else base.parent / 'OpenPoseGen-Assets' / 'RuiC-PoseAtlas'
    models_root = args.models_root.resolve() if args.models_root else base / 'models'
    if asset_root.is_relative_to(target):
        raise ValueError('素材和用户数据不能放入插件源码目录。')
    specifications, dependencies = dependency_targets(base)
    selected = set(args.models.replace(' ', '').split(',')) - {'none', ''}
    if args.with_speech:
        selected.add('speech')
    model_data = json.loads((ROOT / 'install/models.json').read_text(encoding='utf-8'))
    if selected - set(model_data['presets']):
        raise ValueError('未知模型方案：' + ', '.join(selected - set(model_data['presets'])))
    plan = {'version': '1.2.0', 'comfy_base': str(base), 'comfy_core': str(core), 'python': str(python), 'plugin': str(target),
            'asset_root': str(asset_root), 'models_root': str(models_root), 'comfy_url': args.comfy_url,
            'models': sorted(selected), 'text_model': args.text_model, 'dependencies': [item['directory'] for item, destination in dependencies]}
    if args.plan:
        print(json.dumps(plan, ensure_ascii=False, indent=2)); return
    ollama = shutil.which('ollama') if args.text_model != 'none' else None
    if args.text_model != 'none' and not ollama:
        raise ValueError('可选文字优化需要先安装官方Ollama，或保持 --text-model none 使用已有API服务；未自动启动其他软件。')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backups = base / '.openposegen-install' / stamp
    # Preflight every shared dependency before changing the main extension.
    dependency_report = [install_dependency(item, destination, backups) for item, destination in dependencies]
    gguf = base / 'custom_nodes' / 'ComfyUI-GGUF'
    if not gguf.exists():
        run(['git', 'clone', '--depth', '1', 'https://github.com/city96/ComfyUI-GGUF.git', gguf])
    patch_gguf(gguf, backups)
    copied = copy_preserving(ROOT, target, backups / PACKAGE) if ROOT.resolve() != target.resolve() else []
    # The standalone product must not depend on a hard-coded shared-core folder name.
    # This file is generated from the one maintained host source, not another fork.
    host = target / 'comfy_web/host.js'
    host.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / 'web/asset_library.js', host)
    config = {key: plan[key] for key in ('comfy_base', 'comfy_core', 'python', 'asset_root', 'comfy_url')}
    config['port'] = 8191
    atomic_json(target / 'runtime.json', config)
    if not args.skip_pip:
        specs = ['aiohttp>=3.10', 'Pillow>=10', 'requests', 'numpy', 'safetensors', 'gguf']
        if args.with_speech or 'speech' in selected:
            specs += ['onnxruntime', 'kaldi-native-fbank', 'PyYAML']
        protected_pip(python, specs)
    chosen = {key for preset in selected for key in model_data['presets'][preset]}
    model_report = [download_model(model_data['files'][key], models_root) for key in sorted(chosen)]
    if ollama:
        run([ollama, 'pull', args.text_model])
    workflow_dir = base / 'user/default/workflows/OpenPoseGen'
    for filename in ('qwen.json', 'qwen_turbo8.json', 'moss.json'):
        path = ROOT / 'workflows' / filename
        destination = workflow_dir / filename
        if destination.is_file() and digest(path) != digest(destination):
            destination = workflow_dir / (path.stem + '-v1.2.0.json')
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, destination)
    asset_root.mkdir(parents=True, exist_ok=True)
    report = {**plan, 'dependencies': dependency_report, 'files_updated': len(copied), 'models': model_report, 'restart_required': True,
              'validation': 'Installed files and models verified; no GPU inference or paid request was submitted.'}
    atomic_json(base / '.openposegen-install' / 'last-install.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print('安装失败：' + str(error), file=sys.stderr)
        sys.exit(1)
