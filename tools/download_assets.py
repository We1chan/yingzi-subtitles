"""Download pinned local assets, with restartable transfers and SHA-256 checks."""
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / 'models'
RUNTIME = ROOT / 'runtime'


def request(url, headers=None):
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Yingzi-Setup/1.0', **(headers or {})}), timeout=90)


def json_url(url):
    with request(url) as response:
        return json.load(response)


def download(url, dest, size=None, sha256=None):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and (size is None or dest.stat().st_size == size):
        if not sha256 or digest(dest) == sha256:
            print(f'Already verified: {dest.name}', flush=True)
            return
    partial = dest.with_suffix(dest.suffix + '.part')
    for attempt in range(6):
        try:
            offset = partial.stat().st_size if partial.exists() else 0
            with request(url, {'Range': f'bytes={offset}-'} if offset else {}) as response:
                if response.status != 206:
                    offset = 0
                total = size or (int(response.headers.get('Content-Length', 0)) + offset)
                done = offset
                last = time.monotonic()
                with partial.open('ab' if offset else 'wb') as out:
                    while data := response.read(4 * 1024 * 1024):
                        out.write(data)
                        done += len(data)
                        if time.monotonic() - last > 10:
                            print(f'{dest.name}: {done / 1e6:.0f}/{total / 1e6:.0f} MB', flush=True)
                            last = time.monotonic()
            if size and partial.stat().st_size != size:
                raise ValueError(f'Incomplete download: {dest.name}')
            if sha256 and digest(partial) != sha256:
                partial.unlink()
                raise ValueError(f'SHA-256 mismatch: {dest.name}')
            partial.replace(dest)
            print(f'Complete: {dest.name}', flush=True)
            return
        except Exception as exc:
            print(f'Retry {attempt + 1}: {dest.name}: {exc}', flush=True)
            if attempt == 5:
                raise
            time.sleep(min(2 ** attempt, 20))


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        while block := source.read(8 * 1024 * 1024):
            value.update(block)
    return value.hexdigest()


def snapshot(repo, dirname):
    target = MODELS / dirname
    if (target / '.complete').exists():
        revision = json.loads((target / '.complete').read_text(encoding='utf-8'))['revision']
    else:
        revision = json_url(f'https://huggingface.co/api/models/{repo}')['sha']
    entries = json_url(f'https://huggingface.co/api/models/{repo}/tree/{revision}?recursive=true&limit=1000')
    wanted = [e for e in entries if e['type'] == 'file' and '/' not in e['path'] and e['path'].endswith(('.json', '.safetensors', '.txt', '.model', '.md'))]
    print(f'Snapshot {repo}@{revision}: {len(wanted)} files', flush=True)
    def fetch(entry):
        download(f"https://huggingface.co/{repo}/resolve/{revision}/{entry['path']}", target / entry['path'], entry.get('size'), entry.get('lfs', {}).get('oid'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(fetch, wanted))
    (target / '.complete').write_text(json.dumps({'repo': repo, 'revision': revision}), encoding='utf-8')


def translation():
    repo = 'unsloth/Qwen3-4B-Instruct-2507-GGUF'
    target = MODELS / 'Qwen3-4B-Instruct-2507-Q4_K_M.gguf'
    marker = target.with_suffix('.gguf.complete')
    revision = json.loads(marker.read_text(encoding='utf-8'))['revision'] if marker.exists() else json_url(f'https://huggingface.co/api/models/{repo}')['sha']
    entries = json_url(f'https://huggingface.co/api/models/{repo}/tree/{revision}?limit=1000')
    entry = next(e for e in entries if e['path'].endswith('Q4_K_M.gguf'))
    download(f"https://huggingface.co/{repo}/resolve/{revision}/{entry['path']}", target, entry['size'], entry.get('lfs', {}).get('oid'))
    target.with_suffix('.gguf.complete').write_text(json.dumps({'repo': repo, 'revision': revision}), encoding='utf-8')


def runtime():
    for name, checksum, folder in [
        ('llama-b10809-bin-win-cuda-12.4-x64.zip', 'c77bfcd9ed8d91e8721a2d6a290b907fddd4fa5412a47b21c6fa1709116b85f9', 'llama-cuda'),
        ('cudart-llama-bin-win-cuda-12.4-x64.zip', '8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6', 'llama-cuda'),
    ]:
        install_runtime_archive(name, checksum, folder)
    (RUNTIME / 'release.json').write_text(json.dumps({'tag': 'b10809', 'backend': 'CUDA 12.4'}), encoding='utf-8')


def install_runtime_archive(name, expected, folder):
    # Known-compatible release; checksums match GitHub's published asset digests.
    tag = 'b10809'
    url = f'https://github.com/ggml-org/llama.cpp/releases/download/{tag}/{name}'
    archive = RUNTIME / name
    RUNTIME.mkdir(exist_ok=True)
    if not archive.exists() or digest(archive) != expected:
        # PowerShell honors Windows' system proxy configuration on this host.
        subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'tools' / 'fetch.ps1'), '-Uri', url, '-Destination', str(archive)], check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if digest(archive) != expected:
        raise ValueError('Translation runtime checksum mismatch')
    target = RUNTIME / folder
    target.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as zip_file:
        for item in zip_file.infolist():
            resolved = (target / item.filename).resolve()
            if not resolved.is_relative_to(target.resolve()):
                raise ValueError('Invalid archive path')
            if resolved.is_file() and resolved.stat().st_size == item.file_size:
                crc = 0
                with resolved.open('rb') as existing:
                    while block := existing.read(4 * 1024 * 1024):
                        crc = zlib.crc32(block, crc)
                if crc == item.CRC:
                    continue
            zip_file.extract(item, target)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    tasks = {'asr': lambda: snapshot('Qwen/Qwen3-ASR-1.7B', 'Qwen3-ASR-1.7B'), 'asr-small': lambda: snapshot('Qwen/Qwen3-ASR-0.6B', 'Qwen3-ASR-0.6B'), 'align': lambda: snapshot('Qwen/Qwen3-ForcedAligner-0.6B', 'Qwen3-ForcedAligner-0.6B'), 'translate': translation, 'runtime': runtime}
    selected = sys.argv[1:] or ['asr', 'align', 'translate', 'runtime']
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(tasks[name]): name for name in selected}
        for future in concurrent.futures.as_completed(futures):
            future.result()
            print(f'READY: {futures[future]}', flush=True)
