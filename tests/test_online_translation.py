import contextlib
import http.client
import json
import os
import socket
import ssl
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import pytest
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtCore import QEventLoop, QTimer

from app import config, worker
from app.api_settings import (DEFAULT_API, completion_url, save_api_settings, load_api_key,
    load_api_settings, clear_api_key, load_translation_backend, normalize_api)
from app.api_dialog import ApiDialog
from app.online_translation import OnlineTranslator, translation_batches
from app.subtitles import Cue, save_project, load_project
from app.gui import Studio, initialize_fonts, STYLE


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'SETTINGS', tmp_path / 'settings.json')
    return config.SETTINGS


@pytest.fixture(scope='module')
def application():
    app = QApplication.instance() or QApplication([])
    initialize_fonts(app)
    app.setStyleSheet(STYLE)
    return app


@contextlib.contextmanager
def api_server(respond=None):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, self.headers.get('Authorization'), data))
            rows = json.loads(data['messages'][1]['content'])['subtitles']
            answer = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(
                {'translations': [{'id': row['id'], 'text': '你好。'} for row in rows]})}}]}
            result = respond(requests, answer) if respond else (200, answer, {})
            if result is None:
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                return
            status, body, headers = result
            body = json.dumps(body).encode('utf-8')
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    try:
        yield {**DEFAULT_API, 'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'model': 'test-model',
               'concurrency': 1, 'requests_per_minute': 0}, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_encrypted_keys_bound_to_endpoint_and_preferences_preserved(settings):
    settings.write_text('{"theme":"orange","translation_model":"test.gguf"}')
    options = {**DEFAULT_API, 'base_url': 'https://service.example/v1', 'model': 'custom-model'}
    save_api_settings(options, 'example-private-key')
    assert 'example-private-key' not in settings.read_text()
    assert load_api_key() == 'example-private-key'
    assert load_api_key({**options, 'base_url': 'https://different.example/v1'}) == ''
    data = json.loads(settings.read_text())
    assert data['theme'] == 'orange' and data['translation_model'] == 'test.gguf'
    # Editing the public endpoint alone must never send an old key to a new server.
    data['online_translation']['base_url'] = 'https://different.example/v1'
    settings.write_text(json.dumps(data))
    assert load_api_key() == ''
    clear_api_key()
    assert 'online_translation_secret' not in settings.read_text()


@pytest.mark.parametrize('url', ['http://public.example/v1', 'https://user:password@example.com',
    'https://example.com/v1?key=secret', 'https://example.com/#secret', 'not-a-url'])
def test_unsafe_or_malformed_endpoint_rejected(url):
    with pytest.raises(ValueError):
        completion_url(url)


def test_json_fallback_and_subtitle_context(monkeypatch):
    def respond(requests, answer):
        if len(requests) == 1:
            return 400, {'error': {'message': 'Unsupported response_format json_object'}}, {}
        return 200, answer, {}
    with api_server(respond) as (options, requests):
        translator = OnlineTranslator(options, 'test-key')
        result = translator.translate([{'id': 9, 'text': 'Hello', 'language': 'English'}], 'previous words', 'next words')
        assert result == {9: '你好。'} and len(requests) == 2
        assert requests[0][0] == '/v1/chat/completions'
        assert requests[0][1] == 'Bearer test-key'
        assert requests[0][2]['response_format'] == {'type': 'json_object'}
        assert 'response_format' not in requests[1][2]
        assert requests[1][2]['model'] == 'test-model'
        assert 'temperature' not in requests[1][2]
        message = json.loads(requests[1][2]['messages'][1]['content'])
        assert message['previous_context'] == 'previous words' and message['following_context'] == 'next words'


@pytest.mark.parametrize('count', [1, 4])
def test_auth_errors_redacted_and_redirect_not_followed(count):
    rows = [{'id': i + 1, 'text': 'Hello'} for i in range(count)]
    with api_server(lambda calls, answer: (401, {'error': 'example-secret-key'}, {})) as (options, calls):
        with pytest.raises(RuntimeError, match='密钥无效') as error:
            OnlineTranslator(options, 'example-secret-key').translate(rows)
        assert 'example-secret-key' not in str(error.value) and len(calls) == 1
    with api_server() as (other, other_calls):
        redirect = lambda calls, answer: (307, {}, {'Location': completion_url(other['base_url'])})
        with api_server(redirect) as (options, calls):
            with pytest.raises(RuntimeError, match='重定向'):
                OnlineTranslator(options, 'example-secret-key').translate(rows)
        assert not other_calls


