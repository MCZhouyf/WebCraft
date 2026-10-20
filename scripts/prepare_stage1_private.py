#!/usr/bin/env python3
import hashlib
import json
import os
from pathlib import Path

root=Path('/root/autodl-tmp/webcraft')
private=root/'evaluator/private'
private.mkdir(parents=True,exist_ok=True,mode=0o700)
os.chmod(private,0o700)
path=private/'version_hashes.json'
mapping={hashlib.sha256(v.encode()).hexdigest():v for v in ('1.16.5','1.19','1.21.11')}
fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as handle: json.dump(mapping,handle,indent=2)
os.chmod(path,0o600)
print('Evaluator private map prepared with mode 0600; no contents printed.')
