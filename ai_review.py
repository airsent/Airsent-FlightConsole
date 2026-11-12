"""AI flight review: send the Jetson's log report to Claude, get back a structured review,
and check every proposed parameter change against a fixed allowlist before a person sees it.

Runs on the VPS (relay.py handles the console's 'ai_analyze' request). The API key lives only
on the VPS: /etc/airsent/ai.env  ->  ANTHROPIC_API_KEY=...   (set with deploy/set_ai_key.sh)

Safety model
  • The model only ever sees the analyser's report (numbers + findings), never the raw log.
  • It can only PROPOSE. Nothing is written to the aircraft until an operator selects
    changes on the Logs page and confirms; the Jetson then writes and reads back each one.
  • Proposals are filtered here: tuning parameters only, inside fixed bounds, and gain
    changes limited to a step from the value the aircraft flew with. Anything else is shown
    as BLOCKED with the reason. Failsafes, fence, arming, battery limits are never allowed.
"""
import json
import math
import os
import time
import urllib.error
import urllib.request

API_URL = os.environ.get('ANTHROPIC_API_URL', 'https://api.anthropic.com/v1/messages')
MODEL = os.environ.get('AIRSENT_AI_MODEL', 'claude-opus-5-5')
MAX_REPORT_BYTES = 120_000

# param: (min, max, max relative step from the flown value or None, integer)
ALLOW = {
    'INS_HNTCH_ENABLE': (0, 1, None, True), 'INS_HNTCH_MODE': (0, 5, None, True),
    'INS_HNTCH_FREQ': (10, 300, None, False), 'INS_HNTCH_BW': (5, 150, None, False),
    'INS_HNTCH_REF': (0.05, 0.9, None, False), 'INS_HNTCH_ATT': (5, 50, None, False),
    'INS_HNTCH_HMNCS': (1, 255, None, True), 'INS_HNTCH_OPTS': (0, 63, None, True),
    'INS_GYRO_FILTER': (10, 120, 0.5, False), 'INS_ACCEL_FILTER': (5, 40, 0.5, False),
    'INS_LOG_BAT_MASK': (0, 7, None, True), 'INS_LOG_BAT_OPT': (0, 7, None, True),
    'MOT_THST_HOVER': (0.1, 0.75, None, False), 'MOT_THST_EXPO': (0.0, 0.9, None, False),
    'MOT_SPIN_ARM': (0.03, 0.25, None, False), 'MOT_SPIN_MIN': (0.05, 0.3, None, False),
    'ATC_RAT_RLL_P': (0.01, 0.5, 0.3, False), 'ATC_RAT_RLL_I': (0.01, 0.5, 0.3, False),
    'ATC_RAT_RLL_D': (0.0, 0.02, 0.3, False), 'ATC_RAT_RLL_FLTD': (0, 100, 0.5, False),
    'ATC_RAT_RLL_FLTT': (0, 100, 0.5, False),
    'ATC_RAT_PIT_P': (0.01, 0.5, 0.3, False), 'ATC_RAT_PIT_I': (0.01, 0.5, 0.3, False),
    'ATC_RAT_PIT_D': (0.0, 0.02, 0.3, False), 'ATC_RAT_PIT_FLTD': (0, 100, 0.5, False),
    'ATC_RAT_PIT_FLTT': (0, 100, 0.5, False),
    'ATC_RAT_YAW_P': (0.05, 1.0, 0.3, False), 'ATC_RAT_YAW_I': (0.005, 0.2, 0.3, False),
    'ATC_RAT_YAW_FLTE': (0, 10, 0.5, False),
    'ATC_ANG_RLL_P': (2, 12, 0.3, False), 'ATC_ANG_PIT_P': (2, 12, 0.3, False), 'ATC_ANG_YAW_P': (2, 12, 0.3, False),
    'ATC_ACCEL_R_MAX': (10000, 200000, 0.5, False), 'ATC_ACCEL_P_MAX': (10000, 200000, 0.5, False),
    'ATC_ACCEL_Y_MAX': (5000, 72000, 0.5, False),
    'PSC_ACCZ_P': (0.1, 1.5, 0.3, False), 'PSC_ACCZ_I': (0.2, 3.0, 0.3, False),
    'PSC_VELZ_P': (1.0, 8.0, 0.3, False), 'PSC_POSZ_P': (0.5, 3.0, 0.3, False),
    'PSC_VELXY_P': (0.5, 6.0, 0.3, False), 'PSC_VELXY_I': (0.1, 3.0, 0.3, False),
    'PSC_VELXY_D': (0.0, 1.0, 0.3, False), 'PSC_POSXY_P': (0.5, 2.0, 0.3, False),
}
REBOOT_PARAMS = {'INS_HNTCH_ENABLE', 'INS_LOG_BAT_MASK', 'INS_LOG_BAT_OPT', 'INS_GYRO_FILTER', 'INS_ACCEL_FILTER'}

SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['verdict', 'headline', 'summary', 'findings', 'proposals', 'rejected_rule_proposals',
                 'before_next_flight', 'next_flight_plan', 'data_gaps'],
    'properties': {
        'verdict': {'type': 'string', 'enum': ['go', 'caution', 'no_go'],
                    'description': 'Is it safe to fly the planned next flight?'},
        'headline': {'type': 'string', 'description': 'One sentence, plain language.'},
        'summary': {'type': 'string', 'description': '3-6 sentences on how the aircraft flew.'},
        'findings': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['level', 'area', 'text'],
            'properties': {'level': {'type': 'string', 'enum': ['info', 'warning', 'critical']},
                           'area': {'type': 'string'}, 'text': {'type': 'string'}}}},
        'proposals': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['param', 'value', 'reason', 'confidence', 'recommended'],
            'properties': {'param': {'type': 'string'}, 'value': {'type': 'number'}, 'reason': {'type': 'string'},
                           'confidence': {'type': 'string', 'enum': ['low', 'medium', 'high']},
                           'recommended': {'type': 'boolean', 'description': 'true = apply this before the next flight (it will be pre-ticked); false = optional, for the operator to consider'}}}},
        'rejected_rule_proposals': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['param', 'reason'],
            'properties': {'param': {'type': 'string'}, 'reason': {'type': 'string'}}}},
        'before_next_flight': {'type': 'array', 'items': {'type': 'string'},
                               'description': 'Physical checks or fixes to do before flying again.'},
        'next_flight_plan': {'type': 'string', 'description': 'What the next flight should be and why.'},
        'data_gaps': {'type': 'array', 'items': {'type': 'string'},
                      'description': 'What the report could not tell you, and what to log next time.'},
    },
}

SYSTEM = """You are a senior ArduPilot multirotor flight-test engineer reviewing the analysis of a flight log \
for Airsent, a small drone-delivery startup flying its first test flights of a heavy 16-inch quad. A student \
team reads your review on their ground console between flights, with the aircraft on the ground.

You receive a JSON report produced by their log analyser (numbers measured from the ArduPilot .bin, rule-based \
findings and rule-based parameter proposals) plus the operator's notes and the planned next flight.

How to review:
- Safety first. Be conservative: an early test aircraft that is not clearly healthy gets "caution" or "no_go".
- Use only the numbers in the report. Do not invent measurements. If something matters and is missing, say so in data_gaps.
- Thresholds used by ArduPilot practice: vibration under 15 m/s² good, 15-30 acceptable, over 30 problem, any accel \
clipping is bad; hover throttle 0.2-0.5 healthy, over 0.6 underpowered; attitude error RMS under ~1.5° is fine.
- Parameter proposals: only for parameters in the ALLOWED list below. Keep gain changes small (at most about 30% from \
the flown value, given in report.params). Prefer the harmonic notch and hover-throttle fixes first; for roll/pitch/yaw \
gains prefer recommending an AutoTune flight over guessing values. Do not propose a parameter that is already at the value.
- The analyser's rule proposals are a starting point: include the ones you agree with in proposals (adjusted if needed) \
and list the ones you disagree with in rejected_rule_proposals with the reason.
- Set recommended=true only for changes you are confident should be applied before the planned next flight; they are pre-ticked on the console (the operator still confirms). Optional or speculative changes get recommended=false.
- Mechanical problems (balance, centre of gravity, loose props, motor mounts, damping) go in before_next_flight, not as \
parameter changes.
- Write for capable engineering students: short, specific, plain language, metric units.

ALLOWED parameters: """ + ', '.join(sorted(ALLOW))


PROPOSAL_ITEM = SCHEMA['properties']['proposals']