def test_rate_limit_retry_and_length_parameter(monkeypatch):
    delays = []
    monkeypatch.setattr(OnlineTranslator, 'wait_to_retry', lambda self, seconds: delays.append(seconds))
    def respond(requests, answer):
        return (429, {}, {'Retry-After': '2'}) if len(requests) == 1 else (200, answer, {})
    with api_server(respond) as (options, requests):
        translator = OnlineTranslator({**options, 'token_parameter': 'max_completion_tokens', 'response_format': 'prompt'}, 'test-key')
        translator.translate([{'id': 1, 'text': 'Hello'}])
        assert delays == [2]
        assert requests[-1][2]['max_completion_tokens'] == 4096
        assert 'max_tokens' not in requests[-1][2] and 'response_format' not in requests[-1][2]


def test_online_resume_preserves_each_success_and_uses_local_config(settings, tmp_path, monkeypatch):
    project = tmp_path / 'subtitles.yingzi.json'
    cues = [Cue(0, 1, '前文', language='Chinese'), Cue(1, 2, 'Hello', language='English'),
            Cue(2, 3, 'Next', language='English'), Cue(3, 4, '后文', language='Chinese')]
    # Foreign project endpoints cannot override the locally configured service.
    save_project(project, tmp_path / 'missing.mp4', cues, {'translation_backend': 'online', 'base_url': 'https://foreign.invalid/v1'})
    def respond(requests, answer):
        if len(requests) == 1:
            # Invalid whole-batch output must not be saved. Split it and checkpoint each half.
            answer['choices'][0]['message']['content'] = '{"translations":[{"id":2,"text":"不应保存"}]}'
        return (200, answer, {}) if len(requests) <= 2 else (401, {}, {})
    with api_server(respond) as (options, requests):
        save_api_settings(options, 'test-key')
        monkeypatch.setattr(worker, 'readiness', lambda *args: pytest.fail('Online mode must not check local translation weights'))
        monkeypatch.setattr(worker, 'event', lambda *args, **kwargs: None)
        with pytest.raises(RuntimeError, match='密钥无效'):
            worker.process({'video': str(tmp_path / 'missing.mp4'), 'output': str(tmp_path), 'resume_project': str(project), 'bilingual': True})
        data, result = load_project(project)
        assert result[1].translation == '你好。' and result[2].translation == ''
        assert data['status'] == 'translating'
        assert 'test-key' not in project.read_text(encoding='utf-8')
        message = json.loads(requests[0][2]['messages'][1]['content'])
        assert message['previous_context'] == '前文' and message['following_context'] == '后文'
        assert [len(json.loads(call[2]['messages'][1]['content'])['subtitles']) for call in requests] == [2, 1, 1]
        retry = json.loads(requests[1][2]['messages'][1]['content'])
        assert retry['following_context'] == 'Next\n后文'
    with api_server() as (options, requests):
        save_api_settings(options, 'test-key')
        worker.process({'video': str(tmp_path / 'missing.mp4'), 'output': str(tmp_path), 'resume_project': str(project), 'bilingual': True})
        assert len(requests) == 1
        assert json.loads(requests[0][2]['messages'][1]['content'])['subtitles'][0]['id'] == 3
        assert load_project(project)[0]['status'] == 'complete'


