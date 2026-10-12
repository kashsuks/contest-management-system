"""Safety copies of the SQLite database (and the contest config that goes with it).

Copies are made with SQLite's online backup API, so they are consistent even while the server is running and
writing. Each copy is a normal database file, named  cms-<YYYYMMDD-HHMMSS>-<reason>.db , next to an optional
cms-<...>.config.json  holding the contest settings of that moment.

Reasons decide how long a copy is kept (see prune): "manual" copies are never deleted, "periodic" copies are
the timer's and are thinned out quickly, every other reason (startup, pre-delete-problem, ...) is an automatic
safety copy and is kept for longer.
"""
import os
import re
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

DEFAULT_DIR = 'backups'
NAME = re.compile(r'^cms-(\d{8}-\d{6})-([a-z0-9-]+?)(?:\.(\d+))?\.db$')


class BackupError(Exception):
    pass


def sqlite_file(uri, instance_path=None):
    """The database file behind a SQLAlchemy URI, or None when it is not a file-based SQLite database."""
    match = re.match(r'^sqlite:///(.*)$', uri or '')
    if not match or match.group(1) in ('', ':memory:') or match.group(1).startswith('file:'):
        return None
    path = match.group(1)
    if not os.path.isabs(path) and instance_path:
        path = os.path.join(instance_path, path)
    return os.path.abspath(path)


def _config_copy(path):
    return Path(path).with_name(Path(path).name[:-len('.db')] + '.config.json')


def _connect_readonly(path):
    return sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro', uri=True, timeout=30)


def _slug(reason):
    slug = re.sub(r'[^a-z0-9]+', '-', str(reason).lower()).strip('-')
    return slug or 'manual'


def create_backup(db_path, backup_dir, reason='manual', config_path=None, now=None):
    """Copy the database to backup_dir and return the new file's path. Raises BackupError if it could not be made."""
    if not db_path or not os.path.exists(db_path):
        raise BackupError(f'There is no database file at {db_path}')
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)  # copies hold password hashes and code

    stamp = (now or datetime.now()).strftime('%Y%m%d-%H%M%S')
    reason = _slug(reason)
    target = backup_dir / f'cms-{stamp}-{reason}.db'
    counter = 1
    while target.exists():
        counter += 1
        target = backup_dir / f'cms-{stamp}-{reason}.{counter}.db'

    partial = target.with_name(target.name + '.partial')  # never leave a half-written file that looks like a backup
    try:
        source = _connect_readonly(db_path)
        try:
            destination = sqlite3.connect(partial)
            try:
                source.backup(destination)
                if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise BackupError('The copy failed its integrity check')
            finally:
                destination.close()
        finally:
            source.close()
        os.chmod(partial, 0o600)
        os.replace(partial, target)
    except (sqlite3.Error, OSError) as error:
        raise BackupError(f'Could not back up {db_path}: {error}') from error
    finally:
        if partial.exists():
            partial.unlink()

    if config_path and os.path.exists(config_path):
        shutil.copyfile(config_path, _config_copy(target))
        os.chmod(_config_copy(target), 0o600)
    return target


def list_backups(backup_dir):
    """Every backup in the folder, newest first: dicts with path, name, reason, created (datetime) and size."""
    found = []
    folder = Path(backup_dir)
    if not folder.is_dir():
        return found
    for path in folder.iterdir():
        match = NAME.match(path.name)
        if not match:
            continue
        found.append({
            'path': path,
            'name': path.name,
            'reason': match.group(2),
            'created': datetime.strptime(match.group(1), '%Y%m%d-%H%M%S'),
            'size': path.stat().st_size,
            'has_config': _config_copy(path).exists(),
        })
    found.sort(key=lambda item: (item['created'], item['path'].stat().st_mtime_ns), reverse=True)
    return found


def kind_of(reason):
    if reason.startswith('manual'):
        return 'manual'
    return 'periodic' if reason.startswith('periodic') else 'automatic'


def prune(backup_dir, keep_periodic=48, keep_automatic=100):
    """Delete the oldest periodic and automatic copies beyond the limits. Manual copies are never touched."""
    removed = []
    seen = {'periodic': 0, 'automatic': 0}
    limits = {'periodic': keep_periodic, 'automatic': keep_automatic}
    for item in list_backups(backup_dir):  # newest first
        kind = kind_of(item['reason'])
        if kind == 'manual':
            continue
        seen[kind] += 1
        if seen[kind] > limits[kind]:
            item['path'].unlink(missing_ok=True)
            _config_copy(item['path']).unlink(missing_ok=True)
            removed.append(item['name'])
    return removed


def changed_since_last_backup(db_path, backup_dir):
    """True when the database was written after the newest backup was made (or there is no backup yet)."""
    backups = list_backups(backup_dir)
    if not backups:
        return True
    newest = max(item['path'].stat().st_mtime for item in backups)
    touched = max((os.path.getmtime(p) for p in (db_path, db_path + '-wal') if os.path.exists(p)), default=0)
    return touched > newest


def restore_backup(backup_path, db_path, config_path=None):
    """Replace the database with a backup, using SQLite's backup API so it is safe even if the server is running."""
    backup_path = Path(backup_path)
    if not backup_path.is_file():
        raise BackupError(f'{backup_path} does not exist')
    try:
        source = _connect_readonly(backup_path)
        try:
            if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise BackupError('That backup is damaged (it failed the integrity check)')
            if not source.execute("SELECT 1 FROM sqlite_master WHERE name = 'user'").fetchone():
                raise BackupError('That file is not a contest database (it has no user table)')
            destination = sqlite3.connect(db_path, timeout=30)
            try:
                source.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()
    except sqlite3.Error as error:
        raise BackupError(f'Could not restore {backup_path}: {error}') from error

    config_restored = False
    companion = _config_copy(backup_path)
    if config_path and companion.exists():
        os.makedirs(os.path.dirname(os.path.abspath(config_path)), exist_ok=True)
        shutil.copyfile(companion, config_path)
        config_restored = True
    return config_restored


class PeriodicBackups(threading.Thread):
    """Background timer that copies the database every `interval` seconds, but only if something changed."""

    def __init__(self, interval, make_backup, log=print):
        super().__init__(daemon=True, name='periodic-backups')
        self.interval = interval
        self.make_backup = make_backup
        self.log = log
        self._stop_event = threading.Event()

    def run(self):
        while not self._stop_event.wait(self.interval):
            try:
                self.make_backup()
            except Exception as error:  # a failed backup must never take the server down
                self.log(f'Periodic backup failed: {error}')

    def stop(self):
        self._stop_event.set()
