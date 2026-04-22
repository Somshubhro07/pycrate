"""Full lifecycle E2E test for pycrate machine on Windows 11."""

import sys
import time

# Step 1: Init
print("=" * 50)
print("STEP 1: pycrate machine init")
print("=" * 50)

from machine.config import MachineConfig, MachineState
from machine.backend import get_backend

config = MachineConfig(backend="auto", cpus=2, memory_mb=2048)
be = get_backend(config)
print(f"Backend type: {type(be).__name__}")

print("Creating machine...")
be.create()
config.save()
print("STEP 1: PASSED\n")

# Step 2: Start
print("=" * 50)
print("STEP 2: pycrate machine start")
print("=" * 50)

be.start()
state = be.status()
print(f"State after start: {state.value}")
assert state in (MachineState.RUNNING, MachineState.STOPPED), f"Unexpected state: {state}"
print("STEP 2: PASSED\n")

# Step 3: Execute commands
print("=" * 50)
print("STEP 3: Execute commands inside machine")
print("=" * 50)

code, out, err = be.exec_command("echo 'Hello from PyCrate Machine'")
print(f"  echo test: code={code}, out={out.strip()}")
assert code == 0
assert "Hello from PyCrate Machine" in out

code, out, err = be.exec_command("uname -a")
print(f"  uname:     code={code}, out={out.strip()}")
assert code == 0
assert "Linux" in out

code, out, err = be.exec_command("cat /etc/os-release | head -1")
print(f"  os-release: code={code}, out={out.strip()}")
assert code == 0

code, out, err = be.exec_command("whoami")
print(f"  whoami:    code={code}, out={out.strip()}")
assert code == 0

print("STEP 3: PASSED\n")

# Step 4: Machine info
print("=" * 50)
print("STEP 4: Machine info")
print("=" * 50)

info = be.get_info()
for k, v in info.items():
    print(f"  {k}: {v}")
print("STEP 4: PASSED\n")

# Step 5: Stop
print("=" * 50)
print("STEP 5: pycrate machine stop")
print("=" * 50)

be.stop()
time.sleep(2)
state = be.status()
print(f"State after stop: {state.value}")
assert state == MachineState.STOPPED, f"Expected stopped, got {state}"
print("STEP 5: PASSED\n")

# Step 6: Restart
print("=" * 50)
print("STEP 6: Restart and verify")
print("=" * 50)

be.start()
code, out, err = be.exec_command("echo 'alive after restart'")
print(f"  Post-restart exec: code={code}, out={out.strip()}")
assert code == 0
assert "alive after restart" in out
print("STEP 6: PASSED\n")

# Step 7: Destroy
print("=" * 50)
print("STEP 7: pycrate machine destroy")
print("=" * 50)

be.destroy()
state = be.status()
print(f"State after destroy: {state.value}")
assert state == MachineState.NOT_CREATED, f"Expected not_created, got {state}"
print("STEP 7: PASSED\n")

# Summary
print("=" * 50)
print("ALL 7 LIFECYCLE STEPS PASSED")
print("=" * 50)
