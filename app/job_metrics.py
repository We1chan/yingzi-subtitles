"""Safe request counters. Never persist headers, keys, prompts or reasoning text."""
import json
import threading
import time


class RequestMetrics:
    def __init__(self, path, emit):
        self.path, self.emit = path, emit
        self.lock = threading.RLock()
        self.started = time.monotonic()
        self.requests = 0
        self.tokens = None
        self.reasoning_tokens = None

    def record(self, record):
        with self.lock:
            self.requests += 1
            if 'total_tokens' in record:
                self.tokens = (self.tokens or 0) + record['total_tokens']
            if 'reasoning_tokens' in record:
                self.reasoning_tokens = (self.reasoning_tokens or 0) + record['reasoning_tokens']
            with self.path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + '\n')
            self.emit('api_diagnostic', **record, requests=self.requests, tokens=self.tokens)

    def snapshot(self, processed=0, remaining=0):
        elapsed = time.monotonic() - self.started
        return {'requests': self.requests, 'tokens': self.tokens, 'reasoning_tokens': self.reasoning_tokens,
                'elapsed': round(elapsed), 'eta': round(elapsed / processed * remaining) if processed else None}
