import json
import os
import subprocess
import sys
import tempfile
import uuid
from typing import Dict, Any, List

# How submissions are executed:
#   docker (default): every compile/run happens in a throwaway container with no network, a read-only
#                     filesystem, dropped capabilities, and memory / pid / cpu limits.
#   local:            runs directly on this machine. Development only: contestant code can touch the host.
SANDBOX = os.environ.get('JUDGE_SANDBOX', 'docker').lower()
DOCKER_IMAGE = os.environ.get('JUDGE_IMAGE', 'cms-judge')

RUNNER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'runner.py')
COMPILE_TIME_LIMIT_MS = 30_000
COMPILE_MEMORY_LIMIT_MB = 1024
DOCKER_START_GRACE_SECONDS = 20  # extra wall-clock time allowed for docker itself on top of the program's limit

_warned_local = False


class JudgeError(Exception):
    """The judge itself (not the submission) failed, e.g. Docker is unavailable."""


def _sandboxed_command(workdir: str, runner_args: List[str], writable: bool, memory_mb: int, name: str) -> List[str]:
    """Build the full command line that executes runner.py with the given arguments."""
    if SANDBOX == 'local':
        return [sys.executable, RUNNER_PATH] + runner_args

    return [
        'docker', 'run', '--rm', '-i', '--name', name,
        '--network', 'none',
        '--read-only', '--tmpfs', '/tmp:rw,exec,size=128m',
        '--memory', f'{memory_mb}m', '--memory-swap', f'{memory_mb}m',
        '--pids-limit', '64', '--cpus', '1',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '-v', f'{workdir}:/work:{"rw" if writable else "ro"}',
        '-v', f'{RUNNER_PATH}:/runner.py:ro',
        '-w', '/work',
        DOCKER_IMAGE, 'python', '/runner.py',
    ] + runner_args


def _execute(workdir: str, command: List[str], stdin: str, time_limit_ms: int, memory_limit_mb: int,
             writable: bool = False) -> Dict[str, Any]:
    """Run `command` (paths as seen inside the sandbox) and return runner.py's JSON report."""
    global _warned_local
    if SANDBOX == 'local' and not _warned_local:
        print('WARNING: JUDGE_SANDBOX=local - submissions run directly on this machine. Do not use for a real contest.',
              file=sys.stderr)
        _warned_local = True
    elif SANDBOX not in ('local', 'docker'):
        raise JudgeError(f"Unknown JUDGE_SANDBOX '{SANDBOX}' (expected 'docker' or 'local')")

    # The container also needs room for the runtime itself, so give it headroom over the program's limit
    container_memory_mb = max(memory_limit_mb, 64) + 64
    name = f'cms-judge-{uuid.uuid4().hex[:12]}'
    full_command = _sandboxed_command(workdir, [str(time_limit_ms), str(memory_limit_mb)] + command,
                                      writable, container_memory_mb, name)
    wall_clock = time_limit_ms / 1000 + DOCKER_START_GRACE_SECONDS

    try:
        completed = subprocess.run(full_command, input=stdin, capture_output=True, text=True, errors='replace',
                                   timeout=wall_clock)
    except subprocess.TimeoutExpired:
        if SANDBOX == 'docker':
            subprocess.run(['docker', 'kill', name], capture_output=True)
        return {'timed_out': True, 'returncode': None, 'stdout': '', 'stderr': '', 'time_ms': time_limit_ms,
                'memory_kb': 0}
    except FileNotFoundError as e:
        raise JudgeError(f'Cannot start the sandbox ({e}). Is Docker installed?')

    if SANDBOX == 'docker':
        if completed.returncode == 137:  # container was OOM-killed
            return {'timed_out': False, 'returncode': -9, 'stdout': '', 'stderr': '', 'time_ms': 0,
                    'memory_kb': memory_limit_mb * 1024 + 1}
        if completed.returncode in (125, 126, 127):
            raise JudgeError(f'Docker could not run the judge image "{DOCKER_IMAGE}": {completed.stderr.strip()[-300:]}'
                             ' (build it with: docker build -t cms-judge judge/)')

    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        raise JudgeError(f'Judge runner produced no result: {completed.stderr.strip()[-300:]}')


def prepare(code: str, language: str, workdir: str, memory_limit: int) -> Dict[str, Any]:
    """Write the source into `workdir` and compile it once.

    Returns {'command': [...]} to run each test case, or {'status': 'CE', 'error': ...}.
    """
    # Paths as seen from inside the sandbox
    base = workdir if SANDBOX == 'local' else '/work'
    filenames = {'python': 'solution.py', 'cpp': 'solution.cpp', 'java': 'Solution.java'}
    if language not in filenames:
        return {'status': 'CE', 'error': f'Unsupported language: {language}'}

    source_path = os.path.join(base, filenames[language])
    with open(os.path.join(workdir, filenames[language]), 'w') as f:
        f.write(code)
    # The container runs as an unprivileged user that must be able to read the sources and write binaries
    os.chmod(workdir, 0o777)
    os.chmod(os.path.join(workdir, filenames[language]), 0o644)

    if language == 'python':
        return {'command': ['python' if SANDBOX == 'docker' else sys.executable, source_path]}

    if language == 'cpp':
        binary = os.path.join(base, 'solution')
        compile_command = ['g++', '-O2', '-o', binary, source_path]
        run_command = [binary]
    else:
        compile_command = ['javac', '-d', base, source_path]
        run_command = ['java', f'-Xmx{max(memory_limit, 32)}m', '-cp', base, 'Solution']

    report = _execute(workdir, compile_command, '', COMPILE_TIME_LIMIT_MS, COMPILE_MEMORY_LIMIT_MB, writable=True)
    if report['timed_out']:
        return {'status': 'CE', 'error': 'Compilation timed out'}
    if report['returncode'] != 0:
        return {'status': 'CE', 'error': report['stderr'] or report['stdout']}
    return {'command': run_command}


