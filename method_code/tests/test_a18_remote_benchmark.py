"""Synthetic concurrency selection only; no endpoint calls."""
import os
import ctypes

from scripts.a18_remote_backend import clear_author_config
from scripts.benchmark_a18_remote import select_workers


def test_smallest_near_best_is_selected():
    assert select_workers([{'workers': 1, 'requests_per_second': 1},
                           {'workers': 2, 'requests_per_second': 1.95},
                           {'workers': 4, 'requests_per_second': 2}]) == 2
    assert select_workers([{'workers': 1, 'requests_per_second': 1},
                           {'workers': 2, 'requests_per_second': 1.8},
                           {'workers': 4, 'requests_per_second': 2.1}]) == 4


def test_memfd_credentials_and_task_environment_are_cleaned(monkeypatch):
    libc = ctypes.CDLL(None)
    libc.memfd_create.argtypes = [ctypes.c_char_p, ctypes.c_uint]
    libc.memfd_create.restype = ctypes.c_int
    fd = libc.memfd_create(b'synthetic-test-only', 0)
    assert fd >= 0
    for name in ('QWEN_MODEL', 'QWEN_JUMP_HOST', 'QWEN_JUMP_PORT', 'QWEN_JUMP_USER',
                 'QWEN_LOCAL_PORT', 'QWEN_KEY_FD'):
        monkeypatch.setenv(name, 'synthetic-test-only')
    clear_author_config(fd)
    assert 'QWEN_KEY_FD' not in os.environ
    try:
        os.fstat(fd)
    except OSError:
        pass
    else:
        raise AssertionError('anonymous_descriptor_not_closed')
