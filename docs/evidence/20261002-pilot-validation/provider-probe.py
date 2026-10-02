"""Single-attempt connectivity probes; never solves a problem or runs an Agent."""
import json
import os
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path('/Users/susenyang/Code/CodeHarness')
sys.path.insert(0, str(ROOT))
from agent.execution import fingerprint
from agent.models.registry import ModelRegistry
from agent.models.router import ModelRouter
from agent.models.types import ChatMessage, ModelProfile
from experiments.freeze import runtime_source_hash

OUT = ROOT / 'workspace/.pilot-validation/pilot-gate-20261002'
MESSAGES = [ChatMessage('system', 'Connectivity probe. Output only OK.'),
            ChatMessage('user', 'Reply exactly OK.')]
PROFILES = (ModelProfile.STANDARD, ModelProfile.STRONG)


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


def numeric_metadata(value):
    if isinstance(value, dict):
        return {key: numeric_metadata(item) for key, item in value.items()
                if isinstance(item, (dict, int, float)) and not isinstance(item, bool)}
    return value


values = {**os.environ, **{key: value for key, value in dotenv_values(ROOT / '.env').items()
                         if value is not None}}
SECRETS = [value for key, value in values.items() if value and any(
    marker in key.upper() for marker in ('SECRET', 'TOKEN', 'API_KEY', 'PASSWORD'))]
registry = ModelRegistry.from_yaml(ROOT / 'config/models.yaml', environ=values,
                                   required_profiles=PROFILES)
router = ModelRouter(registry)
routes = [router.route(profile) for profile in PROFILES]
assert len({(route.provider, route.model) for route in routes}) == len(PROFILES)
for route in routes:
    assert route.parameters == {'max_tokens': 8192, 'enable_thinking': False}
    assert route.currency == 'CNY'
    assert route.pricing_known
assert len(''.join(message.content for message in MESSAGES)) < 80
source = {'git_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
          'runtime_source_hash': runtime_source_hash(),
          'model_config_file_hash': sha256((ROOT / 'config/models.yaml').read_bytes()).hexdigest(),
          'script_sha256': sha256(Path(__file__).read_bytes()).hexdigest()}
plan = [{'profile': profile.value, 'provider': route.provider,
         'model': route.model, 'parameters': route.parameters,
         'messages': [asdict(message) for message in MESSAGES], 'maximum_attempts': 1}
        for profile, route in zip(PROFILES, routes)]
if sys.argv[1:] != ['--execute']:
    print(json.dumps({'dry_run': True, 'source': source, 'plan': plan}, indent=2))
    sys.exit(0)
OUT.mkdir(parents=True, exist_ok=True)
if any(OUT.glob('provider-attempt-*.json')):
    raise SystemExit('Existing provider attempt ledger: no retries permitted')

results = []
for profile, route in zip(PROFILES, routes):
    provider = registry.provider(route.provider)
    observed = {}
    attempt_key = fingerprint([provider.base_url, route.model])
    attempted = []

    def before_request(request):
        assert request.method == 'POST'
        assert str(request.url) == provider.base_url + '/chat/completions'
        payload = json.loads(request.content)
        expected = {'model': route.model,
                    'messages': [asdict(message) for message in MESSAGES], **route.parameters}
        assert payload == expected
        assert not attempted
        safe_write(OUT / ('provider-attempt-' + attempt_key + '.json'), {
            'status': 'attempt_started', 'at': now(), 'purpose': 'provider_connectivity_probe',
            'maximum_attempts': 1, 'provider': route.provider, 'profile': profile.value,
            'requested_model': route.model, 'endpoint_fingerprint': fingerprint(provider.base_url),
            'request_payload': payload, 'request_payload_hash': fingerprint(payload), **source})
        attempted.append(True)

    def after_response(response):
        response.read()
        observed['http_status'] = response.status_code
        observed['response_body_sha256'] = sha256(response.content).hexdigest()
        if response.status_code != 200:
            return
        body = response.json()
        if not isinstance(body, dict):
            return
        observed['actual_model_id'] = body.get('model') if isinstance(body.get('model'), str) else None
        observed['observed_usage_metadata'] = numeric_metadata(body.get('usage'))
        observed['billing_fields_present'] = [key for key in ('cost', 'billing', 'charged_amount', 'currency')
                                              if key in body]
        # Standard compatible responses expose usage, not a settled account charge.
        observed['actual_billed_cost_cny'] = None

    provider.client.close()
    provider.client = httpx.Client(timeout=provider.timeout_seconds, follow_redirects=False,
        transport=httpx.HTTPTransport(retries=0),
        event_hooks={'request': [before_request], 'response': [after_response]})
    print(json.dumps({'profile': profile.value, 'status': 'calling_once'}), flush=True)
    try:
        response = router.complete(profile, MESSAGES)
        cost = router.estimate_cost(response)
        result = {'profile': profile.value, 'provider': route.provider,
                  'requested_model_id': route.model, 'adapter_model_id': response.model,
                  'status': response.status.value, 'response_content': response.content[:80],
                  'content_matches_expected': response.content.strip() == 'OK',
                  'finish_reason': response.finish_reason, 'request_id': response.request_id,
                  'usage': asdict(response.usage) if response.usage else None,
                  'latency_ms': response.latency_ms,
                  'error_kind': response.error.kind.value if response.error else None,
                  'configured_tariff_cost_cny': cost.amount,
                  'actual_billed_cost_cny': None,
                  'cost_status': 'configured_tariff_estimate_only',
                  'generation_parameters_sent': route.parameters,
                  'temperature_sent': False, 'top_p_sent': False,
                  'temperature_effective_observed': None, 'top_p_effective_observed': None,
                  'endpoint_fingerprint': fingerprint(provider.base_url),
                  'attempts': len(attempted), 'observed_at': now(), **source, **observed}
    except Exception as exc:
        result = {'profile': profile.value, 'provider': route.provider,
                  'requested_model_id': route.model, 'status': 'failed',
                  'exception_class': type(exc).__name__, 'attempts': len(attempted),
                  'actual_billed_cost_cny': None, 'observed_at': now(), **source, **observed}
    finally:
        provider.client.close()
    safe_write(OUT / ('provider-' + profile.value + '.json'), result)
    results.append(result)
    print(json.dumps({key: result.get(key) for key in ('profile', 'status', 'http_status',
        'actual_model_id', 'usage', 'configured_tariff_cost_cny', 'actual_billed_cost_cny')}), flush=True)

costs_known = all(result.get('configured_tariff_cost_cny') is not None for result in results)
safe_write(OUT / 'provider-probe.json', {'schema_version': 'pilot_provider_probe_v1',
    'results': results, 'calls_attempted': sum(result['attempts'] for result in results),
    'configured_tariff_cost_cny': float(sum(Decimal(str(result['configured_tariff_cost_cny']))
                                           for result in results)) if costs_known else None,
    'actual_billed_cost_cny': None, 'algorithm_tasks': 0, 'formal_submissions': 0,
    'automatic_retries': 0, 'source': source})
