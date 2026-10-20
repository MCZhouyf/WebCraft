"""Stage-zero read-only scope audit; no gateway/simulator/method imports."""
from __future__ import annotations

import ast
import csv
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path('/root/autodl-tmp/webcraft')
OUT = ROOT / 'reports/main/post_review/cds_retention_scope'
METHODS = {'parametric', 'probe_then_parametric', 'naive_rag', 'majority_rag',
           'truthfinder_rag', 'verify_all', 'arbiter_shell_naive', 'arbiter',
           'oracle', 'probe_then_rag'}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path):
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt') as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def summarize(name: str, paths: list[Path], expected: int, *, llm: bool = False) -> tuple[dict, list[dict]]:
    keys, cells, markers = set(), Counter(), Counter()
    n = 0
    for path in paths:
        for row in jsonl(path):
            n += 1
            mode = row.get('reading_mode', 'LLM_CDS')
            instance = row.get('instance_id', row.get('cds_instance_id', row['task_id']))
            key = (mode, row['method'], row['condition'], row.get('world', row.get('target_world')), instance)
            keys.add(key)
            cells[(mode, row['method'], row.get('cds_condition', row['condition']))] += 1
            for field in ('prompt_log', 'action_model_calls', 'boundary_model_calls', 'policy_trace', 'reader'):
                markers[field] += int(field in row)
    if n != expected or len(keys) != expected:
        raise RuntimeError(f'{name}: expected {expected} distinct records, got {n}/{len(keys)}')
    observed = {key[1] for key in keys}
    if observed != ({'direct_llm', 'react'} if llm else METHODS):
        raise RuntimeError(f'{name}: unexpected method set')
    return {
        'dataset': name, 'records': n, 'unique_keys_excluding_seed': len(keys),
        'methods': sorted(observed), 'record_field_counts': dict(markers),
        'files': [{'path': str(path), 'sha256': sha(path)} for path in paths],
    }, [{'dataset': name, 'reading_mode': mode, 'method': method, 'condition': condition, 'records': count}
        for (mode, method, condition), count in sorted(cells.items())]


def source_evidence() -> list[dict]:
    selected = [
        ('EXT runner', WEBCRAFT / 'scripts/run_ext_v2_cds_20261016.py', 264, 277,
         'run_web invokes run_matrix with an evidence bundle and frozen prior; zero-LLM manifest at 301-306.'),
        ('CDS pipeline runner', WEBCRAFT / 'scripts/run_ext_v2_cds_20261016.py', 465, 483,
         '52 instances x 5 conditions x 10 pipeline methods; cds_bundle passed directly to run_episode.'),
        ('CDS pipeline boundary', WEBCRAFT / 'scripts/run_ext_v2_cds_20261016.py', 416, 440,
         'Boundary scoring reads a deterministic method belief; it does not make an LLM question call.'),
        ('Main runner', ROOT / 'scripts/run_main.py', 137, 144,
         'Main jobs execute run_matrix rather than run_external_episode.'),
        ('Evidence lifetime', ROOT / 'src/arbiter/evaluation/p6_execution.py', 789, 834,
         'Bundles are cached by condition/task/seed and passed to every run_episode.'),
        ('Compile and configure', ROOT / 'src/arbiter/evaluation/p6_execution.py', 593, 657,
         'Evidence is compiled/aggregated into operators and belief, then retained by agent.configure.'),
        ('Persistent state', ROOT / 'src/arbiter/agent/core.py', 70, 75,
         'configure stores self._operators and self._belief across actions.'),
        ('Per-action replanning', ROOT / 'src/arbiter/agent/core.py', 145, 164,
         'Each loop reads persistent operators and belief while refreshing only inventory.'),
        ('A8 LLM adapter wiring', ROOT / 'scripts/run_cds_agents_20261017.py', 229, 237,
         'Only the separate A8 LLM runner wires CDSKnowledgeAdapter into run_external_episode.'),
        ('Once-only injection', ROOT / 'src/arbiter/evaluation/cds_agents.py', 93, 98,
         'The known A11 adapter injects public facts only at initial inventory.'),
        ('Stateless action requests', ROOT / 'src/arbiter/evaluation/external_agents.py', 180, 196,
         'Direct uses trace[-2:], other external agents trace[-8:], with latest observation replacing initial knowledge.'),
    ]
    return [{'evidence': label, 'path': str(path), 'line_start': start, 'line_end': end,
             'sha256': sha(path), 'conclusion': conclusion,
             'source_excerpt': '\n'.join(path.read_text().splitlines()[start - 1:end])}
            for label, path, start, end, conclusion in selected]


