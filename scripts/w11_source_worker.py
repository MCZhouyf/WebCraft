#!/usr/bin/env python3
"""Pure captured-state planner diagnostic; never constructs a world or agent."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from w11_common import canonical,planner_from_fixture
import pulp


def main():
    parser=argparse.ArgumentParser();parser.add_argument('fixture');parser.add_argument('output')
    args=parser.parse_args();data=json.loads(Path(args.fixture).read_text())
    planner=planner_from_fixture(data);captured=[]
    original=pulp.LpProblem.solve
    def solve(problem,*a,**kw):
        constraints=[{'name':name,'coefficients':sorted((v.name,float(c)) for v,c in value.items()),
                      'constant':float(value.constant),'sense':value.sense}
                     for name,value in problem.constraints.items()]
        objective=sorted((v.name,float(c)) for v,c in problem.objective.items())
        invariant={'objective':objective,'constraints':sorted(
            ({k:v for k,v in c.items() if k!='name'} for c in constraints),key=canonical)}
        captured.append({'row_order_sha256':hashlib.sha256(canonical(constraints).encode()).hexdigest(),
                         'matrix_without_row_order_sha256':hashlib.sha256(canonical(invariant).encode()).hexdigest(),
                         'first_balance_rows':constraints[:12]})
        return original(problem,*a,**kw)
    pulp.LpProblem.solve=solve
    plans=planner.candidates(data['goal']['item'],int(data['goal']['count']),data['n_plans'])
    payload={'python_hash_seed':os.environ.get('PYTHONHASHSEED'),'fixture_sha256':hashlib.sha256(Path(args.fixture).read_bytes()).hexdigest(),
             'solves':captured,'plans':[{'objective':p.objective,'primary_cost':p.primary_cost,
                'selected_counts':p.selected_counts,'steps':[s.__dict__ for s in p.steps]} for p in plans],
             'no_world_or_agent_constructed':True}
    Path(args.output).write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n')


if __name__=='__main__':main()
