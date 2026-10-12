from flask import Flask, request, jsonify, render_template, redirect, url_for, Response, send_from_directory
from functools import wraps
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_socketio import SocketIO
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone
import pytz
from urllib.parse import urlparse
from judge.judge import judge_submission, check_judge, JudgeError
from credentials_pdf import build_credentials_pdf
from sqlalchemy import select, func, inspect as sa_inspect, text
from sqlalchemy.exc import IntegrityError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

load_dotenv()

USERNAME_PATTERN = re.compile(r'^[A-Za-z0-9_]{3,32}$')
BULK_PREFIX_PATTERN = re.compile(r'^[A-Za-z0-9_]{1,24}$')
BULK_MAX_ACCOUNTS = 200
# Generated passwords are printed, so leave out look-alikes (0/O, 1/l/I) that are easy to misread
PASSWORD_ALPHABET = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
GENERATED_PASSWORD_LENGTH = 10

app = Flask(__name__)

# The secret key signs session cookies, so it must never be a shared/default value
secret_key = os.environ.get('SECRET_KEY')
if not secret_key:
    raise RuntimeError(
        "SECRET_KEY is not set. Copy .env.example to .env and set a random value, e.g. "
        "python -c \"import secrets; print(secrets.token_hex(32))\""
    )
app.config['SECRET_KEY'] = secret_key
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///coding_contest.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # reject request bodies over 5 MB
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# SESSION_COOKIE_SECURE is intentionally left off: the contest is served over plain HTTP on a LAN IP

MAX_CODE_LENGTH = 100_000  # characters per submission
MAX_RUN_TEST_CASES = 5
RUN_CODE_TIME_LIMIT = 1000  # ms; /run_code never takes limits from the client
RUN_CODE_MEMORY_LIMIT = 256  # MB


def rate_limit_key():
    # Per user once logged in, per client IP otherwise
    if current_user.is_authenticated:
        return f'user:{current_user.get_id()}'
    return get_remote_address()

limiter = Limiter(rate_limit_key, app=app, storage_uri='memory://')

@app.errorhandler(429)
def rate_limited(e):
    return jsonify({'error': 'Too many requests, slow down and try again shortly'}), 429

@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'Request too large'}), 413

@app.before_request
def reject_oversized_body():
    # Checked up front so the 413 isn't swallowed by the catch-all handlers in the views
    if request.content_length and request.content_length > app.config['MAX_CONTENT_LENGTH']:
        return too_large(None)

# Initialize SocketIO (default CORS policy only allows same-origin connections)
socketio = SocketIO(app)

@socketio.on('connect')
def handle_connect():
    # Only logged-in users may receive live updates
    if not current_user.is_authenticated:
        return False

