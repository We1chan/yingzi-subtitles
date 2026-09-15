import io
import json
from types import SimpleNamespace

import pytest

from app import asr_process


@pytest.mark.parametrize('tail,code,success', [({'type': 'done'}, 0, True),
    ({'type': 'error', 'message': '显存不足'}, 1, False), ({'type': 'backend'}, 0, False)])
def test_child_protocol_requires_successful_exit_and_completion(tmp_path, monkeypatch, tail, code, success):
    messages = [{'type': 'record', 'index': 0, 'record': {'text': 'Hello.'}}, tail]
    child = SimpleNamespace(stdout=io.StringIO('\n'.join(map(json.dumps, messages))),
        wait=lambda: code, poll=lambda: code)
    def popen(args, **kwargs):
        request = json.loads((tmp_path / 'asr-request.json').read_text(encoding='utf-8'))
        assert request['asr_model'] == 'chosen-model'
        assert request['spans'] == [[0, 2]]
        assert args[3] == 'app.external_asr'
        return child
    monkeypatch.setattr(asr_process.subprocess, 'Popen', popen)
    stream = asr_process.external_records({'asr_model': 'chosen-model'}, tmp_path / 'audio.wav', [(0, 2)])
    if success:
        assert list(stream) == messages
    else:
        with pytest.raises(RuntimeError):
            list(stream)
    assert child.stdout.closed
