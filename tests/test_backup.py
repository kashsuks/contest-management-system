"""Backups: the copy/restore/prune machinery, the safety copies the app takes before risky changes, and manage.py."""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta

import pytest

import backup
from conftest import GOOD_PROBLEM, ROOT


@pytest.fixture
def folder(tmp_path):
    return tmp_path / 'backups'


@pytest.fixture
def database(tmp_path):
    path = tmp_path / 'live.db'
    connection = sqlite3.connect(path)
    connection.execute('CREATE TABLE user (id INTEGER PRIMARY KEY, name TEXT)')
    connection.executemany('INSERT INTO user (name) VALUES (?)', [('ana',), ('bo',)])
    connection.commit()
    connection.close()
    return str(path)


def names(path):
    connection = sqlite3.connect(path)
    try:
        return [row[0] for row in connection.execute('SELECT name FROM user ORDER BY id')]
    finally:
        connection.close()


def backup_files(app):
    return [item for item in backup.list_backups(app.backup_dir())]


def newest(app, reason):
    matching = [item for item in backup.list_backups(app.backup_dir()) if item['reason'] == reason]
    assert matching, f'no {reason} backup in {[i["name"] for i in backup.list_backups(app.backup_dir())]}'
    return matching[0]['path']


class TestCreate:
    def test_copy_is_a_complete_database(self, database, folder):
        copy = backup.create_backup(database, folder, 'manual')
        assert copy.parent == folder and copy.suffix == '.db'
        assert names(copy) == ['ana', 'bo']

    def test_copies_are_private_to_the_owner(self, database, folder):
        copy = backup.create_backup(database, folder)
        assert copy.stat().st_mode & 0o077 == 0 and folder.stat().st_mode & 0o077 == 0

    def test_copy_does_not_change_with_the_original(self, database, folder):
        copy = backup.create_backup(database, folder)
        connection = sqlite3.connect(database)
        connection.execute("INSERT INTO user (name) VALUES ('cy')")
        connection.commit()
        connection.close()
        assert names(copy) == ['ana', 'bo']

    def test_copy_while_a_writer_is_active(self, database, folder):
        writer = sqlite3.connect(database)
        writer.execute("INSERT INTO user (name) VALUES ('uncommitted')")  # open transaction, not committed
        copy = backup.create_backup(database, folder)
        writer.rollback()
        writer.close()
        assert names(copy) == ['ana', 'bo']

    def test_config_is_copied_alongside(self, database, folder, tmp_path):
        config = tmp_path / 'contest_config.json'
        config.write_text(json.dumps({'contest_name': 'Cup'}))
        copy = backup.create_backup(database, folder, config_path=str(config))
        companion = copy.with_name(copy.name[:-3] + '.config.json')
        assert json.loads(companion.read_text()) == {'contest_name': 'Cup'}
        assert backup.list_backups(folder)[0]['has_config'] is True

    def test_missing_config_is_fine(self, database, folder, tmp_path):
        backup.create_backup(database, folder, config_path=str(tmp_path / 'nope.json'))
        assert backup.list_backups(folder)[0]['has_config'] is False

    def test_names_never_collide_and_reasons_are_slugged(self, database, folder):
        moment = datetime(2026, 10, 12, 14, 30, 5)
        first = backup.create_backup(database, folder, 'Before Round 2!', now=moment)
        second = backup.create_backup(database, folder, 'Before Round 2!', now=moment)
        assert first.name == 'cms-20261012-143005-before-round-2.db'
        assert second.name == 'cms-20261012-143005-before-round-2.2.db'
        assert {item['reason'] for item in backup.list_backups(folder)} == {'before-round-2'}

    def test_missing_database_is_an_error_not_a_crash(self, folder, tmp_path):
        with pytest.raises(backup.BackupError):
            backup.create_backup(str(tmp_path / 'absent.db'), folder)
        assert not list(folder.glob('*.db')) if folder.exists() else True

    def test_a_file_that_is_not_a_database_leaves_nothing_behind(self, folder, tmp_path):
        junk = tmp_path / 'junk.db'
        junk.write_text('this is not sqlite at all' * 100)
        with pytest.raises(backup.BackupError):
            backup.create_backup(str(junk), folder)
        assert list(folder.iterdir()) == []

    @pytest.mark.parametrize('uri,expected', [
        ('sqlite:///coding_contest.db', '/inst/coding_contest.db'),
        ('sqlite:////var/data/x.db', '/var/data/x.db'),
        ('sqlite://', None), ('sqlite:///:memory:', None),
        ('postgresql://u:p@h/db', None), (None, None),
    ])
    def test_database_file_from_uri(self, uri, expected):
        assert backup.sqlite_file(uri, '/inst') == expected