# Load contest configuration
def load_contest_config():
    try:
        with open('config/contest_config.json', 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {'contest_name': 'Coding Contest'}  # Default name

contest_config = load_contest_config()
contest_timezone = pytz.timezone(contest_config.get('time_zone', 'UTC'))


# ---- Contest schedule: optional start and end instants, stored in the config as UTC ISO-8601 strings
def parse_instant(value):
    """An ISO-8601 string with a time zone (or Z) as an aware UTC datetime, or None if it is not one."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        return moment.astimezone(timezone.utc) if moment.tzinfo else None
    except (ValueError, OverflowError):
        return None


def format_instant(moment):
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if moment else None


def contest_phase(now=None):
    """'upcoming' before the start, 'ended' from the end on, otherwise 'running' (also when nothing is scheduled)."""
    now = now or datetime.now(timezone.utc)
    start = parse_instant(contest_config.get('start_time'))
    end = parse_instant(contest_config.get('end_time'))
    if start and now < start:
        return 'upcoming'
    if end and now >= end:
        return 'ended'
    return 'running'


def schedule_block_message(user):
    """Why `user` may not work on problems right now because of the schedule, or None. Organisers are never blocked."""
    if user.is_admin:
        return None
    phase = contest_phase()
    if phase == 'upcoming':
        return 'The contest has not started yet'
    if phase == 'ended':
        return 'The contest has ended'
    return None

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    is_admin = db.Column(db.Boolean, default=False)
    disabled = db.Column(db.Boolean, nullable=False, default=False)
    submissions = db.relationship('Submission', backref='user', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def get_id(self):
        # The session id includes a fingerprint of the password hash, so resetting a password signs out
        # every session that was opened with the old one
        fingerprint = hashlib.sha256((self.password_hash or '').encode()).hexdigest()[:16]
        return f'{self.id}.{fingerprint}'

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Problem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    shortname = db.Column(db.String(10), nullable=False)  # A-Z for problem shortnames
    description = db.Column(db.Text, nullable=False)
    difficulty = db.Column(db.String(20), nullable=False)
    time_limit = db.Column(db.Integer, nullable=False)  # in milliseconds
    memory_limit = db.Column(db.Integer, nullable=False)  # in MB
    batches = db.Column(db.JSON, nullable=False)  # List of batches, each containing test cases and points
    submissions = db.relationship('Submission', backref='problem', lazy=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

class Submission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    problem_id = db.Column(db.Integer, db.ForeignKey('problem.id'), nullable=False)
    code = db.Column(db.Text, nullable=False)
    language = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), nullable=False)
    execution_time = db.Column(db.Float)  # in milliseconds
    memory_used = db.Column(db.Float)  # in KB
    points_earned = db.Column(db.Integer, default=0)  # Points earned for this submission
    submitted_at = db.Column(db.DateTime, default=lambda: datetime.now(contest_timezone))
    batch_results = db.Column(db.JSON) # List of batches, containing result of each test case
    submitted_while_frozen = db.Column(db.Boolean, nullable=False, default=False)

@login_manager.user_loader
def load_user(session_id):
    try:
        user_id, fingerprint = session_id.split('.', 1)
        user = db.session.get(User, int(user_id))
    except (ValueError, AttributeError):
        return None  # a cookie from before sessions carried a fingerprint, or a malformed one
    if user is None or user.disabled or not hmac.compare_digest(fingerprint, user.get_id().split('.', 1)[1]):
        return None
    return user

def ensure_schema():
    """Add columns introduced after a database was first created (create_all() never alters existing tables)."""
    with db.engine.connect() as connection:  # a fresh inspector, so it never reports stale column lists
        columns = {column['name'] for column in sa_inspect(connection).get_columns('user')}
    if 'disabled' not in columns:
        db.session.execute(text('ALTER TABLE "user" ADD COLUMN disabled BOOLEAN NOT NULL DEFAULT 0'))
        db.session.commit()

def generate_password():
    return ''.join(secrets.choice(PASSWORD_ALPHABET) for _ in range(GENERATED_PASSWORD_LENGTH))

MIN_PASSWORD_LENGTH = 8

def init_admin():
    """Create the admin user if it doesn't exist.

    The password comes from ADMIN_PASSWORD.
    """
    with app.app_context():
        if not User.query.filter_by(username='admin').first():
            password = os.environ.get('ADMIN_PASSWORD')
            if not password:
                raise RuntimeError('ADMIN_PASSWORD is not set. Copy .env.example to .env and set ADMIN_PASSWORD.')
            if len(password) < MIN_PASSWORD_LENGTH:
                raise RuntimeError(f'ADMIN_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters')

            admin = User(username='admin', email='admin@example.com', is_admin=True)
            admin.set_password(password)
            db.session.add(admin)
            db.session.commit()

@app.before_request
def reject_cross_origin_posts():
    # Browsers always send Origin on cross-site POSTs; refuse any that don't come from this host
    origin = request.headers.get('Origin')
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and origin and urlparse(origin).netloc != request.host:
        return jsonify({'error': 'Cross-origin request blocked'}), 403

def require_json_fields(*fields):
    """Reject requests whose JSON body is missing any of `fields` or has non-string/empty values."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return jsonify({'error': 'Request body must be a JSON object'}), 400
            for field in fields:
                value = data.get(field)
                if not isinstance(value, str) or not value.strip():
                    return jsonify({'error': f'Missing or invalid field: {field}'}), 400
            return view(*args, **kwargs)
        return wrapper
    return decorator

def next_shortname():
    """The first free label (A, B, ... Z, AA, ...). Labels of existing problems never change, so deleting B
    leaves a gap that the next new problem fills rather than colliding with C."""
    used = {name for (name,) in db.session.query(Problem.shortname).all()}
    index = 0
    while shortname_for(index) in used:
        index += 1
    return shortname_for(index)

def shortname_for(index):
    """0 -> A, 25 -> Z, 26 -> AA, ..."""
    name = ''
    index += 1
    while index > 0:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name

def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)

def validate_problem(data):
    """Return an error message if `data` isn't a well-formed problem, else None.

    Stored values are later summed, compared and fed to the judge, so wrong types here would break
    every contestant's problem list rather than just this problem.
    """
    title = data.get('title')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
        return 'Title must be 1-200 characters'
    description = data.get('description')
    if not isinstance(description, str) or not description.strip() or len(description) > 100_000:
        return 'Description is required (max 100000 characters)'
    if data.get('difficulty') not in ('Easy', 'Medium', 'Hard'):
        return 'Difficulty must be Easy, Medium or Hard'
    time_limit, memory_limit = data.get('time_limit'), data.get('memory_limit')
    if not _is_int(time_limit) or not 50 <= time_limit <= 60_000:
        return 'Time limit must be a whole number of milliseconds between 50 and 60000'
    if not _is_int(memory_limit) or not 16 <= memory_limit <= 2048:
        return 'Memory limit must be a whole number of MB between 16 and 2048'

    batches = data.get('batches')
    if not isinstance(batches, list) or not 1 <= len(batches) <= 100:
        return 'Provide between 1 and 100 batches'
    for number, batch in enumerate(batches, start=1):
        if not isinstance(batch, dict):
            return f'Batch {number} is malformed'
        if not _is_int(batch.get('points')) or not 0 <= batch['points'] <= 100_000:
            return f'Batch {number}: points must be a whole number between 0 and 100000'
        cases = batch.get('test_cases')
        if not isinstance(cases, list) or not 1 <= len(cases) <= 1000:
            return f'Batch {number}: provide between 1 and 1000 test cases'
        for case in cases:
            if (not isinstance(case, dict) or not isinstance(case.get('input'), str)
                    or not isinstance(case.get('output'), str)):
                return f'Batch {number}: every test case needs text input and output'
            if len(case['input']) > 1_000_000 or len(case['output']) > 1_000_000:
                return f'Batch {number}: a test case is too large'
    return None

# Routes
@app.route('/favicon.ico')
def favicon():
    # Browsers ask for this path by default, whatever the page's <link rel="icon"> says
    return send_from_directory(app.static_folder, 'favicon.svg', mimetype='image/svg+xml', max_age=86400)

@app.route('/')
def index():
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    return render_template('index.html', contest_name=contest_config.get('contest_name', 'Coding Contest'))

