import json
import tempfile
import unittest
from pathlib import Path
import importlib.util

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('doctor',ROOT/'.opencode/skills/toolkit-doctor/scripts/doctor.py')
d=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(d)

class DoctorTests(unittest.TestCase):
    def test_skill_drift(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)/'repo'; installed=Path(t)/'install'; (root/'.opencode/skills/a').mkdir(parents=True); (root/'.opencode/skills/a/SKILL.md').write_text('source')
            installed.mkdir(); (installed/'a').mkdir(); (installed/'a/SKILL.md').write_text('stale'); (installed/'extra').mkdir()
            result=d.doctor(root,installed,None,Path(t),'/definitely-empty',.1)
            drift=next(x for x in result if x['id']=='skills.drift')['details']['skills']
            self.assertEqual({x['skill']:x['status'] for x in drift},{'a':'stale','extra':'extra'})
    def test_config_only_exposes_names(self):
        obj={'mcpServers':{'safe-name':{'env':{'TOKEN':'fake-secret'},'headers':{'Authorization':'fake-header'},'url':'https://user:pass@example.invalid'}}}
        result=d.servers_from(obj)
        self.assertEqual(result,['safe-name']); self.assertNotIn('fake-secret',json.dumps(result))
