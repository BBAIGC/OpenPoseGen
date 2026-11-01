#!/usr/bin/env python3
"""Install BBAIGC workflows while preserving user files and the installed PyTorch stack."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
REPOS = {'h3': 'ComfyUI-BBAIGC-MiniMax-H3', 'image': 'ComfyUI-BBAIGC-Image-Studio', 'bbapi': 'ComfyUI-BBAIGC-BBAPI'}
PROTECTED = ('torch', 'torchvision', 'torchaudio')


def run(args, capture=False):
    result = subprocess.run([str(v) for v in args], check=True, text=True, encoding='utf-8',
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else result


def git(target, *args):
    return run(['git', '-C', target, *args], True)


def origin(url):
    return url.strip().removesuffix('.git').rstrip('/').replace('git@github.com:', 'https://github.com/').lower()


def remote_default(url):
    for line in run(['git', 'ls-remote', '--symref', url, 'HEAD'], True).splitlines():
        if line.startswith('ref: refs/heads/') and line.endswith('\tHEAD'):
            return line.split('\t', 1)[0].removeprefix('ref: refs/heads/')
    raise RuntimeError(f'无法确定仓库默认分支：{url}')


def check_clean(target):
    if git(target, 'status', '--porcelain', '--untracked-files=all'):
        raise RuntimeError(f'{target} 有本地修改或未跟踪文件，安装器不会覆盖。')


def checkout(url, target):
    target = target.resolve()
    if target.exists():
        if not (target / '.git').exists():
            raise RuntimeError(f'{target} 已存在且不是 Git 安装；请按安装说明迁移，避免覆盖或重复节点。')
        if origin(git(target, 'remote', 'get-url', 'origin')) != origin(url):
            raise RuntimeError(f'{target} 指向其他仓库，停止更新。')
        check_clean(target)
        branch = remote_default(url)
        if git(target, 'branch', '--show-current') != branch:
            raise RuntimeError(f'{target} 不在默认分支 {branch}，不会自动切换用户分支。')
        run(['git', '-C', target, 'fetch', 'origin', branch])
        check = subprocess.run(['git', '-C', str(target), 'merge-base', '--is-ancestor', 'HEAD', f'origin/{branch}'])
        if check.returncode:
            raise RuntimeError(f'{target} 有未推送或分叉提交，已保留，不会重置。')
        run(['git', '-C', target, 'merge', '--ff-only', f'origin/{branch}'])
        return target
    # Keep partial clones outside custom_nodes, so ComfyUI cannot import them.
    staging_root = target.parent.parent / '.bbaigc-install'
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = staging_root / target.name
    if staging.exists():
        if not (staging / '.git').exists() or origin(git(staging, 'remote', 'get-url', 'origin')) != origin(url):
            raise RuntimeError(f'暂存目录需检查，已保留：{staging}')
        has_head = subprocess.run(['git', '-C', str(staging), 'rev-parse', '--verify', 'HEAD'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if has_head:
            check_clean(staging)
        else:
            if any(p.name != '.git' for p in staging.iterdir()):
                raise RuntimeError(f'不完整暂存目录含文件，未覆盖：{staging}')
            branch = remote_default(url)
            run(['git', '-C', staging, 'fetch', '--depth', '1', 'origin', branch])
            run(['git', '-C', staging, 'checkout', '-b', branch, '--track', f'origin/{branch}'])
    else:
        run(['git', 'clone', '--depth', '1', url, staging])
    if not staging.resolve().is_relative_to(staging_root.resolve()):
        raise RuntimeError('暂存路径越界。')
    target.parent.mkdir(parents=True, exist_ok=True)
    staging.rename(target)
    return target


def existing_package(custom, url, names, markers=()):
    matches = []
    for directory in custom.iterdir() if custom.exists() else ():
        if not directory.is_dir() or directory.name.startswith('.'):
            continue
        found = directory.name.casefold() in {name.casefold() for name in names}
        if (directory / '.git').exists():
            try:
                found = found or origin(git(directory, 'remote', 'get-url', 'origin')) == origin(url)
            except subprocess.CalledProcessError:
                pass
        entry = directory / '__init__.py'
        if not found and markers and entry.is_file():
            text = entry.read_text(encoding='utf-8', errors='replace')
            found = any(marker in text for marker in markers)
        if found:
            matches.append(directory)
    if len(matches) > 1:
        raise RuntimeError('发现重复节点安装，请先整理为一份：' + ', '.join(map(str, matches)))
    return matches[0] if matches else custom / names[0]


def environment_info(python):
    code = "import sys,json,importlib.metadata as m; d={};\nfor n in ['torch','torchvision','torchaudio']:\n try:d[n]=m.version(n)\n except m.PackageNotFoundError:pass\nprint(json.dumps({'executable':sys.executable,'prefix':sys.prefix,'version':list(sys.version_info[:3]),'packages':d}))"
    return json.loads(run([python, '-c', code], True))


def protected_pip(python, specs, snapshot):
    """Resolve first, reject every planned torch-stack install, then keep exact constraints."""
    with tempfile.TemporaryDirectory(prefix='bbaigc-pip-') as temp:
        folder = Path(temp)
        constraints, report = folder / 'torch.txt', folder / 'plan.json'
        constraints.write_text('\n'.join(f'{n}==={v}' for n, v in snapshot.items()) + '\n', encoding='utf-8')
        command = [python, '-m', 'pip', 'install', '--upgrade-strategy', 'only-if-needed', '-c', constraints, *specs]
        try:
            run([*command, '--dry-run', '--report', report])
        except subprocess.CalledProcessError as exc:
            raise RuntimeError('依赖预检失败，未开始该批 pip 安装。请解决冲突或使用支持 --dry-run 的 pip；不会自动替换 PyTorch。') from exc
        plan = json.loads(report.read_text(encoding='utf-8'))
        forbidden = [item.get('metadata', {}).get('name', '').lower().replace('_', '-')
                     for item in plan.get('install', [])
                     if item.get('metadata', {}).get('name', '').lower().replace('_', '-') in PROTECTED]
        if forbidden:
            raise RuntimeError('依赖计划会安装或替换 ' + ', '.join(forbidden) + '，已拒绝。')
        run(command)
    if environment_info(python)['packages'] != snapshot:
        raise RuntimeError('安装后 PyTorch 组件版本变化，请检查环境；不会继续下载模型。')


def atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.{time.time_ns()}.tmp')
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def install_workflow(payload, target_dir, identity, version):
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f'{identity}.json'
    if target.exists() and target.read_bytes() != payload:
        target = target_dir / f'{identity}-V{version}.json'
        if target.exists() and target.read_bytes() != payload:
            target = target_dir / f'{identity}-V{version}-{time.time_ns()}.json'
    if not target.exists():
        atomic_write(target, payload)
    return target


def inside(root, relative):
    result = (root / relative).resolve()
    if not result.is_relative_to(root.resolve()):
        raise RuntimeError('清单含越界路径。')
    return result


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


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
        backup = backup_root / 'loader.py'
        backup.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, backup)
        path.write_text(text, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comfy-root', required=True, type=Path, help='含 folder_paths.py 的核心目录')
    parser.add_argument('--base-dir', type=Path, help='Desktop 实例数据目录，与核心目录分开')
    parser.add_argument('--python', help='该 ComfyUI 的 Python；优先 base/.venv')
    parser.add_argument('--models-dir', type=Path)
    parser.add_argument('--custom-nodes-dir', type=Path)
    parser.add_argument('--user-dir', type=Path, help='启动参数 --user-directory 对应目录')
    parser.add_argument('--workflow-dir', type=Path)
    parser.add_argument('--download-models', choices=['none', 'speech', 'qwen-turbo', 'krea', 'h3'], default='none')
    parser.add_argument('--with-speech', action='store_true', help='额外下载完整 CPU 语音模型')
    parser.add_argument('--skip-pip', action='store_true', help='已有依赖时跳过 pip')
    args = parser.parse_args()
    root = args.comfy_root.resolve()
    if not (root / 'folder_paths.py').is_file():
        parser.error('--comfy-root 必须指向真正的 ComfyUI 核心目录')
    base = (args.base_dir or root).resolve()
    candidates = [base / '.venv/Scripts/python.exe', base / '.venv/bin/python', root / '.venv/Scripts/python.exe',
                  root / '.venv/bin/python', root.parent / 'python_embeded/python.exe']
    python = args.python or next((str(p) for p in candidates if p.is_file()), sys.executable)
    env = environment_info(python)
    python = env['executable']
    if env['version'] < [3, 10, 0] or 'torch' not in env['packages']:
        parser.error('请用 --python 指定已有 ComfyUI 的 Python 3.10+ / PyTorch 环境；不会另装 GPU 栈。')
    if not args.base_dir and 'comfyui-installs' in str(root).lower():
        prefix = Path(env['prefix']).resolve()
        if prefix.name == '.venv' and prefix.parent != root:
            base = prefix.parent
        else:
            parser.error('检测到 Desktop 核心，请用 --base-dir 指定实际实例数据目录。')
    custom = (args.custom_nodes_dir or base / 'custom_nodes').resolve()
    users = (args.user_dir or base / 'user').resolve()
    models = (args.models_dir or base / 'models').resolve()
    target_dir = (args.workflow_dir or users / 'default/workflows/BBAIGC').resolve()
    manifest = json.loads((HERE / 'release.json').read_text(encoding='utf-8'))
    identity, version = manifest['id'], manifest['version']
    if identity not in REPOS or not version or any(c not in '0123456789.' for c in version):
        raise RuntimeError('不支持的工作流类型或版本。')
    payload = inside(HERE, manifest['workflow']).read_bytes()
    if hashlib.sha256(payload).hexdigest() != manifest['sha256']:
        raise RuntimeError('工作流 SHA256 与清单不符，未修改安装。')
    json.loads(payload)
    print(f'核心：{root}\n数据：{base}\nPython：{python}\n节点：{custom}\n用户：{users}\n模型：{models}')
    custom.mkdir(parents=True, exist_ok=True)
    url = 'https://github.com/BBAIGC/' + REPOS['h3']
    packages = [checkout(url, existing_package(custom, url, (REPOS['h3'], 'ComfyUI-H3-PromptDirector'), ('from .studio_nodes import',)))]
    if identity in ('image', 'h3'):
        url = 'https://github.com/city96/ComfyUI-GGUF'
        gguf_path = checkout(url, existing_package(custom, url, ('ComfyUI-GGUF',)))
        patch_gguf(gguf_path, base / '.bbaigc-install' / 'gguf-compat-backup')
        packages.append(gguf_path)
    if identity in ('image', 'bbapi'):
        url = 'https://github.com/BBAIGC/' + REPOS['bbapi']
        packages.append(checkout(url, existing_package(custom, url, (REPOS['bbapi'], 'ComfyUI-BBAPI-Private'))))
        url = 'https://github.com/rgthree/rgthree-comfy'
        packages.append(checkout(url, existing_package(custom, url, ('rgthree-comfy',))))
    if identity == 'h3':
        # Existing minimal FFN package already registers the same node as KJNodes.
        minimal = custom / 'ComfyUI-MiniMaxH3-LowVRAM'
        minimal_entry = minimal / '__init__.py'
        if minimal_entry.is_file() and 'MiniMaxChunkFeedForward' in minimal_entry.read_text(encoding='utf-8', errors='replace'):
            print('复用现有 MiniMaxChunkFeedForward：', minimal)
        else:
            url = 'https://github.com/kijai/ComfyUI-KJNodes'
            packages.append(checkout(url, existing_package(custom, url, ('ComfyUI-KJNodes',))))
    if not args.skip_pip:
        requirements = [arg for package in packages if (package / 'requirements.txt').is_file()
                        for arg in ('-r', package / 'requirements.txt')]
        if requirements:
            protected_pip(python, requirements, env['packages'])
    target = install_workflow(payload, target_dir, identity, version)
    presets = json.loads((HERE / 'model-presets.json').read_text(encoding='utf-8'))
    selected = [] if args.download_models == 'none' else list(presets[args.download_models])
    if args.with_speech and args.download_models != 'speech':
        selected.extend(presets['speech'])
    if selected:
        pending = []
        for item in selected:
            destination = inside(models, item['dest'])
            if destination.exists():
                if not destination.is_file() or destination.stat().st_size != item['bytes']:
                    raise RuntimeError(f'同名模型体积不符，已保留，未覆盖：{destination}')
                print('复用已有模型：', destination)
            else:
                pending.append(item)
        models.mkdir(parents=True, exist_ok=True)
        needed = sum(item['bytes'] for item in pending)
        if pending and shutil.disk_usage(models).free < needed * 1.05 + 512 * 1024 * 1024:
            raise RuntimeError(f'模型空间不足，待下载约 {needed / 1e9:.2f} GB，已完成步骤保留。')
        if pending:
            has_hub = run([python, '-c', "import importlib.util;print(int(importlib.util.find_spec('huggingface_hub') is not None))"], True) == '1'
            if not has_hub:
                if args.skip_pip:
                    raise RuntimeError('下载需 huggingface_hub，但已设置 --skip-pip，请先在目标环境安装。')
                protected_pip(python, ['huggingface_hub'], env['packages'])
            helper = 'from huggingface_hub import hf_hub_download;import sys;hf_hub_download(repo_id=sys.argv[1],filename=sys.argv[2],local_dir=sys.argv[3])'
            for item in pending:
                staging = models / '.bbaigc-download' / item['repo'].replace('/', '--')
                run([python, '-c', helper, item['repo'], item['file'], staging])
                downloaded = inside(staging, item['file'])
                if downloaded.stat().st_size != item['bytes']:
                    raise RuntimeError('模型体积校验失败，暂存保留：' + str(downloaded))
                if item.get('sha256') and file_sha256(downloaded) != item['sha256']:
                    raise RuntimeError('模型 SHA256 校验失败，暂存保留：' + str(downloaded))
                destination = inside(models, item['dest'])
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    raise RuntimeError('下载期间出现同名文件，未覆盖：' + str(destination))
                downloaded.rename(destination)
                print('模型已安装：', destination)
    state_path = users / 'default/bbaigc/installed_versions.json'
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    state[identity] = version
    atomic_write(state_path, json.dumps(state, ensure_ascii=False, indent=2).encode('utf-8'))
    print('安装完成。工作流：', target)
    print('原有工作流与密钥保持不变；已有同名文件时公开版另存带版本号的新文件。')
    print('请重启 ComfyUI。Ollama 与可选插件按 MODELS.md 安装；未指定时不下载模型。')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print('安装未全部完成：', error, file=sys.stderr)
        print('已完成文件与下载暂存均已保留，修正阻塞后重试即可。', file=sys.stderr)
        raise SystemExit(1)
