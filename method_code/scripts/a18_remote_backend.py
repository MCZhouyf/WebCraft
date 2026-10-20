"""Credential-safe transport and globally guarded concurrent remote calls."""
from __future__ import annotations

import hashlib
import http.client
import importlib.util
import json
import os
import socket
import stat
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from arbiter.llm.gateway import CanaryFailed, GatewayError
from scripts.a18_local_backend import CANARY_MESSAGES, DEADLINE


class RemoteError(GatewayError):
    def __init__(self, code, *, retryable=False):
        super().__init__(code)
        self.code, self.retryable = code, retryable


def load_private_config(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise RemoteError('private_configuration_permissions_required')
        with os.fdopen(fd, closefd=False) as handle:
            config = json.load(handle)
    except (ValueError, OSError):
        raise RemoteError('private_configuration_invalid') from None
    finally:
        os.close(fd)
    if (not isinstance(config, dict) or not isinstance(config.get('model'), str)
            or not config['model'].strip() or '://' in config['model']
            or any(c in config['model'] for c in '\r\n')):
        raise RemoteError('private_configuration_missing_model')
    return config


def load_author_config():
    """Use the existing read-only loader; the supplied key stays in a memfd."""
    source = Path('/root/autodl-tmp/webcraft/reports/main/post_review/second_family_qwen/transport.py')
    spec = importlib.util.spec_from_file_location('_a18_author_transport', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        fd = module.configure_from_author_message()
    except Exception:
        raise RemoteError('author_memory_configuration_unavailable') from None
    config = {'model': os.environ['QWEN_MODEL'], 'ssh': {
        'host': os.environ['QWEN_JUMP_HOST'], 'port': int(os.environ['QWEN_JUMP_PORT']),
        'user': os.environ['QWEN_JUMP_USER'], 'remote_port': int(os.environ['QWEN_LOCAL_PORT']),
        'key_path': f'/proc/{os.getpid()}/fd/{fd}', 'key_fd': fd,
    }}
    return config, fd


def clear_author_config(fd):
    if fd is not None:
        os.close(fd)
    for name in ('QWEN_MODEL', 'QWEN_JUMP_HOST', 'QWEN_JUMP_PORT', 'QWEN_JUMP_USER',
                 'QWEN_LOCAL_PORT', 'QWEN_KEY_FD'):
        os.environ.pop(name, None)


class Endpoint:
    def __init__(self, config, *, timeout=240):
        self.process = None
        self.secret = config.get('api_key', '')
        self.model = config['model']
        self.timeout = timeout
        if 'ssh' in config:
            ssh = config['ssh']
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            command = ['ssh', '-N', '-T', '-i', ssh['key_path'], '-o', 'IdentitiesOnly=yes',
                       '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                       '-o', 'ExitOnForwardFailure=yes', '-o', 'ConnectTimeout=10',
                       '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3',
                       '-p', str(ssh['port']), '-L',
                       f"127.0.0.1:{port}:127.0.0.1:{int(ssh['remote_port'])}",
                       ssh['user'] + '@' + ssh['host']]
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                            pass_fds=(ssh['key_fd'],) if 'key_fd' in ssh else (),
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.url = urlsplit(f'http://127.0.0.1:{port}/v1')
            for _ in range(40):
                if self.process.poll() is not None:
                    raise RemoteError('owned_local_forward_exited')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=0.5):
                        break
                except OSError:
                    time.sleep(0.5)
            else:
                self.close()
                raise RemoteError('owned_local_forward_unreachable')
        else:
            self.url = urlsplit(config.get('base_url', ''))
        if (self.url.scheme not in {'http', 'https'} or not self.url.hostname
                or self.url.username or self.url.password or self.url.query or self.url.fragment):
            self.close()
            raise RemoteError('private_endpoint_invalid')

    def request(self, suffix, payload=None):
        if suffix not in {'models', 'chat/completions'}:
            raise RemoteError('route_not_allowed')
        if self.process is not None and self.process.poll() is not None:
            raise RemoteError('owned_local_forward_exited')
        factory = http.client.HTTPSConnection if self.url.scheme == 'https' else http.client.HTTPConnection
        connection = factory(self.url.hostname, self.url.port, timeout=self.timeout)
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        headers = {'Content-Type': 'application/json'}
        if self.secret:
            headers['Authorization'] = 'Bearer ' + self.secret
        try:
            connection.request('GET' if body is None else 'POST',
                               self.url.path.rstrip('/') + '/' + suffix, body, headers)
            response = connection.getresponse()
            content = response.read()
        except (TimeoutError, socket.timeout):
            raise RemoteError('request_or_queue_timeout') from None
        except (OSError, http.client.HTTPException):
            raise RemoteError('transport_error', retryable=True) from None
        finally:
            connection.close()
        if response.status != 200:
            raise RemoteError('http_' + str(response.status), retryable=response.status in {429, 500, 502, 503})
        try:
            result = json.loads(content)
        except ValueError:
            raise RemoteError('invalid_response_envelope') from None
        return result

    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.secret = ''
        self.url = None


class Coordinator:
    def __init__(self, endpoint, out):
        self.endpoint, self.out, self.model = endpoint, out, endpoint.model
        self.condition = threading.Condition()
        self.write_lock = threading.Lock()
        self.failed = None
        self.started = self.finished = self.consecutive_errors = 0
        self.next_canary = 200
        self.canary_running = False
        self.calls = self.task_calls = self.input_tokens = self.output_tokens = 0

    def append(self, path, row):
        with self.write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a') as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
                handle.flush()

    def fail(self, code):
        with self.condition:
            self.failed = self.failed or code
            self.condition.notify_all()

    def raw(self, call_type, messages, *, canary=False, audit_path=None, **kwargs):
        if (kwargs.get('temperature') != 0.0 or kwargs.get('thinking') != 'disabled'
                or not kwargs.get('json_object') or kwargs.get('max_tokens') not in {1024, 4096}):
            raise RemoteError('unregistered_decoding')
        if time.time() >= DEADLINE:
            self.fail('registered_deadline')
            raise RemoteError('registered_deadline')
        payload = {'model': self.model, 'messages': messages, 'temperature': 0.0,
                   'max_tokens': kwargs['max_tokens'], 'response_format': {'type': 'json_object'},
                   'chat_template_kwargs': {'enable_thinking': False}}
        request_hash = hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        self.append(audit_path or self.out / 'canary_prompts.jsonl',
                    {'record_type': 'full_prompt', 'call_type': call_type, 'messages': messages,
                     'request_sha256': request_hash, 'parameters': kwargs, 'model': self.model})
        for attempt in range(3):
            with self.condition:
                if self.failed:
                    raise RemoteError('global_circuit_open')
            start = time.monotonic()
            try:
                result = self.endpoint.request('chat/completions', payload)
                if result.get('model') != self.model:
                    raise RemoteError('response_model_identifier_mismatch')
                choice = (result.get('choices') or [{}])[0]
                content = (choice.get('message') or {}).get('content')
                usage = result.get('usage') or {}
                if (not isinstance(content, str) or any(type(usage.get(k)) is not int or usage[k] < 0
                        for k in ('prompt_tokens', 'completion_tokens'))):
                    raise RemoteError('invalid_response_envelope')
                response = {'model': self.model, 'content': content, 'usage': usage,
                            'finish_reason': choice.get('finish_reason'), 'cost': None}
                with self.condition:
                    self.consecutive_errors = 0
                    self.calls += 1
                    self.task_calls += int(not canary)
                    self.input_tokens += usage['prompt_tokens']
                    self.output_tokens += usage['completion_tokens']
                self.append(self.out / 'ledger.jsonl', {'record_type': 'generation', 'status': 'ok',
                            'timestamp': datetime.now(timezone.utc).isoformat(), 'model': self.model,
                            'call_type': call_type, 'canary': canary, 'attempt': attempt + 1,
                            'usage': usage, 'elapsed_seconds': time.monotonic() - start,
                            'request_sha256': request_hash, 'finish_reason': response['finish_reason']})
                self.append(audit_path or self.out / 'canary_prompts.jsonl',
                            {'record_type': 'full_response', 'call_type': call_type, **response})
                return response
            except RemoteError as exc:
                with self.condition:
                    self.consecutive_errors += 1
                    consecutive = self.consecutive_errors
                self.append(self.out / 'ledger.jsonl', {'record_type': 'infrastructure_error',
                            'code': exc.code, 'attempt': attempt + 1, 'call_type': call_type,
                            'request_sha256': request_hash, 'canary': canary})
                if not exc.retryable or attempt == 2 or consecutive >= 3:
                    self.fail(exc.code)
                    raise
                time.sleep((2, 5)[attempt])

    def canary(self):
        try:
            reply = self.raw('canary', CANARY_MESSAGES, canary=True, temperature=0.0,
                             max_tokens=1024, json_object=True, thinking='disabled')
            valid = reply['finish_reason'] == 'stop' and json.loads(reply['content']) == {
                'action': 'inventory', 'args': {}, 'intent': 'commit'}
            if not valid:
                raise ValueError
        except (GatewayError, ValueError, TypeError):
            self.fail('canary_failed')
            raise CanaryFailed('canary_failed') from None

    def reserve(self):
        while True:
            leader = False
            with self.condition:
                if self.failed:
                    raise RemoteError('global_circuit_open')
                if self.started < self.next_canary:
                    self.started += 1
                    return
                if self.finished == self.started and not self.canary_running:
                    self.canary_running, leader = True, True
                else:
                    self.condition.wait()
            if leader:
                try:
                    self.canary()
                finally:
                    with self.condition:
                        if not self.failed:
                            self.next_canary += 200
                        self.canary_running = False
                        self.condition.notify_all()


class WorkerClient:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.task_calls = self.task_input_tokens = self.task_output_tokens = 0
        self.audit_path = None

    def chat(self, call_type, messages, **kwargs):
        self.coordinator.reserve()
        try:
            response = self.coordinator.raw(call_type, messages, audit_path=self.audit_path, **kwargs)
            self.task_calls += 1
            self.task_input_tokens += response['usage']['prompt_tokens']
            self.task_output_tokens += response['usage']['completion_tokens']
            return response
        finally:
            with self.coordinator.condition:
                self.coordinator.finished += 1
                self.coordinator.condition.notify_all()
