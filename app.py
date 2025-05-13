from flask import Flask, request, jsonify, render_template, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import json
import os
from datetime import datetime
from judge.judge import judge_submission

app = Flask(__name__)
app.config['SECRET_KEY'] = 'key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///coding_contest.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Load contest configuration
def load_contest_config():
    try:
        with open('config/contest_config.json', 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {'contest_name': 'Coding Contest'}  # Default name

contest_config = load_contest_config()

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
    solved_problems = db.Column(db.Integer, default=0)
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
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    batch_results = db.Column(db.JSON) # List of batches, containing result of each test case

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def init_admin():
    """Initialize admin user if it doesn't exist"""
    with app.app_context():
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', email='admin@example.com', is_admin=True)
            admin.set_password('admin')
            db.session.add(admin)
            db.session.commit()

# Routes
@app.route('/')
def index():
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    
    if User.query.filter_by(username=data['username']).first():
        return jsonify({'error': 'Username already exists'}), 400
    
    if User.query.filter_by(email=data['email']).first():
        return jsonify({'error': 'Email already exists'}), 400
    
    user = User(username=data['username'], email=data['email'])
    user.set_password(data['password'])
    
    db.session.add(user)
    db.session.commit()
    
    return jsonify({'message': 'Registration successful'}), 201

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.get_json()
        user = User.query.filter_by(username=data['username']).first()
        
        if user and user.check_password(data['password']):
            login_user(user)
            return jsonify({'message': 'Login successful'}), 200
        
        return jsonify({'error': 'Invalid username or password'}), 401
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/create_user', methods=['POST'])
@login_required
def create_user():
    if not current_user.is_admin:
        return jsonify({'error': 'Unauthorized'}), 403
    
    data = request.get_json()
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
        data = request.get_json()
        print("Received data:", data)  # Debug print
        
        required_fields = ['title', 'description', 'difficulty', 'time_limit', 'memory_limit', 'batches']
        
        # Validate required fields
        for field in required_fields:
            if field not in data:
                print(f"Missing field: {field}")  # Debug print
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Validate batches
        if not isinstance(data['batches'], list) or len(data['batches']) == 0:
            print("Invalid batches")  # Debug print
            return jsonify({'error': 'At least one batch is required'}), 400
        
        for batch in data['batches']:
            if 'points' not in batch or 'test_cases' not in batch:
                print("Invalid batch format")  # Debug print
                return jsonify({'error': 'Each batch must have points and test_cases'}), 400
            
            if not isinstance(batch['test_cases'], list) or len(batch['test_cases']) == 0:
                print("Invalid test cases in batch")  # Debug print
                return jsonify({'error': 'Each batch must have at least one test case'}), 400
            
            for test_case in batch['test_cases']:
                if 'input' not in test_case or 'output' not in test_case:
                    print("Invalid test case format")  # Debug print
                    return jsonify({'error': 'Each test case must have input and output'}), 400
        
        # Generate shortname based on problem count
        problem_count = Problem.query.count()
        shortname = chr(65 + problem_count)  # A, B, C, etc.
        
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
        print("Problem created successfully")  # Debug print
        
        return jsonify({'message': 'Problem created successfully', 'id': problem.id}), 201
    except Exception as e:
        print("Error creating problem:", str(e))  # Debug print
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

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
    return jsonify({
        'id': problem.id,
        'title': problem.title,
        'shortname': problem.shortname,
        'description': problem.description,
        'difficulty': problem.difficulty,
        'time_limit': problem.time_limit,
        'memory_limit': problem.memory_limit,
        'batches': problem.batches
    })

@app.route('/submit', methods=['POST'])
@login_required
def submit():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
            
        if 'problem_id' not in data:
            return jsonify({'error': 'Problem ID is required'}), 400
            
        if 'code' not in data:
            return jsonify({'error': 'Code is required'}), 400
            
        if 'language' not in data:
            return jsonify({'error': 'Language is required'}), 400

        problem = Problem.query.get_or_404(data['problem_id'])
        
        # Create submission record
        count = Submission.query.count()
        submission = Submission(
            user_id=current_user.id,
            problem_id=problem.id,
            code=data['code'],
            language=data['language'],
            status='PENDING',
            id=count
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
            
            # Update submission record
            submission.status = result['status']
            submission.execution_time = result.get('execution_time')
            submission.memory_used = result.get('memory_used')
            submission.points_earned = result.get('points_earned', 0)
            submission.batch_results = result['batch_results']
            result['id'] = count
            
            # If this is the first successful submission for this problem by this user
            if result['status'] == 'AC':
                previous_success = Submission.query.filter_by(
                    user_id=current_user.id,
                    problem_id=problem.id,
                    status='AC'
                ).first()
                
                if not previous_success:
                    current_user.solved_problems += 1
                    db.session.commit()
            
            db.session.commit()
            return jsonify(result)
            
        except Exception as e:
            print(f"Judge error: {str(e)}")
            submission.status = 'ERROR'
            db.session.commit()
            return jsonify({'error': f'Judge error: {str(e)}'}), 500
            
    except Exception as e:
        print(f"Submission error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/leaderboard')
@login_required
def get_leaderboard():
    # Get all users who are not admins
    users = User.query.filter_by(is_admin=False).all()
    
    # Get all problems
    problems = Problem.query.order_by(Problem.id).all()
    
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
            ).order_by(Submission.points_earned.desc()).first()
            
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
    submission = Submission.query.filter_by(user_id=current_user.id, id=submission_id).first()
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
        }
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
    
    data = request.get_json()
    if 'contest_name' not in data:
        return jsonify({'error': 'Contest name is required'}), 400
    
    contest_config['contest_name'] = data['contest_name']
    
    # Handle leaderboard freeze
    if 'leaderboard_frozen' in data:
        contest_config['leaderboard_frozen'] = data['leaderboard_frozen']
    
    # Save to config file
    with open('config/contest_config.json', 'w') as f:
        json.dump(contest_config, f, indent=4)
    
    return jsonify({'message': 'Settings updated successfully'})

if __name__ == '__main__':
    with app.app_context():
        # Drop all tables and recreate them
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
    
    app.run(host='0.0.0.0', port=5000, debug=True) 