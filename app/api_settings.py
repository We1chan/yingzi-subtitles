"""Machine-local API configuration; keys are encrypted for the Windows user."""
import base64
import ctypes
from ctypes import wintypes
import ipaddress
import json
import os
from urllib.parse import urlsplit, urlunsplit

from . import config
from .subtitles import atomic_write

DEFAULT_API = {'base_url': '', 'model': '', 'response_format': 'auto',
               'timeout': 120, 'max_tokens': 4096, 'token_parameter': 'max_tokens', 'batch_size': 10,
               'concurrency': 2, 'requests_per_minute': 60, 'thinking_mode': 'auto'}


def is_loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return str(host).lower() == 'localhost'


def completion_url(value):
    value = str(value).strip()
    if not value or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError('请填写有效的 API 地址。')
    try:
        parts = urlsplit(value)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise ValueError('API 地址格式无效。') from None
    if not host or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('API 地址不能包含密钥、用户名、查询参数或片段。')
    if parts.scheme != 'https' and not (parts.scheme == 'http' and is_loopback(host)):
        raise ValueError('在线 API 请使用 HTTPS；HTTP 仅支持本机地址。')
    path = parts.path.rstrip('/') or '/v1'
    if not path.endswith('/chat/completions'):
        path += '/chat/completions'
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, '', ''))


def normalize_api(values, require_complete=True):
    values = values if isinstance(values, dict) else {}
    result = {key: values.get(key, default) for key, default in DEFAULT_API.items()}
    result['base_url'] = str(result['base_url']).strip()
    result['model'] = str(result['model']).strip()
    if require_complete or result['base_url']:
        completion_url(result['base_url'])
    if require_complete and not result['model']:
        raise ValueError('请填写服务商提供的模型 ID。')
    if len(result['model']) > 200 or any(ord(char) < 32 for char in result['model']):
        raise ValueError('模型 ID 格式无效。')
    if result['response_format'] not in ('auto', 'json_object', 'json_schema', 'prompt'):
        raise ValueError('请选择有效的 JSON 输出模式。')
    if result['token_parameter'] not in ('max_tokens', 'max_completion_tokens'):
        raise ValueError('请选择有效的输出长度参数。')
    if result['thinking_mode'] not in ('auto', 'default', 'enabled', 'disabled'):
        raise ValueError('请选择有效的思考模式。')
    for key, low, high in [('timeout', 5, 600), ('max_tokens', 256, 32768), ('batch_size', 1, 20),
                           ('concurrency', 1, 4), ('requests_per_minute', 0, 600)]:
        try:
            result[key] = int(result[key])
            if not low <= result[key] <= high:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('请求超时、输出长度、分组、并发或限速超出可设置范围。') from None
    return result


def read_settings():
    try:
        data = json.loads(config.SETTINGS.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_api_settings():
    try:
        return normalize_api(read_settings().get('online_translation'), require_complete=False)
    except ValueError:
        return dict(DEFAULT_API)


def protect(value, decrypt=False):
    if os.name != 'nt':
        raise ValueError('API 密钥保存需要 Windows 用户加密服务。')

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    raw = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(raw, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError('无法读取或保存 API 密钥，请在当前 Windows 账户下重新填写。')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def load_api_key(options=None):
    options = options or load_api_settings()
    blob = read_settings().get('online_translation_secret', '')
    if not blob:
        return ''
    try:
        stored = json.loads(protect(base64.b64decode(blob, validate=True), decrypt=True))
        if stored['endpoint'] != completion_url(options['base_url']):
            return ''
        key = stored['key']
        return key if isinstance(key, str) else ''
    except (ValueError, TypeError, KeyError):
        return ''


def validate_api_key(key):
    if not isinstance(key, str) or not key.strip() or len(key) > 8192 or any(ord(char) < 33 or ord(char) > 126 for char in key):
        raise ValueError('请填写有效的 API 密钥。')
    return key


def save_api_settings(options, key):
    options = normalize_api(options)
    key = validate_api_key(key)
    stored = json.dumps({'endpoint': completion_url(options['base_url']), 'key': key}).encode('utf-8')
    encrypted = base64.b64encode(protect(stored)).decode('ascii')
    data = read_settings()
    data.update(online_translation=options, online_translation_secret=encrypted)
    atomic_write(config.SETTINGS, json.dumps(data, ensure_ascii=False, indent=2))


def clear_api_key():
    data = read_settings()
    data.pop('online_translation_secret', None)
    atomic_write(config.SETTINGS, json.dumps(data, ensure_ascii=False, indent=2))


def api_issue():
    try:
        options = normalize_api(load_api_settings())
        validate_api_key(load_api_key(options))
    except ValueError as exc:
        return str(exc)
    return ''


def load_translation_backend():
    return 'online' if read_settings().get('translation_backend') == 'online' else 'local'


def save_translation_backend(backend):
    data = read_settings()
    data['translation_backend'] = 'online' if backend == 'online' else 'local'
    atomic_write(config.SETTINGS, json.dumps(data, ensure_ascii=False, indent=2))
