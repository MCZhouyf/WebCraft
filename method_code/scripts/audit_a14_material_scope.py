"""Document a pre-computation A14 registration discrepancy; no outcome analysis."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = Path('/root/autodl-tmp/webcraft')
OUT = ROOT / 'reports/main/post_review/a14_cds_retention'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if OUT.exists():
        raise RuntimeError('Refusing to overwrite an existing A14 report')
    manifest_path = WEB / 'cds_materials/cds_manifest.json'
    prereg = ROOT / 'prereg/a14_cds_retention_20261017.md'
    constructor = WEB / 'scripts/build_cds_materials_20261016.py'
    lock = Path('/root/autodl-tmp/server.lock')
    protected = {str(path): sha(path) for path in (manifest_path, prereg, constructor, lock)}
    manifest = json.loads(manifest_path.read_text())
    rows, examples = [], []
    for entry in manifest['rows']:
        path = WEB / entry['visible_path']
        actual = sha(path)
        if actual != entry['visible_sha256']:
            raise RuntimeError('Frozen visible material hash mismatch')
        protected[str(path)] = actual
        material = json.loads(path.read_text())
        facts = material['conditions']['empty']
        rows.append({'instance_id': entry['instance_id'], 'visible_path': entry['visible_path'],
                     'visible_sha256': actual, 'empty_injected_fact_count': len(facts)})
        if facts and not examples:
            examples.append({'instance_id': entry['instance_id'], 'visible_path': entry['visible_path'],
                             'empty_injected_fact_count': len(facts), 'first_fact': facts[0]})
    if len(rows) != 52 or len({row['instance_id'] for row in rows}) != 52:
        raise RuntimeError('Material coverage is not the author-confirmed 52 instances')
    distribution = Counter(row['empty_injected_fact_count'] for row in rows)
    nonempty = sum(row['empty_injected_fact_count'] > 0 for row in rows)
    if not nonempty:
        raise RuntimeError('Expected documented input discrepancy not reproduced; stop')
    evidence = []
    for label, path, token in [
        ('registered assumption', prereg, 'In empty, expected count is zero'),
        ('frozen constructor', constructor, '"empty": (nonfocal_visible, nonfocal_private)'),
    ]:
        lines = path.read_text().splitlines()
        index = next(i for i, line in enumerate(lines) if token in line)
        evidence.append({'label': label, 'path': str(path), 'line': index + 1,
                         'excerpt': '\n'.join(lines[max(0, index - 2):index + 3]), 'sha256': sha(path)})
    if protected != {path: sha(Path(path)) for path in protected}:
        raise RuntimeError('An input changed while collecting source evidence')
    status = {
        'status': 'STOP_PREREG_EMPTY_CONDITION_ASSUMPTION_INVALID',
        'prereg_commit': '4764388', 'registration_error_origin': 'analyst assumption, not author data',
        'material_instances': 52, 'empty_condition_nonzero_fact_instances': nonempty,
        'empty_fact_count_distribution': dict(sorted(distribution.items())),
        'retention_or_prefix_outcomes_computed': False, 'A1_A2_A3_executed': False,
        'B0_complete': False, 'B1_B2_executed': False,
        'llm_calls': 0, 'new_episodes': 0, 'scorer_keys_read': False,
        'source_evidence': evidence, 'material_example': examples[0], 'input_hashes': protected,
    }
    OUT.mkdir(parents=True)
    with (OUT / 'material_cardinality.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    (OUT / 'precomputation_stop.json').write_text(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    report = f'''# A14: pre-computation stop

预注册提交：4764388。新保持率、前缀效果与区间尚未计算。
按作者“任何一步与预期不符就停下报告，不自行调整口径”的要求，此处暂停。

## 差异与责任

新预注册第 68–70 行将 empty（∅）定义为始终零条注入事实，并据此把前缀设为全回合。
这是本次分析者在冻结前未充分核实的假设，不是作者给错数据，也不是 416 条数据缺失。

冻结构造实际为：

```python
"S": (nonfocal_visible + stale_visible, nonfocal_private + stale_private),
"empty": (nonfocal_visible, nonfocal_private),
```

即 ∅ 去除焦点说法，但仍可保留非焦点机制事实。S 也不是只含陈旧焦点说法。
52 份材料的 ∅ 条目数分布：{json.dumps(dict(sorted(distribution.items())), ensure_ascii=False)}。
其中 **{nonempty}/52** 份不是零注入；其余 **{52 - nonempty}/52** 份确实无条目。
这只是一项冻结输入材料的基数核查，不是请求保持率或结果重评分。

例：`{examples[0]['instance_id']}` 的 ∅ 中有 {examples[0]['empty_injected_fact_count']} 条事实，
包括 `lapis_ore drops lapis_lazuli.`（来源 truth_source_1，时间戳 2021-02-13T00:00:00Z）。
材料 SHA256 与冻结 manifest 全部相符，逐实例记录见 material_cardinality.csv。

## 为什么不能照原新预注册继续

若将这 {nonempty} 个实例也按“零知识，前缀为全回合”处理，会误算保持前缀，
继而影响 ∅−K 与半回合筛选口径。不能在已提交后静默改口径并继续计算。
原预注册保持原样；没有更改冻结结果或任何智能体、提示词、WebCraft 文件。

建议待作者确认后，新建一份**计算前更正**（不改原注册）：各条件，包括 ∅，
均以各回合冻结材料的实际事实列表作为预期条目；只有实际条目数为 0 的回合，
保持比例记 NA，前缀采用真空成立约定。其余既定规则不变。
该建议尚未获确认、尚未实施。

## 未执行的部分

- A1：未计算逐请求保持率、三档保持或前缀分布。
- A2：未计算任何新效果或区间；原 +19.23 pp 和 0.6333 没有被替换。
- A3：未计算边界请求占比；不能从动作期遗失直接断言边界问询也遗失。
- B0：仅阅读了条件供给源码，未完成等价性核查或作通过/不通过结论。
- B1/B2：未执行，也未把确定性 CDS 作为替代因果证据报告。

## 纪律与文件

零 LLM 调用、零新回合、没有运行任何智能体或模拟器、没有读取人工审计 key。
WebCraft 与预注册只读，原文件和 server.lock 的 SHA256 前后一致；没有操作其它进程或推送远程。
precomputation_stop.json 保存源码行号、引文、材料例子和输入 SHA256；
scripts/audit_a14_material_scope.py 仅重现本次输入差异核查，不执行 A1–A3 或 B1/B2。
'''
    (OUT / 'STOP_REPORT.md').write_text(report, encoding='utf-8')
    print(json.dumps({key: status[key] for key in ('status', 'material_instances',
          'empty_condition_nonzero_fact_instances', 'empty_fact_count_distribution', 'llm_calls', 'new_episodes')}, sort_keys=True))


if __name__ == '__main__':
    main()
