# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

映字 (Yingzi): a Windows-only PySide6 desktop app that turns a video into bilingual subtitles. It runs local speech recognition (Chinese/Japanese/English), aligns word timestamps, translates Japanese/English into Simplified Chinese (local llama.cpp GGUF or an OpenAI-compatible online API), and lets the user edit, style and export subtitles (SRT/VTT or burned-in MP4). The README and all user-facing strings (UI text, error messages raised as `ValueError`) are in Simplified Chinese; keep new user-facing text in Chinese.

## Commands

Everything runs on the project venv (Python 3.12). From PowerShell:

```powershell
.\.venv\Scripts\python.exe main.py                       # launch the GUI
.\.venv\Scripts\python.exe -m pytest -q                  # full suite (~245 tests, 1–1.5 min, no models needed)
.\.venv\Scripts\python.exe -m pytest -q tests/test_pipeline.py::test_name   # single test
.\.venv\Scripts\python.exe tools\preview_ui.py --demo --stage review --size 1100x760 --output logs\ui.png   # render UI offscreen to a PNG (--stage generate|review|export, --models for the model dialog)
.\.venv\Scripts\python.exe tools\preview_projects.py --demo --size 1100x760 --output logs\projects.png
```

Real-model checks (need downloaded models; fetch test audio into the gitignored `tests/fixtures/` and write to `projects/`):

```powershell
.\.venv\Scripts\python.exe tools\run_validation.py [--languages english] [--asr-model models/Qwen3-ASR-0.6B] [--device cpu]
.\.venv\Scripts\python.exe tools\smoke_ui_flow.py [path\to\subtitles.yingziproj] [--generate]   # --generate runs the full ASR pipeline through the GUI QProcess
.\.venv\Scripts\python.exe tools\smoke_video_export.py
```

Setup/assets: `setup.cmd` → `tools/setup.ps1` (installs torch cu128, then `requirements-lock.txt`, then downloads assets). `tools\download_assets.py [asr|asr-small|align|translate|runtime|cohere|parakeet|extra-runtime]` downloads pinned, SHA-256-checked, resumable assets. There is no linter or formatter config.

## Two Python environments

- `.venv`: main app plus Qwen3-ASR / Qwen3-ForcedAligner (`qwen-asr`, **Transformers 4**).
- `runtime/asr-env`: Cohere Transcribe and Parakeet ONNX (**Transformers 5**, onnxruntime-gpu pinned to CUDA 12). It's built by `tools/setup_extra.py` from `requirements-extra-lock.txt` and marked ready by `runtime/asr-env/.ready`.

Never install the extras into `.venv`, because that would upgrade Transformers and break Qwen. `app.external_asr` is the only module that runs inside `asr-env`.

## Process architecture

The GUI never runs inference in-process. Every job is a child process that talks back over **JSON lines on stdout**:

1. `Studio` builds the job and hands it to `app/job_runner.JobRunner` (`Studio.jobs`), which writes `job.json` into the job's output folder and starts `QProcess`: `.venv\Scripts\python.exe -u -m app.worker --job job.json`. The runner splits stdout into JSON lines, appends the child's stderr to `run.log`, remembers whether a terminal event (`done`, `error`, `export_done`, `export_cancelled`) arrived, and emits `event`, `ended(unexpected)` and `failedToStart`. `Studio.handle_event` dispatches on `type`: `progress`, `checkpoint`, `translation_update`, `api_diagnostic`, `done`, `error` (other types, such as `backend`, are ignored). Tests swap the child program by patching `app.job_runner.QProcess`.
2. `app/worker.py` wraps `process()` in `contextlib.redirect_stdout(sys.stderr)` so library prints can't corrupt the event stream; events go through `worker.event()` → `EVENT_STREAM`. Keep that separation when adding output.
3. For Cohere/Parakeet, `app/asr_process.py` spawns `runtime/asr-env` Python with `-m app.external_asr`, which uses the same JSON-line protocol (`backend`, `record`, `done`, `error`). Cleanup uses `taskkill /T /F`, so cancelling kills the whole tree.
4. Video export is a separate child: `-m app.video_export --job <work>/job.json`. It runs Qt offscreen, emits `export_done` / `export_cancelled` (handled by the same `handle_event`), logs to `video-export.log`, and is cancelled by creating a `cancel` file in its work dir. `JobRunner.start_export` creates that work dir beside the target and removes it (keeping `ffmpeg.log` in `video-export.log`) when the child ends.

