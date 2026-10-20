#!/usr/bin/env python3
"""Owned A21 background continuation; credentials are loaded by child runners."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from scripts import run_a21_mds as run

CONTROL = run.BASE / 'continuation'
STATUS = CONTROL / 'status.json'
LOCK = CONTROL / 'controller.lock'


def publish(status: str, **details: object) -> None:
    value = {'status': status, 'controller_pid': os.getpid(),
             'updated_at_utc': datetime.now(timezone.utc).isoformat(), **details}
    temporary = STATUS.with_suffix('.tmp')
    run.write(temporary, value)
    temporary.replace(STATUS)


def wait_for_resources() -> None:
    while True:
        enough = run.effective_available_bytes() >= run.MIN_BYTES
        with Path('/root/autodl-tmp/server.lock').open() as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
                available = True
            except BlockingIOError:
                available = False
        if enough and available:
            return
        publish('WAITING_RESOURCES', enough_memory=enough, server_lock_available=available,
                next_poll_seconds=600)
        time.sleep(600)


def step(name: str, arguments: list[str]) -> bool:
    wait_for_resources()
    environment = os.environ.copy()
    environment['PYTHONPATH'] = os.pathsep.join(map(str, (run.ROOT / 'src', run.ROOT, run.ROOT / 'scripts')))
    process = subprocess.Popen([sys.executable, *arguments], cwd=run.ROOT,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, env=environment, start_new_session=True)
    try:
        while process.poll() is None:
            publish('RUNNING', step=name, runner_pid=process.pid, model=run.MODEL,
                    max_workers=6, wait_on_http_429=True)
            time.sleep(15)
        if process.returncode != 0:
            publish('STOPPED_PROTECTED', step=name, exit_code=process.returncode,
                    model=run.MODEL, note='Inspect fixed-class worker failure and ledger records; no auto-restart.')
            return False
        return True
    finally:
        if process.poll() is None:
            # This process group contains only the child created above.
            os.killpg(process.pid, signal.SIGTERM)
            process.wait()


def stage(name: str, phase: str = 'K') -> bool:
    if not step('verify_' + name, ['scripts/verify_a21_saved_prefix.py', '--stage', name]):
        return False
    return step(name + '_' + phase, [
        'scripts/run_a21_mds.py', '--stage', name, '--formal-phase', phase,
        '--author-context', '--workers', '6', '--request-interval', '15',
        '--start-stagger', '10', '--global-request-gap', '20', '--wait-on-429',
        '--resume', '--credential-slots', '5', '6', '1', '4', '2', '3',
    ])


def gate(name: str) -> bool:
    path = run.REPORT / name
    if not path.is_file():
        publish('STOPPED_MISSING_GATE', gate=name)
        return False
    value = json.loads(path.read_text())
    if value.get('passed'):
        return True
    status = ('NEEDS_REGISTERED_SHORTENING' if value['feasible_success_rate'] < value['lower']
              else 'STOPPED_GATE_OUT_OF_RANGE')
    publish(status, gate=name, gate_result=value, formal_controls_authorized=False)
    return False


def workflow(through: str = 'formal_K') -> None:
    development = run.completed_records(run.BASE / 'development_run', run.material_rows('development'))
    if len(development) < 8 and not stage('development'):
        return
    technical = run.development_executability()
    run.write(run.REPORT / 'development_executability.json', technical)
    if not technical['passed']:
        publish('STOPPED_DEVELOPMENT_EXECUTABILITY', formal_controls_authorized=False)
        return
    records = run.completed_records(run.BASE / 'formal_run', run.material_rows('formal'))
    if sum(row['condition'] == 'K' for row in records) < 32 and not stage('formal'):
        return
    # The K checkpoint is frozen before controls. Its cost ledger must not
    # absorb later control requests when this controller resumes.
    if not (run.REPORT / 'formal_K/provenance.json').is_file() and not step(
            'formal_K_export', ['scripts/export_a21_formal_k.py']):
        return
    if through == 'formal_K':
        value = json.loads((run.REPORT / 'formal_K_gate.json').read_text())
        publish('FORMAL_K_COMPLETE', gate_result=value, formal_episodes=32,
                formal_controls_authorized=False, model=run.MODEL,
                report_dir=str((run.REPORT / 'formal_K').relative_to(run.ROOT)))
        return
    if not gate('formal_K_gate.json'):
        return
    records = run.completed_records(run.BASE / 'formal_run', run.material_rows('formal'))
    if len(records) < 128 and not stage('formal', 'controls'):
        return
    if step('offline_analysis', ['scripts/analyze_a21_mds.py']):
        publish('COMPLETED', model=run.MODEL, formal_episodes=128,
                report_dir=str(run.REPORT.relative_to(run.ROOT)))


def interrupted(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def main() -> int:
    parser = argparse.ArgumentParser()
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--detach', action='store_true')
    choice.add_argument('--run', action='store_true')
    choice.add_argument('--status', action='store_true')
    parser.add_argument('--through', choices=('formal_K', 'all'), default='formal_K')
    args = parser.parse_args()
    if args.status:
        print(STATUS.read_text() if STATUS.is_file() else json.dumps({'status': 'NOT_STARTED'}))
        return 0
    CONTROL.mkdir(parents=True, exist_ok=True)
    with LOCK.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'status': 'CONTROLLER_ALREADY_RUNNING'}))
            return 0
        if args.detach:
            # Release the launch check before the child takes the durable
            # lifetime lock; concurrent launchers still have one winner.
            fcntl.flock(lock, fcntl.LOCK_UN)
            descriptor = os.open(CONTROL / 'console.log', os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
            with os.fdopen(descriptor, 'a') as output:
                # The child loads this conversation's configuration in memory;
                # no credential is placed in argv, files, or this status reply.
                process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--run',
                                            '--through', args.through],
                                           cwd=run.ROOT, stdin=subprocess.DEVNULL, stdout=output,
                                           stderr=subprocess.STDOUT, start_new_session=True,
                                           close_fds=True)
            print(json.dumps({'status': 'LAUNCHED', 'controller_pid': process.pid}))
            return 0
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        os.nice(max(0, 15-os.getpriority(os.PRIO_PROCESS, 0)))
        try:
            publish('STARTING', model=run.MODEL, max_workers=6)
            workflow(args.through)
            return 0
        except KeyboardInterrupt:
            publish('STOPPED_USER', model=run.MODEL)
            return 130
        except Exception as exc:
            publish('STOPPED_PROTECTED', error_type=type(exc).__name__, model=run.MODEL)
            return 2
        finally:
            run.clean_env()


if __name__ == '__main__':
    raise SystemExit(main())
