#!/usr/bin/env python3
"""One-shot GLM-5.2 probe through the author-specified OpenAI-compatible gateway."""
import fcntl
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from openai import OpenAI

ROOT = Path('/root/autodl-tmp/webcraft')
REPORT = ROOT / 'reports/sensenova_glm52_probe.json'
LEDGER = ROOT / 'logs/stages/sensenova_glm52_budget.json'
LOCK = ROOT / 'logs/stages/sensenova_glm52_probe.lock'


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as handle:
        json.dump(value, handle, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def configured_decimal(name):
    value = Decimal(os.environ[name])
    if not value.is_finite() or value < 0:
        raise ValueError('invalid numeric configuration')
    return value


def main():
    os.umask(0o077)
    with LOCK.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if REPORT.exists() or LEDGER.exists():
            print(json.dumps({'status': 'STOP_PRIOR_GATEWAY_PROBE_REQUIRES_REVIEW'}))
            return 2

        required = ('WC_LLM_PROVIDER', 'WC_LLM_MODEL', 'WC_LLM_BASE_URL',
                    'WC_LLM_API_KEY', 'WC_LLM_CURRENCY', 'WC_LLM_BUDGET',
                    'WC_LLM_PRICE_IN_PER_MTOK', 'WC_LLM_PRICE_OUT_PER_MTOK',
                    'WC_LLM_PRICE_CACHED_IN_PER_MTOK', 'WC_LLM_THINKING')
        if any(not os.environ.get(name) for name in required):
            raise ValueError('missing configuration')
        expected = {
            'WC_LLM_PROVIDER': 'sensenova-openai-compatible',
            'WC_LLM_MODEL': 'glm-5.2',
            'WC_LLM_BASE_URL': os.environ['WC_LLM_AUTHORIZED_BASE_URL'],
            'WC_LLM_CURRENCY': 'CNY',
            'WC_LLM_THINKING': 'disabled',
        }
        if any(os.environ[name] != value for name, value in expected.items()):
            raise ValueError('configuration differs from authorized gateway test')
        if (ROOT / 'env/llm.env').stat().st_mode & 0o077:
            raise ValueError('private file permissions')
        if os.environ.get('CUDA_VISIBLE_DEVICES') != '':
            raise ValueError('CUDA must be disabled')

        budget = configured_decimal('WC_LLM_BUDGET')
        price_in = configured_decimal('WC_LLM_PRICE_IN_PER_MTOK')
        price_out = configured_decimal('WC_LLM_PRICE_OUT_PER_MTOK')
        price_cached = configured_decimal('WC_LLM_PRICE_CACHED_IN_PER_MTOK')
        reserve = (Decimal(1024) * max(price_in, price_cached)
                   + Decimal(16) * price_out) / Decimal(1_000_000)
        if budget <= 0 or reserve >= budget * Decimal('0.8'):
            raise ValueError('budget gate')

        report = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'interface': 'OpenAI Python SDK chat.completions',
            'requested_model': 'glm-5.2',
            'thinking_parameter': {'type': 'disabled'},
            'max_output_tokens': 16,
            'requests_attempted': 0,
            'automatic_retries': 0,
            'key_fallbacks': 0,
            'endpoint_or_model_substitution': False,
            'response_content_stored': False,
            'passed': False,
            'status': 'started',
        }
        ledger = {
            'status': 'reserved_before_request',
            'reserved_amount': str(reserve),
            'spent_known_amount': '0',
            'billing_reconciled': False,
            'currency': 'CNY',
        }
        write_json(REPORT, report)
        write_json(LEDGER, ledger)

        client = OpenAI(
            base_url=os.environ['WC_LLM_BASE_URL'],
            api_key=os.environ['WC_LLM_API_KEY'],
            max_retries=0,
            timeout=45.0,
        )
        started = time.monotonic()
        report['requests_attempted'] = 1
        write_json(REPORT, report)
        try:
            response = client.chat.completions.create(
                model=os.environ['WC_LLM_MODEL'],
                messages=[{'role': 'user', 'content': 'Reply only OK.'}],
                max_tokens=16,
                stream=False,
                extra_body={'thinking': {'type': 'disabled'}},
            )
            usage = response.usage
            if usage is None:
                raise ValueError('missing usage')
            prompt = usage.prompt_tokens
            completion = usage.completion_tokens
            total = usage.total_tokens
            if any(type(value) is not int or value < 0
                   for value in (prompt, completion, total)):
                raise ValueError('invalid usage')
            if total != prompt + completion:
                raise ValueError('inconsistent usage')
            details = getattr(usage, 'prompt_tokens_details', None)
            cached = getattr(details, 'cached_tokens', None) if details else None
            cache_reported = type(cached) is int and 0 <= cached <= prompt
            if not cache_reported:
                cached = 0
            # Missing cache detail is billed conservatively at the full input rate.
            cost = ((prompt - cached) * price_in + cached * price_cached
                    + completion * price_out) / Decimal(1_000_000)
            ledger.update(status='usage_accounted', reserved_amount='0',
                          spent_known_amount=str(cost), billing_reconciled=True,
                          cache_detail_reported=cache_reported,
                          missing_cache_billed_at_full_input_rate=not cache_reported)
            message = response.choices[0].message
            report.update(
                status='PASS',
                passed=True,
                returned_model_present=bool(response.model),
                returned_model_matches_requested=response.model == os.environ['WC_LLM_MODEL'],
                answer_nonempty=bool(message.content),
                reasoning_content_absent=not bool(getattr(message, 'reasoning_content', None)),
                usage={
                    'prompt_tokens': prompt,
                    'completion_tokens': completion,
                    'total_tokens': total,
                    'cached_tokens': cached,
                    'cache_detail_reported': cache_reported,
                },
            )
            report['passed'] = all((report['returned_model_present'],
                                    report['answer_nonempty'],
                                    report['reasoning_content_absent']))
            if not report['passed']:
                report['status'] = 'STOP_RESPONSE_VALIDATION'
        except Exception as exc:
            report.update(
                status='STOP_GATEWAY_OR_MODEL_ERROR',
                error_class=type(exc).__name__,
                http_status=getattr(exc, 'status_code', None),
            )
        finally:
            client.close()

        report['wall_seconds'] = round(time.monotonic() - started, 3)
        report['billing_reconciled'] = ledger['billing_reconciled']
        write_json(LEDGER, ledger)
        write_json(REPORT, report)
        print(json.dumps(report))
        return 0 if report['passed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'status': 'STOP_LOCAL_VALIDATION',
                          'error_class': type(exc).__name__}))
        raise SystemExit(2)