COMPARE_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['verdict', 'headline', 'summary', 'change_effects', 'improved', 'worse', 'proposals',
                 'before_next_flight', 'next_flight_plan', 'data_gaps'],
    'properties': {
        'verdict': {'type': 'string', 'enum': ['go', 'caution', 'no_go'], 'description': 'For the planned next flight, based on the LATEST flight.'},
        'headline': {'type': 'string'},
        'summary': {'type': 'string', 'description': 'How the flights differ and why, 3-6 sentences.'},
        'change_effects': {'type': 'array', 'description': 'Each parameter or setup change between the flights and what it did.', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['change', 'effect', 'result'],
            'properties': {'change': {'type': 'string'}, 'effect': {'type': 'string'},
                           'result': {'type': 'string', 'enum': ['improved', 'worse', 'no_clear_change', 'unknown']}}}},
        'improved': {'type': 'array', 'items': {'type': 'string'}},
        'worse': {'type': 'array', 'items': {'type': 'string'}},
        'proposals': PROPOSAL_ITEM,
        'before_next_flight': {'type': 'array', 'items': {'type': 'string'}},
        'next_flight_plan': {'type': 'string'},
        'data_gaps': {'type': 'array', 'items': {'type': 'string'}},
    },
}

ASK_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['answer', 'confidence', 'followups'],
    'properties': {'answer': {'type': 'string', 'description': 'Direct answer in plain language, using numbers from the reports.'},
                   'confidence': {'type': 'string', 'enum': ['low', 'medium', 'high']},
                   'followups': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Up to 3 useful follow-up questions.'}},
}

COMPARE_TASK = """
You are now comparing several flights of the same aircraft (labelled A, B, C in time order; the LAST one is the most
recent). Work out what changed between them (parameters in report.params, changes applied from the console, conditions,
the flight profile) and what effect each change had on the measurements. Be careful about confounders: a different
flight profile, wind, payload or battery state can explain a difference as well as a parameter change; say so.
Proposals and the verdict are for the next flight, starting from the LATEST flight's parameters."""

ASK_TASK = """
Answer the operator's question about the flight(s) below. Use only the numbers in the reports; say what you cannot tell
from them. Keep it short and practical. Do not propose parameter values here — point them to the review for that."""


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def check_proposals(proposals, flown):
    """Mark each proposal allowed / blocked. Never raises."""
    out = []
    spin = {}
    for p in proposals or []:
        name = str(p.get('param', '')).strip().upper()
        val = p.get('value')
        item = {'param': name, 'value': val, 'reason': str(p.get('reason', ''))[:600],
                'confidence': p.get('confidence', 'medium'), 'source': p.get('source', 'ai'),
                'recommended': bool(p.get('recommended')),
                'current': flown.get(name), 'allowed': False, 'blocked': None, 'reboot': name in REBOOT_PARAMS}
        rule = ALLOW.get(name)
        if rule is None:
            item['blocked'] = 'Not a tuning parameter — change it by hand on the Parameters page if you agree'
        elif not _finite(val):
            item['blocked'] = 'Not a number'
        else:
            lo, hi, step, integer = rule
            if integer:
                val = int(round(val))
                item['value'] = val
            if not lo <= val <= hi:
                item['blocked'] = f'Outside the safe range {lo}–{hi}'
            elif step and _finite(flown.get(name)) and flown[name] > 0 and abs(val - flown[name]) > step * flown[name] + 1e-9:
                item['blocked'] = f'Step too big: {flown[name]:g} → {val:g} is more than {step * 100:.0f}% — change it in smaller steps'
            elif _finite(flown.get(name)) and abs(val - flown[name]) <= 1e-6 * max(1.0, abs(flown[name])):
                item['blocked'] = 'Already at this value'
            else:
                item['allowed'] = True
                if name in ('MOT_SPIN_ARM', 'MOT_SPIN_MIN'):
                    spin[name] = item
        out.append(item)
    arm = spin.get('MOT_SPIN_ARM', {}).get('value', flown.get('MOT_SPIN_ARM'))
    mn = spin.get('MOT_SPIN_MIN', {}).get('value', flown.get('MOT_SPIN_MIN'))
    if _finite(arm) and _finite(mn) and arm >= mn:
        for it in spin.values():
            it['allowed'], it['blocked'] = False, 'MOT_SPIN_ARM must stay below MOT_SPIN_MIN'
    # one entry per parameter: keep the last allowed one
    seen = {}
    for it in out:
        if it['param'] not in seen or it['allowed']:
            seen[it['param']] = it
    return list(seen.values())


def build_request(report, notes='', next_flight=''):
    slim = {k: v for k, v in report.items() if k not in ('generated_at',)}
    body = json.dumps(slim, separators=(',', ':'))
    if len(body) > MAX_REPORT_BYTES:
        raise ValueError(f'Report too large ({len(body)} bytes)')
    user = ('Flight log analysis report (JSON):\n' + body +
            '\n\nOperator notes: ' + (str(notes)[:2000] or 'none') +
            '\nPlanned next flight: ' + (str(next_flight)[:1000] or 'not stated') +
            '\n\nReview it and answer in the required JSON format.')
    return {'model': MODEL, 'max_tokens': 4096, 'system': SYSTEM,
            'messages': [{'role': 'user', 'content': user}],
            'output_config': {'format': {'type': 'json_schema', 'schema': SCHEMA}}}


