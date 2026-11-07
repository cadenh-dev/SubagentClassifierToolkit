#!/usr/bin/env python3
"""Read-only health report for a local agent toolchain."""
from __future__ import annotations
import argparse, hashlib, json, os, re, shlex, shutil, subprocess, sys
from pathlib import Path

RUNTIMES = ['python3','node','npm','pnpm','yarn','bun','go','rustc','cargo','java','dotnet']
CLIS = ['git','gh','codex','opencode','claude','cursor-agent','gemini']

def report(id, status, summary, details=None): return {'id':id,'status':status,'summary':summary,'details':details or {}}
def run(argv, cwd=None, timeout=10, env=None):
    try: return subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e: return e

def version(cmd, path, timeout):
    exe=shutil.which(cmd, path=path)
    if not exe: return report('command.'+cmd,'skip',cmd+' not found')
    r=run([exe,'--version'],timeout=timeout,env={**os.environ,'PATH':path})
    if isinstance(r,Exception): return report('command.'+cmd,'warn',cmd+' present; version unavailable')
    line=(r.stdout or r.stderr).strip().splitlines()
    return report('command.'+cmd,'ok',cmd+' present',{'version':line[0][:180] if line else 'version unavailable'})

def safe_error(s):
    s=re.sub(r'https?://[^\s]+','remote URL',s)
    s=re.sub(r'(?i)(token|password|authorization|secret)[=: ]+\S+',r'\1=[redacted]',s)
    return s.strip()[:400]

def servers_from(obj):
    names=[]
    def rec(x):
        if isinstance(x,dict):
            for key,val in x.items():
                if key in ('mcpServers','mcp_servers') and isinstance(val,dict): names.extend(str(k) for k in val)
                rec(val)
        elif isinstance(x,list):
            for val in x: rec(val)
    rec(obj); return names

def digest(p):
    h=hashlib.sha256()
    for f in sorted(p.rglob('*')):
        if f.is_file() and '__pycache__' not in f.parts and f.suffix!='.pyc':
            h.update(f.relative_to(p).as_posix().encode()); h.update(f.read_bytes())
    return h.hexdigest()

def doctor(repo, skills_dir, profile, home, path, timeout, fetch=False):
    out=[]
    out += [version(c,path,timeout) for c in RUNTIMES+CLIS]
    gh=shutil.which('gh',path=path); authenticated=False
    if gh:
        r=run([gh,'auth','status'],timeout=timeout,env={**os.environ,'PATH':path})
        authenticated=not isinstance(r,Exception) and r.returncode==0
        # Only report protocol/login state, never raw output.
        proto='HTTPS' if authenticated and 'https' in (r.stdout+r.stderr).lower() else 'unknown'
        out.append(report('auth.gh','ok' if authenticated else 'warn','gh authenticated' if authenticated else 'gh not authenticated',{'protocol':proto}))
    else: out.append(report('auth.gh','skip','gh not found'))
    git=shutil.which('git',path=path)
    if git:
        rr=run([git,'remote'],cwd=repo,timeout=timeout,env={**os.environ,'PATH':path})
        remotes=rr.stdout.split() if not isinstance(rr,Exception) and rr.returncode==0 else []
        for name in remotes:
            url=run([git,'remote','get-url',name],cwd=repo,timeout=timeout,env={**os.environ,'PATH':path})
            if isinstance(url,Exception) or url.returncode: continue
            address=url.stdout.strip(); e={**os.environ,'GIT_TERMINAL_PROMPT':'0','GIT_SSH_COMMAND':'ssh -o BatchMode=yes -o ConnectTimeout=5','PATH':path}
            if fetch: run([git,'fetch',name],cwd=repo,timeout=timeout,env=e)
            r=run([git,'ls-remote','--heads',address],cwd=repo,timeout=timeout,env=e)
            if isinstance(r,Exception) or r.returncode:
                err='timeout' if isinstance(r,subprocess.TimeoutExpired) else safe_error(getattr(r,'stderr','') or str(r))
                ssh='git@' in address or address.startswith('ssh://')
                det={'reason':err}
                if ssh and authenticated: det['suggestion']='Switch this remote to its HTTPS URL; gh is authenticated.'
                out.append(report('git.remote.'+name,'error','remote unreachable',det))
            else: out.append(report('git.remote.'+name,'ok','remote reachable'))
    # MCP config paths, names only.
    candidates=[home/'.config/opencode/opencode.json',home/'.config/opencode/opencode.jsonc',home/'.codex/config.toml',home/'.claude.json',repo/'.mcp.json',home/'.gemini/settings.json']
    found=[]
    for p in candidates:
        if not p.is_file(): continue
        try:
            raw=p.read_text(errors='replace')
            if p.suffix=='.toml':
                import tomllib; names=servers_from(tomllib.loads(raw))
            else:
                raw=re.sub(r'//[^\n]*|/\*.*?\*/','',raw,flags=re.S); names=servers_from(json.loads(raw))
            found.extend(names)
        except Exception: pass
    out.append(report('mcp.servers','ok' if found else 'skip', 'MCP servers discovered' if found else 'No MCP servers found',{'names':sorted(set(found))}))
    if profile:
        try:
            data=json.loads(profile.read_text()); missing=[]
            for x in data.get('languages',[]):
                cmd=x.get('runtime_command')
                if x.get('runtime_available') and cmd and not shutil.which(cmd,path=path): missing.append(cmd)
            for x in data.get('commands',[]):
                try: cmd=shlex.split(x.get('command',''))[0]
                except (ValueError,IndexError): continue
                if cmd and not shutil.which(cmd,path=path): missing.append(cmd)
            out.append(report('profile.expected','warn' if missing else 'ok','Expected commands missing' if missing else 'Profile expectations available',{'missing':sorted(set(missing))}))
        except Exception: raise ValueError('invalid profile JSON')
    source=repo/'.opencode/skills'
    if source.is_dir() and skills_dir.is_dir():
        drift=[]
        for d in source.iterdir():
            if not d.is_dir(): continue
            installed=skills_dir/d.name
            drift.append({'skill':d.name,'status':'missing' if not installed.is_dir() else ('ok' if digest(d)==digest(installed) else 'stale')})
        for d in skills_dir.iterdir():
            if d.is_dir() and not (source/d.name).exists(): drift.append({'skill':d.name,'status':'extra'})
        out.append(report('skills.drift','warn' if any(x['status'] in ('missing','stale') for x in drift) else 'ok','Installed skill comparison',{'skills':drift}))
    return out
def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--repo',type=Path,default=Path.cwd()); ap.add_argument('--profile',type=Path); ap.add_argument('--skills-dir',type=Path); ap.add_argument('--timeout',type=float,default=10); ap.add_argument('--json',action='store_true'); ap.add_argument('--fetch',action='store_true')
    a=ap.parse_args(argv); home=Path.home(); path=os.environ.get('PATH',''); install=a.skills_dir or home/'.config/opencode/skills'
    if a.timeout<=0 or not a.repo.is_dir() or (a.profile and not a.profile.is_file()): ap.error('invalid repo, profile, or timeout')
    try: items=doctor(a.repo.resolve(),install, a.profile,home,path,a.timeout,a.fetch)
    except ValueError as e: ap.error(str(e))
    if a.json: print(json.dumps(items,indent=2))
    else:
        print(f"{'ID':28} {'STATUS':7} SUMMARY")
        for x in items: print(f"{x['id']:28} {x['status']:7} {x['summary']}")
        for x in items:
            if x['status']=='error': print('  '+str(x['details']))
    return 1 if any(x['status']=='error' for x in items) else 0
if __name__=='__main__': sys.exit(main())
