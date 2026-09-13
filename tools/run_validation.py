"""Run both actual model pipelines and capture unbuffered per-job diagnostics."""
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
    fixtures = ROOT / 'tests/fixtures'
    fixtures.mkdir(parents=True, exist_ok=True)
    sources = {
        'chinese.wav': 'https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen3-ASR-Repo/asr_zh.wav',
        'japanese.mp3': 'https://huggingface.co/kotoba-tech/kotoba-whisper-v2.2/resolve/main/sample_audio/sample_diarization_japanese.mp3',
    }
    for name, url in sources.items():
        destination = fixtures / name
        if not destination.exists() or destination.stat().st_size == 0:
            partial = destination.with_suffix(destination.suffix + '.part')
            subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'tools/fetch.ps1'), '-Uri', url, '-Destination', str(partial)], check=True)
            partial.replace(destination)
    run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'color=c=0x142a35:s=1280x720:r=24', '-i', ROOT / 'tests/fixtures/chinese.wav', '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', '-shortest', ROOT / 'tests/fixtures/chinese-test.mp4'])
    run_ffmpeg(['-y', '-f', 'lavfi', '-i', 'color=c=0x142a35:s=1280x720:r=24', '-i', fixtures / 'japanese.mp3', '-t', '16', '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', '-shortest', fixtures / 'japanese-test.mp4'])
    for name, language, bilingual in [('chinese', 'Chinese', False), ('japanese', 'Japanese', True)]:
        out = ROOT / 'output' / ('验证_中文' if name == 'chinese' else '验证_日中双语')
        job = {'video': str(ROOT / f'tests/fixtures/{name}-test.mp4'), 'output': str(out), 'language': language, 'bilingual': bilingual, 'track': 0, 'device': 'auto', 'max_chars': 30}
        path = ROOT / f'tests/fixtures/job-{name}.json'
        path.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
        with (ROOT / f'logs/{name}-events.jsonl').open('wb', buffering=0) as events, (ROOT / f'logs/{name}-error.log').open('wb', buffering=0) as errors:
            print(f'Starting actual {name} model pipeline', flush=True)
            subprocess.run([sys.executable, '-u', '-m', 'app.worker', '--job', str(path)], cwd=ROOT, stdout=events, stderr=errors, check=True)
        data, cues = load_project(out / 'subtitles.yingzi.json')
        validate(cues)
        assert data['status'] == 'complete'
        assert len(cues) > 0
        assert all(cue.translation for cue in cues) if bilingual else True
        assert all((out / filename).exists() for filename in ('original.srt', 'translated.srt', 'bilingual.srt', 'bilingual.vtt'))
        print(f'PASS {name}: {len(cues)} aligned cues; bilingual={bilingual}', flush=True)
        print((out / 'bilingual.srt').read_text(encoding='utf-8-sig'), flush=True)


if __name__ == '__main__':
    main()
