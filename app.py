from flask import Flask, request, jsonify, render_template, redirect, url_for
from functools import wraps
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_socketio import SocketIO, emit
import json
import os
from datetime import datetime
import pytz
from urllib.parse import urlparse
from judge.judge import judge_submission
from sqlalchemy import select
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# The secret key signs session cookies, so it must never be a shared/default value
secret_key = os.environ.get('SECRET_KEY')
if not secret_key:
    raise RuntimeError(
        "SECRET_KEY is not set. Copy .env.example to .env and set a random value, e.g. "
        "python -c \"import secrets; print(secrets.token_hex(32))\""
    )
app.config['SECRET_KEY'] = secret_key
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///coding_contest.db'
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
    submissions = db.relationship('Submission', backref='user', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

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
def load_user(user_id):
    stmt = select(User).where(User.id == int(user_id))
    return db.session.execute(stmt).scalar_one_or_none()

MIN_PASSWORD_LENGTH = 8

def init_admin():
    """Create the admin user if it doesn't exist.

    The password comes from ADMIN_PASSWORD; if unset a random one is generated and printed once.
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
    if request.method == 'POST' and origin and urlparse(origin).netloc != request.host:
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

def shortname_for(index):
    """0 -> A, 25 -> Z, 26 -> AA, ..."""
    name = ''
    index += 1
    while index > 0:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name

# Routes
@app.route('/')
def index():
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit('10 per minute', methods=['POST'], key_func=get_remote_address)
def login():
    if request.method == 'POST':
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get('username'), str) or not isinstance(data.get('password'), str):
            return jsonify({'error': 'Username and password are required'}), 400
        user = User.query.filter_by(username=data['username']).first()
        
        if user and user.check_password(data['password']):
            login_user(user)
            return jsonify({'message': 'Login successful'}), 200
        
        return jsonify({'error': 'Invalid username or password'}), 401
    
    return render_template('login.html')

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

@app.route('/create_problem', methods=['POST'])
@login_required
def create_problem():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'error': 'Request body must be a JSON object'}), 400
        
        required_fields = ['title', 'description', 'difficulty', 'time_limit', 'memory_limit', 'batches']
        
        # Validate required fields
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Validate batches
        if not isinstance(data['batches'], list) or len(data['batches']) == 0:
            return jsonify({'error': 'At least one batch is required'}), 400
        
        for batch in data['batches']:
            if 'points' not in batch or 'test_cases' not in batch:
                return jsonify({'error': 'Each batch must have points and test_cases'}), 400
            
            if not isinstance(batch['test_cases'], list) or len(batch['test_cases']) == 0:
                return jsonify({'error': 'Each batch must have at least one test case'}), 400
            
            for test_case in batch['test_cases']:
                if 'input' not in test_case or 'output' not in test_case:
                    return jsonify({'error': 'Each test case must have input and output'}), 400
        
        # Generate shortname based on problem count
        problem_count = Problem.query.count()
        shortname = shortname_for(problem_count)  # A, B, ..., Z, AA, ...
        
        problem = Problem(
            title=data['title'],
            shortname=shortname,
            description=data['description'],
            difficulty=data['difficulty'],
            time_limit=data['time_limit'],
            memory_limit=data['memory_limit'],
            batches=data['batches']
        )
        
        db.session.add(problem)
        db.session.commit()
        
        # Emit WebSocket event for new problem
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
    problems = Problem.query.order_by(Problem.created_at.desc()).all()
    return jsonify([{
        'id': p.id,
        'title': p.title,
        'shortname': p.shortname,
        'difficulty': p.difficulty,
        'time_limit': p.time_limit,
        'memory_limit': p.memory_limit
    } for p in problems])

@app.route('/problem/<int:problem_id>')
@login_required
def get_problem(problem_id):
    problem = Problem.query.get_or_404(problem_id)
    data = {
        'id': problem.id,
        'title': problem.title,
        'shortname': problem.shortname,
        'description': problem.description,
        'difficulty': problem.difficulty,
        'time_limit': problem.time_limit,
        'memory_limit': problem.memory_limit
    }
    # Test cases are hidden from contestants
    if current_user.is_admin:
        data['batches'] = problem.batches
    return jsonify(data)

@app.route('/submit', methods=['POST'])
@login_required
@limiter.limit('6 per minute')
def submit():
    if contest_config.get('submissions_stopped', False):
        return jsonify({'error': 'Submissions have been stopped'}), 400
    
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

        problem = Problem.query.get_or_404(data['problem_id'])
        
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
                code=data['code'].replace("<br>", "\n"),
                language=data['language'],
                batches=problem.batches,
                time_limit=problem.time_limit,
                memory_limit=problem.memory_limit
            )
            
            # Never reveal hidden test output to the contestant
            for batch_result in result['batch_results']:
                for test_case_result in batch_result['test_case_results']:
                    test_case_result.pop('expected', None)
                    test_case_result.pop('got', None)

            # Update submission record
            submission.status = result['status']
            submission.execution_time = result.get('execution_time')
            submission.memory_used = result.get('memory_used')
            submission.points_earned = result.get('points_earned', 0)
            submission.batch_results = result['batch_results']
            result['id'] = submission.id
            
            # Emit WebSocket event for new submission - update leaderboard
            socketio.emit('update_leaderboard')
            
            db.session.commit()
            return jsonify(result)
            
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
                code=data['code'].replace("<br>", "\n"),
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
            
        except Exception as e:
            app.logger.exception('Run error')
            return jsonify({'error': 'The judge failed to run this code'}), 500
            
    except Exception as e:
        app.logger.exception('Run error')
        return jsonify({'error': 'Could not run code'}), 500

@app.route('/leaderboard')
@login_required
def get_leaderboard():
    # Get all users who are not admins
    users = User.query.filter_by(is_admin=False).all()
    
    # Get all problems
    problems = Problem.query.order_by(Problem.id).all()
    
    # Get whether leaderboard is frozen
    is_frozen = contest_config.get('leaderboard_frozen', False)
    
    # Calculate points for each user
    leaderboard_data = []
    for user in users:
        user_data = {
            'username': user.username,
            'total_points': 0,
            'problem_points': []
        }
        
        # For each problem, find the best submission
        for problem in problems:
            best_submission = Submission.query.filter_by(
                user_id=user.id,
                problem_id=problem.id,
                status='AC'
            )
            if is_frozen: # Frozen leaderboard - filter out submissions made while frozen
                best_submission = best_submission.filter_by(
                    submitted_while_frozen=False
                )
            best_submission = best_submission.order_by(Submission.points_earned.desc()).order_by(Submission.submitted_at).first()
            
            points = best_submission.points_earned if best_submission else 0
            submission_time = best_submission.submitted_at if best_submission else None
            
            user_data['problem_points'].append({
                'points': points,
                'submission_time': submission_time.strftime('%H:%M:%S') if submission_time else None
            })
            user_data['total_points'] += points
        
        # Add user to leaderboard even if they have no submissions
        leaderboard_data.append(user_data)
    
    # Sort by total points in descending order
    leaderboard_data.sort(key=lambda x: x['total_points'], reverse=True)
    
    return jsonify({
        'problems': [{'id': p.id, 'title': p.title, 'shortname': p.shortname} for p in problems],
        'users': leaderboard_data,
        'is_frozen': contest_config.get('leaderboard_frozen', False)
    })

@app.route('/submission/<int:submission_id>')
@login_required
def get_submission(submission_id):
    submission = Submission.query.filter_by(user_id=current_user.id, id=submission_id).first_or_404()
    return jsonify({
        'id': submission.id,
        'user_id': submission.user_id,
        'problem_id': submission.problem_id,
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
    if not isinstance(data, dict) or not isinstance(data.get('contest_name'), str):
        return jsonify({'error': 'Contest name is required'}), 400
    
    contest_config['contest_name'] = data['contest_name']
    
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
    
    return jsonify({'message': 'Settings updated successfully'})

if __name__ == '__main__':
    with app.app_context():
        # Data is kept across restarts; wiping it must be requested explicitly (RESET_DB=1)
        if os.environ.get('RESET_DB', '0').lower() in ('1', 'true', 'yes'):
            db.drop_all()
        db.create_all()
        
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
    
    # Debug mode (Werkzeug debugger + reloader) is opt-in: set FLASK_DEBUG=1 for local development only
    debug = os.environ.get('FLASK_DEBUG', '0').lower() in ('1', 'true', 'yes')
    host = os.environ.get('HOST', '0.0.0.0')  # all interfaces so contestants on the LAN can connect
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)