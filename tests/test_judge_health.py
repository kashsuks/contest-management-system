"""Judge availability checks and how a judge outage is reported."""
import subprocess

import pytest

from judge import judge as judge_module
from judge.judge import JudgeError, check_judge


class Completed:
    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


@pytest.fixture
def docker_mode(monkeypatch):
    monkeypatch.setattr(judge_module, 'SANDBOX', 'docker')
    monkeypatch.setattr(judge_module, '_health_cache', {'at': 0.0, 'result': None})
    return monkeypatch


def test_local_mode_is_ok_but_warns():
    result = check_judge(force=True)
    assert result['ok'] and result['warning'] and 'NOT sandboxed' in result['message']


def test_docker_not_installed(docker_mode):
    def run(*args, **kwargs):
        raise FileNotFoundError('docker')
    docker_mode.setattr(judge_module.subprocess, 'run', run)
    result = check_judge(force=True)
    assert not result['ok'] and 'not installed' in result['message']


def test_docker_daemon_not_running(docker_mode):
    docker_mode.setattr(judge_module.subprocess, 'run', lambda *a, **k: Completed(returncode=1))
    result = check_judge(force=True)
    assert not result['ok'] and 'not running' in result['message']


def test_docker_not_responding(docker_mode):
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired('docker', 15)
    docker_mode.setattr(judge_module.subprocess, 'run', run)
    assert 'not responding' in check_judge(force=True)['message']


def test_image_missing_is_reported_with_the_build_command(docker_mode):
    docker_mode.setattr(judge_module.subprocess, 'run', lambda *a, **k: Completed())  # docker itself is fine

    def run_missing_image(*args, **kwargs):
        raise JudgeError('Docker could not run the judge image "cms-judge": Unable to find image (build it with: docker build -t cms-judge judge/)')
    docker_mode.setattr(judge_module, 'judge_submission', run_missing_image)
    result = check_judge(force=True)
    assert not result['ok'] and 'docker build' in result['message']


def test_missing_image_error_is_a_clean_sentence(monkeypatch):
    monkeypatch.setattr(judge_module, 'SANDBOX', 'docker')
    monkeypatch.setattr(judge_module, 'DOCKER_IMAGE', 'cms-judge')
    stderr = "docker: Error response from daemon: No such image: cms-judge:latest\n\nRun 'docker run --help' for more information"
    monkeypatch.setattr(judge_module.subprocess, 'run', lambda *a, **k: Completed(returncode=125, stderr=stderr))
    with pytest.raises(JudgeError) as caught:
        judge_module._execute('/tmp/x', ['true'], '', 1000, 64)
    assert str(caught.value) == "The judge image 'cms-judge' is missing. Build it with: docker build -t cms-judge judge/"


def test_docker_run_never_pulls_images(monkeypatch):
    monkeypatch.setattr(judge_module, 'SANDBOX', 'docker')
    command = judge_module._sandboxed_command('/tmp/x', ['1000', '64', 'true'], False, 128, 'name')
    assert command[command.index('--pull') + 1] == 'never'


def test_healthy_docker_runs_a_smoke_test(docker_mode):
    docker_mode.setattr(judge_module.subprocess, 'run', lambda *a, **k: Completed())
    docker_mode.setattr(judge_module, 'judge_submission', lambda *a, **k: {'batch_results': [{'status': 'AC'}]})
    assert check_judge(force=True) == {'ok': True, 'sandbox': 'docker', 'message': 'The judge is working.'}


def test_smoke_test_failure_is_reported(docker_mode):
    docker_mode.setattr(judge_module.subprocess, 'run', lambda *a, **k: Completed())
    docker_mode.setattr(judge_module, 'judge_submission', lambda *a, **k: {'batch_results': [{'status': 'WA'}]})
    assert not check_judge(force=True)['ok']


def test_result_is_cached(docker_mode):
    calls = []
    def run(*a, **k):
        calls.append(1)
        return Completed(returncode=1)
    docker_mode.setattr(judge_module.subprocess, 'run', run)
    check_judge(force=True)
    check_judge()
    check_judge()
    assert len(calls) == 1


def test_judge_status_endpoint_is_admin_only(admin, student, anon):
    assert admin.get('/judge_status').get_json()['ok'] is True
    assert student.get('/judge_status').status_code == 403
    assert anon.get('/judge_status').status_code == 302


def test_outage_gives_contestants_a_clear_503(student, make_problem, app, monkeypatch):
    problem_id = make_problem()
    def down(*a, **k):
        raise JudgeError('docker went away')
    monkeypatch.setattr(app, 'judge_submission', down)

    response = student.post('/submit', json={'problem_id': problem_id, 'language': 'python', 'code': 'print(3)'})
    assert response.status_code == 503
    assert 'temporarily unavailable' in response.get_json()['error']
    assert 'docker' not in response.get_json()['error'].lower()  # internals aren't leaked

    response = student.post('/run_code', json={'language': 'python', 'code': 'print(1)', 'test_cases': [{'input': '', 'output': '1'}]})
    assert response.status_code == 503

    # the failed submission is recorded as ERROR so it can be rejudged later
    submissions = student.get('/submissions').get_json()
    assert [s['status'] for s in submissions] == ['ERROR']