class TestPrune:
    def make(self, database, folder, reason, minutes_ago):
        return backup.create_backup(database, folder, reason, now=datetime(2026, 1, 1, 12, 0, 0) - timedelta(minutes=minutes_ago))

    def test_limits_apply_per_kind_and_manual_is_kept(self, database, folder):
        for age in range(5):
            self.make(database, folder, 'periodic', age)
        for age in range(4):
            self.make(database, folder, 'pre-delete-user', age)
        for age in range(3):
            self.make(database, folder, 'manual-x', age)

        removed = backup.prune(folder, keep_periodic=2, keep_automatic=3)
        left = backup.list_backups(folder)
        kinds = [backup.kind_of(item['reason']) for item in left]
        assert (kinds.count('periodic'), kinds.count('automatic'), kinds.count('manual')) == (2, 3, 3)
        assert len(removed) == 4

    def test_oldest_go_first(self, database, folder):
        for age in (0, 10, 20, 30):
            self.make(database, folder, 'periodic', age)
        backup.prune(folder, keep_periodic=2)
        assert [item['created'].minute for item in backup.list_backups(folder)] == [0, 50]  # 12:00 and 11:50

    def test_companion_config_goes_with_it(self, database, folder, tmp_path):
        config = tmp_path / 'c.json'
        config.write_text('{}')
        for age in (0, 10):
            backup.create_backup(database, folder, 'periodic', config_path=str(config),
                                 now=datetime(2026, 1, 1, 12, 0, 0) - timedelta(minutes=age))
        backup.prune(folder, keep_periodic=1)
        assert len(list(folder.glob('*.config.json'))) == 1

    def test_other_files_are_ignored(self, database, folder):
        self.make(database, folder, 'periodic', 0)
        (folder / 'notes.txt').write_text('keep me')
        backup.prune(folder, keep_periodic=1, keep_automatic=1)
        assert (folder / 'notes.txt').exists()


class TestChangeDetection:
    def test_unchanged_database_is_skipped(self, database, folder):
        assert backup.changed_since_last_backup(database, folder) is True      # nothing backed up yet
        backup.create_backup(database, folder)
        assert backup.changed_since_last_backup(database, folder) is False
        future = os.path.getmtime(database) + 60
        os.utime(database, (future, future))                                     # a write after the backup
        assert backup.changed_since_last_backup(database, folder) is True


