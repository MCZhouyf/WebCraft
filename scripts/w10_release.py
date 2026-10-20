#!/usr/bin/env python3
"""Audit and build anonymous online/offline release copies; never run a matrix."""
from __future__ import annotations
import argparse
import ast
import csv
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
import zipfile
from functools import lru_cache
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'release/package'
OUT=ROOT/'reports/paperA/w10_release_final/release'
OFFLINE=ROOT/'release/w10_offline'
ONLINE=ROOT/'release/w10_online'
PASSWORD=Path('/root/.local/share/webcraft-review/w9_password')
TEXT={'.py','.md','.txt','.json','.jsonl','.csv','.sh','.lock','.toml','.yaml','.yml','.html','.ipynb'}
PROTECTED={'text','span_text','payload','assertion','assertions'}


def digest(data):return hashlib.sha256(data).hexdigest()


def write_csv(path,rows,fields=None):
    if not rows and not fields:return
    fields=fields or list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,lineterminator='\n');w.writeheader();w.writerows(rows)


@lru_cache(maxsize=1)
def authors():
    result=set()
    for repo in (ROOT,ROOT.parent/'arbiter'):
        raw=subprocess.check_output(['git','log','--all','--format=%an\t%ae'],cwd=repo,text=True)
        for line in raw.splitlines():
            for value in line.split('\t'):
                if value and value.lower() not in {'root','codex','anonymous','anonymous authors'}:result.add(value)
    return tuple(sorted(result))


def patterns():
    values=authors()
    return {
        'project_author':re.compile('|'.join(re.escape(v) for v in values),re.I) if values else re.compile(r'(?!)'),
        'email':re.compile(r'\b[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,24}\b'),
        'host':re.compile(re.escape(socket.gethostname())+r'|autodl-container-[A-Za-z0-9\-]+'),
        'absolute_path':re.compile(r'/(?:root|home|Users|tmp|private|var/folders|opt/conda|workspace|mnt)(?:/[A-Za-z0-9_+.@%~:=\-]+)+'),
        'git_author':re.compile(r'(?im)^(?:Author|Committer|Signed-off-by):[^\r\n]+'),
    }


def decrypt_archive(path):
    return subprocess.run(['openssl','enc','-d','-aes-256-cbc','-pbkdf2','-in',str(path),
                           '-pass',f'file:{PASSWORD}'],check=True,capture_output=True).stdout


def manifest_names(root):
    return [l.split('  ',1)[1] for l in (root/'MANIFEST.sha256').read_text().splitlines()]


def archive_bytes(entries):
    raw=io.BytesIO()
    with gzip.GzipFile(fileobj=raw,mode='wb',filename='',mtime=0) as zipped:
        with tarfile.open(fileobj=zipped,mode='w',format=tarfile.USTAR_FORMAT) as tar:
            for name,data,mode in sorted(entries):
                info=tarfile.TarInfo(name);info.size=len(data);info.mode=mode
                info.uid=info.gid=info.mtime=0;info.uname=info.gname=''
                tar.addfile(info,io.BytesIO(data))
    return raw.getvalue()


