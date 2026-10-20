"""Credential-isolated A17 canaries; never print secrets or provider addresses."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from arbiter.llm.openai_probe import OpenAIProbeClient

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = Path('/root/.config/scope_credentials/glm52_pending.json')
OUT = ROOT / 'artifacts/post_review/a17_ext_agents_20261017/preflight'
SECRET_ENV = ('WC_LLM_API_KEY', 'WC_LLM_API_KEYS', 'WC_LLM_BASE_URL', 'WC_LLM_MODEL', 'ARB_LLM_BUDGET')


def configure(config, slot):
    if config.get('model') != 'glm-5.2' or not config.get('enabled') or not config.get('allow_api_calls'):
        raise ValueError('private_configuration_not_enabled_for_registered_model')
    os.environ.pop('WC_LLM_API_KEYS', None)
    os.environ.update(WC_LLM_API_KEY=config['api_keys'][slot - 1], WC_LLM_BASE_URL=config['base_url'],
                      WC_LLM_MODEL='glm-5.2', ARB_LLM_BUDGET='1000000', WC_LLM_PRICE_IN_PER_MTOK='8',
                      WC_LLM_PRICE_CACHED_IN_PER_MTOK='2', WC_LLM_PRICE_OUT_PER_MTOK='28',
                      WC_LLM_CURRENCY='CNY')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--slot', type=int, required=True, choices=range(1, 7))
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    ledger = OUT / f'slot_{args.slot}_ledger.jsonl'
    result = {'slot': args.slot, 'status': 'not_available', 'requested_model': 'glm-5.2'}
    config = {}
    try:
        config = json.loads(PRIVATE.read_text())
        configure(config, args.slot)
        client = OpenAIProbeClient(ledger, model='glm-5.2')
        client.validate_canary(thinking='disabled')
        result.update(status='passed', server_model_identifiers=sorted(client.returned_models),
                      successful_provider_responses=client.calls, input_tokens=client.input_tokens,
                      output_tokens=client.output_tokens, token_rate_estimated_CNY=client.priced_cost)
    except Exception as exc:
        result.update(status='failed', error_type=type(exc).__name__)
        if ledger.exists():
            rows = [json.loads(s) for s in ledger.read_text().splitlines() if s.strip()]
            result['http_statuses'] = sorted({r['http_status'] for r in rows if type(r.get('http_status')) is int})
            result['server_model_identifiers'] = sorted({r['model'] for r in rows if isinstance(r.get('model'), str)})
    finally:
        for name in SECRET_ENV:
            os.environ.pop(name, None)
        config.clear()
    (OUT / f'slot_{args.slot}_status.json').write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
