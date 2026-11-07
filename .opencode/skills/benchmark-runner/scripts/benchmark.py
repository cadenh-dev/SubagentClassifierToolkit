#!/usr/bin/env python3
"""Generate, score, compare, and record human-reviewed model benchmarks."""
from __future__ import annotations
import argparse, json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
ORCH = ROOT / ".opencode/skills/subagent-orchestrator/scripts"
RESULTS = Path(".benchmark-results")

def run(cmd):
    return subprocess.run(cmd, text=True, capture_output=True)

def plan(args):
    task=json.loads(Path(args.task).read_text()); targets=json.loads(Path(args.targets).read_text())
    if not isinstance(task,dict) or not isinstance(targets,list) or not targets: raise ValueError("task must be an object and targets a non-empty array")
    required=("objective","acceptance","commands","write_scope")
    if any(k not in task for k in required): raise ValueError("task requires objective, acceptance, commands, and write_scope")
    bid="bench-"+__import__('uuid').uuid4().hex[:12]
    units=[]
    for i,t in enumerate(targets,1):
        if not isinstance(t,dict) or not isinstance(t.get("providerInstanceId"),str) or not isinstance(t.get("model"),str): raise ValueError("each target needs providerInstanceId and model")
        uid=f"{bid}-{i}"
        brief=f"Run benchmark {bid}. Implement the identical task: {task['objective']}\nAcceptance: {json.dumps(task['acceptance'])}\nCommands: {json.dumps(task['commands'])}\nWrite scope: {json.dumps(task['write_scope'])}. Commit your work on your assigned branch."
        units.append(dict(id=uid,title=f"Benchmark target {i}",role="implementation",objective=task["objective"],depends_on=[],isolation="worktree",mode="async",target=t,write_scope=task["write_scope"],acceptance=task["acceptance"],commands=task["commands"],deliverable=f"Commit implementation on distinct branch bench/{bid}/{i} and report commit SHA.",brief=brief))
    out=dict(goal=f"{bid}: {task['objective']}",mode="delegated",units=units,integration=dict(order=[u["id"] for u in units],tests=[],review=True,notes=f"Benchmark id {bid}; each unit must use a separate worktree and branch."),fallback="Parent launches each target and reviews results.")
    Path(args.out).write_text(json.dumps(out,indent=2)+"\n")
    check=run([sys.executable,str(ORCH/"validate_plan.py"),"--plan",args.out,"--strict"])
    if check.returncode: Path(args.out).unlink(missing_ok=True); raise ValueError(check.stdout+check.stderr)
    print(json.dumps({"benchmark_id":bid,"plan":args.out,"units":[u["id"] for u in units]}))

def score(args):
    report=RESULTS/args.plan.stem/f"{args.unit}.json"; report.parent.mkdir(parents=True,exist_ok=True)
    cmd=[sys.executable,str(ORCH/"verify_unit.py"),"--plan",str(args.plan),"--unit",args.unit,"--repo",str(args.repo),"--base",args.base]
    if args.allow_commands: cmd.append("--allow-commands")
    p=run(cmd); report.write_text(p.stdout)
    print(json.dumps({"report":str(report),"exit_code":p.returncode})); return p.returncode

def pairs(vals):
    out={}
    for v in vals:
        k,sep,val=v.partition("=")
        if not sep: raise ValueError(f"expected id=value: {v}")
        out[k]=val
    return out