def scan(root,label,extra_local=False):
    pats=patterns();hits=[];containers=[]
    def inspect(name,data,origin='project',depth=0):
        if depth>8:raise RuntimeError('unexpected archive nesting')
        lower=name.lower()
        if '/.git/' in '/'+name or name.endswith('/.git'):
            hits.append({'file':name,'category':'git_directory','value':'.git','occurrences':1,'origin':origin,'disposition':'exclude'})
        if '__pycache__' in name or lower.endswith('.pyc') or '/.pytest_cache/' in '/'+name:
            hits.append({'file':name,'category':'cache_or_bytecode','value':Path(name).name,'occurrences':1,'origin':origin,'disposition':'exclude' if origin!='third_party' else 'third_party_dependency'})
        if lower.endswith('.enc'):
            if name.endswith('w9_replay_restricted.tar.gz.enc'):
                inspect(name+'!decrypted.tar.gz',decrypt_archive(root/name),origin,depth+1)
                return
        if lower.endswith('.whl'):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    if not info.is_dir():inspect(name+'!'+info.filename,z.read(info),'third_party',depth+1)
            containers.append({'file':name,'kind':'wheel','policy':'retain official third-party archive and required legal attribution; absent from online form'})
            return
        if lower.endswith(('.tar','.tar.gz')):
            with tarfile.open(fileobj=io.BytesIO(data),mode='r:*') as tar:
                for info in tar:
                    if not info.isfile():continue
                    if info.uid or info.gid or info.uname or info.gname or info.mtime or info.pax_headers:
                        hits.append({'file':name+'!'+info.name,'category':'archive_header_identity_or_time',
                                     'value':json.dumps({'uid':info.uid,'gid':info.gid,'uname':info.uname,'gname':info.gname,'mtime':info.mtime,'pax':info.pax_headers},sort_keys=True),
                                     'occurrences':1,'origin':origin,'disposition':'normalize owner and archive timestamp'})
                    inspect(name+'!'+info.name,tar.extractfile(info).read(),origin,depth+1)
            return
        if lower.endswith('.gz') and data.startswith(b'\x1f\x8b'):
            if len(data)>8 and (int.from_bytes(data[4:8],'little') or data[3]&8):
                hits.append({'file':name,'category':'gzip_header_metadata','value':'non-neutral mtime or filename','occurrences':1,'origin':origin,'disposition':'neutralize'})
            inspect(name[:-3],gzip.decompress(data),origin,depth+1);return
        text=data.decode('utf-8','replace')
        for kind,pattern in pats.items():
            if kind=='email' and '@' not in text:continue
            if kind=='email':
                found=Counter()
                for marker in re.finditer('@',text):
                    start=max(0,marker.start()-65);end=min(len(text),marker.start()+280)
                    for match in pattern.finditer(text[start:end]):
                        if start+match.start()+match.group(0).index('@')==marker.start():found[match.group(0)]+=1
            else:found=Counter(m.group(0) for m in pattern.finditer(text))
            for value,n in found.items():
                category=kind
                if kind=='absolute_path':
                    matches=list(re.finditer(re.escape(value),text))
                    if matches and all(m.start()>0 and (text[m.start()-1].isalnum() or text[m.start()-1] in '._/') for m in matches):
                        category='relative_path_suffix'
                if origin=='third_party':disposition='retain third-party attribution/build provenance; no project identity'
                elif category=='relative_path_suffix':disposition='retain relative corpus path; not an absolute local path'
                elif value.startswith(('/home/runner/','/home/conda/')):disposition='review third-party build provenance'
                elif kind=='absolute_path' and not value.startswith(('/root/autodl','/root/.','/tmp/','/root/codex','/root/iap','/root/webcraft')):
                    disposition='review example/public-content path'
                else:disposition='anonymize release copy only'
                hits.append({'file':name,'category':category,'value':value,'occurrences':n,'origin':origin,'disposition':disposition})
    for name in manifest_names(root):
        inspect(name,(root/name).read_bytes())
    if extra_local:
        listed=set(manifest_names(root))|{'MANIFEST.sha256'}
        local=[]
        for p in root.rglob('*'):
            if p.is_file() and str(p.relative_to(root)) not in listed:
                rel=str(p.relative_to(root))
                local.append({'file':rel,'bytes':p.stat().st_size,
                              'category':'local_decrypted_reviewer_asset' if rel.startswith(('assets/restricted/','assets/frozen/','corpus/indexes/formal_p1/polluted/','corpus/claims_cache/formal_p1/polluted/')) else 'local_cache_or_unlisted_file',
                              'disposition':'not copied to anonymous distribution'})
        write_csv(OUT/f'{label}_local_excluded.csv',local)
    write_csv(OUT/f'{label}_hits.csv',hits,fields=['file','category','value','occurrences','origin','disposition'])
    write_csv(OUT/f'{label}_containers.csv',containers)
    summary={'hit_rows':len(hits),'occurrences':sum(r['occurrences'] for r in hits),
             'by_category':dict(Counter(r['category'] for r in hits)),
             'by_origin':dict(Counter(r['origin'] for r in hits)),
             'project_identity_rows':sum(r['origin']=='project' and r['category'] in {'project_author','email','host','git_author'} for r in hits)}
    (OUT/f'{label}_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary


def replace_text(text):
    if not ('/root/' in text or 'autodl-container-' in text or socket.gethostname() in text
            or any(v.lower() in text.lower() for v in authors())):return text
    text=text.replace('/root/autodl-tmp/webcraft','PACKAGE_ROOT')
    text=text.replace('/root/autodl-tmp/arbiter','PACKAGE_ROOT/method_code')
    text=text.replace('/root/autodl-tmp','TEMP_ROOT')
    text=re.sub(r'/root/\.local/share/[^\s\"\'<>]+','REVIEW_PASSWORD_FILE',text)
    text=re.sub(r'autodl-container-[A-Za-z0-9\-]+','anonymous-host',text)
    text=text.replace(socket.gethostname(),'anonymous-host')
    for value in authors():text=re.sub(re.escape(value),'Anonymous contributor',text,flags=re.I)
    return text


def clean_python(data,logical):
    text=data.decode();tree=ast.parse(text)
    lines=text.encode().splitlines(keepends=True);offsets=[0]
    for line in lines:offsets.append(offsets[-1]+len(line))
    patches=[];depth=len(Path(logical).parent.parts)
    rootexpr=f'__import__("pathlib").Path(__file__).resolve().parents[{depth}]'
    for node in ast.walk(tree):
        if not isinstance(node,ast.Constant) or not isinstance(node.value,str):continue
        value=node.value;new=replace_text(value)
        if value==new:continue
        if value.startswith('/root/autodl-tmp/webcraft') and '\n' not in value:
            suffix=value.removeprefix('/root/autodl-tmp/webcraft').lstrip('/')
            expression=f'str({rootexpr} / {suffix!r})' if suffix else f'str({rootexpr})'
        elif value.startswith('/root/autodl-tmp/arbiter') and '\n' not in value:
            suffix=value.removeprefix('/root/autodl-tmp/arbiter').lstrip('/')
            expression=f'str({rootexpr} / "method_code" / {suffix!r})'
        elif value=='/root/autodl-tmp':expression='__import__("tempfile").gettempdir()'
        else:expression=repr(new)
        patches.append((offsets[node.lineno-1]+node.col_offset,offsets[node.end_lineno-1]+node.end_col_offset,expression.encode()))
    result=text.encode()
    for start,end,value in sorted(patches,reverse=True):result=result[:start]+value+result[end:]
    # Only comments remain outside AST string literals.
    result=replace_text(result.decode()).encode()
    ast.parse(result.decode())
    return result


def clean_json(value,path=()):
    if isinstance(value,dict):return {replace_text(k).removeprefix('PACKAGE_ROOT/'):clean_json(v,path+(k,)) for k,v in value.items()}
    if isinstance(value,list):return [clean_json(v,path) for v in value]
    if not isinstance(value,str):return value
    new=replace_text(value)
    if new!=value and any(k in PROTECTED for k in path):
        raise RuntimeError('identity in protected research evidence: '+'.'.join(path))
    # Local filesystem provenance becomes package-relative, not a fake absolute path.
    if new.startswith('PACKAGE_ROOT/'):new=new.removeprefix('PACKAGE_ROOT/')
    return new


def transform(data,logical):
    lower=logical.lower()
    if lower.endswith('.whl'):return data
    if lower.endswith(('.tar','.tar.gz')):
        entries=[]
        with tarfile.open(fileobj=io.BytesIO(data),mode='r:*') as tar:
            for info in tar:
                if info.isfile():
                    inner=tar.extractfile(info).read()
                    entries.append((info.name,transform(inner,'method_code/'+info.name),info.mode))
        packed=archive_bytes(entries)
        return packed if lower.endswith('.gz') else gzip.decompress(packed)
    if lower.endswith('.gz'):
        raw=transform(gzip.decompress(data),logical[:-3])
        return gzip.compress(raw,mtime=0)
    if Path(logical).suffix=='.py':return clean_python(data,logical)
    if lower.endswith('.json'):
        value=json.loads(data)
        cleaned=clean_json(value)
        return data if value==cleaned else (json.dumps(cleaned,indent=2,sort_keys=True)+'\n').encode()
    if lower.endswith('.jsonl'):
        raw=io.StringIO(data.decode());result=io.StringIO();changed=False
        for line in raw:
            if not line.strip():result.write(line);continue
            value=json.loads(line);cleaned=clean_json(value)
            if value==cleaned:result.write(line)
            else:result.write(json.dumps(cleaned,sort_keys=True,separators=(',',':'))+'\n');changed=True
        return result.getvalue().encode() if changed else data
    if Path(logical).suffix in TEXT:return replace_text(data.decode()).encode()
    return data


def public_files(root):
    for p in sorted(root.rglob('*')):
        if not p.is_file():continue
        rel=p.relative_to(root)
        if {'__pycache__','.pytest_cache','.venv','runs'}.intersection(rel.parts):continue
        if str(rel).startswith(('assets/restricted/','assets/frozen/main.jsonl.gz','assets/frozen/cp_rag.jsonl.gz',
                                'corpus/indexes/formal_p1/polluted/','corpus/claims_cache/formal_p1/polluted/')):continue
        if p.name=='MANIFEST.sha256':continue
        yield p


def manifest(root):
    entries=[]
    for p in public_files(root):entries.append(f'{digest(p.read_bytes())}  {p.relative_to(root)}')
    (root/'MANIFEST.sha256').write_text('\n'.join(entries)+'\n')


def opening(variant):
    return f'''# WebCraft-Versions: Anonymous Review Release

**Reproduction statement.** Of 11,220 main instances, **11,211 agree on every
reported metric and 9 have metric differences**. Within the 11,211 metric-equal
instances, **75 differ only in equivalent resource-node selection** (not 75
additional failures). **EXT-v2: 600/600** and **CDS: 2,600/2,600** agree on every
compared metric. This is not an all-instance exact-reproduction claim.

```bash
WC_REVIEW_PASSWORD_FILE=/path/to/reviewer-password ./reproduce_main.sh
```

Expected full replay: approximately **2--3 hours** on one CPU worker, measured
W9 runtime **7,568 seconds** (main 7,115; EXT-v2 128; CDS 325) on an Intel Xeon
Gold 6248 @ 2.50 GHz. Linux x86-64, Python 3.10.8, PYTHONHASHSEED=0, CBC one
thread, no GPU and **zero online LLM calls**; plan for 2 GB RAM plus headroom
(observed peak below 0.8 GB). This is the **{variant}** form.

The pollution index/cache, restricted retrieved evidence and complete main/CP
reference logs require a reviewer password. Obtain it **from the authors via
the submission system**, not from this repository. Set the environment variable
above to a local password file; never put the password in a command or commit.

'''


def build():
    names=manifest_names(SOURCE)
    ledger=[]
    for target in (OFFLINE,ONLINE):
        if target.exists():raise RuntimeError('refusing to overwrite a built form: '+str(target))
    OFFLINE.mkdir(parents=True)
    for name in names:
        source=SOURCE/name; data=source.read_bytes()
        if name=='corpus/encrypted/w9_replay_restricted.tar.gz.enc':continue
        transformed=transform(data,name)
        dest=OFFLINE/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(transformed)
        dest.chmod(source.stat().st_mode&0o777)
        if transformed!=data:ledger.append({'file':name,'original_sha256':digest(data),'anonymous_sha256':digest(transformed),'policy':'path/identity metadata or neutral archive headers only'})
    restricted=[]
    archive=decrypt_archive(SOURCE/'corpus/encrypted/w9_replay_restricted.tar.gz.enc')
    with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz') as tar:
        for info in tar:
            if not info.isfile():continue
            data=tar.extractfile(info).read(); transformed=transform(data,info.name)
            restricted.append((info.name,transformed,info.mode))
            ledger.append({'file':'encrypted!'+info.name,'original_sha256':digest(data),'anonymous_sha256':digest(transformed),
                           'policy':'metadata-only JSON transform; archive owner/time neutralized'})
    with tempfile.TemporaryDirectory(prefix='w10_encrypt_') as tmp:
        plain=Path(tmp)/'reviewer.tar.gz';plain.write_bytes(archive_bytes(restricted))
        encrypted=OFFLINE/'corpus/encrypted/w9_replay_restricted.tar.gz.enc';encrypted.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['openssl','enc','-aes-256-cbc','-pbkdf2','-salt','-in',str(plain),'-out',str(encrypted),
                        '-pass',f'file:{PASSWORD}'],check=True)
    (OFFLINE/'docs/RESTRICTED_FILES.json').write_text(json.dumps([
        {'path':name,'bytes':len(data),'sha256':digest(data)} for name,data,_ in restricted],indent=2)+'\n')
    # The old metadata-only provenance checksum is distinct from research source IDs.
    (OFFLINE/'LICENSE-CODE').write_text('CODE LICENSE: AUTHOR DECISION PENDING\n\nNo code license is selected or granted by this placeholder. The authors must\nspecify the intended code license before public distribution. Bundled third-party\ncomponents retain their own notices and licenses.\n')
    (OFFLINE/'LICENSE-DATA').write_text('DATA LICENSE AND ATTRIBUTION: AUTHOR REVIEW PENDING\n\nDerived structured claims and frozen snippets originate from Wiki content\nidentified in corpus/source_attribution.csv under source CC BY-NC-SA terms.\nRetain the revision/page/source links and the exact source-specific license\nnotices in that attribution file. Original Wiki pages are not distributed.\nThis notice does not relicense game artifacts, project code, or third-party\ndependencies; source-specific obligations remain applicable. Authors must\nconfirm the final data notice before public publication.\n')
    source_entry=(ROOT/'scripts/w9_reproduce_main.sh').read_text()
    source_entry=source_entry.replace('"$WC_ENV/bin/python" -m pip --isolated install --no-index \\\n        --find-links "$WC_PACKAGE_ROOT/wheelhouse" -r "$WC_PACKAGE_ROOT/requirements.lock"',
        'if [[ -d "$WC_PACKAGE_ROOT/wheelhouse" ]]; then\n        "$WC_ENV/bin/python" -m pip --isolated install --no-index --find-links "$WC_PACKAGE_ROOT/wheelhouse" -r "$WC_PACKAGE_ROOT/requirements.lock"\n    else\n        "$WC_ENV/bin/python" -m pip --isolated install --no-cache-dir -r "$WC_PACKAGE_ROOT/requirements.lock"\n    fi')
    source_entry=source_entry.replace('for WC_SCOPE in main ext_v2 cds; do',
        'if [[ "${WC_REPLAY_TEST_ONLY:-0}" == 1 ]]; then exit 0; fi\nfor WC_SCOPE in main ext_v2 cds; do')
    (OFFLINE/'reproduce_main.sh').write_text(source_entry);(OFFLINE/'reproduce_main.sh').chmod(0o755)
    license_note='''## Licensing

Code license: **not yet specified by the authors**; see LICENSE-CODE. No code
license is selected by this preparation step. Data consists of derived Wiki
claims/snippets from CC BY-NC-SA sources; see LICENSE-DATA and
corpus/source_attribution.csv for page/revision attribution and source-specific
terms. Original pages are not included. Third-party wheels retain their own
licenses, author metadata and public build provenance; these are not project
author identities. Public publication awaits the authors' license decision.

'''
    tail='''## Contents and checks

method_code/ contains methods, reader/compiler/MIP planner; assets/prior/ the
frozen prior; corpus/ derived indexes and claim caches; assets/evidence/ frozen
retrieved evidence. cds_materials/ is agent-visible; evaluator/cds/ is scorer
truth and must never be shown to an agent. data/ext_v2/ and config/ext_v2/ define
the extension and item packages. assets/frozen/ contains reference trajectories
after authorized decryption. No raw pages, .git history, local cache or .pyc is
distributed. Source bibliographic attribution and scientific identifiers are
retained; local machine metadata is anonymized. docs/ANONYMIZATION.md explains
the distinction from research-source hashes.

Run WC_REPLAY_TEST_ONLY=1 with the command above for package tests only. No new
matrix is run by that option. Python 3.10.8 and OpenSSL are host prerequisites.
Online form: pip installs the exact requirements.lock without a bundled
wheelhouse. Offline form: no-index installation uses wheelhouse/. New outputs
go to runs/reproduction/ and never replace frozen reference records.
'''
    (OFFLINE/'README.md').write_text(opening('complete offline')+license_note+tail)
    (OFFLINE/'docs/ANONYMIZATION.md').write_text('# Anonymous distribution transforms\n\n'
        'Only distribution copies are changed. Absolute local provenance and executable '\
        'root paths are relocated; project author names/emails and hostnames are replaced. '\
        'Tar/gzip owner and container timestamps are neutral. Research claim payloads, '\
        'evidence text, action/metric values and revision timestamps are unchanged. '\
        'Third-party wheel authors/licenses and compiler build paths are retained for '\
        'attribution and binary integrity and are absent from the online repository form. '\
        'Frozen research-source hashes remain provenance identifiers, not a statement '\
        'that a relocated source file has the original bytes. MANIFEST.sha256 describes '\
        'the anonymous distribution bytes. The detailed transform audit is retained '\
        'outside the anonymous package.\n')
    manifest(OFFLINE)
    shutil.copytree(OFFLINE,ONLINE,ignore=shutil.ignore_patterns('wheelhouse'))
    (ONLINE/'README.md').write_text(opening('online repository')+license_note+tail)
    manifest(ONLINE)
    write_csv(OUT/'transforms.csv',ledger)
    (OUT/'README_FIRST_SCREEN.md').write_text(opening('online repository'))
    return statistics_and_download()


