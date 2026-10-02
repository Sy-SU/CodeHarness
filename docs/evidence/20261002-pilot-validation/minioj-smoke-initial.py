"""Controlled token echo smoke, plus GET-only formal feedback projection."""
import json
import os
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import httpx
from dotenv import dotenv_values

ROOT = Path('/Users/susenyang/Code/CodeHarness')
sys.path.insert(0, str(ROOT))
from agent.config import ClientSettings
from agent.core.checker import CheckerSpec, check_run
from agent.execution import fingerprint
from agent.oj_client.client import OJClient, OJClientError
from agent.oj_client.feedback import VERDICT_ONLY_POLICY, compatible_feedback
from agent.tools.runtime import ToolRuntime
from experiments.config import ExperimentConfig
from experiments.freeze import runtime_source_hash

OUT = ROOT / 'workspace/.pilot-validation/pilot-gate-20261002'
STDIN = 'CODEHARNESS_TOKEN_A CODEHARNESS_TOKEN_B 42\n'
EXPECTED = 'CODEHARNESS_TOKEN_A CODEHARNESS_TOKEN_B 42\n'
CPP = '''#include <iostream>
#include <string>
int main() {
    std::string token;
    while (std::cin >> token) std::cout << "\\t " << token << " \\n";
    return 0;
}
'''
SUBMISSION_ID = '103'
ALLOWED_GETS = {'/openapi.json', '/api/v1/me', '/api/v1/submissions/103',
                '/api/v1/submissions/103/feedback'}


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_write(path, data):
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    for secret in SECRETS:
        text = text.replace(secret, '<redacted>')
    with path.open('x', encoding='utf-8') as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


class MemoryTrace:
    def __init__(self):
        self.events = []

    def append(self, event_type, payload, **kwargs):
        self.events.append({'event_type': event_type, 'payload': payload, **kwargs})


values = {**os.environ, **{key: value for key, value in dotenv_values(ROOT / '.env').items()
                         if value is not None}}
SECRETS = [value for key, value in values.items() if value and any(
    marker in key.upper() for marker in ('SECRET', 'TOKEN', 'API_KEY', 'PASSWORD'))]
settings = ClientSettings.from_environment(values)
config = ExperimentConfig.from_yaml(ROOT / 'config/experiment.pilot.yaml')
source = {'git_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
          'runtime_source_hash': runtime_source_hash(),
          'experiment_config_file_hash': sha256((ROOT / 'config/experiment.pilot.yaml').read_bytes()).hexdigest(),
          'script_sha256': sha256(Path(__file__).read_bytes()).hexdigest()}
plan = {'purpose': 'controlled_token_echo_only', 'source_code': CPP, 'stdin': STDIN,
        'pilot_send_custom_run_code_alias': config.send_custom_run_code_alias,
        'maximum_custom_run_posts': 2, 'fallback_only_on_confirmed_missing_code_422': True,
        'formal_submissions': 0, 'algorithm_tasks': 0, 'llm_calls': 0,
        'formal_reads_of_existing_submission': SUBMISSION_ID, **source}
if sys.argv[1:] != ['--execute']:
    print(json.dumps({'dry_run': True, 'plan': plan}, indent=2))
    sys.exit(0)
OUT.mkdir(parents=True, exist_ok=True)
if any(OUT.glob('smoke-attempt-*.json')):
    raise SystemExit('Existing smoke attempt ledger: no retries permitted')

ledger = []
posts = []


def before_request(request):
    assert str(request.url).startswith(settings.oj_base_url + '/')
    path = str(request.url)[len(settings.oj_base_url):]
    if request.method == 'GET':
        assert path in ALLOWED_GETS
    else:
        assert request.method == 'POST' and path == '/api/v1/runs'
        payload = json.loads(request.content)
        expected = {'language': 'cpp20', 'source_code': CPP, 'stdin': STDIN}
        if 'code' in payload:
            expected['code'] = CPP
        assert payload == expected
        assert len(posts) < 2
        if posts:
            assert ledger[-1]['http_status'] == 422
            assert any(item['location'] == ['body', 'code'] and item['type'] == 'missing'
                       for item in ledger[-1].get('validation_errors', []))
            assert 'code' in payload
        safe_write(OUT / ('smoke-attempt-' + str(len(posts) + 1) + '.json'), {
            'status': 'attempt_started', 'at': now(), 'purpose': 'controlled_token_echo',
            'payload_keys': sorted(payload), 'code_sha256': sha256(CPP.encode()).hexdigest(),
            'stdin_sha256': sha256(STDIN.encode()).hexdigest(),
            'send_custom_run_code_alias': 'code' in payload, **source})
        posts.append(True)
    ledger.append({'method': request.method, 'path': path, 'at': now(),
                   'payload_keys': sorted(json.loads(request.content)) if request.method == 'POST' else []})


def after_response(response):
    response.read()
    entry = ledger[-1]
    entry['http_status'] = response.status_code
    entry['response_body_sha256'] = sha256(response.content).hexdigest()
    if response.status_code == 422:
        body = response.json()
        details = body.get('detail', [])
        if isinstance(details, list):
            entry['validation_errors'] = [
                {'location': item.get('loc'), 'type': item.get('type')}
                for item in details if isinstance(item, dict)]
    if entry['path'].startswith('/api/v1/submissions/') and response.status_code == 200:
        body = response.json()
        if isinstance(body, dict):
            entry['raw_field_count'] = len(body)
            entry['raw_summary_present'] = 'summary' in body
            entry['raw_details_present'] = 'details' in body


