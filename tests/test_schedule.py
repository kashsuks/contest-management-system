"""Contest schedule: start and end times, what each phase allows, and validation of the settings."""
from datetime import datetime, timedelta, timezone

import pytest

SOLVE = 'print(input())'


def iso(delta):
    return (datetime.now(timezone.utc) + delta).strftime('%Y-%m-%dT%H:%M:%SZ')


HOUR = timedelta(hours=1)


def schedule(admin, start=None, end=None, **extra):
    body = {'contest_name': 'Test Contest', 'start_time': start, 'end_time': end, **extra}
    response = admin.post('/update_contest_settings', json=body)
    assert response.status_code == 200, response.get_data(as_text=True)


def submit(client, problem_id):
    return client.post('/submit', json={'problem_id': problem_id, 'language': 'python', 'code': SOLVE})


def run(client):
    return client.post('/run_code', json={'language': 'python', 'code': SOLVE, 'test_cases': [{'input': '1\n', 'output': '1\n'}]})


class TestPhase:
    def test_no_schedule_means_running(self, student):
        state = student.get('/contest_state').get_json()
        assert state['phase'] == 'running'
        assert state['start_time'] is None and state['end_time'] is None
        assert state['server_time'].endswith('Z')

    @pytest.mark.parametrize('start,end,phase', [
        (HOUR, 2 * HOUR, 'upcoming'),
        (-HOUR, HOUR, 'running'),
        (-2 * HOUR, -HOUR, 'ended'),
        (None, HOUR, 'running'),
        (None, -HOUR, 'ended'),
        (HOUR, None, 'upcoming'),
        (-HOUR, None, 'running'),
    ])
    def test_phases(self, admin, student, start, end, phase):
        schedule(admin, iso(start) if start else None, iso(end) if end else None)
        assert student.get('/contest_state').get_json()['phase'] == phase

    def test_requires_login(self, anon):
        assert anon.get('/contest_state').status_code in (302, 401)

    def test_survives_a_restart(self, admin, app):
        schedule(admin, iso(HOUR), iso(2 * HOUR))
        assert app.load_contest_config()['start_time'] == app.contest_config['start_time']


class TestBeforeStart:
    @pytest.fixture(autouse=True)
    def upcoming(self, admin, make_problem):
        self.problem_id = make_problem()
        schedule(admin, iso(HOUR), iso(2 * HOUR))

    def test_contestants_see_no_problems(self, student):
        assert student.get('/problems').get_json() == []
        response = student.get(f'/problem/{self.problem_id}')
        assert response.status_code == 403
        assert 'not started' in response.get_json()['error']

    def test_contestants_cannot_submit_or_run(self, student):
        for response in (submit(student, self.problem_id), run(student)):
            assert response.status_code == 403
            assert 'not started' in response.get_json()['error']

    def test_leaderboard_hides_problem_names(self, admin, student):
        assert student.get('/leaderboard').get_json()['problems'] == []
        assert len(admin.get('/leaderboard').get_json()['problems']) == 1

    def test_organisers_are_not_blocked(self, admin):
        assert len(admin.get('/problems').get_json()) == 1
        assert admin.get(f'/problem/{self.problem_id}').status_code == 200
        assert submit(admin, self.problem_id).status_code == 200
        assert run(admin).status_code == 200

    def test_new_problems_are_not_announced(self, admin, student, app, monkeypatch):
        events = []
        monkeypatch.setattr(app.socketio, 'emit', lambda name, *args, **kw: events.append(name))
        admin.post('/create_problem', json={
            'title': 'Secret', 'description': 'x', 'difficulty': 'Easy', 'time_limit': 1000, 'memory_limit': 64,
            'batches': [{'points': 1, 'test_cases': [{'input': '1', 'output': '1'}]}]})
        assert 'new_problem' not in events and 'problems_changed' in events

    def test_problems_appear_when_the_contest_starts(self, admin, student):
        schedule(admin, iso(-HOUR), iso(HOUR))
        assert len(student.get('/problems').get_json()) == 1
        assert submit(student, self.problem_id).status_code == 200