def statistics_and_download():
    variants=[];large=[]
    for label,target in (('online',ONLINE),('offline',OFFLINE)):
        files=[p for p in target.rglob('*') if p.is_file()]
        variants.append({'variant':label,'files':len(files),'bytes':sum(p.stat().st_size for p in files),
                         'wheelhouse':(target/'wheelhouse').exists(),'root':str(target.relative_to(ROOT))})
        for p in files:
            size=p.stat().st_size
            if size>50_000_000:large.append({'variant':label,'file':str(p.relative_to(target)),'bytes':size,
                                            'over_50_MB':True,'over_100_MB':size>100_000_000,
                                            'over_GitHub_100_MiB':size>100*1024**2})
        if label=='online':assert all(p.stat().st_size<=100*1024**2 for p in files)
    write_csv(OUT/'variant_sizes.csv',variants)
    write_csv(OUT/'large_files.csv',large,['variant','file','bytes','over_50_MB','over_100_MB','over_GitHub_100_MiB'])
    # A separate download artifact; no raw or local-decrypted trees are included.
    entries=[(str(p.relative_to(OFFLINE)),p.read_bytes(),p.stat().st_mode&0o777) for p in sorted(OFFLINE.rglob('*')) if p.is_file()]
    download=ROOT/'release/WebCraft-Versions-offline-anonymous.tar.gz'
    download.write_bytes(archive_bytes(entries))
    if download.stat().st_size>50_000_000:
        size=download.stat().st_size
        large.append({'variant':'offline_download','file':str(download.relative_to(ROOT)),
                      'bytes':size,'over_50_MB':True,'over_100_MB':size>100_000_000,
                      'over_GitHub_100_MiB':size>100*1024**2})
        write_csv(OUT/'large_files.csv',large)
    (OUT/'offline_download.json').write_text(json.dumps({'file':str(download.relative_to(ROOT)),
        'bytes':download.stat().st_size,'sha256':digest(download.read_bytes()),
        'note':'Download artifact, not a Git blob; upload separately without remote action here.'},indent=2)+'\n')
    return variants