@app.route('/login', methods=['GET', 'POST'])
# Only failed attempts count, so a classroom of students logging in at once (possibly behind one shared IP)
# isn't locked out, while password guessing is still throttled
@limiter.limit('20 per minute', methods=['POST'], key_func=get_remote_address,
               deduct_when=lambda response: response.status_code >= 400)
def login():
    if request.method == 'POST':
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get('username'), str) or not isinstance(data.get('password'), str):
            return jsonify({'error': 'Username and password are required'}), 400
        user = User.query.filter_by(username=data['username']).first()
        
        if user and user.check_password(data['password']):
            if user.disabled:
                return jsonify({'error': 'This account has been disabled. Please see an organiser.'}), 403
            login_user(user)
            return jsonify({'message': 'Login successful'}), 200
        
        return jsonify({'error': 'Invalid username or password'}), 401
    
    return render_template('login.html', contest_name=contest_config.get('contest_name', 'Coding Contest'))

@app.route('/username')
@login_required
def get_username():
    return current_user.username

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'), code=303)

@app.route('/create_user', methods=['POST'])
@login_required
@require_json_fields('username', 'email', 'password')
def create_user():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
    if not USERNAME_PATTERN.match(data['username']):
        return jsonify({'error': 'Username must be 3-32 characters: letters, digits or underscore'}), 400
    if len(data['password']) < MIN_PASSWORD_LENGTH:
        return jsonify({'error': f'Password must be at least {MIN_PASSWORD_LENGTH} characters'}), 400

    if User.query.filter_by(username=data['username']).first():
        return jsonify({'error': 'Username already exists'}), 400
    
    if User.query.filter_by(email=data['email']).first():
        return jsonify({'error': 'Email already exists'}), 400
    
    user = User(username=data['username'], email=data['email'])
    user.set_password(data['password'])
    
    db.session.add(user)
    db.session.commit()
    
    return jsonify({'message': 'User created successfully'}), 201

def next_username_number(prefix):
    """One more than the highest number already used with this prefix (User1, User2, ... -> 3)."""
    pattern = re.compile(rf'^{re.escape(prefix)}(\d+)$', re.IGNORECASE)
    like = prefix.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    rows = db.session.query(User.username).filter(User.username.ilike(like, escape='\\')).all()
    numbers = [int(m.group(1)) for (name,) in rows if (m := pattern.match(name))]
    return max(numbers, default=0) + 1

@app.route('/create_users_batch', methods=['POST'])
@login_required
# Hashing hundreds of passwords is the expensive part, so only successful batches count toward the limit
@limiter.limit('5 per minute', deduct_when=lambda response: response.status_code == 200)
def create_users_batch():
    """Create `count` accounts with random passwords and return a printable PDF of the credentials.

    Passwords are only ever stored hashed, so this PDF is the single place they can be read.
    """
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Request body must be a JSON object'}), 400
    count = data.get('count')
    prefix = data.get('prefix', 'User')
    if not _is_int(count) or not 1 <= count <= BULK_MAX_ACCOUNTS:
        return jsonify({'error': f'Number of accounts must be between 1 and {BULK_MAX_ACCOUNTS}'}), 400
    if not isinstance(prefix, str) or not BULK_PREFIX_PATTERN.match(prefix):
        return jsonify({'error': 'Prefix must be 1-24 characters: letters, digits or underscore'}), 400

    start = next_username_number(prefix)
    usernames = [f'{prefix}{start + offset}' for offset in range(count)]
    if len(usernames[-1]) > 32:
        return jsonify({'error': 'Those usernames would be longer than 32 characters; use a shorter prefix'}), 400
    taken = db.session.query(User.username).filter(func.lower(User.username).in_([u.lower() for u in usernames])).first()
    if taken:
        return jsonify({'error': f'The username {taken[0]} already exists'}), 409

    try:
        accounts = []
        for username in usernames:
            password = generate_password()
            user = User(username=username, email=f'{username.lower()}@contest.invalid')
            user.set_password(password)
            db.session.add(user)
            accounts.append((username, password))

        # Build the PDF before committing so a failure can't leave accounts whose passwords nobody has
        pdf = build_credentials_pdf(accounts, contest_config.get('contest_name', 'Coding Contest'),
                                    request.host_url.rstrip('/'))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'Some of those usernames or emails already exist. Please try again.'}), 409
    except Exception:
        app.logger.exception('Bulk account creation failed')  # never log the passwords
        db.session.rollback()
        return jsonify({'error': 'Could not create the accounts'}), 500

    socketio.emit('update_leaderboard')
    return Response(pdf, mimetype='application/pdf', headers={
        'Content-Disposition': f'attachment; filename="accounts-{usernames[0]}-to-{usernames[-1]}.pdf"',
        'Cache-Control': 'no-store',
        'X-Accounts-Created': str(count),
        'X-First-Username': usernames[0],
        'X-Last-Username': usernames[-1],
    })

@app.route('/create_problem', methods=['POST'])
@login_required
def create_problem():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'error': 'Request body must be a JSON object'}), 400
        
        error = validate_problem(data)
        if error:
            return jsonify({'error': error}), 400

        problem = Problem(
            title=data['title'].strip(),
            shortname=next_shortname(),
            description=data['description'],
            difficulty=data['difficulty'],
            time_limit=data['time_limit'],
            memory_limit=data['memory_limit'],
            batches=data['batches']
        )
        
        db.session.add(problem)
        db.session.commit()
        
        # Emit WebSocket event for new problem (nothing about it may leak before the contest starts)
        if contest_phase() == 'upcoming':
            socketio.emit('problems_changed')
        else:
            socketio.emit('new_problem', {
                'id': problem.id,
                'title': problem.title,
                'shortname': problem.shortname,
                'difficulty': problem.difficulty,
                'time_limit': problem.time_limit,
                'memory_limit': problem.memory_limit
            })
        
        return jsonify({'message': 'Problem created successfully', 'id': problem.id}), 201
    except Exception as e:
        app.logger.exception('Error creating problem')
        db.session.rollback()
        return jsonify({'error': 'Could not create problem'}), 500