def test_settings_dialog_and_gui_online_process(application, settings, tmp_path, monkeypatch):
    with api_server() as (options, requests):
        dialog = ApiDialog()
        dialog.base_url.setText(options['base_url'])
        dialog.model.setText('test-model')
        dialog.key.setText('test-key')
        assert dialog.batch_size.value() == 10
        dialog.batch_size.setValue(8)
        dialog.test_connection()
        assert not dialog.batch_size.isEnabled()
        loop = QEventLoop()
        poll = QTimer()
        poll.timeout.connect(lambda: loop.quit() if not dialog.testing else None)
        poll.start(30)
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        poll.stop()
        assert not dialog.testing and '连接成功' in dialog.status.text()
        dialog.save()
        assert dialog.result() == QDialog.Accepted and load_api_key() == 'test-key'
        assert load_api_settings()['batch_size'] == 8
        restored = ApiDialog()
        assert restored.batch_size.value() == 8
        restored.close()
        project = tmp_path / 'project.yingzi.json'
        save_project(project, tmp_path / 'missing.mp4', [Cue(i, i + 1, 'Hello', language='English') for i in range(3)], {'translation_backend': 'online'})
        window = Studio()
        window.load_saved_project(project)
        assert window.translation_backend.currentData() == 'online'
        monkeypatch.setattr('app.api_dialog.ApiDialog.exec', lambda self: QDialog.Accepted)
        window.configure_api()
        assert load_translation_backend() == 'online'
        assert '在线 API' in window.network_badge.text()
        assert 'base_url' not in window.current_options() and 'key' not in window.current_options()
        # Keep the test's machine-local settings path in the child process, without real credentials.
        from PySide6.QtCore import QProcess
        class TestProcess(QProcess):
            def start(self):
                self.setArguments(['-u', '-c',
                    'from pathlib import Path; from app import config; config.SETTINGS=Path(' + repr(str(settings)) + '); from app.worker import main; raise SystemExit(main())',
                    '--job', str(tmp_path / 'job.json')])
                super().start()
        monkeypatch.setattr('app.gui.QProcess', TestProcess)
        window.retry_translation()
        loop = QEventLoop()
        window.worker.finished.connect(loop.quit)
        QTimer.singleShot(10000, loop.quit)
        loop.exec()
        assert not window.busy() and window.cues[0].translation == '你好。'
        assert all(cue.translation == '你好。' for cue in window.cues)
        assert len(json.loads(requests[-1][2]['messages'][1]['content'])['subtitles']) == 3
        assert 'test-key' not in (tmp_path / 'job.json').read_text(encoding='utf-8')
        assert 'test-key' not in (tmp_path / 'run.log').read_text(encoding='utf-8')
        window.close()


def test_batch_sent_once_with_shared_context_schema_and_shuffled_result():
    rows = [{'id': i + 7, 'text': text, 'language': 'English'} for i, text in enumerate([
        'I thought', 'he had already left.', 'But he was waiting outside.'])]
    def respond(requests, answer):
        answer['choices'][0]['message']['content'] = json.dumps({'translations': [
            {'id': row['id'], 'text': f"译文{row['id']}"} for row in reversed(rows)]})
        return 200, answer, {}
    with api_server(respond) as (options, requests):
        translator = OnlineTranslator({**options, 'response_format': 'json_schema'}, 'test-key')
        result = translator.translate(rows, '前' * 700, '后' * 700)
        assert result == {row['id']: f"译文{row['id']}" for row in rows}
        assert len(requests) == 1
        payload = requests[0][2]
        message = json.loads(payload['messages'][1]['content'])
        assert message == {'previous_context': '前' * 600, 'following_context': '后' * 600, 'subtitles': rows}
        assert '成段翻译任务' in payload['messages'][0]['content']
        assert '当前这一条' not in payload['messages'][0]['content']
        schema = payload['response_format']['json_schema']['schema']['properties']['translations']
        assert schema['minItems'] == schema['maxItems'] == 3
        assert schema['items']['properties']['id']['enum'] == [7, 8, 9]


@pytest.mark.parametrize('failure', ['missing', 'duplicate', 'unknown', 'blank', 'punctuation', 'truncated', 'json', 'fence'])
def test_bad_batch_output_retries_in_smaller_groups(failure):
    rows = [{'id': i + 1, 'text': f'Sentence {i + 1}', 'language': 'English'} for i in range(4)]
    def respond(requests, answer):
        if len(requests) == 1:
            choice = answer['choices'][0]
            items = json.loads(choice['message']['content'])['translations']
            if failure == 'missing':
                items.pop()
            elif failure in ('duplicate', 'unknown'):
                items[-1]['id'] = 1 if failure == 'duplicate' else 999
            elif failure in ('blank', 'punctuation'):
                items[-1]['text'] = '' if failure == 'blank' else '。'
            elif failure == 'truncated':
                choice['finish_reason'] = 'length'
            choice['message']['content'] = '{' if failure == 'json' else '```' if failure == 'fence' else json.dumps({'translations': items})
        return 200, answer, {}
    with api_server(respond) as (options, requests):
        result = OnlineTranslator(options, 'test-key').translate(rows, 'before', 'after')
        assert result == {i: '你好。' for i in range(1, 5)}
        messages = [json.loads(call[2]['messages'][1]['content']) for call in requests]
        assert [[row['id'] for row in message['subtitles']] for message in messages] == [[1, 2, 3, 4], [1, 2], [3, 4]]
        assert messages[1]['following_context'] == 'Sentence 3\nSentence 4\nafter'
        assert messages[2]['previous_context'] == 'before\nSentence 1\nSentence 2'


