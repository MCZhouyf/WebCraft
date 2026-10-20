"""Task-owned, single-copy local Qwen inference for the A18 amendment."""
from __future__ import annotations

import gc
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from arbiter.llm.gateway import CanaryFailed, GatewayError
from scripts.prepare_a18_local_model import SNAPSHOT, VENDOR

IDENTITY = 'Qwen/Qwen3-14B@40c069824f4251a91eefaf281ebe4c544efd3e18:nf4-dq-bf16'
DEADLINE = datetime(2026, 10, 20, tzinfo=timezone.utc).timestamp()
CANARY_MESSAGES = [
    {'role': 'system', 'content': 'Return only this exact JSON object: {"action":"inventory","args":{},"intent":"commit"}.'},
    {'role': 'user', 'content': 'Return the exact object now.'},
]


def append(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')


def gpu_state():
    output = subprocess.check_output(
        ['nvidia-smi', '--query-gpu=index,name,memory.total,memory.free', '--format=csv,noheader,nounits'], text=True)
    rows = [line.strip().split(', ') for line in output.splitlines() if line.strip()]
    if len(rows) != 1:
        raise GatewayError('requires_one_unambiguous_local_GPU')
    values = rows[0]
    active = subprocess.check_output(
        ['nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader,nounits'], text=True)
    foreign = [int(p.strip()) for p in active.splitlines() if p.strip().isdigit() and int(p.strip()) != os.getpid()]
    return {'index': int(values[0]), 'name': values[1], 'total_MiB': int(values[2]),
            'free_MiB': int(values[3]), 'foreign_compute_processes': len(foreign)}


def validate_request(kwargs):
    if kwargs.get('temperature', 0.0) != 0.0 or kwargs.get('thinking') != 'disabled' or not kwargs.get('json_object'):
        raise GatewayError('local_request_differs_from_registered_decoding')
    if kwargs.get('max_tokens') not in {1024, 4096}:
        raise GatewayError('unregistered_local_completion_limit')


def check_context(input_tokens, output_tokens, maximum):
    if input_tokens + output_tokens > maximum:
        raise GatewayError('context_overflow_no_truncation_allowed')


class LocalClient:
    def __init__(self, directory):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        if str(VENDOR) not in sys.path:
            sys.path.insert(0, str(VENDOR))
        from lmformatenforcer.integrations.transformers import build_token_enforcer_tokenizer_data

        self.directory = directory
        self.ledger = directory / 'ledger.jsonl'
        self.audit_path = directory / 'canary_prompts.jsonl'
        self.model = None
        self.torch = torch
        self.calls = self.task_calls = self.input_tokens = self.output_tokens = 0
        self.task_input_tokens = self.task_output_tokens = 0
        self.priced_cost = self.task_priced_cost = None
        self.returned_models = set()
        self._last_canary = 0
        self._blocked = False
        state = gpu_state()
        if state['foreign_compute_processes'] or state['free_MiB'] < 11 * 1024:
            raise GatewayError('GPU_admission_gate_failed_no_other_process_touched')
        self.admission = state
        torch.set_num_threads(1)
        torch.manual_seed(20261017)
        torch.cuda.set_device(0)
        torch.cuda.init()
        torch.cuda.set_per_process_memory_fraction(0.90, 0)
        torch.cuda.reset_peak_memory_stats(0)
        self.tokenizer = AutoTokenizer.from_pretrained(SNAPSHOT, local_files_only=True)
        started = time.monotonic()
        self.model = AutoModelForCausalLM.from_pretrained(
            SNAPSHOT, local_files_only=True,
            quantization_config=BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type='nf4',
                bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16),
            torch_dtype=torch.bfloat16, attn_implementation='sdpa',
            device_map={'': 0}, low_cpu_mem_usage=True,
        )
        self.model.requires_grad_(False)
        self.model.eval()
        self.model.generation_config.temperature = None
        self.model.generation_config.top_p = None
        self.model.generation_config.top_k = None
        self.context_limit = int(self.model.config.max_position_embeddings)
        self.tokenizer_data = build_token_enforcer_tokenizer_data(self.tokenizer)
        # Preserve tokenizer Unicode while excluding raw JSON control characters.
        self.tokenizer_data.tokenizer_alphabet = ''.join(
            sorted(c for c in set(self.tokenizer_data.tokenizer_alphabet) if ord(c) >= 32))
        self.load_seconds = time.monotonic() - started

    def validate_canary(self, thinking='disabled'):
        try:
            response = self.chat('canary', CANARY_MESSAGES, temperature=0.0,
                                 max_tokens=1024, json_object=True, thinking=thinking, canary=True)
            if response['finish_reason'] != 'stop' or json.loads(response['content']) != {
                    'action': 'inventory', 'args': {}, 'intent': 'commit'}:
                raise ValueError('canary_answer_mismatch')
        except Exception:
            self._blocked = True
            append(self.ledger, {'record_type': 'canary_validation', 'status': 'failed'})
            raise CanaryFailed('local_canary_failed') from None
        self._last_canary = self.task_calls
        append(self.ledger, {'record_type': 'canary_validation', 'status': 'passed',
                             'timestamp': datetime.now(timezone.utc).isoformat()})

    def chat(self, call_type, messages, **kwargs):
        from lmformatenforcer import JsonSchemaParser, CharacterLevelParserConfig
        from lmformatenforcer.integrations.transformers import build_transformers_prefix_allowed_tokens_fn
        from transformers import StoppingCriteria, StoppingCriteriaList

        validate_request(kwargs)
        if self._blocked or time.time() >= DEADLINE:
            raise GatewayError('local_circuit_or_deadline_stop')
        if gpu_state()['foreign_compute_processes']:
            self._blocked = True
            raise GatewayError('foreign_GPU_contention_stop_own_task_only')
        canary = bool(kwargs.get('canary'))
        if not canary and self.task_calls - self._last_canary >= 200:
            self.validate_canary()
        started = time.monotonic()
        limit = int(kwargs['max_tokens'])
        serialized = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        request_sha = hashlib.sha256(serialized.encode()).hexdigest()
        if self.audit_path:
            append(self.audit_path, {'record_type': 'full_prompt', 'call_type': call_type,
                                    'messages': messages, 'parameters': kwargs,
                                    'request_sha256': request_sha, 'model': IDENTITY,
                                    'identity_source': 'local_weight_lock_not_server'})
        class Timer(StoppingCriteria):
            def __call__(self, input_ids, scores, **unused):
                return time.monotonic() - started >= 600 or time.time() >= DEADLINE
        inputs = self.tokenizer.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True, enable_thinking=False,
            return_dict=True, return_tensors='pt',
        ).to('cuda:0')
        n_input = int(inputs['input_ids'].shape[1])
        check_context(n_input, limit, self.context_limit)
        parser = JsonSchemaParser({'type': 'object'}, config=CharacterLevelParserConfig(
            alphabet=self.tokenizer_data.tokenizer_alphabet))
        prefix = build_transformers_prefix_allowed_tokens_fn(self.tokenizer_data, parser)
        try:
            with self.torch.inference_mode():
                generated = self.model.generate(
                    **inputs, max_new_tokens=limit, do_sample=False, num_beams=1,
                    pad_token_id=self.tokenizer.eos_token_id,
                    prefix_allowed_tokens_fn=prefix, stopping_criteria=StoppingCriteriaList([Timer()]),
                )
            elapsed = time.monotonic() - started
            if elapsed >= 600 or time.time() >= DEADLINE:
                raise GatewayError('local_generation_timeout_no_scored_answer')
            output_ids = generated[0, n_input:]
            content = self.tokenizer.decode(output_ids, skip_special_tokens=True)
            n_output = int(output_ids.shape[0])
            eos = self.model.generation_config.eos_token_id
            eos = eos if isinstance(eos, list) else [eos]
            finish = 'stop' if n_output and int(output_ids[-1]) in eos else 'length'
            response = {'model': IDENTITY, 'content': content, 'finish_reason': finish,
                        'usage': {'prompt_tokens': n_input, 'completion_tokens': n_output}, 'cost': None}
            self.calls += 1
            self.input_tokens += n_input
            self.output_tokens += n_output
            if not canary:
                self.task_calls += 1
                self.task_input_tokens += n_input
                self.task_output_tokens += n_output
            self.returned_models.add(IDENTITY)
            append(self.ledger, {'record_type': 'generation', 'status': 'ok',
                                 'timestamp': datetime.now(timezone.utc).isoformat(), 'call_type': call_type,
                                 'model': IDENTITY, 'canary': canary, 'elapsed_seconds': elapsed,
                                 'usage': response['usage'], 'finish_reason': finish,
                                 'request_sha256': request_sha,
                                 'identity_source': 'local_weight_lock_not_server'})
            if self.audit_path:
                append(self.audit_path, {'record_type': 'full_response', 'call_type': call_type, **response})
            return response
        except Exception as exc:
            append(self.ledger, {'record_type': 'generation', 'status': 'infrastructure_abort',
                                 'call_type': call_type, 'model': IDENTITY,
                                 'error_type': type(exc).__name__, 'request_sha256': request_sha})
            if isinstance(exc, GatewayError):
                raise
            raise GatewayError('local_generation_' + type(exc).__name__) from None
        finally:
            if 'generated' in locals():
                del generated
            if 'output_ids' in locals():
                del output_ids
            del inputs, prefix, parser
            self.torch.cuda.empty_cache()

    def close(self):
        peaks = {'allocated': self.torch.cuda.max_memory_allocated(0),
                 'reserved': self.torch.cuda.max_memory_reserved(0)}
        self.model = None
        gc.collect()
        self.torch.cuda.empty_cache()
        return {'own_CUDA_peak_bytes': peaks, 'owned_model_unloaded': True,
                'own_allocated_after_bytes': self.torch.cuda.memory_allocated(0),
                'own_reserved_after_bytes': self.torch.cuda.memory_reserved(0)}
