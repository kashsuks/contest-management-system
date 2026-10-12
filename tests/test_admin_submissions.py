"""Admin view of all submissions, and rejudging."""
import copy
import time

from conftest import GOOD_PROBLEM

SOLVE = 'print(input())'


def submit(client, problem_id, code=SOLVE):
    response = client.post('/submit', json={'problem_id': problem_id, 'language': 'python', 'code': code})
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def test_admin_sees_everyones_submissions_newest_first(admin, make_user, make_problem):
    problem_id = make_problem()
    ana, bo = make_user('ana'), make_user('bo_b')
    first, second = submit(ana, problem_id), submit(bo, problem_id, 'print("wrong")')

    data = admin.get('/admin/submissions').get_json()
    assert data['total'] == 2
    assert [item['id'] for item in data['items']] == [second['id'], first['id']]
    newest = data['items'][0]
    assert newest['username'] == 'bo_b' and newest['status'] == 'WA' and newest['points_earned'] == 0
    assert newest['problem'] == {'id': problem_id, 'shortname': 'A', 'title': 'Echo', 'total_points': 10}
    assert 'code' not in newest  # the list stays light


def test_filters_and_paging(admin, make_user, make_problem):
    one, two = make_problem(title='One'), make_problem(title='Two')
    ana, bo = make_user('ana'), make_user('bo_b')
    for _ in range(3):
        submit(ana, one)
    submit(bo, two, 'print("x")')

    assert admin.get(f'/admin/submissions?problem_id={two}').get_json()['total'] == 1
    assert admin.get('/admin/submissions?username=AN').get_json()['total'] == 3      # case-insensitive substring
    assert admin.get('/admin/submissions?username=o_b').get_json()['total'] == 1
    assert admin.get('/admin/submissions?username=a_a').get_json()['total'] == 0     # '_' is literal ('ana' would match a wildcard)
    assert admin.get('/admin/submissions?username=%25').get_json()['total'] == 0     # '%' is literal, not a wildcard
    assert admin.get('/admin/submissions?status=WA').get_json()['total'] == 1
    assert admin.get('/admin/submissions?status=nonsense').get_json()['total'] == 4  # unknown status is ignored
    page = admin.get('/admin/submissions?limit=2&offset=1').get_json()
    assert page['total'] == 4 and len(page['items']) == 2
    assert len(admin.get('/admin/submissions?limit=9999').get_json()['items']) == 4  # capped, not an error


def test_detail_includes_code_and_results(admin, student, make_problem):
    problem_id = make_problem()
    sub = submit(student, problem_id, 'print(input())  # my solution')
    detail = admin.get(f"/admin/submission/{sub['id']}").get_json()
    assert detail['code'] == 'print(input())  # my solution'
    assert detail['batch_results'][0]['status'] == 'AC' and detail['username'] == 'student'
    assert admin.get('/admin/submission/9999').status_code == 404


def test_all_admin_submission_routes_are_admin_only(student, anon, make_problem):
    problem_id = make_problem()
    sub = submit(student, problem_id)
    paths = ['/admin/submissions', f"/admin/submission/{sub['id']}", '/admin/rejudge_status']
    for path in paths:
        assert student.get(path).status_code == 403, path
        assert anon.get(path).status_code == 302, path
    assert student.post(f"/admin/submission/{sub['id']}/rejudge").status_code == 403
    assert student.post(f'/admin/problem/{problem_id}/rejudge').status_code == 403


def _break_the_tests(admin, problem_id):
    """Change the expected output so the old solution no longer passes."""
    body = copy.deepcopy(GOOD_PROBLEM)
    body['batches'] = [{'points': 10, 'test_cases': [{'input': '3\n', 'output': 'something else\n'}]}]
    assert admin.put(f'/problem/{problem_id}', json=body).status_code == 200


