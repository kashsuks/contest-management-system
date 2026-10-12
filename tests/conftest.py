"""Shared test setup: an isolated temp database and working directory, with the judge in local mode.

The app reads its configuration when it is imported, so the environment is prepared first.
Run with:  venv/bin/python -m pytest
"""
import json
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKDIR = tempfile.mkdtemp(prefix='cms-tests-')
os.makedirs(os.path.join(WORKDIR, 'config'))
with open(os.path.join(WORKDIR, 'config', 'contest_config.json'), 'w') as f:
    json.dump({'contest_name': 'Test Contest', 'time_zone': 'UTC'}, f)
os.chdir(WORKDIR)  # the app reads config/contest_config.json relative to the working directory

os.environ.update({
    'SECRET_KEY': 'test-secret-key-0123456789abcdef',
    'ADMIN_PASSWORD': 'AdminPass-123',
    'DATABASE_URL': f"sqlite:///{os.path.join(WORKDIR, 'test.db')}",
    'JUDGE_SANDBOX': 'local',  # no Docker needed for the test suite
})
sys.path.insert(0, ROOT)

import app as app_module  # noqa: E402

app_module.app.config['TESTING'] = True
app_module.limiter.enabled = False

ADMIN_PASSWORD = os.environ['ADMIN_PASSWORD']


@pytest.fixture(autouse=True)
def fresh_database():
    """Every test starts with an empty database containing only the admin."""
    with app_module.app.app_context():
        app_module.db.drop_all()
        app_module.db.create_all()
        app_module.init_admin()
    app_module.contest_config.clear()
    app_module.contest_config.update({'contest_name': 'Test Contest', 'time_zone': 'UTC'})
    yield


@pytest.fixture
def app():
    return app_module


@pytest.fixture
def anon():
    return app_module.app.test_client()


def _login(client, username, password):
    response = client.post('/login', json={'username': username, 'password': password})
    assert response.status_code == 200, response.get_data(as_text=True)
    return client


@pytest.fixture
def admin():
    return _login(app_module.app.test_client(), 'admin', ADMIN_PASSWORD)


@pytest.fixture
def make_user(admin):
    """Create a contestant through the admin API and return a logged-in client for them."""
    def make(username='student', password='studentpass1'):
        response = admin.post('/create_user', json={'username': username, 'email': f'{username}@x.com', 'password': password})
        assert response.status_code == 201, response.get_data(as_text=True)
        return _login(app_module.app.test_client(), username, password)
    return make


@pytest.fixture
def student(make_user):
    return make_user()


GOOD_PROBLEM = {
    'title': 'Echo', 'description': 'Print the input.', 'difficulty': 'Easy', 'time_limit': 2000, 'memory_limit': 256,
    'batches': [{'points': 10, 'test_cases': [{'input': '3\n', 'output': '3\n'}]}],
}


@pytest.fixture
def make_problem(admin):
    def make(**overrides):
        body = {**GOOD_PROBLEM, **overrides}
        response = admin.post('/create_problem', json=body)
        assert response.status_code == 201, response.get_data(as_text=True)
        return response.get_json()['id']
    return make
