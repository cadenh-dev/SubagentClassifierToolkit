import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / '.opencode/skills/subagent-orchestrator'
VALIDATOR = ORCH / 'scripts/validate_plan.py'
VERIFY = ORCH / 'scripts/verify_unit.py'
EXAMPLE = ORCH / 'assets/plan.example.json'
FIXTURES = ROOT / 'tests/fixtures/multi_repo'


def unit(uid, *, repo='one', deps=None, isolation='worktree', role='implementation',
         scope=None, brief='Do the assigned work.', deliverable='A commit.'):
    return {
        'id': uid, 'title': uid, 'role': role, 'objective': 'Complete the work.',
        'depends_on': deps or [], 'isolation': isolation, 'mode': 'wait',
        'target': {'providerInstanceId': 'codex', 'model': 'gpt-6.1-sol'},
        'write_scope': scope if scope is not None else ['src/**'],
        'acceptance': ['The work is complete.'], 'commands': [],
        'deliverable': deliverable, 'brief': brief, 'repo': repo,
    }


def plan(units=None, *, repos=None, integration=None, mode='delegated'):
    units = units if units is not None else [unit('worker')]
    return {
        'goal': 'Complete the delegated work.', 'mode': mode, 'units': units,
        'integration': integration or {
            'order': [u['id'] for u in units], 'tests': [], 'review': False,
        },
        'repos': repos if repos is not None else {
            'one': {'remote': 'owner/one', 'base_branch': 'main', 'integration_branch': 'integrate'},
            'two': {'remote': 'owner/two', 'base_branch': 'main', 'integration_branch': 'integrate'},
        },
    }


