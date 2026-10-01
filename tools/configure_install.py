"""Persist first-run installation choices without replacing existing API preferences or secrets."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def configure(path, profile, device):
    if profile not in {'original', 'online', 'local'} or device not in {'cpu', 'cuda'}:
        raise ValueError('安装方式或设备无效。')
    path = Path(path)
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError('本机设置格式无效，已保留原文件。')
    data['installation_profile'] = profile
    data['installation_device'] = device
    data.setdefault('translation_backend', 'online' if profile == 'online' else 'local')
    from app.subtitles import atomic_write
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', required=True, choices=('original', 'online', 'local'))
    parser.add_argument('--device', required=True, choices=('cpu', 'cuda'))
    args = parser.parse_args()
    configure(ROOT / 'settings.json', args.profile, args.device)