def test_single_invalid_result_retries_only_twice_and_empty_input_sends_nothing():
    def respond(requests, answer):
        answer['choices'][0]['message']['content'] = '{"translations":[]}'
        return 200, answer, {}
    with api_server(respond) as (options, requests):
        translator = OnlineTranslator(options, 'test-key')
        assert translator.translate([]) == {} and not requests
        with pytest.raises(RuntimeError, match='字幕 5 翻译失败'):
            translator.translate([{'id': 5, 'text': 'Hello'}])
        assert len(requests) == 2


@pytest.mark.parametrize('batch_size', [1, 10])
def test_worker_batch_count_checkpoints_context_and_timestamps(settings, tmp_path, monkeypatch, batch_size):
    project = tmp_path / 'subtitles.yingzi.json'
    cues = [Cue(i * 2, i * 2 + 1.5, f'Line {i + 1}', language='English') for i in range(23)]
    cues[0].translation = '已经完成'
    save_project(project, tmp_path / 'missing.mp4', cues, {'translation_backend': 'online'})
    events = []
    monkeypatch.setattr(worker, 'event', lambda kind, **data: events.append((kind, data)))
    with api_server() as (options, requests):
        save_api_settings({**options, 'batch_size': batch_size}, 'test-key')
        worker.process({'video': str(tmp_path / 'missing.mp4'), 'output': str(tmp_path), 'resume_project': str(project), 'bilingual': True})
        messages = [json.loads(call[2]['messages'][1]['content']) for call in requests]
        assert [len(message['subtitles']) for message in messages] == ([1] * 22 if batch_size == 1 else [10, 10, 2])
        assert messages[0]['previous_context'] == 'Line 1'
        if batch_size == 10:
            assert messages[0]['following_context'] == 'Line 12\nLine 13\nLine 14'
            assert messages[1]['previous_context'] == 'Line 9\nLine 10\nLine 11'
        data, translated = load_project(project)
        assert data['status'] == 'complete' and translated[0].translation == '已经完成'
        assert all(cue.translation == '你好。' for cue in translated[1:])
        assert [(c.start, c.end, c.text, c.language) for c in translated] == [(c.start, c.end, c.text, c.language) for c in cues]
        assert any(kind == 'progress' and event['message'] == '翻译字幕 22/22' for kind, event in events)


def test_batches_keep_language_pauses_completed_gaps_and_size_boundaries():
    cues = [Cue(i, i + .8, 'English', language='English') for i in range(8)]
    cues[2].language = cues[3].language = 'Japanese'
    cues[4].translation = '已完成'
    cues[7].start, cues[7].end = 12, 13
    pending = [0, 1, 2, 3, 5, 6, 7]
    assert list(translation_batches(cues, pending, 10)) == [[0, 1], [2, 3], [5, 6], [7]]
    assert list(translation_batches(cues, [0, 1], 10, max_chars=10)) == [[0], [1]]
    cues[0].text = 'x' * 2000
    assert list(translation_batches(cues, [0, 1], 10)) == [[0], [1]]


def test_old_api_preferences_default_to_ten_and_invalid_batch_size_rejected(settings):
    settings.write_text(json.dumps({'online_translation': {'base_url': 'https://service.example/v1', 'model': 'example'}}))
    assert load_api_settings()['batch_size'] == 10
    for size in (0, 21, None, 'wrong'):
        with pytest.raises(ValueError):
            normalize_api({**DEFAULT_API, 'batch_size': size}, require_complete=False)


