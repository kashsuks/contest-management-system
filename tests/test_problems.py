"""Creating, editing and deleting problems."""
import copy

import pytest

from conftest import GOOD_PROBLEM

SOLVE = 'print(input())'


def submit(client, problem_id, code=SOLVE):
    response = client.post('/submit', json={'problem_id': problem_id, 'language': 'python', 'code': code})
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


class TestEdit:
    def test_admin_can_edit_everything_but_the_label(self, admin, make_problem):
        problem_id = make_problem()
        before = admin.get('/problems').get_json()[0]
        body = copy.deepcopy(GOOD_PROBLEM)
        body.update(title='Renamed', description='New text', difficulty='Hard', time_limit=3000, memory_limit=512,
                    batches=[{'points': 5, 'test_cases': [{'input': 'a\n', 'output': 'a\n'}]},
                             {'points': 7, 'test_cases': [{'input': 'b\n', 'output': 'b\n'}]}])
        response = admin.put(f'/problem/{problem_id}', json=body)
        assert response.status_code == 200

        updated = admin.get(f'/problem/{problem_id}').get_json()
        assert (updated['title'], updated['description'], updated['difficulty']) == ('Renamed', 'New text', 'Hard')
        assert (updated['time_limit'], updated['memory_limit'], updated['total_points']) == (3000, 512, 12)
        assert updated['batches'] == body['batches']
        assert updated['shortname'] == before['shortname']

    def test_edits_are_validated(self, admin, make_problem):
        problem_id = make_problem()
        body = copy.deepcopy(GOOD_PROBLEM)
        body['batches'] = [{'points': 'ten', 'test_cases': [{'input': '1', 'output': '1'}]}]
        assert admin.put(f'/problem/{problem_id}', json=body).status_code == 400
        assert admin.put(f'/problem/{problem_id}', json={**GOOD_PROBLEM, 'title': ''}).status_code == 400
        assert admin.put(f'/problem/{problem_id}', data='nope', content_type='application/json').status_code == 400
        assert admin.get(f'/problem/{problem_id}').get_json()['title'] == 'Echo'  # nothing was changed

    def test_unknown_problem_is_404(self, admin):
        assert admin.put('/problem/999', json=GOOD_PROBLEM).status_code == 404

    def test_students_cannot_edit(self, student, make_problem):
        problem_id = make_problem()
        assert student.put(f'/problem/{problem_id}', json=GOOD_PROBLEM).status_code == 403

    def test_existing_submissions_keep_their_results(self, admin, student, make_problem):
        problem_id = make_problem()
        first = submit(student, problem_id)
        assert first['points_earned'] == 10
        body = copy.deepcopy(GOOD_PROBLEM)
        body['batches'] = [{'points': 99, 'test_cases': [{'input': 'x\n', 'output': 'y\n'}]}]
        admin.put(f'/problem/{problem_id}', json=body)
        # the stored result is untouched until someone rejudges
        stored = student.get(f"/submission/{first['id']}").get_json()
        assert stored['points_earned'] == 10


class TestDelete:
    def test_delete_removes_problem_and_its_submissions(self, admin, student, make_problem):
        keep, remove = make_problem(title='Keep'), make_problem(title='Remove')
        submit(student, keep)
        submit(student, remove)
        submit(student, remove)

        response = admin.delete(f'/problem/{remove}')
        assert response.status_code == 200 and response.get_json()['deleted_submissions'] == 2
        assert [p['title'] for p in admin.get('/problems').get_json()] == ['Keep']
        assert admin.get(f'/problem/{remove}').status_code == 404
        assert len(student.get('/submissions').get_json()) == 1

    def test_students_cannot_delete(self, student, make_problem):
        problem_id = make_problem()
        assert student.delete(f'/problem/{problem_id}').status_code == 403

    def test_deleting_twice_is_404(self, admin, make_problem):
        problem_id = make_problem()
        assert admin.delete(f'/problem/{problem_id}').status_code == 200
        assert admin.delete(f'/problem/{problem_id}').status_code == 404

    def test_submitting_to_a_deleted_problem_is_a_clear_404(self, admin, student, make_problem):
        problem_id = make_problem()
        admin.delete(f'/problem/{problem_id}')
        response = student.post('/submit', json={'problem_id': problem_id, 'language': 'python', 'code': SOLVE})
        assert response.status_code == 404 and 'no longer exists' in response.get_json()['error']

    def test_submit_rejects_a_non_numeric_problem_id(self, student):
        response = student.post('/submit', json={'problem_id': 'abc', 'language': 'python', 'code': SOLVE})
        assert response.status_code == 400


class TestLabels:
    def test_a_freed_label_is_reused_instead_of_colliding(self, admin, make_problem):
        ids = [make_problem(title=t) for t in ('one', 'two', 'three')]
        admin.delete(f'/problem/{ids[1]}')  # frees "B"
        make_problem(title='four')
        labels = sorted(p['shortname'] for p in admin.get('/problems').get_json())
        assert labels == ['A', 'B', 'C']  # no duplicate C

    def test_labels_continue_past_z(self, admin, make_problem):
        for i in range(27):
            make_problem(title=f'p{i}')
        labels = [p['shortname'] for p in admin.get('/problems').get_json()]
        assert len(labels) == len(set(labels)) == 27
        assert {'A', 'Z', 'AA'} <= set(labels)


class TestAdminOnlyFields:
    def test_submission_counts_are_only_for_admins(self, admin, student, make_problem):
        problem_id = make_problem()
        submit(student, problem_id)
        assert admin.get('/problems').get_json()[0]['submission_count'] == 1
        assert 'submission_count' not in student.get('/problems').get_json()[0]


class TestCsrfCoversEveryUnsafeMethod:
    @pytest.mark.parametrize('method', ['put', 'delete'])
    def test_cross_origin_requests_are_blocked(self, admin, make_problem, method):
        problem_id = make_problem()
        response = getattr(admin, method)(f'/problem/{problem_id}', json=GOOD_PROBLEM, headers={'Origin': 'http://evil.example'})
        assert response.status_code == 403
        assert admin.get(f'/problem/{problem_id}').status_code == 200  # still there

    def test_same_origin_is_allowed(self, admin, make_problem):
        problem_id = make_problem()
        response = admin.delete(f'/problem/{problem_id}', headers={'Origin': 'http://localhost'})
        assert response.status_code == 200


def test_favicon_is_served_without_logging_in(anon):
    for path in ('/favicon.ico', '/static/favicon.svg'):
        response = anon.get(path)
        assert response.status_code == 200 and response.mimetype == 'image/svg+xml', path