class TestAfterEnd:
    @pytest.fixture(autouse=True)
    def ended(self, admin, make_problem):
        self.problem_id = make_problem()
        schedule(admin, iso(-2 * HOUR), iso(-HOUR))

    def test_contestants_can_read_but_not_submit_or_run(self, student):
        assert student.get(f'/problem/{self.problem_id}').status_code == 200
        for response in (submit(student, self.problem_id), run(student)):
            assert response.status_code == 403
            assert 'ended' in response.get_json()['error']

    def test_organisers_can_still_judge(self, admin):
        assert submit(admin, self.problem_id).status_code == 200

    def test_extending_the_end_reopens_the_contest(self, admin, student):
        schedule(admin, iso(-2 * HOUR), iso(HOUR))
        assert submit(student, self.problem_id).status_code == 200

    def test_manual_stop_is_still_enforced_inside_the_window(self, admin, student):
        schedule(admin, iso(-HOUR), iso(HOUR), submissions_stopped=True)
        assert submit(student, self.problem_id).status_code == 400
        assert student.get('/contest_state').get_json()['submissions_stopped'] is True


class TestSettingsValidation:
    @pytest.mark.parametrize('field,value', [
        ('start_time', 'tomorrow'), ('start_time', '2026-10-12T14:00:00'),  # no time zone
        ('end_time', 12345), ('end_time', '2026-13-45T99:00:00Z'), ('end_time', '0001-01-01T00:00:00+14:00'),
    ])
    def test_rejects_bad_times(self, admin, field, value):
        response = admin.post('/update_contest_settings', json={'contest_name': 'X', field: value})
        assert response.status_code == 400

    def test_end_must_be_after_start(self, admin):
        for start, end in ((iso(HOUR), iso(HOUR)), (iso(2 * HOUR), iso(HOUR))):
            response = admin.post('/update_contest_settings', json={'contest_name': 'X', 'start_time': start, 'end_time': end})
            assert response.status_code == 400 and 'after' in response.get_json()['error']

    def test_end_is_checked_against_the_saved_start(self, admin):
        schedule(admin, iso(2 * HOUR), None)
        response = admin.post('/update_contest_settings', json={'contest_name': 'X', 'end_time': iso(HOUR)})
        assert response.status_code == 400

    def test_a_rejected_save_changes_nothing(self, admin, app):
        schedule(admin, iso(HOUR), iso(2 * HOUR))
        before = dict(app.contest_config)
        admin.post('/update_contest_settings', json={'contest_name': 'Changed', 'start_time': 'junk'})
        assert app.contest_config == before

    def test_times_are_normalised_to_utc(self, admin, app):
        schedule(admin, '2030-01-01T12:00:00+02:00', '2030-01-01T18:30:00Z')
        assert app.contest_config['start_time'] == '2030-01-01T10:00:00Z'
        assert app.contest_config['end_time'] == '2030-01-01T18:30:00Z'

    def test_omitted_fields_are_kept_and_null_clears(self, admin, app):
        schedule(admin, iso(HOUR), iso(2 * HOUR))
        admin.post('/update_contest_settings', json={'contest_name': 'Renamed'})
        assert app.contest_config.get('start_time') and app.contest_config.get('end_time')
        schedule(admin, None, '')
        assert 'start_time' not in app.contest_config and 'end_time' not in app.contest_config

    def test_only_admins_can_change_it(self, student):
        response = student.post('/update_contest_settings', json={'contest_name': 'X', 'start_time': iso(HOUR)})
        assert response.status_code == 403

    def test_settings_endpoint_returns_the_schedule(self, admin):
        schedule(admin, iso(HOUR), iso(2 * HOUR))
        settings = admin.get('/contest_settings').get_json()
        assert settings['start_time'] and settings['end_time']


class TestFreezeNotice:
    """Saving the settings form must not announce a freeze change that did not happen."""

    def test_only_real_changes_are_announced(self, admin, app, monkeypatch):
        messages = []
        monkeypatch.setattr(app.socketio, 'emit', lambda name, *args, **kw: messages.append((name, args)))

        def save(frozen):
            admin.post('/update_contest_settings', json={'contest_name': 'T', 'leaderboard_frozen': frozen})
            return [args[0] for name, args in messages if name == 'update_leaderboard' and args]

        assert save(False) == []
        assert 'frozen' in save(True)[-1]
        assert len(save(True)) == 1               # saving again changes nothing
        assert 'unfrozen' in save(False)[-1]
