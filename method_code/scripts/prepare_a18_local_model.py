"""CPU-only model integrity freeze; no model loading or generation."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = Path('/root/autodl-tmp/schema2weights/rq07/hf/hub/models--Qwen--Qwen3-14B/snapshots/40c069824f4251a91eefaf281ebe4c544efd3e18')
VENDOR = Path('/root/autodl-tmp/arbiter_runtime/a18_vendor')
LOCK = ROOT / 'prereg/a18_local_model_lock_20261017.json'


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def main():
    if LOCK.exists():
        raise RuntimeError('local_model_lock_already_exists_no_overwrite')
    index = json.loads((SNAPSHOT / 'model.safetensors.index.json').read_text())
    shards = sorted(set(index['weight_map'].values()))
    if len(shards) != 8:
        raise RuntimeError('expected_eight_local_weight_shards')
    result = {'model': 'Qwen/Qwen3-14B', 'revision': SNAPSHOT.name,
              'snapshot': str(SNAPSHOT), 'weights': {}, 'metadata': {}, 'packages': {},
              'quantization': 'NF4_double_quant_BF16', 'model_calls': 0,
              'a18_input_lock_sha256': sha(ROOT / 'prereg/a18_cds_inputs_20261017.json')}
    for name in shards:
        path = SNAPSHOT / name
        actual = sha(path)
        blob_name = path.resolve().name
        if re.fullmatch('[0-9a-f]{64}', blob_name) and blob_name != actual:
            raise RuntimeError('local_weight_blob_hash_mismatch')
        result['weights'][name] = {'bytes': path.stat().st_size, 'sha256': actual}
        print(json.dumps({'hashed_weight': name, 'bytes': path.stat().st_size}), flush=True)
    for name in ('config.json', 'generation_config.json', 'model.safetensors.index.json',
                 'tokenizer.json', 'tokenizer_config.json', 'merges.txt', 'vocab.json'):
        path = SNAPSHOT / name
        if path.exists():
            result['metadata'][name] = sha(path)
    config = json.loads((SNAPSHOT / 'config.json').read_text())
    if config['architectures'] != ['Qwen3ForCausalLM']:
        raise RuntimeError('local_model_architecture_mismatch')
    for package in ('torch', 'transformers', 'bitsandbytes', 'accelerate', 'tokenizers', 'safetensors'):
        result['packages'][package] = importlib.metadata.version(package)
    result['vendor_files'] = {str(path.relative_to(VENDOR)): sha(path)
                              for path in sorted(VENDOR.rglob('*.py')) if '__pycache__' not in path.parts}
    result['gpu_admission_snapshot'] = subprocess.check_output(
        ['nvidia-smi', '--query-gpu=index,name,memory.total,memory.used,memory.free',
         '--format=csv,noheader'], text=True).strip()
    LOCK.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': 'MODEL_HASH_LOCK_READY', 'shards': len(shards), 'model_calls': 0}))


if __name__ == '__main__':
    main()