def _request(user, schema, system_extra='', max_tokens=4096):
    return {'model': MODEL, 'max_tokens': max_tokens, 'system': SYSTEM + system_extra,
            'messages': [{'role': 'user', 'content': user}],
            'output_config': {'format': {'type': 'json_schema', 'schema': schema}}}


def _slim(report):
    return {k: v for k, v in (report or {}).items() if k not in ('generated_at', 'has_series')}


def _flights_text(flights):
    parts = []
    for i, f in enumerate(flights[:3]):
        label = 'ABC'[i]
        applied = f.get('applied') or []
        parts.append(f'--- Flight {label}: {str(f.get("name", ""))[:80]} ({str(f.get("folder", ""))[:80]}) ---\n'
                     + (f'Changes applied from the console after this flight: {json.dumps(applied)[:3000]}\n' if applied else '')
                     + json.dumps(_slim(f.get('report')), separators=(',', ':')))
    body = '\n\n'.join(parts)
    if len(body) > MAX_REPORT_BYTES * 2:
        raise ValueError(f'Reports too large ({len(body)} bytes)')
    return body


def call_api(payload, api_key, timeout=150, opener=urllib.request.urlopen):
    req = urllib.request.Request(API_URL, data=json.dumps(payload).encode(), method='POST', headers={
        'content-type': 'application/json', 'x-api-key': api_key, 'anthropic-version': '2023-06-01'})
    try:
        with opener(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get('error', {}).get('message', '')
        except Exception:
            detail = ''
        raise RuntimeError(f'Claude API error {e.code}' + (f': {detail}' if detail else '')) from None
    except urllib.error.URLError as e:
        raise RuntimeError(f'Could not reach the Claude API: {e.reason}') from None


def _post(payload, api_key, opener):
    """-> (data, resp) or raises RuntimeError with a user-facing message."""
    resp = call_api(payload, api_key, opener=opener)
    stop = resp.get('stop_reason')
    text = next((b.get('text', '') for b in resp.get('content', []) if b.get('type') == 'text'), '')
    if stop == 'refusal':
        raise RuntimeError('The model declined to answer')
    try:
        return json.loads(text), resp
    except ValueError:
        raise RuntimeError('The reply was cut off or not valid JSON' + (' (hit the length limit)' if stop == 'max_tokens' else '')) from None


def _meta(resp, t0, **extra):
    usage = resp.get('usage') or {}
    return {'model': resp.get('model', MODEL), 'seconds': round(time.time() - t0, 1), 'input_tokens': usage.get('input_tokens'),
            'output_tokens': usage.get('output_tokens'), 'at': time.time(), **extra}


ORDER = {'go': 0, 'no_flight': 1, 'caution': 1, 'no_go': 2}


def _floor_verdict(data, rule_verdict):
    data['verdict'] = str(data.get('verdict', 'caution')).lower()
    if data['verdict'] not in ORDER:
        data['verdict'] = 'caution'
    if ORDER.get(rule_verdict, 0) > ORDER[data['verdict']]:
        data['verdict_raised_by_rules'] = data['verdict']
        data['verdict'] = 'caution' if rule_verdict == 'no_flight' else rule_verdict


def compare(flights, notes='', next_flight='', api_key=None, opener=urllib.request.urlopen):
    """flights: [{'name','folder','report','applied'}] oldest first (2-3)."""
    key = api_key if api_key is not None else os.environ.get('ANTHROPIC_API_KEY', '')
    if not key:
        return {'ok': False, 'msg': 'AI review is not set up: no ANTHROPIC_API_KEY on the server (run deploy/set_ai_key.sh on the VPS)'}
    flights = [f for f in (flights or []) if isinstance(f, dict) and isinstance(f.get('report'), dict)]
    if len(flights) < 2:
        return {'ok': False, 'msg': 'Pick 2 or 3 analysed flights to compare'}
    t0 = time.time()
    try:
        user = (_flights_text(flights) + '\n\nOperator notes: ' + (str(notes)[:2000] or 'none')
                + '\nPlanned next flight: ' + (str(next_flight)[:1000] or 'not stated')
                + '\n\nCompare the flights and answer in the required JSON format.')
        data, resp = _post(_request(user, COMPARE_SCHEMA, COMPARE_TASK), key, opener)
    except (RuntimeError, ValueError) as e:
        return {'ok': False, 'msg': str(e)}
    latest = flights[-1]['report']
    _floor_verdict(data, latest.get('verdict'))
    flown = {k: v for k, v in (latest.get('params') or {}).items() if _finite(v)}
    data['proposals'] = check_proposals(data.get('proposals'), flown)
    data['meta'] = _meta(resp, t0, flights=[f.get('folder') for f in flights])
    return {'ok': True, 'msg': f"AI comparison: {data['verdict'].upper()}", 'compare': data}


def ask(question, flights, api_key=None, opener=urllib.request.urlopen):
    key = api_key if api_key is not None else os.environ.get('ANTHROPIC_API_KEY', '')
    if not key:
        return {'ok': False, 'msg': 'AI review is not set up: no ANTHROPIC_API_KEY on the server (run deploy/set_ai_key.sh on the VPS)'}
    q = str(question or '').strip()
    if not q:
        return {'ok': False, 'msg': 'Type a question'}
    flights = [f for f in (flights or []) if isinstance(f, dict) and isinstance(f.get('report'), dict)]
    if not flights:
        return {'ok': False, 'msg': 'Analyse the flight first'}
    t0 = time.time()
    try:
        user = _flights_text(flights) + '\n\nQuestion: ' + q[:2000]
        data, resp = _post(_request(user, ASK_SCHEMA, ASK_TASK, max_tokens=2000), key, opener)
    except (RuntimeError, ValueError) as e:
        return {'ok': False, 'msg': str(e)}
    data['question'] = q[:2000]
    data['meta'] = _meta(resp, t0, flights=[f.get('folder') for f in flights])
    return {'ok': True, 'msg': 'Answered', 'answer': data}


def run(cmd):
    """Entry point for relay.py / the Jetson: cmd['mode'] = review | compare | ask."""
    mode = str(cmd.get('mode') or 'review')
    if mode == 'compare':
        return compare(cmd.get('flights'), cmd.get('notes', ''), cmd.get('next_flight', ''))
    if mode == 'ask':
        return ask(cmd.get('question'), cmd.get('flights') or [{'report': cmd.get('report'), 'folder': cmd.get('folder')}])
    return review(cmd.get('report'), cmd.get('notes', ''), cmd.get('next_flight', ''))


def review(report, notes='', next_flight='', api_key=None, opener=urllib.request.urlopen):
    """-> {'ok': True, 'review': {...}} or {'ok': False, 'msg': ...}"""
    key = api_key if api_key is not None else os.environ.get('ANTHROPIC_API_KEY', '')
    if not key:
        return {'ok': False, 'msg': 'AI review is not set up: no ANTHROPIC_API_KEY on the server (run deploy/set_ai_key.sh on the VPS)'}
    if not isinstance(report, dict) or 'verdict' not in report:
        return {'ok': False, 'msg': 'No analysis report — run ANALYSE on the Jetson first'}
    t0 = time.time()
    try:
        resp = call_api(build_request(report, notes, next_flight), key, opener=opener)
    except (RuntimeError, ValueError) as e:
        return {'ok': False, 'msg': str(e)}
    stop = resp.get('stop_reason')
    text = next((b.get('text', '') for b in resp.get('content', []) if b.get('type') == 'text'), '')
    if stop == 'refusal':
        return {'ok': False, 'msg': 'The model declined to review this report'}
    try:
        data = json.loads(text)
    except ValueError:
        return {'ok': False, 'msg': 'The reply was cut off or not valid JSON' + (' (hit the length limit)' if stop == 'max_tokens' else '')}
    flown = {k: v for k, v in (report.get('params') or {}).items() if _finite(v)}
    data['verdict'] = str(data.get('verdict', 'caution')).lower()
    if data['verdict'] not in ('go', 'caution', 'no_go'):
        data['verdict'] = 'caution'
    # never let the review be more optimistic than the hard rules
    _floor_verdict(data, report.get('verdict'))
    data['proposals'] = check_proposals(data.get('proposals'), flown)
    usage = resp.get('usage') or {}
    data['meta'] = {'model': resp.get('model', MODEL), 'seconds': round(time.time() - t0, 1),
                    'input_tokens': usage.get('input_tokens'), 'output_tokens': usage.get('output_tokens'),
                    'report_file': report.get('file'), 'report_verdict': report.get('verdict'), 'at': time.time()}
    return {'ok': True, 'msg': f"AI review: {data['verdict'].upper()}", 'review': data}