network = httpx.Client(timeout=config.http_timeout, follow_redirects=False,
    transport=httpx.HTTPTransport(retries=0),
    event_hooks={'request': [before_request], 'response': [after_response]})
client = OJClient(settings.oj_base_url, settings.oj_api_token,
                  send_custom_run_code_alias=config.send_custom_run_code_alias, client=network)
result = {'schema_version': 'pilot_minioj_smoke_v1', 'plan': plan,
          'endpoint_fingerprint': fingerprint(settings.oj_base_url), 'source': source}
try:
    schema_response = network.get(settings.oj_base_url + '/openapi.json',
                                  headers={'Authorization': 'Bearer ' + settings.oj_api_token})
    schema_response.raise_for_status()
    api = schema_response.json()
    wire_schema = api['paths']['/api/v1/runs']['post']['requestBody']['content']['application/json']['schema']
    if '$ref' in wire_schema:
        wire_schema = api['components']['schemas'][wire_schema['$ref'].rsplit('/', 1)[-1]]
    result['custom_run_contract'] = {'source': 'GET /openapi.json#/paths/~1api~1v1~1runs/post',
        'required': wire_schema.get('required', []),
        'properties': sorted(wire_schema.get('properties', {})),
        'schema_sha256': fingerprint(wire_schema)}
    result['feedback_mode'] = client.get_feedback_mode()
    result['feedback_compatible'] = compatible_feedback(config.expected_feedback_mode,
        result['feedback_mode']['mode'], VERDICT_ONLY_POLICY)
    result['effective_feedback_mode'] = ('verdict_only' if result['feedback_compatible']
                                        and result['feedback_mode']['status'] == 'confirmed' else None)
    result['feedback_policy'] = VERDICT_ONLY_POLICY

    memory = MemoryTrace()
    tools = ToolRuntime(SimpleNamespace(trace=memory,
        state=SimpleNamespace(feedback_policy=VERDICT_ONLY_POLICY)))
    tools.register('get_submission', client.get_submission)
    tools.register('get_feedback', client.get_feedback)
    record = tools.call('get_submission', submission_id=SUBMISSION_ID)
    feedback = tools.call('get_feedback', submission_id=SUBMISSION_ID)
    result['formal_projection'] = {'submission': record.as_dict(), 'feedback': feedback.as_dict(),
                                   'tool_trace': memory.events, 'existing_submission_only': True}
    assert set(record.as_dict()) <= {'submission_id', 'status', 'verdict'}
    assert set(feedback.as_dict()) == {'verdict'}

    try:
        run = client.run_code(CPP, STDIN)
        result['pilot_path_passed'] = True
        result['custom_run_path'] = 'current_pilot_configuration'
    except OJClientError as exc:
        result['pilot_path_passed'] = False
        result['pilot_path_error'] = {'error_class': type(exc).__name__, 'kind': exc.kind.value,
                                      'http_status': exc.http_status}
        confirmed_missing = exc.http_status == 422 and any(
            item['location'] == ['body', 'code'] and item['type'] == 'missing'
            for item in ledger[-1].get('validation_errors', []))
        if not confirmed_missing:
            raise
        # Separate diagnostic request; the Pilot config/runtime is never edited.
        client.send_custom_run_code_alias = True
        run = client.run_code(CPP, STDIN)
        result['custom_run_path'] = 'diagnostic_code_alias_only'
    result['custom_run'] = {key: getattr(run, key) for key in (
        'status', 'stdout', 'stderr', 'exit_code', 'time_ms', 'memory_kb',
        'stdout_truncated', 'stderr_truncated')}
    spec = CheckerSpec(kind='token', source='explicit_controlled_token_smoke')
    positive = check_run(spec, {'input': STDIN, 'output': EXPECTED}, run)
    negative = check_run(spec, {'input': STDIN, 'output': EXPECTED.replace('42', '43')}, run)
    result['token_checker'] = {'positive': asdict(positive), 'negative': asdict(negative),
        'passed': positive.passed is True and negative.passed is False and negative.verdict == 'WA'}
    result['live_smoke_status'] = ('PASSED' if result['pilot_path_passed']
        and result['token_checker']['passed'] else 'PILOT_PATH_BLOCKED')
except Exception as exc:
    result['live_smoke_status'] = 'FAILED'
    result['exception_class'] = type(exc).__name__
finally:
    network.close()

result['request_ledger'] = ledger
result['counts'] = {'llm_calls': 0, 'formal_submissions': 0, 'algorithm_tasks': 0,
    'custom_run_post_attempts': len(posts),
    'successful_custom_run_responses': sum(entry['method'] == 'POST' and entry.get('http_status') == 200
                                           for entry in ledger),
    'automatic_retries': 0}
result['observed_at'] = now()
safe_write(OUT / 'minioj-smoke.json', result)
print(json.dumps({key: result.get(key) for key in ('live_smoke_status', 'pilot_path_passed',
    'custom_run_contract', 'feedback_mode', 'effective_feedback_mode', 'token_checker', 'counts')}, indent=2))