## Recognition pipeline and resume cache

`app/pipeline.py:run_pipeline` works in stages: extract audio → Silero VAD into ≤24 s spans → ASR per span → forced alignment per span → `build_cues` (segmentation in `subtitles.aligned_cues` / `segmentation.smart_endpoints`). After every span it persists state to `<output>/.pipeline/state.json` plus `audio.wav`, and it loads one model at a time and releases it (`worker.release`) to save VRAM.

- A `fingerprint` (video path/size/mtime, track, language, context, model paths) guards the cache. A mismatch is an error, never a silent re-run.
- `resegment_only` rebuilds cues from cached alignments without touching any model. It refuses to run if the source text was hand-edited.
- **`run_pipeline` gets its dependencies through `hooks`, which is the `app.worker` module itself** (`h.extract_audio`, `h.speech_chunks`, `h.read_span`, `h.load_options`, `h.event`, `h.model_issue`, …). Tests stub stages by monkeypatching attributes on `app.worker`. To add a new pipeline dependency, import it into `worker.py` and call it via `h.` so it stays mockable.

`config.asr_backend(path)` chooses `qwen` / `cohere` / `parakeet` from `config.json`'s `model_type` or the folder name. Every backend's timestamps come from Qwen3-ForcedAligner.

## Project file and state

`gui.main` starts on `ProjectHome` (a card library with search, sorting, filters and trash), then uses `Studio.screen_stack` to switch to the existing editor. `Studio` (`gui.py`, the largest module) owns editor behaviour, while `studio_layout.build_studio` constructs the editor widgets onto it, `JobRunner` owns child processes and `ProjectSession` owns the open project's state (both below); dialogs, the style panel, waveform and undo/merge/split helpers (`editing.py`) live in their own modules. `ProjectLibrary` owns `projects/<name>_<id>/`: the named `.yingziproj`, thumbnail/sidecar metadata, `work/` caches and default `exports/`. A blank draft has an empty media path and no cues; attaching media must keep that named project. GUI tests isolate `gui.PROJECTS` through `tests/conftest.py` so they never pollute the user's library.

External project opens use `ProjectLibrary.adopt` to copy content and recovery caches into an independent folder; matching unchanged imports reuse the edited library copy. Save As clones into another library folder with independent caches. Trash moves the whole owned folder into `projects/.trash/`, rebases internal media/workspace/cache paths and refuses shared resource removal; active autosave and media handles must be released. `tools/organize_projects.py` collects old `output/` folders with a validated transaction and exact rollback of changed JSON. Always use `project_workspace`, not the project parent, for processing caches.

`.yingziproj` and default `subtitles.yingziproj` (`subtitles.save_project/load_project`, `version: 1`) hold `video`, `options`, `status`, and `cues` (the `Cue` dataclass: `start, end, text, translation, language, translation_error`). Optional `workspace` locates processing output and `.pipeline/`, while `editor` stores the playhead, selected rows, step, preview state, filters and last export target. Relative media/workspace paths support moving a bundle. Use `subtitles.project_workspace` rather than assuming the project lives next to its cache. GUI import immediately creates a draft project; generation and worker checkpoints retain its chosen filename through `job.project_path`. The worker strips `project_path` and `editor_state` from saved processing options.

`Studio.session` (`app/project_session.ProjectSession`) holds the open project file, media, workspace (`output_dir`) and status. `can_save()` is the single autosave rule: no project, `loading`, `closed` or a running worker (`worker_owns`) blocks UI writes, and `save()` turns a finished status into `edited` while keeping resumable ones. Reload worker checkpoints through `session.checkpoint` / `session.recover`. GUI tests patch `app.project_session.OUTPUT` so the default workspace never points at the real `output/`. `tools/register_project.ps1` registers the current-user file association; `gui.main` forwards subsequent opens to the running app over a local socket.

`status` drives what "继续处理/继续翻译" does: `recognizing` / `aligning` → rerun the pipeline from cache; `translating` / `translation_partial` → translate only the cues where `needs_translation()` is true (Japanese/English with an empty or failed translation). The editor requires validated, non-overlapping cues. Write files through `subtitles.atomic_write` (tmp + replace; `.srt` gets a UTF-8 BOM).

