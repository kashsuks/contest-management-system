"""CSV exports."""
import csv
import io

from conftest import GOOD_PROBLEM

SOLVE = 'print(input())'


def parse(response):
    assert response.mimetype == 'text/csv'
    text = response.get_data(as_text=True)
    assert text.startswith('﻿')  # BOM so Excel reads UTF-8 correctly
    return list(csv.reader(io.StringIO(text.lstrip('﻿'))))


def submit(client, problem_id, code=SOLVE):
    client.post('/submit', json={'problem_id': problem_id, 'language': 'python', 'code': code})


def test_standings_with_ties_and_no_submissions(admin, make_user, make_problem):
    one = make_problem(title='One')
    two = make_problem(title='Two')
    ana, bo, cy = make_user('ana'), make_user('bo_b'), make_user('cy_c')
    submit(ana, one); submit(ana, two)           # 20
    submit(bo, one)                              # 10
    submit(cy, one)                              # 10 (ties with bo)
    make_user('dee')                             # nothing

    rows = parse(admin.get('/admin/export/standings.csv'))
    assert rows[0] == ['Rank', 'Username', 'A', 'B', 'Total']
    assert rows[1:] == [['1', 'ana', '10', '10', '20'], ['2', 'bo_b', '10', '0', '10'],
                        ['2', 'cy_c', '10', '0', '10'], ['4', 'dee', '0', '0', '0']]


def test_standings_ignore_a_freeze_but_the_public_board_does_not(admin, student, make_problem):
    problem_id = make_problem()
    admin.post('/update_contest_settings', json={'contest_name': 'T', 'leaderboard_frozen': True})
    submit(student, problem_id)  # made while frozen
    assert student.get('/leaderboard').get_json()['users'][0]['total_points'] == 0
    assert parse(admin.get('/admin/export/standings.csv'))[1][-1] == '10'


def test_submissions_export_has_no_code(admin, student, make_problem):
    problem_id = make_problem()
    submit(student, problem_id, 'print(input())  # SECRET-MARKER')
    response = admin.get('/admin/export/submissions.csv')
    assert 'SECRET-MARKER' not in response.get_data(as_text=True)
    rows = parse(response)
    assert rows[0][:6] == ['Submission', 'Username', 'Problem', 'Problem title', 'Language', 'Status']
    assert rows[1][1:6] == ['student', 'A', 'Echo', 'python', 'AC'] and rows[1][6] == '10'


def test_formula_cells_are_neutralised(admin, student, make_problem):
    make_problem(title='=HYPERLINK("http://evil.example","click")')
    problem_id = admin.get('/problems').get_json()[0]['id']
    submit(student, problem_id)
    title_cell = parse(admin.get('/admin/export/submissions.csv'))[1][3]
    assert title_cell.startswith("'=") and not title_cell.startswith('=')


def test_exports_are_admin_only(student, anon):
    for path in ('/admin/export/standings.csv', '/admin/export/submissions.csv'):
        assert student.get(path).status_code == 403
        assert anon.get(path).status_code == 302


def test_download_headers(admin):
    response = admin.get('/admin/export/standings.csv')
    assert response.headers['Content-Disposition'] == 'attachment; filename="standings.csv"'
    assert response.headers['Cache-Control'] == 'no-store'
