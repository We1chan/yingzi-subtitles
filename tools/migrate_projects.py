"""Migrate a directory or one legacy project to the sole .yingziproj format."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.project_migration import migrate_projects


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='迁移旧 .yingzi.json 工程，保留全部内容与原缓存。')
    parser.add_argument('path', type=Path, help='旧工程文件或包含工程的文件夹')
    parser.add_argument('--dry-run', action='store_true', help='仅检查，不改名')
    parser.add_argument('--report', type=Path, help='将迁移文件路径、内容摘要及字幕条数记录到 JSON')
    args = parser.parse_args()
    try:
        plan = migrate_projects(args.path, dry_run=args.dry_run)
    except (OSError, ValueError) as exc:
        print(f'迁移失败：{exc}')
        return 1
    if args.report:
        report = [{'source': str(item.source), 'target': str(item.target),
                   'sha256': item.sha256, 'cue_count': item.cue_count} for item in plan]
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({'dry_run': args.dry_run, 'projects': report}, ensure_ascii=False, indent=2), encoding='utf-8')
    verb = '检查通过，可迁移' if args.dry_run else '已迁移'
    print(f'{verb} {len(plan)} 个工程 · 共 {sum(item.cue_count for item in plan)} 条字幕 · 文件内容与原缓存保持不变')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
