#!/usr/bin/env python3
"""Single, non-retrying compatibility probe; no secret values or response text logged."""
import fcntl
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path('/root/autodl-tmp/webcraft')
REPORT = ROOT / 'reports/llm_minimal_probe.json'
JOURNAL = ROOT / 'logs/stages/llm_probe_budget.json'
SOURCES = [
    'https://docs.bigmodel.cn/cn/api/introduction',
    'https://docs.bigmodel.cn/cn/guide/capabilities/thinking',
]


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as handle:
        json.dump(value, handle, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def number(name):
    value = Decimal(os.environ[name])
    if not value.is_finite() or value < 0:
        raise ValueError('invalid numeric configuration')
    return value


def usage_fields(body):
    usage = body.get('usage')
    if not isinstance(usage, dict):
        raise ValueError('missing usage')
    names = ('prompt_tokens', 'completion_tokens', 'total_tokens')
    values = {name: usage.get(name) for name in names}
    if any(type(value) is not int or value < 0 for value in values.values()):
        raise ValueError('invalid usage')
    details = usage.get('prompt_tokens_details', {})
    cached = details.get('cached_tokens') if isinstance(details, dict) else None
    if type(cached) is not int or not 0 <= cached <= values['prompt_tokens']:
        raise ValueError('missing or invalid cache accounting')
    if values['total_tokens'] != values['prompt_tokens'] + values['completion_tokens']:
        raise ValueError('inconsistent usage')
    return {**values, 'cached_tokens': cached}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    os.umask(0o077)
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with (JOURNAL.parent / 'llm_probe.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # A previous started request may have been billed even if its client crashed.
        if JOURNAL.exists() or REPORT.exists():
            print(json.dumps({'status': 'STOP_PRIOR_PROBE_REQUIRES_REVIEW'}))
            return 2
        required = ('WC_LLM_PROVIDER', 'WC_LLM_MODEL', 'WC_LLM_BASE_URL',
                    'WC_LLM_API_KEY', 'WC_LLM_CURRENCY', 'WC_LLM_BUDGET',
                    'WC_LLM_PRICE_IN_PER_MTOK', 'WC_LLM_PRICE_OUT_PER_MTOK',
                    'WC_LLM_PRICE_CACHED_IN_PER_MTOK', 'WC_LLM_THINKING')
        if any(not os.environ.get(name) for name in required):
            raise ValueError('missing configuration')
        if (ROOT / 'env/llm.env').stat().st_mode & 0o077:
            raise ValueError('private file permissions')
        if not (ROOT / 'env/crawl_contact.txt').read_text().strip():
            raise ValueError('missing contact')
        expected = {'WC_LLM_PROVIDER': 'zhipu', 'WC_LLM_MODEL': 'glm-5.2',
                    'WC_LLM_CURRENCY': 'CNY', 'WC_LLM_THINKING': 'disabled'}
        if any(os.environ[name] != value for name, value in expected.items()):
            raise ValueError('configuration differs from authorized test')
        base = os.environ['WC_LLM_BASE_URL'].rstrip('/')
        if not os.environ.get('WC_LLM_AUTHORIZED_BASE_URL') or base != os.environ['WC_LLM_AUTHORIZED_BASE_URL'].rstrip('/'):
            raise ValueError('endpoint differs from authorized test')
        if os.environ.get('CUDA_VISIBLE_DEVICES') != '':
            raise ValueError('CUDA must be disabled')
        budget = number('WC_LLM_BUDGET')
        price_in = number('WC_LLM_PRICE_IN_PER_MTOK')
        price_out = number('WC_LLM_PRICE_OUT_PER_MTOK')
        price_cached = number('WC_LLM_PRICE_CACHED_IN_PER_MTOK')
        reserve = (Decimal(1024) * max(price_in, price_cached) + 16 * price_out) / 1000000
        if budget <= 0 or reserve >= budget * Decimal('0.8'):
            raise ValueError('budget gate')
        journal = {'status': 'reserved_before_request', 'reserved_amount': str(reserve),
                   'spent_known_amount': '0', 'billing_reconciled': False,
                   'scope': 'This first probe only; future clients must import this ledger.'}
        report = {'timestamp': datetime.now(timezone.utc).isoformat(),
                  'docs_sources': SOURCES, 'max_output_tokens': 16,
                  'requests_attempted': 0, 'automatic_retries': 0,
                  'endpoint_model_substitution': False, 'status': 'started',
                  'thinking_parameter_documented': True, 'passed': False}
        write_json(JOURNAL, journal)
        write_json(REPORT, report)
        payload = {'model': os.environ['WC_LLM_MODEL'],
                   'messages': [{'role': 'user', 'content': 'Reply only OK.'}],
                   'thinking': {'type': 'disabled'}, 'max_tokens': 16, 'stream': False}
        request = urllib.request.Request(base + '/chat/completions',
            data=json.dumps(payload).encode(), method='POST', headers={
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + os.environ['WC_LLM_API_KEY']})
        opener = urllib.request.build_opener(NoRedirect())
        start = time.monotonic()
        body = None
        report['requests_attempted'] = 1
        write_json(REPORT, report)
        try:
            with opener.open(request, timeout=45) as response:
                report['http_status'] = response.status
                body = json.loads(response.read(1024 * 1024))
            usage = usage_fields(body)
            report['usage'] = usage
            cost = ((usage['prompt_tokens'] - usage['cached_tokens']) * price_in
                    + usage['cached_tokens'] * price_cached
                    + usage['completion_tokens'] * price_out) / 1000000
            journal.update(status='usage_accounted', spent_known_amount=str(cost),
                           reserved_amount='0', billing_reconciled=True)
            message = body['choices'][0]['message']
            report['model_matches_requested'] = body.get('model') == os.environ['WC_LLM_MODEL']
            report['answer_nonempty'] = bool(message.get('content'))
            report['reasoning_content_absent'] = not bool(message.get('reasoning_content'))
            report['output_limit_respected'] = usage['completion_tokens'] <= 16
            report['passed'] = all(report[name] for name in (
                'model_matches_requested', 'answer_nonempty',
                'reasoning_content_absent', 'output_limit_respected'))
            report['status'] = 'PASS' if report['passed'] else 'STOP_RESPONSE_VALIDATION'
        except urllib.error.HTTPError as exc:
            report.update(status='STOP_HTTP_ERROR', http_status=exc.code)
            try:
                error_body = json.loads(exc.read(65536))
                code = str(error_body.get('error', {}).get('code', ''))
                if re.fullmatch(r'[0-9]{1,12}', code):
                    report['provider_error_code'] = code
            except Exception:
                pass
        except Exception as exc:
            # Never serialize exception messages: libraries may include URLs or credentials.
            report.update(status='STOP_RESPONSE_OR_NETWORK_ERROR', error_class=type(exc).__name__)
        report['wall_seconds'] = round(time.monotonic() - start, 3)
        report['billing_reconciled'] = journal['billing_reconciled']
        write_json(JOURNAL, journal)
        write_json(REPORT, report)
        print(json.dumps(report))
        return 0 if report['passed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'status': 'STOP_LOCAL_VALIDATION', 'error_class': type(exc).__name__}))
        raise SystemExit(2)