def run_test_case(workdir: str, command: List[str], test_case: Dict[str, str], time_limit: int,
                  memory_limit: int) -> Dict[str, Any]:
    """Run the prepared program against one test case and return the result."""
    report = _execute(workdir, command, test_case['input'], time_limit, memory_limit)

    if report['timed_out']:
        return {'status': 'TLE', 'error': 'Time limit exceeded'}

    memory_used = report['memory_kb']  # in KB
    execution_time = report['time_ms']

    # SIGKILL with no timeout means the kernel's OOM killer stepped in
    if memory_used > memory_limit * 1024 or report['returncode'] == -9:
        return {'status': 'MLE', 'error': 'Memory limit exceeded'}

    if report['returncode'] != 0:
        return {
            'status': 'RE',
            'error': report['stderr'],
            'execution_time': execution_time,
            'memory_used': memory_used
        }

    expected_output = test_case['output'].strip()
    actual_output = report['stdout'].strip()

    if actual_output == expected_output:
        return {'status': 'AC', 'execution_time': execution_time, 'memory_used': memory_used}
    return {
        'status': 'WA',
        'expected': expected_output,
        'got': actual_output,
        'execution_time': execution_time,
        'memory_used': memory_used
    }


def judge_submission(code, language, batches, time_limit, memory_limit, is_run_code=False):
    """Judge a submission against batches of test cases."""
    total_earned = 0
    max_execution_time = 0
    max_memory_used = 0
    batch_results = []
    all_passed = True

    with tempfile.TemporaryDirectory() as workdir:
        prepared = prepare(code, language, workdir, memory_limit)

        # Run against each batch
        for batch in batches:
            batch_points = batch['points']
            test_cases = batch['test_cases']
            batch_passed = True
            batch_execution_time = 0
            batch_memory_used = 0
            error = ""

            current_batch_result = {
                'status': '',
                'batch_points': 0,
                'test_case_results': []
            }

            # Run against each test case in the batch
            for test_case in test_cases:
                if not batch_passed:
                    current_batch_result['test_case_results'].append({'status': 'skip'})
                    continue

                if 'command' in prepared:
                    result = run_test_case(workdir, prepared['command'], test_case, time_limit, memory_limit)
                else:
                    result = prepared  # compile error

                if result['status'] != 'AC':
                    batch_passed = False
                    all_passed = False
                    error = result['status']

                    # add specific execution time for TLE and MLE
                    time_taken = round(result.get('execution_time', 0), 2) if error != 'TLE' else f">{time_limit:.2f}"
                    memory_taken = round(result.get('memory_used', 0), 2) if error != 'MLE' else f">{memory_limit * 1024:.2f}"  # memory is reported in KB everywhere

                    current_batch_result['test_case_results'].append({
                        'status': result['status'],
                        'error': result.get('error', ''),
                        'expected': result.get('expected', ''),
                        'got': result.get('got', ''),
                        'execution_time': time_taken,
                        'memory_used': memory_taken
                    })
                else:
                    current_batch_result['test_case_results'].append({
                        'status': 'AC',
                        'execution_time': round(result.get('execution_time', 0), 2),
                        'memory_used': result.get('memory_used', 0)
                    })

                batch_execution_time = max(batch_execution_time, result.get('execution_time', 0))
                batch_memory_used = max(batch_memory_used, result.get('memory_used', 0))

            if batch_passed:
                current_batch_result['batch_points'] = batch_points
                current_batch_result['status'] = 'AC'
                total_earned += batch_points
                max_execution_time = max(max_execution_time, batch_execution_time)
                max_memory_used = max(max_memory_used, batch_memory_used)
            else:
                current_batch_result['status'] = error

            batch_results.append(current_batch_result)

    # For run code submissions, use all_passed to determine status
    # For contest submissions, use total_earned as before
    status = 'AC' if (is_run_code and all_passed) or (not is_run_code and total_earned > 0) else 'WA'

    return {
        'status': status,
        'points_earned': total_earned,
        'execution_time': round(max_execution_time, 2),
        'memory_used': max_memory_used,
        'batch_results': batch_results
    }


def main():
    """Main function to handle the judging process."""
    try:
        # Get input from environment variables
        code = os.environ.get('CODE', '')
        language = os.environ.get('LANGUAGE', '')
        time_limit = int(os.environ.get('TIME_LIMIT', 1000))
        memory_limit = int(os.environ.get('MEMORY_LIMIT', 256))
        batches = json.loads(os.environ.get('BATCHES', '[]'))

        if not all([code, language, batches]):
            print(json.dumps({
                'status': 'CE',
                'error': 'Missing required parameters'
            }))
            return

        # Judge the submission
        result = judge_submission(code, language, batches, time_limit, memory_limit)
        print(json.dumps(result))

    except Exception as e:
        print(json.dumps({
            'status': 'CE',
            'error': str(e)
        }))

if __name__ == '__main__':
    main()
