"""Maintenance commands for the contest server.

    python manage.py reset-admin-password

Run it from the project folder with the virtualenv active (venv/bin/python manage.py ...). The server doesn't
need to be stopped. ADMIN_PASSWORD in .env is only used the first time the admin account is created, so use this
command to change the password afterwards without wiping any contest data.
"""
import getpass
import os
import sys

from app import app, db, User, MIN_PASSWORD_LENGTH


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


COMMANDS = {'reset-admin-password': reset_admin_password}

if __name__ == '__main__':
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]]()