Old `.yingzi.json` projects are only read by `app.project_migration` during explicit migration (`tools\migrate_projects.py <file-or-dir> [--dry-run]`). Application loading and saving require `.yingziproj`. The migration tool validates all candidates before renaming files within the selected root, refuses conflicts and symlinks, and verifies identical bytes after each rename.

## Translation

- `translation.translation_payload` builds the one shared prompt/JSON-schema for both backends. `validate_translation_response` / `parse_translations` enforce that each id appears exactly once with real text.
- `LocalTranslator` starts `runtime/llama-cuda/llama-server.exe` on a random `127.0.0.1` port with a per-run random key, bypassing proxies. It still sends one cue per request (so a clause cannot drift into another id), but on the GPU it runs `GPU_SLOTS` server slots (each keeps `SLOT_CONTEXT` tokens) and sends the cues of a batch together, since their prompts only read source text. If the multi-slot server cannot start, for example because the extra KV cache does not fit, it restarts with one slot; CPU mode always uses one. `OnlineTranslator` subclasses it (no-op `start`/`close`) and posts to Chat Completions with `NoRedirect`. It uses the system proxy, except for loopback endpoints (`api_settings.is_loopback`): with `HTTP(S)_PROXY` set and no `NO_PROXY`, urllib ignores the Windows registry bypass list, which would send local services and the tests' fake server through the proxy.
- Failure policy in `OnlineTranslator.translate_batches`: invalid output → split the batch in halves recursively down to single cues, then record a per-cue `translation_error` and continue. Network errors retry the *same* batch (up to 3 attempts, 1 s/2 s backoff) and never split. Auth and certificate errors abort the queue. Tests assert all of this.
- `online_queue.translate_groups` runs batches (from `online_translation.translation_batches`) on a thread pool with one shared `RequestGate` rate limiter. Results are handed back to the worker thread, which checkpoints the project after every batch.

## Secrets and diagnostics

The API key is DPAPI-encrypted in the gitignored `settings.json` (`api_settings.protect`) and bound to the base URL. It must never appear in the project file, `job.json`, `run.log` or `api-diagnostics.jsonl`. Diagnostics may record ids, timing, token counts and error categories, but not prompts, subtitle text, reasoning text or raw exception strings (these can contain proxy credentials). Several tests check for leaks.

## Rendering parity

`caption_style.render_caption` / `render_overlay` (Qt text layout) produce the caption pixels for both the live preview (`gui.VideoView`) and video export (`video_export.build_overlays` → PNG overlays → FFmpeg from `imageio-ffmpeg`). Sizes scale from a 720 px reference height. Change caption drawing only in this shared path. `build_overlays` lays out text on the calling thread (it does not scale across threads), while `place_caption` compositing and PNG saving, which release the GIL and cost most of the time, run on a bounded thread pool. `encoders.select_encoder` picks H.264/HEVC NVENC when a probe encode succeeds; if NVENC is unavailable or the encode fails, `video_export` retries with the same codec on CPU (`libx264`/`libx265`), keeping bitrate and audio settings. Qt-free bitrate/audio planning and size estimates live in `export_settings.py`. The export target is replaced only after a successful encode.

## Conventions

- `app/config.py` has import-time side effects: it creates `models/ .cache/ output/ runtime/ logs/` and sets `HF_HUB_OFFLINE=1`, `HF_HOME`, etc. Inference must stay offline; only `tools/` download scripts go online.
- Model paths in settings and jobs resolve relative to the repo root (`config.model_paths`).
- Qt tests set `os.environ['QT_QPA_PLATFORM'] = 'offscreen'` before importing PySide6 and share a module-scoped `QApplication` fixture. Online-translation tests use a local fake HTTP server (`api_server` in `tests/test_online_translation.py`), never a real API.
- Settings controls use the wrappers in `app/widgets.py` (often imported as `QComboBox`, `QSpinBox`, …) so the mouse wheel scrolls the page and never changes a value. Use them for new combo boxes, spin boxes and sliders. Shared colours and QSS live in `theme.py`.
- Code style: single quotes, compact functions, few comments; comments explain *why*.
- `.gitattributes` forces LF for `.py/.md/.json/.txt` and CRLF for `.ps1/.cmd/.vbs`.
- `VALIDATION.md` is a dated log of verification runs (test counts, real-model trials, screenshots under `logs/`). `models/`, `runtime/`, `output/`, `projects/`, `logs/`, `.cache/` and `settings.json` are gitignored and hold local-only artifacts.
