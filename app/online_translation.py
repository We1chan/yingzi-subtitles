"""OpenAI-compatible Chat Completions transport, sharing subtitle validation."""
import copy
import http.client
import json
import socket
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .api_settings import (completion_url, is_loopback, load_api_key, load_api_settings,
                           normalize_api, validate_api_key)
from .translation import LocalTranslator, translation_payload, validate_translation_response
from .online_queue import RequestGate


def transport_error(exc):
    """Classify connection errors without exposing proxy URLs or response bodies."""
    cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    code = getattr(cause, 'winerror', None) or getattr(cause, 'errno', None)
    if isinstance(cause, ssl.SSLCertVerificationError):
        category, message, retryable = 'tls_certificate', 'HTTPS 证书校验失败，请检查系统时间和代理证书', False
    elif isinstance(cause, (TimeoutError, socket.timeout)) or code in (10060,):
        category, message, retryable = 'timeout', '连接或读取超时', True
    elif isinstance(cause, socket.gaierror):
        category, message, retryable = 'dns', '域名解析失败', True
    elif isinstance(cause, ssl.SSLError):
        category, message, retryable = 'tls', 'HTTPS 连接中断', True
    elif isinstance(cause, http.client.IncompleteRead):
        category, message, retryable = 'incomplete_response', '响应接收不完整', True
    elif isinstance(cause, (ConnectionResetError, http.client.RemoteDisconnected)) or code in (10054,):
        category, message, retryable = 'connection_reset', '连接被中断', True
    elif isinstance(cause, ConnectionRefusedError) or code in (10061,):
        category, message, retryable = 'connection_refused', '连接被拒绝', True
    else:
        category, message, retryable = 'connection_error', '网络连接失败', True
    detail = {'error_category': category, 'error_message': message, 'error_type': type(cause).__name__}
    if type(code) is int:
        detail['error_code'] = code
    return detail, retryable


def translation_batches(cues, pending, batch_size, max_chars=1800, max_gap=3):
    """Group only consecutive pending cues in the same continuous language segment."""
    batch, characters = [], 0
    for index in pending:
        cue = cues[index]
        if batch and (len(batch) >= batch_size or index != batch[-1] + 1
                      or cue.language != cues[batch[-1]].language
                      or cue.start - cues[batch[-1]].end >= max_gap
                      or characters + len(cue.text) > max_chars):
            yield batch
            batch, characters = [], 0
        batch.append(index)
        characters += len(cue.text)
    if batch:
        yield batch


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward credentials or subtitle text to a redirect destination.
        return None


