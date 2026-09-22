"""Bounded online workers with a shared rate gate and serialized result delivery."""
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor


class RequestGate:
    def __init__(self, per_minute=60, stop=None):
        self.interval = 60 / per_minute if per_minute else 0
        self.next_time = 0
        self.lock = threading.Lock()
        self.stop = stop or threading.Event()

    def acquire(self):
        with self.lock:
            now = time.monotonic()
            turn = max(now, self.next_time)
            self.next_time = turn + self.interval
        if self.stop.wait(max(0, turn - now)) or self.stop.is_set():
            raise RuntimeError('翻译队列已停止。')


def translate_groups(manager, cues, batches):
    from .online_translation import OnlineTranslator
    messages = queue.Queue()
    stop = threading.Event()
    gate = RequestGate(manager.options['requests_per_minute'], stop)
    workers = manager.options['concurrency']
    iterator = iter(batches)
    local = threading.local()

    def translate(indexes):
        try:
            if not hasattr(local, 'translator'):
                translator = OnlineTranslator(manager.options, manager.key)
                translator.diagnostic = manager.diagnostic
                translator.glossary = manager.glossary
                translator.style_prompt = manager.style_prompt
                translator.gate = gate
                local.translator = translator
            translator = local.translator
            rows = [{'id': i + 1, 'text': cues[i].text, 'language': cues[i].language} for i in indexes]
            before = cues[max(0, indexes[0] - 3):indexes[0]]
            after = cues[indexes[-1] + 1:indexes[-1] + 4]
            translator.references = [{'original': c.text[:300], 'translation': c.translation[:300]}
                                    for c in before if c.translation and not c.translation_error]
            for result in translator.translate_batches(rows, '\n'.join(c.text for c in before),
                    '\n'.join(c.text for c in after), on_failure=lambda cue_id, error: messages.put(('failure', (cue_id, error)))):
                messages.put(('result', result))
        except Exception as exc:
            messages.put(('error', exc))
        finally:
            messages.put(('done', None))

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='subtitle-api') as pool:
        active = 0
        for _ in range(workers):
            indexes = next(iterator, None)
            if indexes is not None:
                pool.submit(translate, indexes)
                active += 1
        failure = None
        while active:
            kind, value = messages.get()
            if kind == 'done':
                active -= 1
                indexes = next(iterator, None) if failure is None else None
                if indexes is not None:
                    pool.submit(translate, indexes)
                    active += 1
            elif kind == 'error':
                if failure is None:
                    failure = value
                    stop.set()
            else:
                yield kind, value
        if failure is not None:
            raise failure
