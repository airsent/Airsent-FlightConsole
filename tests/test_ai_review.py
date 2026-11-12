"""AI flight review: request format, reply handling, and the parameter allowlist."""
import io
import json
import os
import sys
import unittest
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import ai_review as ar  # noqa: E402

REPORT = {'verdict': 'go', 'file': 'pixhawk.bin',
          'params': {'ATC_RAT_RLL_P': 0.135, 'MOT_SPIN_ARM': 0.1, 'MOT_SPIN_MIN': 0.15, 'INS_HNTCH_ENABLE': 0,
                     'FS_THR_ENABLE': 1},
          'findings': [], 'proposals': []}


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_for(review, captured=None, stop='end_turn'):
    def opener(req, timeout=None):
        if captured is not None:
            captured['req'] = req
        body = {'model': 'claude-opus-5-5', 'stop_reason': stop, 'usage': {'input_tokens': 3000, 'output_tokens': 900},
                'content': [{'type': 'text', 'text': json.dumps(review) if not isinstance(review, str) else review}]}
        return FakeResp(json.dumps(body).encode())
    return opener


def good_review(**over):
    r = {'verdict': 'go', 'headline': 'Flew well', 'summary': 's', 'findings': [], 'rejected_rule_proposals': [],
         'before_next_flight': [], 'next_flight_plan': 'staircase', 'data_gaps': [],
         'proposals': [{'param': 'INS_HNTCH_ENABLE', 'value': 1, 'reason': 'notch', 'confidence': 'high'}]}
    r.update(over)
    return r


class Allowlist(unittest.TestCase):
    def check(self, *props):
        return {p['param']: p for p in ar.check_proposals(list(props), REPORT['params'])}

    def test_tuning_param_allowed(self):
        p = self.check({'param': 'ins_hntch_freq', 'value': 72})['INS_HNTCH_FREQ']
        self.assertTrue(p['allowed'])

    def test_safety_params_blocked(self):
        p = self.check({'param': 'FS_THR_ENABLE', 'value': 0}, {'param': 'ARMING_CHECK', 'value': 0},
                       {'param': 'FENCE_ENABLE', 'value': 0})
        self.assertTrue(all(not x['allowed'] for x in p.values()))
        self.assertIn('Not a tuning parameter', p['FS_THR_ENABLE']['blocked'])

    def test_out_of_range_and_big_steps_blocked(self):
        p = self.check({'param': 'INS_HNTCH_FREQ', 'value': 900}, {'param': 'ATC_RAT_RLL_P', 'value': 0.3})
        self.assertIn('safe range', p['INS_HNTCH_FREQ']['blocked'])
        self.assertIn('Step too big', p['ATC_RAT_RLL_P']['blocked'])
        self.assertTrue(self.check({'param': 'ATC_RAT_RLL_P', 'value': 0.16})['ATC_RAT_RLL_P']['allowed'])

    def test_spin_arm_must_stay_below_spin_min(self):
        p = self.check({'param': 'MOT_SPIN_ARM', 'value': 0.16})
        self.assertFalse(p['MOT_SPIN_ARM']['allowed'])

    def test_non_numbers_and_no_change(self):
        p = self.check({'param': 'INS_HNTCH_FREQ', 'value': 'abc'}, {'param': 'INS_HNTCH_ENABLE', 'value': 0})
        self.assertFalse(p['INS_HNTCH_FREQ']['allowed'])
        self.assertEqual(p['INS_HNTCH_ENABLE']['blocked'], 'Already at this value')

    def test_integers_rounded_and_reboot_flag(self):
        p = self.check({'param': 'INS_HNTCH_ENABLE', 'value': 0.9})['INS_HNTCH_ENABLE']
        self.assertEqual((p['value'], p['allowed'], p['reboot']), (1, True, True))