@app.route('/problems')
@login_required
def get_problems():
    if not current_user.is_admin and contest_phase() == 'upcoming':
        return jsonify([])  # problems stay hidden until the contest starts
    problems = Problem.query.order_by(Problem.created_at.desc()).all()
    counts = {}
    if current_user.is_admin:
        counts = dict(db.session.query(Submission.problem_id, func.count(Submission.id)).group_by(Submission.problem_id).all())
    items = []
    for p in problems:
        item = {
            'id': p.id,
            'title': p.title,
            'shortname': p.shortname,
            'difficulty': p.difficulty,
            'time_limit': p.time_limit,
            'memory_limit': p.memory_limit,
            'total_points': sum(batch['points'] for batch in p.batches)
        }
        if current_user.is_admin:
            item['submission_count'] = counts.get(p.id, 0)
        items.append(item)
    return jsonify(items)

@app.route('/problem/<int:problem_id>')
@login_required
def get_problem(problem_id):
    if not current_user.is_admin and contest_phase() == 'upcoming':
        return jsonify({'error': schedule_block_message(current_user)}), 403
    problem = db.get_or_404(Problem, problem_id)
    data = {
        'id': problem.id,
        'title': problem.title,
        'shortname': problem.shortname,
        'description': problem.description,
        'difficulty': problem.difficulty,
        'time_limit': problem.time_limit,
        'memory_limit': problem.memory_limit,
        'total_points': sum(batch['points'] for batch in problem.batches)
    }
    # Test cases are hidden from contestants
    if current_user.is_admin:
        data['batches'] = problem.batches
    return jsonify(data)

@app.route('/problem/<int:problem_id>', methods=['PUT'])
@login_required
def update_problem(problem_id):
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Request body must be a JSON object'}), 400
    error = validate_problem(data)
    if error:
        return jsonify({'error': error}), 400

    problem = db.get_or_404(Problem, problem_id)
    problem.title = data['title'].strip()
    problem.description = data['description']
    problem.difficulty = data['difficulty']
    problem.time_limit = data['time_limit']
    problem.memory_limit = data['memory_limit']
    problem.batches = data['batches']  # the label (A, B, ...) is kept
    db.session.commit()
    socketio.emit('problems_changed')
    return jsonify({'message': 'Problem updated', 'id': problem.id})

@app.route('/problem/<int:problem_id>', methods=['DELETE'])
@login_required
def delete_problem(problem_id):
    """Delete a problem together with every submission made to it."""
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    problem = db.get_or_404(Problem, problem_id)
    deleted = Submission.query.filter_by(problem_id=problem.id).delete()
    db.session.delete(problem)
    db.session.commit()
    socketio.emit('problems_changed')
    socketio.emit('update_leaderboard')
    return jsonify({'message': 'Problem deleted', 'deleted_submissions': deleted})

@app.route('/judge_status')
@login_required
def judge_status():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    return jsonify(check_judge(force=request.args.get('refresh') == '1'))

def apply_judge_result(submission, result):
    """Store a judging result on the submission, never keeping the hidden tests' expected/actual output."""
    for batch_result in result['batch_results']:
        for test_case_result in batch_result['test_case_results']:
            test_case_result.pop('expected', None)
            test_case_result.pop('got', None)
    submission.status = result['status']
    submission.execution_time = result.get('execution_time')
    submission.memory_used = result.get('memory_used')
    submission.points_earned = result.get('points_earned', 0)
    submission.batch_results = result['batch_results']

def rejudge_submission(submission, problem):
    """Judge the stored code again against the problem's current tests."""
    result = judge_submission(code=submission.code, language=submission.language, batches=problem.batches,
                              time_limit=problem.time_limit, memory_limit=problem.memory_limit)
    apply_judge_result(submission, result)
    db.session.commit()

JUDGE_UNAVAILABLE_MESSAGE = 'The judge is temporarily unavailable. Please tell an organiser.'

@app.route('/submit', methods=['POST'])
@login_required
@limiter.limit('6 per minute')
def submit():
    if contest_config.get('submissions_stopped', False):
        return jsonify({'error': 'Submissions have been stopped'}), 400
    blocked = schedule_block_message(current_user)
    if blocked:
        return jsonify({'error': blocked}), 403

    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
            
        if 'problem_id' not in data:
            return jsonify({'error': 'Problem ID is required'}), 400
            
        if not isinstance(data.get('code'), str) or len(data['code']) == 0:
            return jsonify({'error': 'Code is required'}), 400
            
        if len(data['code']) > MAX_CODE_LENGTH:
            return jsonify({'error': f'Code is too long (max {MAX_CODE_LENGTH} characters)'}), 413

        if 'language' not in data:
            return jsonify({'error': 'Language is required'}), 400

        if not _is_int(data['problem_id']):
            return jsonify({'error': 'Problem ID must be a number'}), 400
        problem = db.session.get(Problem, data['problem_id'])
        if problem is None:
            return jsonify({'error': 'That problem no longer exists'}), 404
        
        # Create submission record
        submission = Submission(
            user_id=current_user.id,
            problem_id=problem.id,
            code=data['code'],
            language=data['language'],
            status='PENDING',
            submitted_while_frozen=contest_config.get('leaderboard_frozen', False)
        )
        db.session.add(submission)
        db.session.commit()
        
        # Judge the submission
        try:
            result = judge_submission(
                code=data['code'],
                language=data['language'],
                batches=problem.batches,
                time_limit=problem.time_limit,
                memory_limit=problem.memory_limit
            )
            
            apply_judge_result(submission, result)
            result['id'] = submission.id
            
            # Emit WebSocket event for new submission - update leaderboard
            socketio.emit('update_leaderboard')
            
            db.session.commit()
            return jsonify(result)
            
        except JudgeError as e:
            app.logger.error('Judge unavailable: %s', e)
            submission.status = 'ERROR'
            db.session.commit()
            return jsonify({'error': JUDGE_UNAVAILABLE_MESSAGE}), 503
        except Exception as e:
            app.logger.exception('Judge error')
            submission.status = 'ERROR'
            db.session.commit()
            return jsonify({'error': 'The judge failed to process this submission'}), 500
            
    except Exception as e:
        app.logger.exception('Submission error')
        return jsonify({'error': 'Could not process submission'}), 500

