# CMS (Contest Management System)

A self-hosted platform for running in-person coding contests on a local network. Contestants open the site in a
browser, read problems, write code in the browser and get judged immediately; organisers manage everything from an
admin area.

## Features

**For contestants**
- Problems with Markdown statements, difficulty, time and memory limits, and partial scoring in batches
- In-browser editor with syntax highlighting for Python, C++ and Java; code is saved as you type
- Run your code against your own input before submitting
- Detailed results per batch and test, with a read-only view of every past submission
- Live leaderboard (can be frozen by the organiser)
- Light and dark themes, usable on phones

**For organisers**
- Create, edit and delete problems; upload test cases as a zip (`input001.txt` / `output001.txt`, ...)
- Create contestant accounts one by one, or in bulk with a printable A4 PDF of logins
- Reset passwords, disable or delete accounts
- See every submission and its code, rejudge one submission or a whole problem after fixing a test
- Freeze the leaderboard, stop submissions, export final standings and all submissions as CSV
- A banner warns you if the judge (Docker) stops working

**Judging**
- Every compile and run happens in a throwaway Docker container: no network, read-only filesystem, non-root user,
  memory / process / CPU limits
- Output is compared ignoring trailing whitespace and blank lines at the ends

## Quick start

You need Python 3.11+ and Docker.

```bash
./setup.sh --start
```

This creates a virtualenv, installs the dependencies, writes `.env` with generated secrets, builds the judge image,
asks for the contest name and time zone, and starts the server. Contestants open `http://<this-machine's-ip>:5000`.
The admin login is the user `admin` with the password stored as `ADMIN_PASSWORD` in `.env`.

See [SETUP.md](SETUP.md) for the manual steps, running on a contest network, and the admin guide.

## Configuration

Set these in `.env` or the environment:

| Variable | Purpose | Default |
|---|---|---|
| `SECRET_KEY` | Signs session cookies (required) | none |
| `ADMIN_PASSWORD` | Password for the `admin` account, used the first time it is created (required) | none |
| `HOST` / `PORT` | Where the server listens | `0.0.0.0` / `5000` |
| `DATABASE_URL` | SQLAlchemy database URL | `sqlite:///coding_contest.db` |
| `JUDGE_IMAGE` | Docker image used for judging | `cms-judge` |
| `JUDGE_SANDBOX` | `docker`, or `local` for development only (**not sandboxed**) | `docker` |
| `FLASK_DEBUG` | `1` enables the debugger and reloader (development only) | off |
| `RESET_DB` | `1` wipes the database on start | off |

## Maintenance

```bash
venv/bin/python manage.py reset-admin-password   # set a new admin password without losing data
```

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest        # no Docker needed: tests use the judge's local mode and a temporary database
```

Project layout:

```
app.py               Flask app: routes, models, admin API
credentials_pdf.py   printable login slips
manage.py            maintenance commands
judge/               judge, in-container runner and the sandbox Dockerfile
templates/ static/   pages, stylesheet, front-end code and vendored libraries
tests/               pytest suite
```

## Security notes

- Contestant code never runs on the host; if Docker or the judge image is unavailable, submissions fail rather than
  running unsandboxed.
- There is no public registration. Accounts are created by an admin, passwords are stored hashed, and passwords
  generated for printing are shown only once.
- The site is served over plain HTTP on the local network, so tell contestants not to reuse real passwords.
- Session cookies are `HttpOnly` and `SameSite=Lax`, state-changing requests must come from the site itself, and
  login attempts and submissions are rate limited.

## Contributing

Issues and pull requests are welcome. Please use the issue and pull request templates.