def test_failed_cue_does_not_block_later_cues_and_diagnostics_are_safe(settings, tmp_path, monkeypatch):
    project = tmp_path / 'subtitles.yingzi.json'
    original = [Cue(i, i + .8, f'Line {i}', language='English') for i in range(4)]
    save_project(project, tmp_path / 'video.mp4', original, {'translation_backend': 'online'})
    def respond(requests, answer):
        rows = json.loads(requests[-1][2]['messages'][1]['content'])['subtitles']
        if any(row['id'] == 2 for row in rows):
            answer['choices'][0]['finish_reason'] = 'length'
        answer['choices'][0]['message']['reasoning_content'] = 'private reasoning should not be logged'
        answer['usage'] = {'prompt_tokens': 100, 'completion_tokens': 200, 'total_tokens': 300,
                          'completion_tokens_details': {'reasoning_tokens': 180}}
        return 200, answer, {}
    monkeypatch.setattr(worker, 'event', lambda *args, **kwargs: None)
    job = {'video': str(tmp_path / 'video.mp4'), 'output': str(tmp_path), 'resume_project': str(project), 'bilingual': True}
    with api_server(respond) as (options, requests):
        save_api_settings(options, 'test-key')
        worker.process(job)
        data, cues = load_project(project)
        assert data['status'] == 'translation_partial'
        assert [c.translation for c in cues] == ['你好。', '', '你好。', '你好。']
        assert '长度限制' in cues[1].translation_error
        logs = (tmp_path / 'api-diagnostics.jsonl').read_text(encoding='utf-8')
        assert all(secret not in logs for secret in ('test-key', 'private reasoning', 'Line 0'))
        diagnostic = json.loads(logs.splitlines()[0])
        assert diagnostic['finish_reason'] == 'length' and diagnostic['total_tokens'] == 300
        assert diagnostic['reasoning_tokens'] == 180 and diagnostic['source_chars'] > 0
    with api_server() as (options, requests):
        save_api_settings(options, 'test-key')
        worker.process(job)
        assert len(requests) == 1
        assert json.loads(requests[0][2]['messages'][1]['content'])['subtitles'][0]['id'] == 2
        data, cues = load_project(project)
        assert data['status'] == 'complete' and not any(c.translation_error for c in cues)


def test_worker_selected_translation_applies_glossary_style_and_completed_references(settings, tmp_path, monkeypatch):
    project = tmp_path / 'subtitles.yingzi.json'
    cues = [Cue(0, 1, 'Earlier', '前文已完成', 'English'), Cue(1, 2, 'Hello', '旧译文', 'English'),
            Cue(2, 3, 'Do not translate', language='English')]
    save_project(project, tmp_path / 'missing.mp4', cues, {'translation_backend': 'online',
                 'translation_glossary': 'Hello = 你好', 'translation_prompt': '自然口语'})
    monkeypatch.setattr(worker, 'event', lambda *args, **kwargs: None)
    with api_server() as (options, requests):
        save_api_settings(options, 'test-key')
        worker.process({'video': str(tmp_path / 'missing.mp4'), 'output': str(tmp_path),
                        'resume_project': str(project), 'selected_ids': [2], 'bilingual': True})
        assert len(requests) == 1
        payload = requests[0][2]
        assert 'Hello = 你好' in payload['messages'][0]['content'] and '自然口语' in payload['messages'][0]['content']
        message = json.loads(payload['messages'][1]['content'])
        assert message['reference_translations'] == [{'original': 'Earlier', 'translation': '前文已完成'}]
        assert [row['id'] for row in message['subtitles']] == [2]
        data, translated = load_project(project)
        assert [c.translation for c in translated] == ['前文已完成', '你好。', '']
        assert 'selected_ids' not in data['options']


def test_online_concurrency_keeps_results_on_original_cues(settings, tmp_path, monkeypatch):
    import time
    project = tmp_path / 'subtitles.yingzi.json'
    save_project(project, tmp_path / 'missing.mp4', [Cue(i, i + .8, f'Line {i}', language='English') for i in range(4)], {'translation_backend': 'online'})
    barrier = threading.Barrier(2)
    state = {'active': 0, 'maximum': 0}
    lock = threading.Lock()
    def respond(requests, answer):
        row = json.loads(answer['choices'][0]['message']['content'])['translations'][0]
        with lock:
            state['active'] += 1
            state['maximum'] = max(state['maximum'], state['active'])
        barrier.wait(timeout=4)
        time.sleep(.02 if row['id'] % 2 else .005)
        answer['choices'][0]['message']['content'] = json.dumps({'translations': [{'id': row['id'], 'text': f"译文{row['id']}"}]})
        with lock:
            state['active'] -= 1
        return 200, answer, {}
    monkeypatch.setattr(worker, 'event', lambda *args, **kwargs: None)
    with api_server(respond) as (options, requests):
        save_api_settings({**options, 'batch_size': 1, 'concurrency': 2}, 'test-key')
        worker.process({'video': str(tmp_path / 'missing.mp4'), 'output': str(tmp_path), 'resume_project': str(project), 'bilingual': True})
        assert state['maximum'] == 2 and len(requests) == 4
        assert [cue.translation for cue in load_project(project)[1]] == ['译文1', '译文2', '译文3', '译文4']