@app.route('/run_code', methods=['POST'])
@login_required
@limiter.limit('20 per minute')
def run_code():
    if contest_config.get('submissions_stopped', False) and not current_user.is_admin:
        return jsonify({'error': 'Submissions have been stopped'}), 400
    blocked = schedule_block_message(current_user)
    if blocked:
        return jsonify({'error': blocked}), 403

    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
            
        if 'code' not in data:
            return jsonify({'error': 'Code is required'}), 400
            
        if 'language' not in data:
            return jsonify({'error': 'Language is required'}), 400
            
        if not isinstance(data['code'], str) or len(data['code']) > MAX_CODE_LENGTH:
            return jsonify({'error': f'Code must be a string of at most {MAX_CODE_LENGTH} characters'}), 400

        if 'test_cases' not in data or not isinstance(data['test_cases'], list):
            return jsonify({'error': 'Test cases are required'}), 400

        if not 0 < len(data['test_cases']) <= MAX_RUN_TEST_CASES:
            return jsonify({'error': f'Provide between 1 and {MAX_RUN_TEST_CASES} test cases'}), 400

        # Create a single batch with the test cases
        batch = {
            'points': 0,  # Points don't matter for running code
            'test_cases': data['test_cases']
        }
        
        # Limits are fixed server-side so a client can't request an unbounded run
        time_limit = RUN_CODE_TIME_LIMIT
        memory_limit = RUN_CODE_MEMORY_LIMIT
        
        # Run the code
        try:
            result = judge_submission(
                code=data['code'],
                language=data['language'],
                batches=[batch],
                time_limit=time_limit,
                memory_limit=memory_limit,
                is_run_code=True  # Set this to True for run code submissions
            )
            
            # Remove submission-specific fields
            result.pop('points_earned', None)
            result.pop('id', None)
            
            return jsonify(result)
            
        except JudgeError as e:
            app.logger.error('Judge unavailable: %s', e)
            return jsonify({'error': JUDGE_UNAVAILABLE_MESSAGE}), 503
        except Exception as e:
            app.logger.exception('Run error')
            return jsonify({'error': 'The judge failed to run this code'}), 500
            
    except Exception as e:
        app.logger.exception('Run error')
        return jsonify({'error': 'Could not run code'}), 500

def build_leaderboard(frozen):
    """Standings for every active contestant: (problems, rows sorted by total points, highest first).

    Each problem counts the contestant's best accepted submission (most points, then earliest). When `frozen`
    is true, submissions made while the board was frozen are left out.
    """
    users = User.query.filter_by(is_admin=False, disabled=False).order_by(User.id).all()
    problems = Problem.query.order_by(Problem.id).all()

    accepted = Submission.query.with_entities(
        Submission.user_id, Submission.problem_id, Submission.points_earned, Submission.submitted_at
    ).filter(Submission.status == 'AC')
    if frozen:
        accepted = accepted.filter(Submission.submitted_while_frozen.is_(False))
    best = {}
    for user_id, problem_id, points, submitted_at in accepted.order_by(
            Submission.points_earned.desc(), Submission.submitted_at, Submission.id):
        best.setdefault((user_id, problem_id), (points, submitted_at))  # first seen is the best

    rows = []
    for user in users:
        row = {'username': user.username, 'total_points': 0, 'problem_points': []}
        for problem in problems:
            points, submitted_at = best.get((user.id, problem.id), (0, None))
            row['problem_points'].append({
                'points': points,
                'submission_time': submitted_at.strftime('%H:%M:%S') if submitted_at else None
            })
            row['total_points'] += points
        rows.append(row)  # contestants without submissions are listed too
    rows.sort(key=lambda row: row['total_points'], reverse=True)
    return problems, rows

@app.route('/leaderboard')
@login_required
def get_leaderboard():
    is_frozen = contest_config.get('leaderboard_frozen', False)
    problems, rows = build_leaderboard(is_frozen)
    if not current_user.is_admin and contest_phase() == 'upcoming':
        problems = []  # problem names stay hidden until the contest starts
    return jsonify({
        'problems': [{'id': p.id, 'title': p.title, 'shortname': p.shortname} for p in problems],
        'users': rows,
        'is_frozen': is_frozen
    })

