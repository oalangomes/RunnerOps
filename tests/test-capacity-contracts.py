#!/usr/bin/env python3
"""Public CLI contracts with strict read-only GitHub/systemd fixtures."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
from urllib.parse import urlparse, parse_qs
args = sys.argv[1:]
tool = pathlib.Path(sys.argv[0]).name
with open(os.environ['CAPACITY_CALLS'], 'a') as log:
    log.write(json.dumps([tool, *args]) + '\n')
data = json.loads(pathlib.Path(os.environ['CAPACITY_FIXTURE']).read_text())
if tool == 'gh':
    if args[:2] == ['repo', 'view']:
        if data.get('identity_fail'): sys.exit(1)
        print('Example/MixedCase'); sys.exit(0)
    assert args[:3] == ['api', '--method', 'GET'], args
    url = urlparse(args[3])
    query = parse_qs(url.query)
    assert url.path.startswith('repos/Example/MixedCase/actions/'), args
    page = int(query['page'][0])
    if url.path.endswith('/runners'):
        if data.get('remote_fail'): sys.exit(1)
        rows, key = data['remote'], 'runners'
    elif url.path.endswith('/jobs'):
        if data.get('jobs_fail'): sys.exit(1)
        run = url.path.split('/runs/')[1].split('/')[0]
        attempt = int(url.path.split('/attempts/')[1].split('/')[0])
        assert attempt == data.get('attempt', 1), args
        rows, key = data['jobs'].get(run, []), 'jobs'
    else:
        assert url.path.endswith('/runs'), args
        status = query['status'][0]
        if data.get('queue_fail') == status: sys.exit(1)
        rows, key = data['runs'].get(status, []), 'workflow_runs'
    if data.get('second_page_fail') == key and page == 2: sys.exit(1)
    total = data.get('total_override', {}).get(key, len(rows))
    payload = {key: rows[(page - 1) * 100:page * 100], 'total_count': total}
    if data.get('malformed') == key: payload = {key: None}
    print(json.dumps(payload)); sys.exit(0)
if tool == 'systemctl':
    if data.get('systemd_fail'): sys.exit(1)
    if args[0] == 'list-unit-files':
        for name in data['services']: print(name, 'disabled')
        sys.exit(0)
    assert args[0] == 'show', args
    props = data['services'].get(args[1], {})
    for key, value in props.items(): print(f'{key}={value}')
    sys.exit(0)
if tool == 'git':
    assert args == ['remote', 'get-url', 'origin'], args
    print('git@github.com:Example/MixedCase.git'); sys.exit(0)
raise AssertionError((tool, args))
'''


class CapacityContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        for name in ('gh', 'systemctl', 'git', 'sudo'):
            target = self.bin / name
            target.write_text(FAKE)
            target.chmod(0o755)
        self.registry = self.base / 'runners.conf'
        self.fixture = self.base / 'fixture.json'
        self.calls = self.base / 'calls.jsonl'
        self.data = {'runs': {}, 'jobs': {}, 'remote': [], 'services': {}}
        self.registry.write_text('')
        self.env = dict(os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}',
                        ACTIONS_RUNNERS_HOME=str(ROOT), ACTIONS_RUNNERS_ENV=str(self.base / 'missing.env'),
                        RUNNERS_CONFIG=str(self.registry), RUNNER_BOOT_POLICY='on-demand',
                        RUNNER_STATE_ROOT=str(self.base / 'state'), RUNNER_CACHE_ROOT=str(self.base / 'cache'),
                        CAPACITY_FIXTURE=str(self.fixture), CAPACITY_CALLS=str(self.calls))

    def runner(self, number=1, state='active', busy=False, repo='example/mixedcase', enabled=True):
        name = f'runner-{number}'
        path = self.base / name
        path.mkdir()
        (path / '.runner').write_text(json.dumps({'agentId': number, 'agentName': name,
                                                'gitHubUrl': f'https://github.com/{repo}'}))
        (path / '.credentials').write_text('private fixture; never output')
        (path / 'run.sh').write_text('#!/bin/sh\nexit 0\n')
        (path / 'run.sh').chmod(0o755)
        unit = f'actions.runner.example.{name}.service'
        (path / '.service').write_text(unit)
        with self.registry.open('a') as file:
            file.write(f'{name}|{path}|generic|{repo}|{str(enabled).lower()}|example\n')
        self.data['services'][unit] = {
            'LoadState': 'loaded', 'ActiveState': state, 'SubState': 'running' if state == 'active' else 'dead',
            'UnitFileState': 'disabled', 'WorkingDirectory': str(path), 'Result': 'success'}
        if repo.lower() == 'example/mixedcase':
            self.data['remote'].append({'id': number, 'name': name,
                                        'status': 'online' if state == 'active' else 'offline', 'busy': busy,
                                        'labels': [{'name': label} for label in ['self-hosted', 'Linux', 'X64']]})
        return path

    def job(self, number=101, run_status='in_progress', required=None, run=10):
        self.data['runs'].setdefault(run_status, [])
        if not any(r['id'] == run for r in self.data['runs'][run_status]):
            self.data['runs'][run_status].append({
                'id': run, 'workflow_id': 7, 'name': 'Build', 'status': run_status,
                'run_attempt': self.data.get('attempt', 1), 'head_sha': 'abc123', 'head_branch': 'feature'})
        self.data['jobs'].setdefault(str(run), []).append({
            'id': number, 'name': 'test "quoted"\njob', 'status': 'queued',
            'created_at': '2020-01-01T00:00:00Z', 'labels': required or ['SELF-HOSTED', 'linux']})

    def invoke(self, *args, expected=0, text=False):
        self.fixture.write_text(json.dumps(self.data))
        before = {str(p): p.read_bytes() for p in self.base.rglob('*')
                  if p.is_file() and p != self.calls}
        result = subprocess.run([str(ROOT / 'runnerctl'), *args, *([] if text else ['--json'])],
                                env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, expected, result.stderr + result.stdout)
        after = {str(p): p.read_bytes() for p in self.base.rglob('*')
                 if p.is_file() and p != self.calls}
        self.assertEqual(before, after, 'observation must not modify or create runtime files')
        self.assertNotIn('private fixture', result.stdout + result.stderr)
        for call in [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []:
            if call[0] == 'gh':
                self.assertTrue(call[1:3] == ['repo', 'view'] or call[1:4] == ['api', '--method', 'GET'], call)
            elif call[0] == 'systemctl':
                self.assertIn(call[1], ('show', 'list-unit-files'))
            else:
                self.assertEqual(call, ['git', 'remote', 'get-url', 'origin'])
        return result.stdout if text else json.loads(result.stdout)

    def test_all_busy_and_queued_is_capacity_pressure(self):
        self.runner(busy=True)
        self.runner(2, busy=True)
        self.job()
        result = self.invoke('capacity', 'example/mixedcase')
        self.assertEqual(result['capacity']['counts']['busy_capacity'], 2)
        job = result['queue']['jobs'][0]
        self.assertEqual(job['capacity_status'], 'busy_capacity')
        self.assertEqual(job['matching_runner_ids'], [1, 2])
        self.assertEqual((job['run_id'], job['run_attempt'], job['workflow_id']), (10, 1, 7))
        self.assertGreater(job['queue_age_seconds'], 0)
        self.assertEqual(job['queue_age_source'], 'job.created_at')
        self.assertEqual(result['queue']['oldest_matching_queued_job_id'], 101)
        self.assertEqual(result['schema_version'], 1)
        self.assertEqual(result['kind'], 'CapacitySnapshot')
        self.assertEqual(result['repository']['nameWithOwner'], 'Example/MixedCase')

    def test_healthy_on_demand_is_provisioned_idle(self):
        self.runner(state='inactive')
        self.job()
        result = self.invoke('capacity')
        self.assertEqual(result['capacity']['counts']['provisioned_idle'], 1)
        self.assertEqual(result['queue']['jobs'][0]['capacity_status'], 'provisioned_idle')
        self.assertEqual(result['host']['active_local_runner_count'], 0)

    def test_available_alias_and_current_repository(self):
        self.runner()
        self.job()
        result = self.invoke('autoscale', 'status', '.')
        self.assertEqual(result['capacity']['counts']['available_now'], 1)
        self.assertEqual(result['queue']['jobs'][0]['capacity_status'], 'available_now')
        self.assertEqual(result['repository']['match_key'], 'example/mixedcase')
        output = self.invoke('capacity', '.', text=True)
        self.assertIn('available now=1', output)
        self.assertIn('Example/MixedCase', output)

    def test_unmatched_labels(self):
        self.runner()
        self.job(required=['self-hosted', 'GPU'])
        result = self.invoke('capacity')
        self.assertEqual(result['queue']['jobs'][0]['capacity_status'], 'no_matching_capacity')
        self.assertIsNone(result['queue']['oldest_matching_queued_job_id'])

    def test_api_failures_remain_inconclusive(self):
        self.runner()
        self.job()
        for key, value in [('remote_fail', True), ('queue_fail', 'queued'), ('jobs_fail', True),
                           ('identity_fail', True), ('malformed', 'runners')]:
            with self.subTest(key=key):
                self.data[key] = value
                result = self.invoke('capacity', expected=3)
                self.assertEqual(result['status'], 'inconclusive')
                if key in ('queue_fail', 'jobs_fail', 'identity_fail'):
                    self.assertIsNone(result['queue']['queued_job_count'])
                if key == 'identity_fail':
                    self.assertIsNone(result['repository']['nameWithOwner'])
                    self.assertEqual(result['repository']['match_key'], 'example/mixedcase')
                del self.data[key]

    def test_systemd_failure_and_contradictions(self):
        self.runner()
        self.job()
        self.data['systemd_fail'] = True
        result = self.invoke('capacity', expected=3)
        self.assertEqual(result['capacity']['counts']['inconclusive'], 1)
        self.assertIsNone(result['host']['active_local_runner_count'])
        del self.data['systemd_fail']
        service = next(iter(self.data['services'].values()))
        original = service.copy()
        for change in ({'ActiveState': 'failed'}, {'LoadState': 'not-found'},
                       {'WorkingDirectory': '/somewhere/else'}, {'SubState': 'exited'},
                       {'ActiveState': 'inactive', 'SubState': 'dead'}):
            with self.subTest(change=change):
                service.clear()
                service.update(original, **change)
                result = self.invoke('capacity', expected=3)
                self.assertEqual(result['capacity']['counts']['available_now'], 0)

    def test_inactive_masked_auto_or_failed_is_not_healthy_idle(self):
        self.runner(state='inactive')
        service = next(iter(self.data['services'].values()))
        original = service.copy()
        for change in ({'UnitFileState': 'masked'}, {'Result': 'exit-code'}):
            service.clear()
            service.update(original, **change)
            result = self.invoke('capacity', expected=3)
            self.assertEqual(result['capacity']['counts']['provisioned_idle'], 0)
        service.clear()
        service.update(original)
        self.env['RUNNER_BOOT_POLICY'] = 'auto'
        self.invoke('capacity', expected=3)

    def test_registration_identity_and_disabled_records(self):
        path = self.runner()
        self.job()
        original = (path / '.runner').read_text()
        for content in ('{}', 'not-json', json.dumps({'agentId': 99, 'agentName': 'runner-1'}),
                        json.dumps({'agentId': 1, 'agentName': 'wrong'})):
            (path / '.runner').write_text(content)
            result = self.invoke('capacity', expected=3)
            self.assertEqual(result['capacity']['counts']['available_now'], 0)
        (path / '.runner').write_text(original)
        self.registry.write_text(self.registry.read_text().replace('|true|', '|false|'))
        result = self.invoke('capacity', expected=3)
        self.assertEqual(result['capacity']['runners'][0]['reason'], 'registry_disabled')
        self.assertEqual(result['queue']['jobs'][0]['matching_runner_ids'], [])

    def test_host_count_includes_other_repositories(self):
        self.runner()
        self.runner(2, repo='example/other')
        result = self.invoke('capacity')
        self.assertEqual(result['host']['active_local_runner_count'], 2)
        self.assertEqual(len(result['capacity']['runners']), 1)

    def test_missing_marker_discovers_systemd_without_pid_fallback(self):
        path = self.runner()
        (path / '.service').unlink()
        self.assertEqual(self.invoke('capacity')['capacity']['counts']['available_now'], 1)

    def test_remote_only_runner_does_not_claim_local_availability(self):
        self.job()
        self.data['remote'] = [{'id': 8, 'name': 'elsewhere', 'status': 'online', 'busy': False,
                                'labels': [{'name': 'self-hosted'}, {'name': 'linux'}]}]
        result = self.invoke('capacity', expected=3)
        self.assertEqual(result['capacity']['runners'][0]['scope'], 'remote_only')
        self.assertEqual(result['queue']['jobs'][0]['capacity_status'], 'inconclusive')

    def test_missing_labels_or_age_never_invents_evidence(self):
        self.runner()
        self.job()
        job = self.data['jobs']['10'][0]
        job['created_at'] = None
        result = self.invoke('capacity', expected=3)
        self.assertIsNone(result['queue']['jobs'][0]['queue_age_seconds'])
        self.assertIsNone(result['queue']['oldest_queued_job_id'])
        job['created_at'] = '2020-01-01T00:00:00Z'
        job['labels'] = []
        result = self.invoke('capacity', expected=3)
        self.assertEqual(result['queue']['jobs'][0]['capacity_status'], 'inconclusive')

    def test_pagination_runs_jobs_and_runners_latest_attempt(self):
        self.runner()
        self.data['attempt'] = 2
        for number in range(101):
            self.job(number=1000 + number, run=10 + number)
        # More than a page of jobs within the first run, too.
        for number in range(101):
            self.job(number=2000 + number, run=10)
        # Nonmatching remote runners still need to be observable on later pages.
        for number in range(2, 103):
            self.data['remote'].append({'id': number, 'name': f'remote-{number}', 'status': 'offline',
                                        'busy': False, 'labels': [{'name': 'other'}]})
        result = self.invoke('capacity', expected=3)
        self.assertEqual(result['queue']['queued_job_count'], 202)
        self.assertEqual(len(result['capacity']['runners']), 102)
        self.assertTrue(all(job['run_attempt'] == 2 for job in result['queue']['jobs']))

    def test_partial_pagination_and_api_cap(self):
        self.runner()
        for number in range(101):
            self.job(number=1000 + number)
        self.data['second_page_fail'] = 'jobs'
        result = self.invoke('capacity', expected=3)
        self.assertEqual(result['queue']['observed_queued_job_count'], 100)
        self.assertIsNone(result['queue']['queued_job_count'])
        del self.data['second_page_fail']
        self.data['total_override'] = {'workflow_runs': 1001}
        result = self.invoke('capacity', expected=3)
        self.assertIn({'source': 'queue', 'reason': 'incomplete_pagination'}, result['errors'])

    def test_dedup_runs_and_excludes_nonqueued_jobs(self):
        self.runner()
        self.job(run_status='queued')
        self.data['runs']['in_progress'] = copy.deepcopy(self.data['runs']['queued'])
        self.data['jobs']['10'].extend([
            {'id': 102, 'status': 'in_progress'}, {'id': 103, 'status': 'completed'}])
        result = self.invoke('capacity')
        self.assertEqual(result['queue']['queued_job_count'], 1)

    def test_unknown_remote_labels_and_busy_are_inconclusive(self):
        self.runner()
        self.job()
        remote = self.data['remote'][0]
        original = copy.deepcopy(remote)
        for change, reason in [({'labels': None}, 'github_labels_unknown'),
                               ({'busy': None}, 'github_state_unknown'),
                               ({'status': 'unknown'}, 'github_state_unknown')]:
            remote.clear()
            remote.update(original, **change)
            result = self.invoke('capacity', expected=3)
            self.assertEqual(result['capacity']['runners'][0]['reason'], reason)
            self.assertEqual(result['queue']['jobs'][0]['capacity_status'], 'inconclusive')

    def test_unknown_candidate_prevents_all_busy_claim(self):
        self.runner(busy=True)
        self.runner(2, state='failed')
        self.job()
        result = self.invoke('capacity', expected=3)
        self.assertEqual(result['queue']['jobs'][0]['capacity_status'], 'inconclusive')
        self.assertEqual(result['queue']['jobs'][0]['matching_capacity']['busy_capacity'], 1)

    def test_duplicate_registry_cannot_inflate_active_host_count(self):
        self.runner()
        self.registry.write_text(self.registry.read_text() * 2)
        result = self.invoke('capacity', expected=3)
        self.assertIsNone(result['host']['active_local_runner_count'])
        self.assertEqual(result['host']['observed_active_local_runner_count'], 1)
        self.assertEqual(result['capacity']['counts']['available_now'], 0)

    def test_exact_filtered_api_limit_remains_inconclusive(self):
        # Exercise GitHub's 1,000-result search ceiling without 1,000 job API calls.
        spec = importlib.util.spec_from_file_location('capacity', ROOT / 'capacity.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        payload = json.dumps({'workflow_runs': [{'id': i} for i in range(100)], 'total_count': 1000})
        errors = []
        with patch.object(module, 'command', return_value=payload):
            rows = module.api_pages('repos/Example/MixedCase/actions/runs?status=queued',
                                    'workflow_runs', errors, 'queue', max_pages=10)
        self.assertEqual(len(rows), 1000)
        self.assertIn({'source': 'queue', 'reason': 'pagination_limit'}, errors)

    def test_missing_registry_is_not_empty_capacity(self):
        self.registry.unlink()
        result = self.invoke('capacity', expected=3)
        self.assertEqual(result['sources']['local'], 'inconclusive')
        self.assertIsNone(result['host']['active_local_runner_count'])

    def test_empty_queue_and_cli_rejections(self):
        result = self.invoke('capacity')
        self.assertEqual(result['queue']['queued_job_count'], 0)
        self.assertIsNone(result['queue']['oldest_queued_job_id'])
        for args, code in [(('capacity', '--apply'), 2), (('capacity', 'bad'), 2),
                           (('capacity', '.', 'Example/MixedCase'), 2),
                           (('autoscale', 'apply'), 1), (('autoscale',), 1)]:
            self.invoke(*args, expected=code, text=True)


if __name__ == '__main__':
    unittest.main()
