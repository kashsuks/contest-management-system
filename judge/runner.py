"""Runs one command with a time limit and reports the outcome as JSON on stdout.

Usage: runner.py <time_limit_ms> <memory_limit_mb> <command> [args...]
The program's stdin is forwarded to the command.

This is executed inside the sandbox container (or directly in JUDGE_SANDBOX=local mode), so peak memory
comes from the kernel's accounting for this one child instead of from polling a process that has exited.
"""
import json
import os
import resource
import signal
import subprocess
import sys
import time

MAX_STDOUT = 1_000_000  # characters of program output we are willing to return
MAX_STDERR = 64_000
MAX_FILE_SIZE = 16 * 1024 * 1024  # bytes a program may write to a file


def apply_limits(time_limit_ms):
    def preexec():
        cpu_seconds = time_limit_ms // 1000 + 2
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_SIZE, MAX_FILE_SIZE))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    return preexec


def peak_memory_kb():
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    # Linux reports kilobytes, macOS reports bytes
    return peak / 1024 if sys.platform == 'darwin' else peak


def main():
    time_limit_ms = int(sys.argv[1])
    memory_limit_mb = int(sys.argv[2])
    command = sys.argv[3:]
    stdin_data = sys.stdin.read()

    result = {'timed_out': False, 'returncode': None, 'stdout': '', 'stderr': '', 'time_ms': 0, 'memory_kb': 0}
    start = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors='replace',
            preexec_fn=apply_limits(time_limit_ms),
            start_new_session=True,  # own process group so the whole tree can be killed
        )
    except OSError as e:
        result['stderr'] = str(e)
        result['returncode'] = 127
        print(json.dumps(result))
        return

    try:
        stdout, stderr = process.communicate(input=stdin_data, timeout=time_limit_ms / 1000)
    except subprocess.TimeoutExpired:
        result['timed_out'] = True
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
    else:
        # Kill anything the program left running in the background
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    result.update({
        'returncode': process.returncode,
        'stdout': stdout[:MAX_STDOUT],
        'stderr': stderr[:MAX_STDERR],
        'time_ms': (time.monotonic() - start) * 1000,
        'memory_kb': peak_memory_kb(),
        'memory_limit_kb': memory_limit_mb * 1024,
    })
    print(json.dumps(result))


if __name__ == '__main__':
    main()