@app.route('/submission/<int:submission_id>')
@login_required
def get_submission(submission_id):
    submission = Submission.query.filter_by(user_id=current_user.id, id=submission_id).first_or_404()
    return jsonify({
        'id': submission.id,
        'user_id': submission.user_id,
        'problem_id': submission.problem_id,
        'language': submission.language,
        'status': submission.status,
        'batch_results': submission.batch_results,
        'submitted_at': submission.submitted_at.isoformat(),
        'points_earned': submission.points_earned,
        'problem': {
            'title': submission.problem.title,
            'total_points': sum(batch['points'] for batch in submission.problem.batches)
        },
        'code': submission.code
    })

@app.route('/submissions')
@login_required
def get_submissions():
    submissions = Submission.query.filter_by(user_id=current_user.id).order_by(Submission.id).all()
    return jsonify([{
        'id': s.id,
        'problem_id': s.problem_id,
        'problem': {
            'title': s.problem.title,
            'total_points': sum(batch['points'] for batch in s.problem.batches)
        },
        'language': s.language,
        'status': s.status,
        'execution_time': s.execution_time,
        'memory_used': s.memory_used,
        'points_earned': s.points_earned,
        'submitted_at': s.submitted_at.isoformat()
    } for s in submissions])

@app.route('/check_admin')
@login_required
def check_admin():
    return jsonify({'is_admin': current_user.is_admin})

@app.route('/problem_creation.html')
@login_required
def problem_creation():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    return render_template('problem_creation.html')

@app.route('/contest_state')
@login_required
def get_contest_state():
    """The schedule as every client needs it. `server_time` lets the browser correct for a wrong clock."""
    return jsonify({
        'phase': contest_phase(),
        'start_time': contest_config.get('start_time'),
        'end_time': contest_config.get('end_time'),
        'server_time': format_instant(datetime.now(timezone.utc)),
        'submissions_stopped': bool(contest_config.get('submissions_stopped', False)),
    })

@app.route('/contest_settings')
@login_required
def get_contest_settings():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    return jsonify(contest_config)

@app.route('/update_contest_settings', methods=['POST'])
@login_required
def update_contest_settings():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('contest_name'), str) or not data['contest_name'].strip():
        return jsonify({'error': 'Contest name is required'}), 400
    if len(data['contest_name']) > 80:
        return jsonify({'error': 'Contest name must be at most 80 characters'}), 400
    for flag in ('leaderboard_frozen', 'submissions_stopped'):
        if flag in data and not isinstance(data[flag], bool):
            return jsonify({'error': f'{flag} must be true or false'}), 400

    # Schedule: null (or empty) clears a time; anything else must be an ISO-8601 instant with a time zone
    schedule = {key: contest_config.get(key) for key in ('start_time', 'end_time')}
    for key, label in (('start_time', 'Start time'), ('end_time', 'End time')):
        if key not in data:
            continue
        if data[key] in (None, ''):
            schedule[key] = None
            continue
        moment = parse_instant(data[key])
        if moment is None:
            return jsonify({'error': f'{label} is not a valid date and time'}), 400
        schedule[key] = format_instant(moment)
    if schedule['start_time'] and schedule['end_time'] and parse_instant(schedule['end_time']) <= parse_instant(schedule['start_time']):
        return jsonify({'error': 'The end time must be after the start time'}), 400

    contest_config['contest_name'] = data['contest_name'].strip()
    for key, value in schedule.items():
        if value is None:
            contest_config.pop(key, None)
        else:
            contest_config[key] = value
    
    # Handle leaderboard freeze
    if 'leaderboard_frozen' in data:
        contest_config['leaderboard_frozen'] = data['leaderboard_frozen']
        if contest_config['leaderboard_frozen']:
            socketio.emit('update_leaderboard', 'Leaderboard has been frozen. The displayed leaderboard may not reflect the most recent standings.')
        else:
            # Submissions made while frozen now count, and keep counting if the board is frozen again
            Submission.query.filter_by(submitted_while_frozen=True).update({'submitted_while_frozen': False})
            db.session.commit()
            socketio.emit('update_leaderboard', 'Leaderboard has been unfrozen.')
    
    # Handle submissions stopped
    if 'submissions_stopped' in data:
        contest_config['submissions_stopped'] = data['submissions_stopped']
    
    # Save to config file
    with open('config/contest_config.json', 'w') as f:
        json.dump(contest_config, f, indent=4)

    socketio.emit('contest_changed')
    return jsonify({'message': 'Settings updated successfully'})


# ======================================================================== admin tools
def admin_required(view):
    """Login required, and the user must be an admin."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            return jsonify({'error': 'Unauthorized'}), 403
        return view(*args, **kwargs)
    return login_required(wrapper)

def _total_points_by_problem():
    return {p.id: sum(batch['points'] for batch in p.batches) for p in Problem.query.all()}

def _submission_summary(submission, username, problems, totals):
    problem = problems.get(submission.problem_id)
    return {
        'id': submission.id,
        'username': username,
        'problem': {
            'id': submission.problem_id,
            'shortname': problem.shortname if problem else '?',
            'title': problem.title if problem else '(deleted)',
            'total_points': totals.get(submission.problem_id, 0),
        },
        'language': submission.language,
        'status': submission.status,
        'points_earned': submission.points_earned,
        'execution_time': submission.execution_time,
        'memory_used': submission.memory_used,
        'submitted_at': submission.submitted_at.isoformat(),
    }

SUBMISSION_STATUSES = ('AC', 'WA', 'ERROR', 'PENDING')

@app.route('/admin/submissions')
@admin_required
def admin_submissions():
    """Every contestant's submissions, newest first, with optional filters and paging."""
    limit = min(max(request.args.get('limit', 50, type=int), 1), 200)
    offset = max(request.args.get('offset', 0, type=int), 0)

    query = db.session.query(Submission, User.username).join(User, Submission.user_id == User.id)
    problem_id = request.args.get('problem_id', type=int)
    if problem_id:
        query = query.filter(Submission.problem_id == problem_id)
    username = (request.args.get('username') or '').strip()
    if username:
        like = username.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        query = query.filter(User.username.ilike(f'%{like}%', escape='\\'))
    status = request.args.get('status')
    if status in SUBMISSION_STATUSES:
        query = query.filter(Submission.status == status)

    total = query.count()
    rows = query.order_by(Submission.id.desc()).offset(offset).limit(limit).all()
    problems = {p.id: p for p in Problem.query.options(db.defer(Problem.batches)).all()}
    totals = _total_points_by_problem()
    return jsonify({
        'total': total,
        'items': [_submission_summary(s, name, problems, totals) for s, name in rows],
    })

