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
    description = db.Column(db.Text, nullable=False)
    difficulty = db.Column(db.String(20), nullable=False)
    time_limit = db.Column(db.Integer, nullable=False)  # in milliseconds
    memory_limit = db.Column(db.Integer, nullable=False)  # in MB
    test_cases = db.Column(db.JSON, nullable=False)
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
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)

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
        
        required_fields = ['title', 'description', 'difficulty', 'time_limit', 'memory_limit', 'test_cases']
        
        # Validate required fields
        for field in required_fields:
            if field not in data:
                print(f"Missing field: {field}")  # Debug print
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Validate test cases
        if not isinstance(data['test_cases'], list) or len(data['test_cases']) == 0:
            print("Invalid test cases")  # Debug print
            return jsonify({'error': 'At least one test case is required'}), 400
        
        for test_case in data['test_cases']:
            if 'input' not in test_case or 'output' not in test_case:
                print("Invalid test case format")  # Debug print
                return jsonify({'error': 'Each test case must have input and output'}), 400
        
        problem = Problem(
            title=data['title'],
            description=data['description'],
            difficulty=data['difficulty'],
            time_limit=data['time_limit'],
            memory_limit=data['memory_limit'],
            test_cases=data['test_cases']
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
        'description': problem.description,
        'difficulty': problem.difficulty,
        'time_limit': problem.time_limit,
        'memory_limit': problem.memory_limit
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
        submission = Submission(
            user_id=current_user.id,
            problem_id=problem.id,
            code=data['code'],
            language=data['language'],
            status='PENDING'
        )
        db.session.add(submission)
        db.session.commit()
        
        # Judge the submission
        try:
            result = judge_submission(
                code=data['code'],
                language=data['language'],
                test_cases=problem.test_cases,
                time_limit=problem.time_limit,
                memory_limit=problem.memory_limit
            )
            
            # Update submission record
            submission.status = result['status']
            submission.execution_time = result.get('execution_time')
            submission.memory_used = result.get('memory_used')
            
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
    users = User.query.filter_by(is_admin=False).order_by(User.solved_problems.desc()).all()
    return jsonify([{
        'username': u.username,
        'solved_problems': u.solved_problems
    } for u in users])

@app.route('/submissions')
@login_required
def get_submissions():
    submissions = Submission.query.filter_by(user_id=current_user.id).order_by(Submission.submitted_at.desc()).all()
    return jsonify([{
        'id': s.id,
        'problem': {'title': s.problem.title},
        'language': s.language,
        'status': s.status,
        'execution_time': s.execution_time,
        'memory_used': s.memory_used,
        'submitted_at': s.submitted_at.isoformat()
    } for s in submissions])

@app.route('/check_admin')
@login_required
def check_admin():
    return jsonify({'is_admin': current_user.is_admin})

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        init_admin()  # Initialize admin user
    app.run(host='0.0.0.0', port=5000, debug=True) 