def remove_vendor_bytecode(data):
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        removed={i.filename for i in source.infolist() if i.filename.endswith('.pyc') or '/__pycache__/' in i.filename}
        if not removed:return data,[]
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                if info.filename in removed:continue
                value=source.read(info)
                if info.filename.endswith('.dist-info/RECORD'):
                    rows=[r for r in csv.reader(io.StringIO(value.decode())) if r and r[0] not in removed]
                    content=io.StringIO();csv.writer(content,lineterminator='\n').writerows(rows);value=content.getvalue().encode()
                new=zipfile.ZipInfo(info.filename,date_time=(1980,1,1,0,0,0))
                new.compress_type=info.compress_type;new.external_attr=info.external_attr
                new.create_system=info.create_system;target.writestr(new,value)
        return output.getvalue(),sorted(removed)


def refresh():
    changes=[]
    name='config/ext_freeze_manifest_20261017.json'
    transformed=transform((SOURCE/name).read_bytes(),name)
    for target in (OFFLINE,ONLINE):(target/name).write_bytes(transformed)
    ledger=list(csv.DictReader((OUT/'transforms.csv').open()))
    ledger=[r for r in ledger if r['file']!=name]
    ledger.append({'file':name,'original_sha256':digest((SOURCE/name).read_bytes()),
                   'anonymous_sha256':digest(transformed),'policy':'absolute provenance-map keys relocated; values preserved'})
    write_csv(OUT/'transforms.csv',ledger)
    for wheel in (OFFLINE/'wheelhouse').glob('*.whl'):
        data=wheel.read_bytes();new,removed=remove_vendor_bytecode(data)
        if removed:
            wheel.write_bytes(new)
            changes.append({'file':str(wheel.relative_to(OFFLINE)),'removed_entries':';'.join(removed),
                            'original_sha256':digest(data),'anonymous_sha256':digest(new),
                            'policy':'remove unnecessary vendor bytecode; keep source/binaries/licenses; update wheel RECORD'})
    write_csv(OUT/'vendor_bytecode_cleanup.csv',changes)
    for target in (OFFLINE,ONLINE):
        (target/'prereg/w10_release_final_20261016.md').write_text(replace_text((ROOT/'prereg/w10_release_final_20261016.md').read_text()))
        readme=target/'README.md';body=readme.read_text()
        if '## Anonymous repository preparation' not in body:
            body+='\n## Anonymous repository preparation\n\nCopy the online-form contents into a new repository with an anonymous commit\nidentity. Do not mirror the working repository Git history: its commit authors\nare not anonymous. No .git directory or pip cache is included here. Publish the\ncomplete offline download separately; do not add that >100 MiB archive as a Git\nblob. No remote repository was created or pushed by this audit.\n'
            readme.write_text(body)
        manifest(target)
    print(json.dumps(statistics_and_download()))


def main():
    p=argparse.ArgumentParser();p.add_argument('phase',choices=('scan','build','rescan','refresh'));args=p.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    if args.phase=='scan':print(json.dumps(scan(SOURCE,'before',extra_local=True)))
    elif args.phase=='build':print(json.dumps(build()))
    elif args.phase=='refresh':refresh()
    else:
        for label,target in (('online_after',ONLINE),('offline_after',OFFLINE)):
            print(json.dumps({'variant':label,**scan(target,label)}))


if __name__=='__main__':main()
