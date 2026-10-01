"""Run actual model pipelines and capture unbuffered per-job diagnostics."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.media import run_ffmpeg
from app.subtitles import load_project, validate


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--languages', nargs='+', choices=['chinese', 'japanese', 'english'], default=['chinese', 'japanese', 'english'])
    parser.add_argument('--asr-model', help='Optional local ASR model directory')
    parser.add_argument('--seconds', type=int, default=16, help='Test video length')
    parser.add_argument('--device', choices=['auto', 'cpu'], default='auto')
    args = parser.parse_args()
    fixtures = ROOT / 'tests/fixtures'
    fixtures.mkdir(parents=True, exist_ok=True)
    sources = {
        'chinese.wav': 'https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen3-ASR-Repo/asr_zh.wav',
        'japanese.mp3': 'https://huggingface.co/kotoba-tech/kotoba-whisper-v2.2/resolve/main/sample_audio/sample_diarization_japanese.mp3',
        'english.wav': 'https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen3-ASR-Repo/asr_en.wav',
    }
    for name, url in sources.items():
        if Path(name).stem not in args.languages:
            continue
        destination = fixtures / name
        if not destination.exists() or destination.stat().st_size == 0:
            partial = destination.with_suffix(destination.suffix + '.part')
            subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'tools/fetch.ps1'), '-Uri', url, '-Destination', str(partial)], check=True)
            partial.replace(destination)
    for name, language, bilingual in [('chinese', 'Chinese', False), ('japanese', 'Japanese', True), ('english', 'English', True)]:
        if name not in args.languages:
            continue
        source = fixtures / (name + ('.mp3' if name == 'japanese' else '.wav'))
        tag = name + ('-' + Path(args.asr_model).name if args.asr_model else '') + ('-cpu' if args.device == 'cpu' else '')
        video = fixtures / f'{tag}-{args.seconds}s-test.mp4'
        run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'color=c=0x142a35:s=1280x720:r=24', '-i', source, '-t', str(args.seconds), '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', '-shortest', video])
        folder = {'chinese': '验证_中文', 'japanese': '验证_日中双语', 'english': '验证_英中双语'}[name]
        out = ROOT / 'output' / (folder + ('_' + Path(args.asr_model).name if args.asr_model else ''))
        if args.device == 'cpu':
            out = out.with_name(out.name + '_CPU')
        job = {'video': str(video), 'output': str(out), 'language': language, 'bilingual': bilingual, 'track': 0, 'device': args.device, 'max_chars': 68 if language == 'English' else 30}
        if args.asr_model:
            job['asr_model'] = str(Path(args.asr_model).resolve())
        path = ROOT / f'tests/fixtures/job-{name}.json'
        path.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
        with (ROOT / f'logs/{tag}-events.jsonl').open('wb', buffering=0) as events, (ROOT / f'logs/{tag}-error.log').open('wb', buffering=0) as errors:
            print(f'Starting actual {name} model pipeline', flush=True)
            subprocess.run([sys.executable, '-u', '-m', 'app.worker', '--job', str(path)], cwd=ROOT, stdout=events, stderr=errors, check=True)
        data, cues = load_project(out / 'subtitles.yingziproj')
        validate(cues)
        assert data['status'] == 'complete'
        assert len(cues) > 0
        assert all(cue.language == language for cue in cues)
        assert all(cue.translation for cue in cues) if bilingual else True
        assert all(any(char.isalnum() for char in cue.translation) for cue in cues) if bilingual else True
        assert all((out / filename).exists() for filename in ('original.srt', 'translated.srt', 'bilingual.srt', 'bilingual.vtt'))
        print(f'PASS {name}: {len(cues)} aligned cues; bilingual={bilingual}', flush=True)
        print((out / 'bilingual.srt').read_text(encoding='utf-8-sig'), flush=True)


if __name__ == '__main__':
    main()
