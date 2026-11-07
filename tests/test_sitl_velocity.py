import ast
from pathlib import Path
import threading
import types
import unittest

class StopLoop(Exception): pass
class Constants:
    def __getattr__(self,key):return 0

class SITLVelocityTests(unittest.TestCase):
    def sends(self,sim,last,streaming=False):
        source=ast.parse((Path(__file__).parents[1]/'airsent_server.py').read_text())
        fn=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='velocity_sender')
        sent=[]
        def stop(*args): raise StopLoop()
        ctx=dict(mav_lock=threading.Lock(),vel_lock=threading.Lock(),mav_conn=types.SimpleNamespace(target_system=1,target_component=1,mav=types.SimpleNamespace(set_position_target_local_ned_send=lambda *args:sent.append(args))),telem={'flight_mode':'GUIDED','armed':True},SIM_MODE=sim,vel_last_cmd_ts=last,time=types.SimpleNamespace(time=lambda:100,sleep=stop),vel_setpoint={'vx':1,'vy':0,'vz':0,'yaw_rate':0},obstacle_blocked=False,VEL_STALE_S=1.0,_vel_streaming=[streaming],mavutil=types.SimpleNamespace(mavlink=Constants()),log_mavlink=lambda *a:None)
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'velocity_test','exec'),ctx)
        with self.assertRaises(StopLoop):ctx['velocity_sender']()
        return len(sent)
    def test_simulation_does_not_cancel_takeoff_with_idle_velocity(self):self.assertEqual(self.sends(True,0),0)
    def test_simulation_manual_commands_stream_only_while_fresh(self):
        self.assertEqual(self.sends(True,99.5),1)
        self.assertEqual(self.sends(True,98),0)
    def test_hardware_streams_only_while_the_joystick_is_sending(self):
        # Stale-command protection: no joystick input → no velocity stream (it would cancel a GUIDED takeoff).
        self.assertEqual(self.sends(False,0),0)
        self.assertEqual(self.sends(False,99.5),1)
    def test_one_stop_when_the_joystick_goes_quiet(self):
        self.assertEqual(self.sends(False,95,streaming=True),1)      # the single zero-velocity stop
