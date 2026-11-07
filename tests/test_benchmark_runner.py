import json, subprocess, tempfile, unittest
from pathlib import Path

SCRIPT=Path('.opencode/skills/benchmark-runner/scripts/benchmark.py').resolve()

class BenchmarkTests(unittest.TestCase):
 def call(self,*args): return subprocess.run(['python3',str(SCRIPT),*map(str,args)],capture_output=True,text=True)
 def test_plan_validated(self):
  with tempfile.TemporaryDirectory() as td:
   d=Path(td); (d/'task.json').write_text(json.dumps(dict(objective='do task',acceptance=['works'],commands=['true'],write_scope=['src/**']))); (d/'targets.json').write_text(json.dumps([dict(providerInstanceId='a',model='m'),dict(providerInstanceId='b',model='n',options={})]))
   out=d/'plan.json'; p=self.call('plan','--task',d/'task.json','--targets',d/'targets.json','--out',out); self.assertEqual(p.returncode,0,p.stderr); self.assertEqual(len(json.loads(out.read_text())['units']),2)
 def test_compare_and_unknown_usage(self):
  with tempfile.TemporaryDirectory() as td:
   d=Path(td); plan={'units':[{'id':'u1','target':{'providerInstanceId':'p','model':'m'}},{'id':'u2','target':{'providerInstanceId':'q','model':'n'}}]}; (d/'plan.json').write_text(json.dumps(plan)); (d/'u1.json').write_text(json.dumps({'verdict':'pass','changed_paths':['a']})); (d/'u2.json').write_text(json.dumps({'verdict':'fail','changed_paths':[]}))
   p=self.call('compare','--plan',d/'plan.json','--results',d,'--json'); data=json.loads(p.stdout); self.assertIsNone(data[0]['usage']); self.assertEqual(data[0]['review_status'],'unreviewed'); self.assertFalse(data[1]['test_pass'])
 def test_record_refuses_unreviewed(self):
  with tempfile.TemporaryDirectory() as td:
   d=Path(td); plan=d/'p.json'; plan.write_text('{}'); p=self.call('record','--plan',plan,'--unit','x','--depth','3','--outcome','partial','--verification','parent','--retries','0','--review-findings','0'); self.assertNotEqual(p.returncode,0); self.assertIn('unreviewed',p.stderr)

if __name__=='__main__': unittest.main()