@app.route('/admin/submission/<int:submission_id>')
@admin_required
def admin_submission(submission_id):
    submission = db.get_or_404(Submission, submission_id)
    problems = {p.id: p for p in Problem.query.all()}
    summary = _submission_summary(submission, submission.user.username, problems, _total_points_by_problem())
    summary.update(code=submission.code, batch_results=submission.batch_results)
    return jsonify(summary)

@app.route('/admin/submission/<int:submission_id>/rejudge', methods=['POST'])
@admin_required
def admin_rejudge_submission(submission_id):
    submission = db.get_or_404(Submission, submission_id)
    problem = db.session.get(Problem, submission.problem_id)
    if problem is None:
        return jsonify({'error': 'The problem for this submission no longer exists'}), 404
    try:
        rejudge_submission(submission, problem)
    except JudgeError as e:
        app.logger.error('Judge unavailable: %s', e)
        db.session.rollback()
        return jsonify({'error': 'The judge is unavailable, so this could not be rejudged'}), 503
    socketio.emit('update_leaderboard')
    problems = {problem.id: problem}
    summary = _submission_summary(submission, submission.user.username, problems, _total_points_by_problem())
    summary.update(batch_results=submission.batch_results)
    return jsonify(summary)

# Rejudging every submission of a problem can take minutes, so it runs in the background (one job at a time)
rejudge_job = {'running': False, 'problem_id': None, 'title': '', 'total': 0, 'done': 0, 'failed': 0, 'error': None}
rejudge_lock = threading.Lock()

def _rejudge_worker(problem_id, submission_ids):
    consecutive_outages = 0
    with app.app_context():
        try:
            for submission_id in submission_ids:
                try:
                    submission = db.session.get(Submission, submission_id)
                    problem = db.session.get(Problem, problem_id)
                    if submission is None or problem is None:
                        continue
                    rejudge_submission(submission, problem)
                    consecutive_outages = 0
                    rejudge_job['done'] += 1
                except JudgeError as e:
                    db.session.rollback()
                    rejudge_job['failed'] += 1
                    consecutive_outages += 1
                    if consecutive_outages >= 3:
                        rejudge_job['error'] = f'Stopped: the judge is unavailable ({e})'
                        break
                except Exception:
                    app.logger.exception('Rejudge failed for submission %s', submission_id)
                    db.session.rollback()
                    rejudge_job['failed'] += 1
        finally:
            db.session.remove()
            rejudge_job['running'] = False
            socketio.emit('update_leaderboard')

@app.route('/admin/problem/<int:problem_id>/rejudge', methods=['POST'])
@admin_required
def admin_rejudge_problem(problem_id):
    problem = db.get_or_404(Problem, problem_id)
    with rejudge_lock:
        if rejudge_job['running']:
            return jsonify({'error': 'A rejudge is already running'}), 409
        ids = [sid for (sid,) in db.session.query(Submission.id).filter_by(problem_id=problem.id).order_by(Submission.id)]
        rejudge_job.update(running=True, problem_id=problem.id, title=problem.title, total=len(ids), done=0, failed=0, error=None)
    if not ids:
        rejudge_job['running'] = False
        return jsonify({'message': 'There are no submissions to rejudge', 'total': 0})
    threading.Thread(target=_rejudge_worker, args=(problem.id, ids), daemon=True).start()
    return jsonify({'message': 'Rejudge started', 'total': len(ids)}), 202

@app.route('/admin/rejudge_status')
@admin_required
def admin_rejudge_status():
    return jsonify(rejudge_job)

# -------------------------------------------------------------------- admin: users
def _contestant_or_error(user_id):
    """The contestant with this id, or a (response, status) error tuple. Admin accounts are off limits here."""
    user = db.session.get(User, user_id)
    if user is None:
        return None, (jsonify({'error': 'No such user'}), 404)
    if user.is_admin:
        return None, (jsonify({'error': 'Admin accounts cannot be changed here (see manage.py reset-admin-password)'}), 400)
    return user, None

@app.route('/admin/users')
@admin_required
def admin_users():
    counts = dict(db.session.query(Submission.user_id, func.count(Submission.id)).group_by(Submission.user_id).all())
    users = User.query.filter_by(is_admin=False).order_by(User.id).all()
    return jsonify([{
        'id': u.id, 'username': u.username, 'email': u.email, 'disabled': u.disabled,
        'submission_count': counts.get(u.id, 0),
    } for u in users])

@app.route('/admin/users/<int:user_id>/disabled', methods=['POST'])
@admin_required
def admin_set_user_disabled(user_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('disabled'), bool):
        return jsonify({'error': '"disabled" must be true or false'}), 400
    user, error = _contestant_or_error(user_id)
    if error:
        return error
    user.disabled = data['disabled']  # existing sessions end at the next request (see load_user)
    db.session.commit()
    socketio.emit('update_leaderboard')
    return jsonify({'message': 'Account disabled' if user.disabled else 'Account enabled', 'disabled': user.disabled})

