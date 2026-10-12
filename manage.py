"""Maintenance commands for the contest server.

    python manage.py reset-admin-password
    python manage.py backup [--note NAME]
    python manage.py list-backups
    python manage.py restore FILE [--yes]

Run it from the project folder with the virtualenv active (venv/bin/python manage.py ...). Everything except
`restore` is safe to run while the server is running. ADMIN_PASSWORD in .env is only used the first time the admin
account is created, so use reset-admin-password to change the password afterwards without wiping any contest data.

backup writes a copy of the database and the contest settings to backups/ (or BACKUP_DIR). The server also does this
by itself: at startup, every BACKUP_INTERVAL_MINUTES (default 10) when something changed, and just before it deletes
or rewrites data. Copies made with this command are never deleted automatically.
"""
import argparse
import getpass
import os
import sys

from app import app, db, User, MIN_PASSWORD_LENGTH, backup, backup_dir, database_file, make_backup


def reset_admin_password():
    password = os.environ.get('NEW_ADMIN_PASSWORD')  # non-interactive use, e.g. scripts
    if not password:
        password = getpass.getpass('New admin password: ')
        if getpass.getpass('Repeat password: ') != password:
            sys.exit('The passwords do not match.')
    if len(password) < MIN_PASSWORD_LENGTH:
        sys.exit(f'The password must be at least {MIN_PASSWORD_LENGTH} characters.')

    with app.app_context():
        db.create_all()
        admin = User.query.filter_by(username='admin').first()
        if admin is None:
            admin = User(username='admin', email='admin@example.com', is_admin=True)
            db.session.add(admin)
            print("No 'admin' account existed, creating it.")
        admin.is_admin = True
        admin.set_password(password)
        db.session.commit()
    print("Admin password updated. Existing sessions stay signed in until they log out or expire.")


def _size(num_bytes):
    return f'{num_bytes / 1024:.0f} KB' if num_bytes < 1024 * 1024 else f'{num_bytes / 1024 / 1024:.1f} MB'


def backup_now(note):
    reason = f'manual-{note}' if note else 'manual'
    try:
        with app.app_context():
            path = make_backup(reason)
    except backup.BackupError as error:
        sys.exit(str(error))
    print(f'Backed up to {path} ({_size(path.stat().st_size)})')


def list_backups():
    items = backup.list_backups(backup_dir())
    if not items:
        sys.exit(f'No backups in {os.path.abspath(backup_dir())}/ yet.')
    print(f'Backups in {os.path.abspath(backup_dir())}/ (newest first):')
    for item in items:
        print(f"  {item['name']:<52} {item['created']:%Y-%m-%d %H:%M:%S}  {_size(item['size']):>8}")


def restore(file, assume_yes):
    path = file if os.path.exists(file) else os.path.join(backup_dir(), file)  # a bare name is looked up in backups/
    with app.app_context():
        target = database_file()
    if target is None:
        sys.exit('Restore only works with a file-based SQLite database.')
    print(f'This replaces the current database ({target}) with {path}.')
    print('Stop the server first if you can, and start it again afterwards so it reloads the contest settings.')
    print('A copy of the current database is saved first, so this can be undone.')
    if not assume_yes and input("Type 'restore' to continue: ").strip() != 'restore':
        sys.exit('Cancelled, nothing changed.')
    try:
        with app.app_context():
            saved = make_backup('manual-before-restore')
        print(f'Current database saved to {saved}')
        config_restored = backup.restore_backup(path, target, config_path='config/contest_config.json')
    except backup.BackupError as error:
        sys.exit(str(error))
    print('Restored the database' + (' and the contest settings.' if config_restored else '.') + ' Restart the server now.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Maintenance commands for the contest server.', epilog=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('reset-admin-password', help='set a new admin password without losing data')
    backup_parser = commands.add_parser('backup', help='copy the database and settings to backups/')
    backup_parser.add_argument('--note', help='short label added to the file name, e.g. --note before-round-2')
    commands.add_parser('list-backups', help='show the copies in backups/')
    restore_parser = commands.add_parser('restore', help='replace the database with a backup')
    restore_parser.add_argument('file', help='path to a backup file, or just its name from backups/')
    restore_parser.add_argument('--yes', action='store_true', help='do not ask for confirmation')
    args = parser.parse_args()

    if args.command == 'reset-admin-password':
        reset_admin_password()
    elif args.command == 'backup':
        backup_now(args.note)
    elif args.command == 'list-backups':
        list_backups()
    else:
        restore(args.file, args.yes)
