"""Keep a same-user parent for the SITL binary's parent-liveness check."""
import signal
import subprocess
import sys
child = subprocess.Popen(sys.argv[1:])
def stop(signum, frame):
    if child.poll() is None: child.terminate()
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
sys.exit(child.wait())