def compare(args):
    plan=json.loads(args.plan.read_text()); d=args.results or RESULTS/args.plan.stem
    reviewed=set(args.reviewed); findings=pairs(args.review_findings); duration=pairs(args.duration); usage=pairs(args.usage); rows=[]
    for u in plan["units"]:
        f=d/f"{u['id']}.json"; r=json.loads(f.read_text()) if f.exists() else {}
        paths=r.get("changed_paths",[]); diff={"files":len(paths),"insertions":None,"deletions":None}
        if r.get("base") and r.get("head"):
            p=run(["git","-C",str(args.repo or Path.cwd()),"diff","--shortstat",f"{r['base']}..{r['head']}"])
            m=re.search(r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?",p.stdout)
            if m: diff={"files":int(m[1]),"insertions":int(m[2] or 0),"deletions":int(m[3] or 0)}
        rows.append({"id":u["id"],"target":u["target"],"test_pass":r.get("verdict")=="pass","out_of_scope":r.get("out_of_scope_paths",[]),"review_findings":int(findings[u["id"]]) if u["id"] in findings else None,"duration":float(duration[u["id"]]) if u["id"] in duration else None,"usage":usage.get(u["id"]),"diff":diff,"review_status":"reviewed" if u["id"] in reviewed else "unreviewed"})
    if args.json: print(json.dumps(rows,indent=2)); return
    print("ID | Target | Tests | Out of scope | Findings | Duration | Usage | Diff | Review")
    for r in rows: print(f"{r['id']} | {r['target']['providerInstanceId']}:{r['target']['model']} | {r['test_pass']} | {len(r['out_of_scope'])} | {r['review_findings'] if r['review_findings'] is not None else 'unknown'} | {r['duration'] if r['duration'] is not None else 'unknown'} | {r['usage'] or 'unknown'} | {r['diff']} | {r['review_status']}")

def record(args):
    if args.unit not in set(args.reviewed): raise ValueError("refusing to record unreviewed unit; pass --reviewed <id>")
    report=(args.results or RESULTS/args.plan.stem)/f"{args.unit}.json"
    cmd=[sys.executable,str(ORCH/"record_verification.py"),"--report",str(report),"--depth",str(args.depth),"--outcome",args.outcome,"--verification",args.verification,"--retries",str(args.retries),"--review-findings",str(args.review_findings)]
    if args.acceptance_reviewed: cmd.append("--acceptance-reviewed")
    for k in ("estimated_units","actual_units","duration","pr","notes","ledger"):
        val=getattr(args,k,None)
        if val is not None: cmd += ["--"+k.replace("_","-"),str(val)]
    p=run(cmd); sys.stdout.write(p.stdout); sys.stderr.write(p.stderr); return p.returncode

def main():
    ap=argparse.ArgumentParser(); sp=ap.add_subparsers(dest="command",required=True)
    p=sp.add_parser("plan"); p.add_argument("--task",required=True); p.add_argument("--targets",required=True); p.add_argument("--out",required=True); p.set_defaults(fn=plan)
    p=sp.add_parser("score"); p.add_argument("--plan",type=Path,required=True); p.add_argument("--unit",required=True); p.add_argument("--repo",type=Path,required=True); p.add_argument("--base",required=True); p.add_argument("--allow-commands",action="store_true"); p.set_defaults(fn=score)
    p=sp.add_parser("compare"); p.add_argument("--plan",type=Path,required=True); p.add_argument("--results",type=Path); p.add_argument("--repo",type=Path); p.add_argument("--review-findings",action="append",default=[]); p.add_argument("--duration",action="append",default=[]); p.add_argument("--usage",action="append",default=[]); p.add_argument("--reviewed",action="append",default=[]); p.add_argument("--json",action="store_true"); p.set_defaults(fn=compare)
    p=sp.add_parser("record"); p.add_argument("--plan",type=Path,required=True); p.add_argument("--unit",required=True); p.add_argument("--reviewed",action="append",default=[]); p.add_argument("--results",type=Path); p.add_argument("--depth",type=int,choices=range(1,6),required=True); p.add_argument("--outcome",choices=("success","failure","partial","cancelled"),required=True); p.add_argument("--verification",choices=("parent","child-report","none"),required=True); p.add_argument("--retries",type=int,required=True); p.add_argument("--review-findings",type=int,required=True); p.add_argument("--acceptance-reviewed",action="store_true")
    for k in ("estimated_units","actual_units","duration","pr","notes","ledger"): p.add_argument("--"+k.replace("_","-"),type=Path if k=="ledger" else str)
    p.set_defaults(fn=record)
    a=ap.parse_args()
    try: result=a.fn(a); return result if isinstance(result,int) else 0
    except (ValueError,KeyError,OSError,json.JSONDecodeError) as e: print(str(e),file=sys.stderr); return 2
if __name__=="__main__": raise SystemExit(main())
