"""Terminate only the native tool calls cancelled by one turn interruption.

Codex emits item/started before cold exec-server startup has finished. A short
watch catches those late spawns without touching a subsequent turn's call ids.
"""
import json
import os
from pathlib import Path
import signal
import sys
import time


def matches(environment,thread_id,call_ids):
    values=environment.split(b'\0')
    return b'CODEX_THREAD_ID='+thread_id.encode() in values and any(
        b'CODEX_TOOL_CALL_ID='+ident.encode() in values for ident in call_ids)


def cancel(thread_id,call_ids):
    if not call_ids:return
    deadline=time.monotonic()+60
    seen=set()
    protected={1,os.getpgrp(),os.getpgid(os.getppid())}
    while time.monotonic()<deadline:
        for path in Path('/proc').iterdir():
            if not path.name.isdigit():continue
            try:
                environment=(path/'environ').read_bytes()
                if not matches(environment,thread_id,call_ids) or path.stat().st_uid!=os.getuid():continue
                pid=int(path.name);group=os.getpgid(pid)
                # Check the native identity again immediately before signalling.
                if not matches((path/'environ').read_bytes(),thread_id,call_ids):continue
                if group not in protected:os.killpg(group,signal.SIGKILL)
                else:os.kill(pid,signal.SIGKILL)
                seen.add(pid)
            except (OSError,ValueError):continue
        time.sleep(.1)


if __name__=='__main__':
    cancel(sys.argv[1],set(json.loads(sys.argv[2])))