@app.route('/admin/users/<int:user_id>', methods=['DELETE'])
@admin_required
def admin_delete_user(user_id):
    """Delete a contestant together with all their submissions."""
    user, error = _contestant_or_error(user_id)
    if error:
        return error
    deleted = Submission.query.filter_by(user_id=user.id).delete()
    db.session.delete(user)
    db.session.commit()
    socketio.emit('update_leaderboard')
    return jsonify({'message': 'Account deleted', 'deleted_submissions': deleted})

@app.route('/admin/users/<int:user_id>/reset_password', methods=['POST'])
@admin_required
def admin_reset_password(user_id):
    """Give a contestant a new random password. It is returned once and never stored in readable form."""
    user, error = _contestant_or_error(user_id)
    if error:
        return error
    password = generate_password()
    user.set_password(password)  # also signs out the sessions opened with the old password
    db.session.commit()
    response = jsonify({'username': user.username, 'password': password})
    response.headers['Cache-Control'] = 'no-store'
    return response

@app.route('/admin/slip_pdf', methods=['POST'])
@admin_required
def admin_slip_pdf():
    """A one-slip PDF for a freshly reset password (the same layout as the bulk sheet)."""
    data = request.get_json(silent=True)
    username = data.get('username') if isinstance(data, dict) else None
    password = data.get('password') if isinstance(data, dict) else None
    if (not isinstance(username, str) or not USERNAME_PATTERN.match(username)
            or not isinstance(password, str) or not re.fullmatch(r'[\x21-\x7e]{1,64}', password)):
        return jsonify({'error': 'A valid username and password are required'}), 400
    pdf = build_credentials_pdf([(username, password)], contest_config.get('contest_name', 'Coding Contest'),
                                request.host_url.rstrip('/'))
    return Response(pdf, mimetype='application/pdf', headers={
        'Content-Disposition': f'attachment; filename="login-{username}.pdf"', 'Cache-Control': 'no-store'})

# ------------------------------------------------------------------- admin: CSV export
def _csv_safe(value):
    """Stop spreadsheet programs from running a cell as a formula (CSV injection)."""
    text_value = '' if value is None else str(value)
    return "'" + text_value if text_value[:1] in ('=', '+', '-', '@', '\t', '\r') else text_value

def _csv_response(rows, filename):
    buffer = io.StringIO()
    csv.writer(buffer).writerows([[_csv_safe(cell) for cell in row] for row in rows])
    return Response('﻿' + buffer.getvalue(), mimetype='text/csv; charset=utf-8', headers={
        'Content-Disposition': f'attachment; filename="{filename}"', 'Cache-Control': 'no-store'})

@app.route('/admin/export/standings.csv')
@admin_required
def export_standings():
    """Final standings. This is the true result, so it ignores any leaderboard freeze."""
    problems, rows = build_leaderboard(frozen=False)
    output = [['Rank', 'Username'] + [p.shortname for p in problems] + ['Total']]
    rank, previous_total = 0, None
    for position, row in enumerate(rows, start=1):
        if row['total_points'] != previous_total:
            rank, previous_total = position, row['total_points']
        output.append([rank, row['username']] + [cell['points'] for cell in row['problem_points']] + [row['total_points']])
    return _csv_response(output, 'standings.csv')

@app.route('/admin/export/submissions.csv')
@admin_required
def export_submissions():
    problems = {p.id: p for p in Problem.query.options(db.defer(Problem.batches)).all()}
    output = [['Submission', 'Username', 'Problem', 'Problem title', 'Language', 'Status', 'Points', 'Time (ms)',
               'Memory (KB)', 'Submitted at', 'Submitted while frozen']]
    rows = db.session.query(Submission, User.username).join(User, Submission.user_id == User.id).order_by(Submission.id)
    for submission, username in rows:
        problem = problems.get(submission.problem_id)
        output.append([submission.id, username, problem.shortname if problem else '?', problem.title if problem else '',
                       submission.language, submission.status, submission.points_earned, submission.execution_time,
                       submission.memory_used, submission.submitted_at.isoformat(sep=' '),
                       'yes' if submission.submitted_while_frozen else 'no'])
    return _csv_response(output, 'submissions.csv')

if __name__ == '__main__':
    with app.app_context():
        # Data is kept across restarts; wiping it must be requested explicitly (RESET_DB=1)
        if os.environ.get('RESET_DB', '0').lower() in ('1', 'true', 'yes'):
            db.drop_all()
        db.create_all()
        ensure_schema()
        
        # Initialize admin user
        init_admin()
        
        # Initialize contest config if it doesn't exist
        if not os.path.exists('config/contest_config.json'):
            os.makedirs('config', exist_ok=True)
            with open('config/contest_config.json', 'w') as f:
                json.dump({
                    'contest_name': 'Coding Contest',
                    'leaderboard_frozen': False
                }, f, indent=4)
    
    judge_health = check_judge(force=True)
    print(('Judge OK: ' if judge_health['ok'] else 'WARNING - JUDGE UNAVAILABLE: ') + judge_health['message'])

    # Debug mode (Werkzeug debugger + reloader) is opt-in: set FLASK_DEBUG=1 for local development only
    debug = os.environ.get('FLASK_DEBUG', '0').lower() in ('1', 'true', 'yes')
    host = os.environ.get('HOST', '0.0.0.0')  # all interfaces so contestants on the LAN can connect
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)