class OnlineTranslator(LocalTranslator):
    def __init__(self, options=None, key=None, emit=lambda *args: None):
        self.options = normalize_api(load_api_settings() if options is None else options)
        self.key = validate_api_key(load_api_key(self.options) if key is None else key)
        self.endpoint = completion_url(self.options['base_url'])
        # HTTP(S)_PROXY without NO_PROXY makes urllib ignore the registry bypass list,
        # so a local service would otherwise be reached through the proxy.
        handlers = [NoRedirect()] + ([urllib.request.ProxyHandler({})] if is_loopback(urlsplit(self.endpoint).hostname) else [])
        self.client = urllib.request.build_opener(*handlers)
        self.emit = emit
        self.json_fallback = False
        self.diagnostic = lambda record: None
        self.gate = RequestGate(self.options['requests_per_minute'])

    def start(self):
        pass

    def close(self):
        pass

    def wait_to_retry(self, seconds):
        if self.gate.stop.wait(seconds):
            raise RuntimeError('翻译队列已停止。')

    def translate(self, rows, context='', following_context=''):
        result = {}
        for translations in self.translate_batches(rows, context, following_context):
            result.update(translations)
        return result

    def translate_batches(self, rows, context='', following_context='', on_failure=None):
        """Yield each validated batch so callers can checkpoint successful fallback halves."""
        if not rows:
            return
        payload = translation_payload(rows, context, following_context, grouped=len(rows) > 1,
            glossary=getattr(self, 'glossary', ''), style_prompt=getattr(self, 'style_prompt', ''), references=getattr(self, 'references', None))
        last = None
        for attempt in range(1 if len(rows) > 1 else 2):
            try:
                translations = validate_translation_response(self.complete(payload), rows)
            except ValueError as exc:
                # Only invalid output is split. Authentication/network failures propagate.
                last = exc
                payload['messages'][0]['content'] += ' 请检查输出：每个编号恰好一次，必须有实际译文，JSON 完整且无额外内容。'
            else:
                yield translations
                return
        if len(rows) == 1:
            detail = getattr(self, 'last_diagnostic', {})
            error = str(last)
            if detail.get('finish_reason') == 'length':
                error += f' 本次输出上限 {detail["output_limit"]} Token'
                if 'completion_tokens' in detail:
                    error += f'，服务报告已生成 {detail["completion_tokens"]} Token'
                if 'reasoning_tokens' in detail:
                    error += f'（其中推理 {detail["reasoning_tokens"]} Token）'
                error += '。可查看运行日志中的 API 诊断。'
            if on_failure is not None:
                on_failure(rows[0]['id'], error)
                return
            raise RuntimeError(f"字幕 {rows[0]['id']} 翻译失败。已完成的译文已保存，可继续翻译。{error}") from last
        middle = len(rows) // 2
        left, right = rows[:middle], rows[middle:]
        yield from self.translate_batches(left, context,
            '\n'.join(row['text'] for row in right) + '\n' + following_context, on_failure)
        yield from self.translate_batches(right,
            context + '\n' + '\n'.join(row['text'] for row in left), following_context, on_failure)

    def complete(self, payload):
        payload = copy.deepcopy(payload)
        payload['model'] = self.options['model']
        # Temperature restrictions vary across reasoning models; use provider defaults.
        payload.pop('temperature', None)
        payload.pop('max_tokens', None)
        payload[self.options['token_parameter']] = self.options['max_tokens']
        thinking = self.options['thinking_mode']
        if thinking == 'auto':
            official = urlsplit(self.options['base_url']).hostname == 'api.deepseek.com'
            thinking = 'disabled' if official and self.options['model'] in (
                'deepseek-flash', 'deepseek-v4-flash', 'deepseek-v4-pro') else 'default'
        if thinking in ('enabled', 'disabled'):
            payload['thinking'] = {'type': thinking}
        mode = self.options['response_format']
        if mode == 'prompt' or self.json_fallback:
            payload.pop('response_format', None)
        elif mode in ('auto', 'json_object'):
            payload['response_format'] = {'type': 'json_object'}
        for attempt in range(3):
            self.gate.acquire()
            rows = json.loads(payload['messages'][1]['content'])['subtitles']
            record = {'model': self.options['model'], 'ids': [row['id'] for row in rows],
                      'source_chars': sum(len(row['text']) for row in rows), 'attempt': attempt + 1,
                      'output_limit': self.options['max_tokens'], 'status': None}
            record['thinking_mode'] = thinking
            started = time.monotonic()
            retry_delay = None
            stage = 'connect'
            request = urllib.request.Request(self.endpoint, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {self.key}'})
            try:
                with self.client.open(request, timeout=self.options['timeout']) as response:
                    record['status'] = response.status
                    stage = 'read_response'
                    content = response.read(4 * 1024 * 1024 + 1)
                    if len(content) > 4 * 1024 * 1024:
                        raise RuntimeError('API 响应过大，请检查服务地址及输出长度设置。')
                    try:
                        result = json.loads(content)
                        choice = (result.get('choices') or [{}])[0] if isinstance(result, dict) else {}
                        if isinstance(choice, dict):
                            record['finish_reason'] = choice.get('finish_reason')
                            message = choice.get('message') or {}
                            if isinstance(message, dict):
                                record['output_chars'] = len(message['content']) if isinstance(message.get('content'), str) else 0
                                record['reasoning_chars'] = len(message['reasoning_content']) if isinstance(message.get('reasoning_content'), str) else 0
                        usage = result.get('usage', {}) if isinstance(result, dict) else {}
                        if isinstance(usage, dict):
                            for field in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                                if type(usage.get(field)) is int:
                                    record[field] = usage[field]
                            detail = usage.get('completion_tokens_details') or {}
                            if isinstance(detail, dict) and type(detail.get('reasoning_tokens')) is int:
                                record['reasoning_tokens'] = detail['reasoning_tokens']
                        return result
                    except (ValueError, UnicodeDecodeError):
                        raise ValueError('API 返回了无效 JSON，请检查接口地址及兼容模式。') from None
            except urllib.error.HTTPError as exc:
                # Inspect only compatibility indicators; never echo response bodies/keys.
                body = exc.read(8192).decode('utf-8', errors='replace').lower()
                status = exc.code
                record['status'] = status
                headers = exc.headers
                exc.close()
                if (status in (400, 422) and mode == 'auto' and 'response_format' in payload
                        and any(name in body for name in ('response_format', 'json_object'))):
                    payload.pop('response_format', None)
                    self.json_fallback = True
                    retry_delay = 0
                elif status in (429, 500, 502, 503, 504) and attempt < 2:
                    try:
                        retry_delay = min(10, max(1, float(headers.get('Retry-After', 2 ** attempt))))
                    except (TypeError, ValueError):
                        retry_delay = 2 ** attempt
                else:
                    messages = {401: 'API 密钥无效或已过期', 403: 'API 无访问权限，请检查密钥及模型权限',
                                404: 'API 地址或模型不存在，请检查服务商配置',
                                429: '请求受限或额度不足，请稍后重试并检查账户额度'}
                    if 300 <= status < 400:
                        message = 'API 返回了重定向，请在设置中填写最终的接口地址'
                    else:
                        message = messages.get(status, f'API 请求失败（HTTP {status}），请检查模型、JSON 模式和输出长度参数')
                    raise RuntimeError(message + '。已完成的字幕会保留。') from None
            except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
                detail, retryable = transport_error(exc)
                record.update(detail, network_stage=stage)
                if retryable and attempt < 2:
                    retry_delay = 2 ** attempt
                else:
                    ids = record['ids']
                    label = str(ids[0]) if len(ids) == 1 else f'{ids[0]}～{ids[-1]}'
                    attempts = f'，已尝试 {attempt + 1} 次' if retryable else ''
                    raise RuntimeError(f'字幕 {label} 请求失败：{detail["error_message"]}{attempts}。'
                                       '已完成的译文已保存，可点击“继续翻译”补全。') from None
            finally:
                record['seconds'] = round(time.monotonic() - started, 3)
                if retry_delay is not None:
                    record['retry_in_seconds'] = retry_delay
                self.last_diagnostic = record
                self.diagnostic(record)
            if retry_delay is not None:
                self.wait_to_retry(retry_delay)
        raise RuntimeError('API 请求未完成，请检查 JSON 输出模式后重试。')