def test_rate_gate_reserves_shared_request_slots_and_honors_stop(monkeypatch):
    from app.online_queue import RequestGate
    waits = []
    class Stop:
        stopped = False
        def wait(self, seconds):
            waits.append(seconds)
            return self.stopped
        def is_set(self):
            return self.stopped
    stop = Stop()
    monkeypatch.setattr('app.online_queue.time.monotonic', lambda: 10)
    gate = RequestGate(60, stop)
    gate.acquire()
    gate.acquire()
    gate.acquire()
    assert waits == [0, 1, 2]
    stop.stopped = True
    with pytest.raises(RuntimeError, match='停止'):
        gate.acquire()


@pytest.mark.parametrize('host,model,mode,expected', [
    ('https://api.deepseek.com', 'deepseek-flash', 'auto', 'disabled'),
    ('https://api.deepseek.com', 'deepseek-v4-pro', 'auto', 'disabled'),
    ('https://relay.example', 'deepseek-flash', 'auto', None),
    ('https://api.deepseek.com', 'unknown-model', 'auto', None),
    ('https://api.deepseek.com', 'deepseek-flash', 'default', None),
    ('https://relay.example', 'deepseek-flash', 'enabled', 'enabled')])
def test_thinking_mode_auto_is_restricted_to_known_official_models(host, model, mode, expected):
    with api_server() as (options, requests):
        translator = OnlineTranslator(options, 'test-key')
        translator.options.update(base_url=host, model=model, thinking_mode=mode)
        translator.translate([{'id': 1, 'text': 'Hello'}])
        payload = requests[0][2]
        assert payload.get('thinking') == ({'type': expected} if expected else None)


@pytest.mark.parametrize('error,category', [
    (TimeoutError('private connection details'), 'timeout'),
    (urllib.error.URLError(TimeoutError('private proxy credentials')), 'timeout'),
    (urllib.error.URLError(socket.gaierror(11001, 'private proxy credentials')), 'dns'),
    (ConnectionResetError(10054, 'private connection details'), 'connection_reset'),
    (ConnectionRefusedError(10061, 'private connection details'), 'connection_refused'),
])
def test_connection_failure_retries_same_batch_and_redacts_diagnostics(monkeypatch, error, category):
    with api_server() as (options, requests):
        translator = OnlineTranslator(options, 'test-key')
        original_open = translator.client.open
        attempts, diagnostics, waits = [], [], []
        def flaky(request, **kwargs):
            attempts.append(request.data)
            if len(attempts) <= 2:
                raise error
            return original_open(request, **kwargs)
        monkeypatch.setattr(translator.client, 'open', flaky)
        monkeypatch.setattr(translator, 'wait_to_retry', waits.append)
        translator.diagnostic = diagnostics.append
        assert translator.translate([{'id': i, 'text': 'Hello'} for i in (30, 31)]) == {30: '你好。', 31: '你好。'}
        assert waits == [1, 2] and len(requests) == 1
        assert len(attempts) == 3 and len(set(attempts)) == 1
        assert [d['attempt'] for d in diagnostics] == [1, 2, 3]
        assert diagnostics[0]['error_category'] == category
        assert diagnostics[0]['network_stage'] == 'connect'
        assert diagnostics[0]['retry_in_seconds'] == 1
        assert diagnostics[-1]['status'] == 200
        assert 'private' not in json.dumps(diagnostics) and 'test-key' not in json.dumps(diagnostics)