def test_rejudge_one_submission_uses_the_current_tests(admin, student, make_problem):
    problem_id = make_problem()
    sub = submit(student, problem_id)
    assert sub['points_earned'] == 10
    _break_the_tests(admin, problem_id)

    response = admin.post(f"/admin/submission/{sub['id']}/rejudge")
    assert response.status_code == 200
    assert response.get_json()['points_earned'] == 0 and response.get_json()['status'] == 'WA'
    detail = admin.get(f"/admin/submission/{sub['id']}").get_json()
    assert detail['points_earned'] == 0
    assert 'expected' not in str(detail['batch_results'])  # hidden test output is still never stored
    assert student.get('/leaderboard').get_json()['users'][0]['total_points'] == 0


def test_rejudge_fixes_submissions_that_failed_during_an_outage(admin, student, make_problem, app, monkeypatch):
    problem_id = make_problem()
    def down(*a, **k):
        raise app.JudgeError('down')
    monkeypatch.setattr(app, 'judge_submission', down)
    student.post('/submit', json={'problem_id': problem_id, 'language': 'python', 'code': SOLVE})
    sub_id = student.get('/submissions').get_json()[0]['id']
    assert student.get('/submissions').get_json()[0]['status'] == 'ERROR'

    monkeypatch.undo()  # the judge is back
    assert admin.post(f'/admin/submission/{sub_id}/rejudge').get_json()['status'] == 'AC'


def test_rejudge_reports_an_outage_without_changing_the_record(admin, student, make_problem, app, monkeypatch):
    problem_id = make_problem()
    sub = submit(student, problem_id)
    monkeypatch.setattr(app, 'judge_submission', lambda *a, **k: (_ for _ in ()).throw(app.JudgeError('down')))
    response = admin.post(f"/admin/submission/{sub['id']}/rejudge")
    assert response.status_code == 503
    assert admin.get(f"/admin/submission/{sub['id']}").get_json()['status'] == 'AC'


def wait_for_rejudge(admin, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = admin.get('/admin/rejudge_status').get_json()
        if not status['running']:
            return status
        time.sleep(0.1)
    raise AssertionError('rejudge did not finish')


def test_rejudge_all_submissions_of_a_problem_in_the_background(admin, make_user, make_problem):
    problem_id = make_problem()
    users = [make_user(f'user{i}') for i in range(3)]
    for client in users:
        submit(client, problem_id)
    _break_the_tests(admin, problem_id)

    response = admin.post(f'/admin/problem/{problem_id}/rejudge')
    assert response.status_code == 202 and response.get_json()['total'] == 3
    status = wait_for_rejudge(admin)
    assert (status['done'], status['failed'], status['total'], status['error']) == (3, 0, 3, None)
    assert {item['points_earned'] for item in admin.get('/admin/submissions').get_json()['items']} == {0}


def test_only_one_rejudge_runs_at_a_time(admin, make_problem, app):
    problem_id = make_problem()
    app.rejudge_job['running'] = True
    try:
        assert admin.post(f'/admin/problem/{problem_id}/rejudge').status_code == 409
    finally:
        app.rejudge_job['running'] = False


def test_rejudging_a_problem_without_submissions_is_a_noop(admin, make_problem):
    problem_id = make_problem()
    response = admin.post(f'/admin/problem/{problem_id}/rejudge')
    assert response.status_code == 200 and response.get_json()['total'] == 0
    assert admin.get('/admin/rejudge_status').get_json()['running'] is False


def test_a_judge_outage_stops_a_bulk_rejudge(admin, make_user, make_problem, app, monkeypatch):
    problem_id = make_problem()
    for i in range(5):
        submit(make_user(f'user{i}'), problem_id)
    monkeypatch.setattr(app, 'judge_submission', lambda *a, **k: (_ for _ in ()).throw(app.JudgeError('down')))
    admin.post(f'/admin/problem/{problem_id}/rejudge')
    status = wait_for_rejudge(admin)
    assert status['error'] and 'unavailable' in status['error']
    assert status['failed'] == 3 and status['done'] == 0  # gave up after three outages in a row