class Review(unittest.TestCase):
    def test_request_shape(self):
        cap = {}
        r = ar.review(REPORT, 'felt fine', '10 m staircase', api_key='sk-ant-test', opener=opener_for(good_review(), cap))
        self.assertTrue(r['ok'], r)
        req = cap['req']
        self.assertEqual(req.get_header('X-api-key'), 'sk-ant-test')
        self.assertEqual(req.get_header('Anthropic-version'), '2023-06-01')
        body = json.loads(req.data)
        self.assertEqual(body['output_config']['format']['type'], 'json_schema')
        self.assertNotIn('tool_choice', body)
        self.assertIn('10 m staircase', body['messages'][0]['content'])
        self.assertEqual(r['review']['meta']['output_tokens'], 900)
        self.assertTrue(r['review']['proposals'][0]['allowed'])

    def test_no_key(self):
        r = ar.review(REPORT, api_key='')
        self.assertFalse(r['ok'])
        self.assertIn('ANTHROPIC_API_KEY', r['msg'])

    def test_review_never_more_optimistic_than_rules(self):
        r = ar.review({**REPORT, 'verdict': 'no_go'}, api_key='k', opener=opener_for(good_review(verdict='go')))
        self.assertEqual(r['review']['verdict'], 'no_go')
        self.assertEqual(r['review']['verdict_raised_by_rules'], 'go')

    def test_bad_json_and_cutoff(self):
        r = ar.review(REPORT, api_key='k', opener=opener_for('{"verdict": "go", "headl', stop='max_tokens'))
        self.assertFalse(r['ok'])
        self.assertIn('length limit', r['msg'])

    def test_http_error(self):
        def opener(req, timeout=None):
            raise urllib.error.HTTPError(ar.API_URL, 401, 'x', {}, io.BytesIO(b'{"error":{"message":"invalid x-api-key"}}'))
        r = ar.review(REPORT, api_key='k', opener=opener)
        self.assertEqual(r['msg'], 'Claude API error 401: invalid x-api-key')

    def test_needs_a_report(self):
        self.assertFalse(ar.review({}, api_key='k')['ok'])


if __name__ == '__main__':
    unittest.main()


class CompareAndAsk(unittest.TestCase):
    FLIGHTS = [{'name': 'Hover 1', 'folder': 'a', 'report': {**REPORT, 'verdict': 'caution'}, 'applied': [{'values': {'INS_HNTCH_ENABLE': 1}}]},
               {'name': 'Hover 2', 'folder': 'b', 'report': {**REPORT, 'params': {**REPORT['params'], 'INS_HNTCH_ENABLE': 1}}}]

    def test_compare(self):
        cap = {}
        out = {'verdict': 'go', 'headline': 'Notch worked', 'summary': 's', 'improved': ['vibration'], 'worse': [],
               'change_effects': [{'change': 'INS_HNTCH_ENABLE 0→1', 'effect': 'gyro peak gone', 'result': 'improved'}],
               'proposals': [{'param': 'FENCE_ENABLE', 'value': 0, 'reason': 'x', 'confidence': 'low', 'recommended': True}],
               'before_next_flight': [], 'next_flight_plan': 'staircase', 'data_gaps': []}
        r = ar.compare(self.FLIGHTS, api_key='k', opener=opener_for(out, cap))
        self.assertTrue(r['ok'], r)
        body = json.loads(cap['req'].data)
        self.assertIn('Flight A: Hover 1', body['messages'][0]['content'])
        self.assertIn('Flight B: Hover 2', body['messages'][0]['content'])
        self.assertIn('comparing several flights', body['system'])
        self.assertFalse(r['compare']['proposals'][0]['allowed'])            # safety params still blocked
        self.assertEqual(r['compare']['meta']['flights'], ['a', 'b'])

    def test_compare_needs_two(self):
        self.assertFalse(ar.compare(self.FLIGHTS[:1], api_key='k')['ok'])

    def test_compare_verdict_floor_uses_latest_flight(self):
        fl = [self.FLIGHTS[0], {**self.FLIGHTS[1], 'report': {**REPORT, 'verdict': 'no_go'}}]
        out = {'verdict': 'go', 'headline': '', 'summary': '', 'improved': [], 'worse': [], 'change_effects': [],
               'proposals': [], 'before_next_flight': [], 'next_flight_plan': '', 'data_gaps': []}
        self.assertEqual(ar.compare(fl, api_key='k', opener=opener_for(out))['compare']['verdict'], 'no_go')

    def test_ask(self):
        r = ar.ask('Why did it drift?', self.FLIGHTS[:1], api_key='k',
                   opener=opener_for({'answer': 'Wind', 'confidence': 'medium', 'followups': []}))
        self.assertEqual(r['answer']['answer'], 'Wind')
        self.assertEqual(r['answer']['question'], 'Why did it drift?')
        self.assertFalse(ar.ask('', self.FLIGHTS, api_key='k')['ok'])

    def test_recommended_flag_kept(self):
        p = ar.check_proposals([{'param': 'INS_HNTCH_FREQ', 'value': 70, 'recommended': True}], {})
        self.assertTrue(p[0]['recommended'])

    def test_run_dispatch(self):
        os.environ.pop('ANTHROPIC_API_KEY', None)
        for mode in ('review', 'compare', 'ask'):
            self.assertIn('ANTHROPIC_API_KEY', ar.run({'mode': mode, 'report': REPORT, 'flights': self.FLIGHTS, 'question': 'q'})['msg'])