class TestRestore:
    def test_round_trip_restores_data_and_config(self, database, folder, tmp_path):
        config = tmp_path / 'contest_config.json'
        config.write_text(json.dumps({'contest_name': 'Before'}))
        copy = backup.create_backup(database, folder, config_path=str(config))

        connection = sqlite3.connect(database)
        connection.execute('DELETE FROM user')
        connection.commit()
        connection.close()
        config.write_text(json.dumps({'contest_name': 'After'}))

        assert backup.restore_backup(copy, database, config_path=str(config)) is True
        assert names(database) == ['ana', 'bo']
        assert json.loads(config.read_text()) == {'contest_name': 'Before'}

    def test_restore_without_companion_config_leaves_config_alone(self, database, folder, tmp_path):
        copy = backup.create_backup(database, folder)
        config = tmp_path / 'c.json'
        config.write_text('{"keep": true}')
        assert backup.restore_backup(copy, database, config_path=str(config)) is False
        assert json.loads(config.read_text()) == {'keep': True}

    def test_refuses_things_that_are_not_contest_backups(self, database, tmp_path):
        other = tmp_path / 'other.db'
        sqlite3.connect(other).execute('CREATE TABLE unrelated (x)').connection.close()
        junk = tmp_path / 'junk.db'
        junk.write_text('garbage' * 200)
        for bad in (other, junk, tmp_path / 'missing.db'):
            with pytest.raises(backup.BackupError):
                backup.restore_backup(bad, database)
        assert names(database) == ['ana', 'bo']  # the live database is untouched by a refused restore


class TestPeriodicThread:
    def test_runs_repeatedly_and_survives_errors(self):
        import threading
        calls, done = [], threading.Event()

        def work():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('disk full')
            if len(calls) >= 3:
                done.set()
        logged = []
        timer = backup.PeriodicBackups(0.01, work, log=logged.append)
        timer.start()
        assert done.wait(5)
        timer.stop()
        assert len(calls) >= 3 and 'disk full' in logged[0]


class TestAppSafetyCopies:
    """Risky admin actions leave a copy of the data as it was just before."""

    def test_deleting_a_problem(self, admin, student, make_problem, app):
        problem_id = make_problem()
        student.post('/submit', json={'problem_id': problem_id, 'language': 'python', 'code': 'print(input())'})
        assert admin.delete(f'/problem/{problem_id}').status_code == 200

        copy = newest(app, 'pre-delete-problem')
        connection = sqlite3.connect(copy)
        assert connection.execute('SELECT COUNT(*) FROM problem').fetchone()[0] == 1
        assert connection.execute('SELECT COUNT(*) FROM submission').fetchone()[0] == 1   # still there in the copy
        connection.close()

    def test_deleting_a_user(self, admin, make_user, app):
        make_user('doomed')
        user_id = next(u['id'] for u in admin.get('/admin/users').get_json() if u['username'] == 'doomed')
        assert admin.delete(f'/admin/users/{user_id}').status_code == 200
        connection = sqlite3.connect(newest(app, 'pre-delete-user'))
        assert connection.execute("SELECT COUNT(*) FROM user WHERE username = 'doomed'").fetchone()[0] == 1
        connection.close()

    def test_editing_a_problem_keeps_the_old_version(self, admin, make_problem, app):
        problem_id = make_problem(title='Original title')
        assert admin.put(f'/problem/{problem_id}', json={**GOOD_PROBLEM, 'title': 'New title'}).status_code == 200
        connection = sqlite3.connect(newest(app, 'pre-edit-problem'))
        assert connection.execute('SELECT title FROM problem').fetchone()[0] == 'Original title'
        connection.close()

    def test_rejudging(self, admin, student, make_problem, app):
        problem_id = make_problem()
        student.post('/submit', json={'problem_id': problem_id, 'language': 'python', 'code': 'print(input())'})
        assert admin.post(f'/admin/problem/{problem_id}/rejudge').status_code == 202
        newest(app, 'pre-rejudge-problem')

    def test_contest_config_is_copied_too(self, admin, make_problem, app):
        admin.post('/update_contest_settings', json={'contest_name': 'Backed Up Cup'})
        problem_id = make_problem()
        admin.delete(f'/problem/{problem_id}')
        companion = newest(app, 'pre-delete-problem')
        companion = companion.with_name(companion.name[:-3] + '.config.json')
        assert json.loads(companion.read_text())['contest_name'] == 'Backed Up Cup'

    def test_a_failing_backup_never_blocks_the_action(self, admin, make_problem, app, monkeypatch):
        problem_id = make_problem()

        def broken(*args, **kwargs):
            raise backup.BackupError('disk full')
        monkeypatch.setattr(backup, 'create_backup', broken)
        assert admin.delete(f'/problem/{problem_id}').status_code == 200
        assert admin.get('/problems').get_json() == []

    def test_old_automatic_copies_are_thinned_out(self, admin, make_problem, app, monkeypatch):
        monkeypatch.setenv('BACKUP_KEEP_AUTOMATIC', '2')
        for _ in range(4):
            admin.put(f'/problem/{make_problem()}', json=GOOD_PROBLEM)
        assert len([i for i in backup.list_backups(app.backup_dir()) if i['reason'] == 'pre-edit-problem']) == 2

    def test_periodic_job_only_copies_when_data_changed(self, admin, make_problem, app):
        make_problem()
        app.periodic_backup()
        count = len(backup_files(app))
        assert count == 1
        app.periodic_backup()                      # nothing happened since
        assert len(backup_files(app)) == count
        future = os.path.getmtime(app.database_file()) + 60
        os.utime(app.database_file(), (future, future))
        app.periodic_backup()
        assert len(backup_files(app)) == count + 1

    def test_in_memory_database_is_reported_not_crashed(self, app, monkeypatch):
        monkeypatch.setitem(app.app.config, 'SQLALCHEMY_DATABASE_URI', 'sqlite://')
        assert app.database_file() is None
        assert app.snapshot('pre-test') is None
        with pytest.raises(backup.BackupError):
            app.make_backup('manual')


