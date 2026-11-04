import tempfile
import unittest
from pathlib import Path
from launch_sequence_server import Sequence


class SharedCountdownTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000
        self.path = Path(self.tmp.name) / 'state.json'
        self.seq = Sequence(self.path, lambda: self.now)

    def command(self, cmd, **extra):
        return self.seq.command(dict(cmd=cmd, revision=self.seq.data['revision'], **extra))

    def test_refresh_and_second_device_share_deadline(self):
        self.command('start')
        self.now += 37.5
        second_device = Sequence(self.path, lambda: self.now)
        self.assertEqual(second_device.snapshot()['remaining'], 143)
        self.assertEqual(second_device.snapshot(), self.seq.snapshot())

    def test_hold_resume_does_not_count_time_while_held(self):
        self.command('start'); self.now += 20
        self.command('hold'); self.now += 500
        self.assertEqual(self.seq.snapshot()['remaining'], 160)
        self.command('resume'); self.now += 10
        self.assertEqual(self.seq.snapshot()['remaining'], 150)

    def test_zero_and_elapsed_without_any_browser(self):
        self.command('start'); self.now += 190
        self.assertEqual(self.seq.snapshot()['state'], 'launch')
        self.assertEqual(self.seq.snapshot()['launchElapsed'], 10)
        self.command('reset')
        self.assertEqual(self.seq.snapshot()['remaining'], 180)

    def test_abort_survives_restart(self):
        self.command('start'); self.now += 7
        self.command('abort'); self.now += 80
        d = Sequence(self.path, lambda: self.now).snapshot()
        self.assertEqual((d['state'], d['remaining']), ('abort', 173))

    def test_conflicting_and_invalid_commands_do_not_change_state(self):
        self.command('start')
        before = self.seq.snapshot()
        for msg in [dict(cmd='reset', revision=0), dict(cmd='start', revision=1),
                    dict(cmd='configure', total=-1, revision=1)]:
            with self.assertRaises(ValueError): self.seq.command(msg)
        self.assertEqual(self.seq.snapshot(), before)

    def test_configuration_shared_and_validated(self):
        self.command('configure', total=60, handoverAt=20)
        self.assertEqual(Sequence(self.path, lambda: self.now).snapshot()['total'], 60)
        for value in [-1, True, '60', 86401]:
            with self.assertRaises(ValueError): self.command('configure', total=value)

if __name__ == '__main__': unittest.main()
