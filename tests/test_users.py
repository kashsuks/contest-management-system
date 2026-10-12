"""Contestant account management, sessions, and the schema migration."""
import io

from pypdf import PdfReader
from sqlalchemy import text


def user_id(admin, username):
    return next(u['id'] for u in admin.get('/admin/users').get_json() if u['username'] == username)


def test_list_users_excludes_admins_and_counts_submissions(admin, make_user, make_problem):
    problem_id = make_problem()
    ana = make_user('ana')
    ana.post('/submit', json={'problem_id': problem_id, 'language': 'python', 'code': 'print(input())'})
    make_user('bo_b')
    users = admin.get('/admin/users').get_json()
    assert [(u['username'], u['submission_count'], u['disabled']) for u in users] == [('ana', 1, False), ('bo_b', 0, False)]
    assert all('password' not in u and 'password_hash' not in u for u in users)


def test_user_management_is_admin_only(student, anon, admin):
    target = user_id(admin, 'student')
    for method, path in [('get', '/admin/users'), ('post', f'/admin/users/{target}/reset_password'),
                         ('post', f'/admin/users/{target}/disabled'), ('delete', f'/admin/users/{target}'),
                         ('post', '/admin/slip_pdf')]:
        assert getattr(student, method)(path, json={}).status_code == 403, path
        assert getattr(anon, method)(path, json={}).status_code == 302, path


class TestDisable:
    def test_disabled_accounts_cannot_log_in_and_existing_sessions_end(self, admin, student, anon):
        target = user_id(admin, 'student')
        assert student.get('/username').status_code == 200

        assert admin.post(f'/admin/users/{target}/disabled', json={'disabled': True}).status_code == 200
        assert student.get('/username').status_code == 302          # already-open session is signed out
        response = anon.post('/login', json={'username': 'student', 'password': 'studentpass1'})
        assert response.status_code == 403 and 'disabled' in response.get_json()['error']
        # a wrong password must not reveal that the account exists but is disabled
        assert anon.post('/login', json={'username': 'student', 'password': 'wrong-password'}).status_code == 401

        assert admin.post(f'/admin/users/{target}/disabled', json={'disabled': False}).status_code == 200
        assert anon.post('/login', json={'username': 'student', 'password': 'studentpass1'}).status_code == 200

    def test_disabled_accounts_leave_the_leaderboard(self, admin, student):
        target = user_id(admin, 'student')
        assert [u['username'] for u in student.get('/leaderboard').get_json()['users']] == ['student']
        admin.post(f'/admin/users/{target}/disabled', json={'disabled': True})
        assert admin.get('/leaderboard').get_json()['users'] == []

    def test_input_is_validated(self, admin, student):
        target = user_id(admin, 'student')
        assert admin.post(f'/admin/users/{target}/disabled', json={'disabled': 'yes'}).status_code == 400
        assert admin.post(f'/admin/users/{target}/disabled', json={}).status_code == 400
        assert admin.post('/admin/users/9999/disabled', json={'disabled': True}).status_code == 404


class TestDelete:
    def test_delete_removes_the_account_and_its_submissions(self, admin, make_user, make_problem):
        problem_id = make_problem()
        ana = make_user('ana')
        for _ in range(2):
            ana.post('/submit', json={'problem_id': problem_id, 'language': 'python', 'code': 'print(input())'})
        response = admin.delete(f"/admin/users/{user_id(admin, 'ana')}")
        assert response.status_code == 200 and response.get_json()['deleted_submissions'] == 2
        assert admin.get('/admin/users').get_json() == []
        assert admin.get('/admin/submissions').get_json()['total'] == 0
        assert ana.get('/username').status_code == 302

    def test_unknown_user(self, admin):
        assert admin.delete('/admin/users/9999').status_code == 404


class TestResetPassword:
    def test_reset_gives_a_working_password_and_ends_old_sessions(self, admin, student, anon):
        target = user_id(admin, 'student')
        response = admin.post(f'/admin/users/{target}/reset_password')
        data = response.get_json()
        assert response.status_code == 200 and data['username'] == 'student' and len(data['password']) == 10
        assert response.headers['Cache-Control'] == 'no-store'

        assert student.get('/username').status_code == 302   # session from before the reset is signed out
        assert anon.post('/login', json={'username': 'student', 'password': 'studentpass1'}).status_code == 401
        assert anon.post('/login', json={'username': 'student', 'password': data['password']}).status_code == 200

    def test_each_reset_is_different(self, admin, student):
        target = user_id(admin, 'student')
        assert admin.post(f'/admin/users/{target}/reset_password').get_json()['password'] != \
            admin.post(f'/admin/users/{target}/reset_password').get_json()['password']

    def test_admin_accounts_cannot_be_changed_here(self, admin, app):
        with app.app.app_context():
            admin_id = app.User.query.filter_by(username='admin').first().id
        for method, path, body in [('post', f'/admin/users/{admin_id}/reset_password', None),
                                   ('post', f'/admin/users/{admin_id}/disabled', {'disabled': True}),
                                   ('delete', f'/admin/users/{admin_id}', None)]:
            response = getattr(admin, method)(path, json=body)
            assert response.status_code == 400, path
        assert admin.get('/admin/users').status_code == 200  # still signed in


class TestSlipPdf:
    def test_returns_a_one_slip_pdf(self, admin):
        response = admin.post('/admin/slip_pdf', json={'username': 'ana', 'password': 'Abc123xyz9'})
        assert response.status_code == 200 and response.mimetype == 'application/pdf'
        text_on_page = PdfReader(io.BytesIO(response.data)).pages[0].extract_text()
        assert 'ana' in text_on_page and 'Abc123xyz9' in text_on_page

    def test_rejects_bad_input(self, admin):
        for body in ({}, {'username': 'ana'}, {'username': 'a b', 'password': 'x'}, {'username': 'ana', 'password': ''},
                     {'username': 'ana', 'password': 'x' * 65}, {'username': 'ana', 'password': 'has space'},
                     {'username': 5, 'password': 'x'}):
            assert admin.post('/admin/slip_pdf', json=body).status_code == 400, body


class TestSessions:
    def test_old_style_cookies_are_rejected(self, anon, admin, app):
        # before sessions carried a fingerprint the cookie held just the numeric id
        with anon.session_transaction() as session:
            session['_user_id'] = '1'
        assert anon.get('/username').status_code == 302

    def test_a_tampered_fingerprint_is_rejected(self, anon, app):
        with anon.session_transaction() as session:
            session['_user_id'] = '1.' + '0' * 16
        assert anon.get('/username').status_code == 302


class TestSchemaMigration:
    def test_adds_the_disabled_column_to_an_old_database(self, app, make_user):
        make_user('veteran')
        with app.app.app_context():
            app.db.session.execute(text('ALTER TABLE "user" DROP COLUMN disabled'))
            app.db.session.commit()
            columns = {c['name'] for c in app.sa_inspect(app.db.engine).get_columns('user')}
            assert 'disabled' not in columns

            app.ensure_schema()
            columns = {c['name'] for c in app.sa_inspect(app.db.engine).get_columns('user')}
            assert 'disabled' in columns
            assert app.User.query.filter_by(username='veteran').first().disabled is False  # existing rows are active
            app.ensure_schema()  # running it again is harmless
