"""Collect existing output projects into the managed local engineering library."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import OUTPUT, PROJECTS
from app.project_library import ProjectLibrary


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=OUTPUT)
    parser.add_argument('--destination', type=Path, default=PROJECTS)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    library = ProjectLibrary(args.destination)
    try:
        paths = library.organize(args.source)
    except (OSError, ValueError) as exc:
        print(f'收纳失败：{exc}')
        return 1
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({'projects': [str(path) for path in paths]}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'已收纳 {len(paths)} 个工程到 {library.root}，字幕、缓存与现有导出文件一同保留。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
