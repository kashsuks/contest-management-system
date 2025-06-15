# CMS (Contest Management System)

An in-person coding contest platform built using modern technologies

## Features

- User authentication (register, login, logout)
- Problem listing with search functionality
- Code editor with syntax highlighting
- Support for multiple programming languages (C++, Java, Python)
- Real-time subprocess-based code execution and judging
- Submission history
- Problem creation form with title, description, time and memory limits, and test cases
- Admin-only panel for problem creation, user account creation, etc.
- Leaderboard that ranks participants in real time (can be frozen to not display updates)

## Prerequisites

- Python 3.8 or higher
- pip (Python package manager)

## Setup

1. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Setup the contest environment
```bash
python setup.py
```

## Running the Application

1. Start the Flask application:
```bash
python app.py
```

2. Open your web browser and navigate to the link the flask app provides

## Project Structure

```
.
├── app.py                      # Main Flask application
├── requirements.txt            # Python dependencies
├── judge.py                    # Code execution and judging script
├── templates/
│   └── index.html              # Main interface of the platform
│   └── base.html               # Sets up the base app
│   └── login.html              # Login page
│   └── problem_creation.html   # Problem Creation Form
```

## Security Considerations

- Each submission runs in an isolated environment
- Resource limits (time and memory) are enforced
- User authentication is required for submissions

## Contributing

Feel free to submit issues and enhancement requests! 