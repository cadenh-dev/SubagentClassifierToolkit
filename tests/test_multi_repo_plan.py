import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / '.opencode/skills/subagent-orchestrator/scripts/validate_plan.py'
EXAMPLE = ROOT / '.opencode/skills/subagent-orchestrator/assets/plan.multi-repo.example.json'

class MultiRepoPlanTests(unittest.TestCase):
    def test_example_strict(self):
        result = subprocess.run(['python3', str(VALIDATOR), '--plan', str(EXAMPLE), '--strict'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

if __name__ == '__main__': unittest.main()