def main() -> None:
    prereg = ROOT / 'prereg/cds_knowledge_retention_scope.md'
    if not prereg.is_file():
        raise RuntimeError('scope preregistration missing')
    datasets, cells = [], []
    inputs = [
        ('EXT_v2_pipeline', [WEBCRAFT / 'reports/paperA/ext_v2/web_run/episodes.jsonl'], 600, False),
        ('CDS_pipeline', [WEBCRAFT / 'reports/paperA/ext_v2/cds_run/episodes.jsonl'], 2600, False),
        ('main_pipeline_frozen', [WEBCRAFT / 'release/package/assets/frozen/main.jsonl.gz'], 11220, False),
        ('A8_CDS_LLM', sorted((ROOT / 'artifacts/post_review/cds_agents_20261017').glob('**/episodes.jsonl')), 416, True),
    ]
    for name, paths, expected, llm in inputs:
        dataset, grouped = summarize(name, paths, expected, llm=llm)
        datasets.append(dataset)
        cells.extend(grouped)
    evidence = source_evidence()
    equivalence = []
    for relative, functions in [
        ('src/arbiter/agent/core.py', ('configure', 'solve')),
        ('src/arbiter/evaluation/p6_execution.py', ('run_episode', 'run_matrix')),
    ]:
        live = ROOT / relative
        frozen = WEBCRAFT / 'release/package/method_code' / relative
        def function_bodies(path):
            return {node.name: ast.dump(node, include_attributes=False) for node in ast.walk(ast.parse(path.read_text()))
                    if isinstance(node, ast.FunctionDef)}
        current, packaged = function_bodies(live), function_bodies(frozen)
        equal = {name: current[name] == packaged[name] for name in functions}
        if not all(equal.values()) or sha(live) != sha(frozen):
            raise RuntimeError('reviewed live executor differs from packaged frozen executor; stop')
        equivalence.append({'live_path': str(live), 'frozen_path': str(frozen), 'sha256': sha(live),
                            'full_file_equal': True, 'function_AST_equal': equal})
    # Link only already-reported metrics to their record set; no new outcome
    # statistic, retention reconstruction or bootstrap is computed in stage 0.
    with (ROOT / 'reports/main/post_review/cds_agents_20261017/primary_contrasts.csv').open() as handle:
        existing = [row for row in csv.DictReader(handle) if row['method'] == 'react'
                    and row['contrast'] == 'K+S - K+N' and row['metric'] == 'discriminative_stale_contact']
    if len(existing) != 1 or existing[0]['n_pairs'] != '52':
        raise RuntimeError('existing requested contrast cannot be linked to the A8 cohort')
    status = {
        'stage': 0, 'EXT_v2_defect_scope': 'OUTSIDE', 'main_matrix_defect_scope': 'OUTSIDE',
        'CDS_2600_pipeline_defect_scope': 'OUTSIDE', 'A8_CDS_416_LLM_defect_scope': 'INSIDE_KNOWN_A11_PATH',
        'stop_reason': 'REQUESTED_2600_COHORT_IS_NOT_THE_LLM_COHORT_FOR_THE_REQUESTED_CONTRASTS',
        'stage_1_2_3_executed': False, 'llm_calls': 0, 'new_episodes': 0,
        'prereg_sha256': sha(prereg), 'datasets': datasets,
        'existing_contrast_source': 'reports/main/post_review/cds_agents_20261017/primary_contrasts.csv',
        'existing_contrast_row_verbatim': existing[0],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'coverage_and_scope.json').write_text(json.dumps(status, indent=2, sort_keys=True) + '\n')
    (OUT / 'source_evidence.json').write_text(json.dumps({'evidence': evidence, 'frozen_source_equivalence': equivalence},
                                                      indent=2, sort_keys=True) + '\n')
    with (OUT / 'record_coverage.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(cells[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(cells)
    report = '''# 第 0 步：CDS 知识保持缺陷的作用域

预注册先提交：b68d42b。本报告只交付第 0 步，不包含保持率、前缀重评分或新 BIA 数值。
全程只读源代码与已有记录；零 LLM 调用、零新回合、未运行智能体；没有推送远程。
未启动服务、未修改 server.lock 或其它进程；WebCraft、冻结文件和已有结果未修改。

## 结论

| 数据集 | 已核对的唯一记录数 | 知识/证据供给路径 | 是否在此次缺陷作用域内 |
| --- | ---: | --- | --- |
| EXT-v2 Web | 600 | run_matrix → bundle → run_episode → 持久算子/信念 | 否 |
| 主矩阵（冻结的种子去重口径） | 11,220 | 同一确定性共享规划器，三种阅读模式 | 否 |
| W7a CDS 管线 | 2,600 | cds_bundle → run_episode；十种管线方法 | 否 |
| A8 CDS LLM | 416 | CDSKnowledgeAdapter → run_external_episode；Direct/ReAct | 是，已知 A11 路径 |

“否”仅针对首次知识随观测替换而丢失的特定缺陷，不代表检索覆盖、编译或信念推断完全无问题。

## (a) EXT-v2 的 600 个回合

记录位置：/root/autodl-tmp/webcraft/reports/paperA/ext_v2/web_run/episodes.jsonl。
已核对 600 行及 600 个不同矩阵键；10 个方法各 60 行，均为 all_public。
web_run_manifest.json 同时声明 zero_llm_calls=true、12 个任务、5 格、10 个方法。

执行入口 webcraft/scripts/run_ext_v2_cds_20261016.py:264-277 直接调用 run_matrix。
p6_execution.py:789-834 保存并传递证据 bundle；593-657 将其编译为算子并聚合为信念，
随后使用 PublicSemanticAdapter 和 ArbiterAgent，不调用 CDSKnowledgeAdapter 或 run_external_episode。
不是“只在首次 observation 注入供 LLM 使用”的另一条等价路径。

## (b) 主矩阵的 11,220 个回合

核对的是已有冻结参考 /root/autodl-tmp/webcraft/release/package/assets/frozen/main.jsonl.gz，
没有运行发布包。记录覆盖 10 方法 × 3 阅读模式 × 374 个任务配置，逐方法模式均 374 条，无重复键。
主入口 scripts/run_main.py:137-144 调用相同的 run_matrix。

检索片段及其公共说法保存在 bundle 中，经 _claims 编译为算子并形成 belief；
agent/core.py:70-75 将二者保存在 self._operators / self._belief。
solve 的每次循环（145-164）从这两个持久成员构造下一次规划输入，更新库存并不会删除它们。
主动探测造成的信念更新、时间戳过滤和编译损失不等同于无状态提示中的意外遗失。

这里没有“后续 LLM 动作请求”：原文片段不反复发给模型，编译后的知识和信念跨动作保留。
不能把“没有片段提示”解释为遗失，因为这些方法通过确定性 MIP 规划与执行。
审计读取的 core.py 和 p6_execution.py 与冻结发布包的对应文件逐字节相同，
configure/solve/run_episode/run_matrix 的 AST 也一致，故结论不只依赖当前仓库的同名函数。
run_matrix 存在缺失缓存时的开发回退分支，但这不是行动期 LLM 循环；本阶段没有执行该分支。

## 必须先纠正的数据集对应关系

2600 = 52 个 CDS 实例 × 5 条件 × 10 种确定性管线方法。
W7a 的 CDS 入口在 run_ext_v2_cds_20261016.py:465-483，直接将 cds_bundle 传入 run_episode。
它的 boundary_score（416-440）从 method belief 计算，不发起 LLM 边界问询。
记录中没有 Direct/ReAct 方法或逐动作 LLM prompt_log。

A8 的 LLM 实验为 416 = 52 ×（Direct 的 3 个条件 + ReAct 的 5 个条件）。
全部 416 个记录键已核对。scripts/run_cds_agents_20261017.py:229-237 才是
CDSKnowledgeAdapter 与 run_external_episode 的连接点。
共享 extv2 ID 说明来源机制和任务材料相关，不能据此把两个执行路径视为同一个。

任务所引 K+S − K+N 陈旧特异接触 +19.23 pp [8.33, 33.33]，
在已有 A8 primary_contrasts.csv 中精确对应 ReAct、52 个配对实例。
已有 A8 main_table.csv 的 ReAct/K 可行任务 SR=0.6333333333333333，对应 30 个可行实例。
这些只是已报告数值的来源定位，本阶段未重算效果或区间。

## 停止点与后续

已单独完成第 0 步。EXT-v2 与主矩阵不在特定缺陷作用域内。
但“对 2600 条逐动作 LLM 请求重建并计算 Direct/ReAct 前缀”的前提与实际记录不符，
按预注册和作者的停止要求，此处停止；未擅自将分析分母改为 416。
需要作者确认后续分析对象应为 A8 的 416 条 LLM CDS，还是另有 2600 条 LLM 数据待提供。
确认对象之后，必须先另行冻结完整保持率、语义痕迹、前缀成功定义和区间稀疏门槛，再进行第 1-3 步。

## 文件

- coverage_and_scope.json：覆盖数量、数据源 SHA-256、作用域判定及停止原因。
- record_coverage.csv：逐数据集、方法、条件的记录数。
- source_evidence.json：相关源码行号、片段、哈希及冻结代码一致性。
- scripts/audit_cds_retention_scope.py：仅读取现有文件的重现脚本；不导入模拟器或网关。
'''
    (OUT / 'REPORT.md').write_text(report)
    print(json.dumps({key: value for key, value in status.items() if key not in {
        'datasets', 'existing_contrast_row_verbatim', 'existing_contrast_source'}}, sort_keys=True))


if __name__ == '__main__':
    main()