class TestManageCommand:
    """The real command line, against a copy of the test database, so it behaves as the organiser will use it."""

    def run(self, tmp_path, *args, stdin=None):
        env = {**os.environ, 'BACKUP_DIR': str(tmp_path / 'cli-backups')}
        return subprocess.run([sys.executable, os.path.join(ROOT, 'manage.py'), *args], cwd=os.getcwd(), env=env,
                              input=stdin, capture_output=True, text=True, timeout=120)

    def test_backup_list_restore(self, admin, make_user, tmp_path, app):
        make_user('keeper')
        result = self.run(tmp_path, 'backup', '--note', 'before round 2')
        assert result.returncode == 0, result.stderr
        assert 'manual-before-round-2' in result.stdout

        listing = self.run(tmp_path, 'list-backups')
        assert listing.returncode == 0 and 'manual-before-round-2' in listing.stdout

        user_id = next(u['id'] for u in admin.get('/admin/users').get_json() if u['username'] == 'keeper')
        admin.delete(f'/admin/users/{user_id}')
        assert 'keeper' not in [u['username'] for u in admin.get('/admin/users').get_json()]

        name = next(line.split()[0] for line in listing.stdout.splitlines() if 'manual-before-round-2' in line)
        declined = self.run(tmp_path, 'restore', name, stdin='no\n')
        assert declined.returncode != 0 and 'Cancelled' in declined.stderr
        assert 'keeper' not in [u['username'] for u in admin.get('/admin/users').get_json()]

        restored = self.run(tmp_path, 'restore', name, '--yes')
        assert restored.returncode == 0, restored.stderr
        assert 'keeper' in [u['username'] for u in admin.get('/admin/users').get_json()]
        assert 'manual-before-restore' in self.run(tmp_path, 'list-backups').stdout   # the undo copy

    def test_restore_of_a_missing_file_says_so(self, tmp_path):
        result = self.run(tmp_path, 'restore', 'nope.db', '--yes')
        assert result.returncode != 0 and 'does not exist' in result.stderr

    def test_list_with_no_backups(self, tmp_path):
        result = self.run(tmp_path, 'list-backups')
        assert result.returncode != 0 and 'No backups' in result.stderr

    def test_unknown_command_prints_usage(self, tmp_path):
        result = self.run(tmp_path, 'explode')
        assert result.returncode != 0 and 'usage' in result.stderr.lower()