def test_interrupted_response_retried_before_output_validation(monkeypatch):
    with api_server() as (options, requests):
        translator = OnlineTranslator(options, 'test-key')
        original_open = translator.client.open
        diagnostics, attempts = [], []
        class BrokenResponse:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, *args):
                raise http.client.IncompleteRead(b'private response body', 500)
        def flaky(request, **kwargs):
            attempts.append(request.data)
            return BrokenResponse() if len(attempts) == 1 else original_open(request, **kwargs)
        monkeypatch.setattr(translator.client, 'open', flaky)
        monkeypatch.setattr(translator, 'wait_to_retry', lambda seconds: None)
        translator.diagnostic = diagnostics.append
        assert translator.translate([{'id': 8, 'text': 'Hello'}]) == {8: '你好。'}
        assert len(attempts) == 2 and len(requests) == 1
        assert diagnostics[0]['network_stage'] == 'read_response'
        assert diagnostics[0]['error_category'] == 'incomplete_response'
        assert 'private response body' not in json.dumps(diagnostics)


@pytest.mark.parametrize('certificate', [False, True])
def test_persistent_network_failure_is_bounded_and_certificate_errors_do_not_retry(monkeypatch, certificate):
    with api_server() as (options, requests):
        translator = OnlineTranslator(options, 'test-key')
        attempts, diagnostics = [], []
        def fail(request, **kwargs):
            attempts.append(request.data)
            if certificate:
                raise urllib.error.URLError(ssl.SSLCertVerificationError(1, 'private certificate detail'))
            raise urllib.error.URLError(TimeoutError('private connection detail'))
        monkeypatch.setattr(translator.client, 'open', fail)
        monkeypatch.setattr(translator, 'wait_to_retry', lambda seconds: None)
        translator.diagnostic = diagnostics.append
        with pytest.raises(RuntimeError, match='证书校验失败' if certificate else '已尝试 3 次') as raised:
            translator.translate([{'id': 30, 'text': 'Hello'}, {'id': 31, 'text': 'World'}])
        assert len(attempts) == (1 if certificate else 3) and not requests
        assert len(set(attempts)) == 1  # Connection failures must not multiply into split batches.
        assert 'private' not in str(raised.value) + json.dumps(diagnostics)
        assert '继续翻译' in str(raised.value)
        assert 'retry_in_seconds' not in diagnostics[-1]


def test_real_disconnection_recovery_keeps_parallel_worker_running(settings, tmp_path, monkeypatch):
    project = tmp_path / 'subtitles.yingzi.json'
    cues = [Cue(i, i + .8, f'Line {i}', language='English') for i in range(6)]
    cues[0].translation = 'Already saved'
    save_project(project, tmp_path / 'video.mp4', cues, {'translation_backend': 'online'})
    seen = []
    def respond(requests, answer):
        rows = json.loads(answer['choices'][0]['message']['content'])['translations']
        first_id = rows[0]['id']
        seen.append(first_id)
        if first_id == 2 and seen.count(2) == 1:
            return None  # Close the actual HTTP socket without a response.
        return 200, answer, {}
    monkeypatch.setattr(worker, 'event', lambda *args, **kwargs: None)
    monkeypatch.setattr(OnlineTranslator, 'wait_to_retry', lambda self, seconds: None)
    with api_server(respond) as (options, requests):
        save_api_settings({**options, 'batch_size': 2, 'concurrency': 2}, 'test-key')
        worker.process({'video': str(tmp_path / 'video.mp4'), 'output': str(tmp_path),
                        'resume_project': str(project), 'bilingual': True})
    data, result = load_project(project)
    assert data['status'] == 'complete'
    assert [c.translation for c in result] == ['Already saved'] + ['你好。'] * 5
    assert not any(c.translation_error for c in result)
    assert sorted(seen) == [2, 2, 4, 6]
    diagnostics = [json.loads(line) for line in (tmp_path / 'api-diagnostics.jsonl').read_text(encoding='utf-8').splitlines()]
    failed = next(d for d in diagnostics if 'error_category' in d)
    assert failed['error_category'] == 'connection_reset' and failed['ids'] == [2, 3]


def test_retry_wait_honors_queue_stop():
    translator = OnlineTranslator({**DEFAULT_API, 'base_url': 'https://service.example', 'model': 'test'}, 'test-key')
    translator.gate.stop.set()
    with pytest.raises(RuntimeError, match='队列已停止'):
        translator.wait_to_retry(60)