class MultiRepoPlanTests(unittest.TestCase):
    def validate(self, data):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', encoding='utf-8') as handle:
            json.dump(data, handle)
            handle.flush()
            result = subprocess.run(
                ['python3', str(VALIDATOR), '--plan', handle.name, '--json'],
                capture_output=True, text=True, check=False,
            )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return json.loads(result.stdout)

    def assert_error(self, data, message):
        result = self.validate(data)
        self.assertTrue(any(message in error for error in result['errors']), result)

    def test_unknown_repo_key_on_unit(self):
        self.assert_error(plan([unit('worker', repo='missing')]), 'unknown repo key')

    def test_implementation_write_scope_requires_repo(self):
        u = unit('worker', repo=None)
        u.pop('repo')
        self.assert_error(plan([u]), 'implementation unit with write_scope must name a repo')

    def test_same_scope_in_different_repos_is_allowed(self):
        result = self.validate(plan([unit('first', repo='one'), unit('second', repo='two')]))
        self.assertFalse(any('share a shared write scope' in e for e in result['errors']))

    def test_same_scope_in_same_shared_repo_is_rejected(self):
        result = self.validate(plan([
            unit('first', isolation='shared'), unit('second', isolation='shared'),
        ]))
        self.assertTrue(any('share a shared write scope' in e for e in result['errors']))

    def test_cross_repo_dependency_cycle_is_rejected(self):
        self.assert_error(plan([
            unit('first', repo='one', deps=['second']),
            unit('second', repo='two', deps=['first']),
        ]), 'dependency cycle')

    def test_stack_unknown_repo_is_rejected(self):
        self.assert_error(plan(integration={
            'order': ['worker'], 'tests': [], 'review': False,
            'stack': [{'repo': 'missing', 'units': ['worker'], 'pr_base': 'main'}],
        }), 'unknown repo key')

    def test_stack_duplicate_unit_is_rejected(self):
        self.assert_error(plan([unit('first'), unit('second')], integration={
            'order': ['first', 'second'], 'tests': [], 'review': False,
            'stack': [
                {'repo': 'one', 'units': ['first'], 'pr_base': 'main'},
                {'repo': 'one', 'units': ['first', 'second'], 'pr_base': 'first'},
            ],
        }), 'more than once')

    def test_stack_unit_in_wrong_repo_is_rejected(self):
        self.assert_error(plan([unit('worker', repo='two')], integration={
            'order': ['worker'], 'tests': [], 'review': False,
            'stack': [{'repo': 'one', 'units': ['worker'], 'pr_base': 'main'}],
        }), 'stack repo does not match')

    def test_stack_order_must_follow_dependencies(self):
        self.assert_error(plan([
            unit('consumer', repo='two', deps=['producer']), unit('producer', repo='one'),
        ], integration={
            'order': ['producer', 'consumer'], 'tests': [], 'review': False,
            'stack': [
                {'repo': 'two', 'units': ['consumer'], 'pr_base': 'main'},
                {'repo': 'one', 'units': ['producer'], 'pr_base': 'main'},
            ],
        }), 'integration.stack puts consumer before dependency producer')

    def test_stack_pr_base_must_be_valid_branch(self):
        self.assert_error(plan(integration={
            'order': ['worker'], 'tests': [], 'review': False,
            'stack': [{'repo': 'one', 'units': ['worker'], 'pr_base': 'random-branch'}],
        }), 'pr_base must be repo base/integration branch')

    def test_stack_pr_base_can_use_prior_unit_branch(self):
        result = self.validate(plan([unit('first'), unit('second')], integration={
            'order': ['first', 'second'], 'tests': [], 'review': False,
            'stack': [
                {'repo': 'one', 'units': ['first'], 'pr_base': 'main'},
                {'repo': 'one', 'units': ['second'], 'pr_base': 'first'},
            ],
        }))
        self.assertFalse(any('pr_base must be' in e for e in result['errors']), result)

    def test_multi_repo_brief_watch_is_error(self):
        self.assert_error(plan([unit('worker', brief='Watch the PR until green.')]),
                         'child must not watch or babysit a PR')

    def test_multi_repo_deliverable_babysit_is_error(self):
        self.assert_error(plan([unit('worker', deliverable='Babysit the pull request.')]),
                         'child must not watch or babysit a PR')

    def test_single_repo_watch_is_warning(self):
        data = plan([unit('worker', brief='Watch the PR until green.')], repos=None, mode='single')
        data.pop('repos')
        result = self.validate(data)
        self.assertFalse(any('child must not watch' in e for e in result['errors']))
        self.assertTrue(any('mentions watching a PR' in w for w in result['warnings']))

    def test_single_repo_example_strict_output_matches_baseline(self):
        result = subprocess.run(
            ['python3', str(VALIDATOR), '--plan', str(EXAMPLE), '--strict'],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, (FIXTURES / 'plan-example.strict.stdout').read_text())
        self.assertEqual(result.stderr, '')

    def test_multi_repo_fixture_passes_strict(self):
        result = subprocess.run(
            ['python3', str(VALIDATOR), '--plan', str(FIXTURES / 'valid-plan.json'), '--strict'],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def _verify_remote(self, actual_remote, *, expected='https://GitHub.com/Owner/Repo.git'):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / 'repo'
            repo.mkdir()
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'test@example.com'], check=True)
            subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'Test'], check=True)
            (repo / 'tracked.txt').write_text('ready\n')
            subprocess.run(['git', '-C', str(repo), 'add', 'tracked.txt'], check=True)
            subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'initial'], check=True)
            subprocess.run(['git', '-C', str(repo), 'remote', 'add', 'origin', actual_remote], check=True)
            data = plan([unit('worker', scope=[], repo='one')], repos={
                'one': {'remote': expected, 'base_branch': 'main'},
            })
            data['units'][0]['commands'] = ['true']
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', encoding='utf-8') as handle:
                json.dump(data, handle)
                handle.flush()
                result = subprocess.run([
                    'python3', str(VERIFY), '--plan', handle.name, '--unit', 'worker',
                    '--repo', str(repo), '--base', 'HEAD', '--allow-commands',
                ], capture_output=True, text=True, check=False)
        report = json.loads(result.stdout)
        return result.returncode, report

    def test_verify_normalizes_scp_ssh_remote(self):
        code, report = self._verify_remote('git@github.com:owner/repo.git')
        self.assertNotEqual(code, 2, report)
        self.assertFalse(any('origin remote mismatch' in e for e in report['errors']), report)

    def test_verify_normalizes_ssh_url_remote(self):
        code, report = self._verify_remote('ssh://git@github.com/owner/repo')
        self.assertNotEqual(code, 2, report)
        self.assertFalse(any('origin remote mismatch' in e for e in report['errors']), report)

    def test_verify_normalizes_https_git_suffix_remote(self):
        code, report = self._verify_remote('https://github.com/owner/repo.git')
        self.assertNotEqual(code, 2, report)
        self.assertFalse(any('origin remote mismatch' in e for e in report['errors']), report)

    def test_verify_normalizes_trailing_slash_and_host_case(self):
        code, report = self._verify_remote('HTTPS://GITHUB.COM/OWNER/REPO.GIT///')
        self.assertNotEqual(code, 2, report)
        self.assertFalse(any('origin remote mismatch' in e for e in report['errors']), report)

    def test_verify_reports_remote_mismatch(self):
        code, report = self._verify_remote('git@github.com:someone/else.git')
        self.assertEqual(code, 2, report)
        self.assertTrue(any('origin remote mismatch' in e for e in report['errors']), report)


if __name__ == '__main__':
    unittest.main()
