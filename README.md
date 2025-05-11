# Coding Contest Platform

A simple coding contest platform built with Flask and Docker for code execution.

## Features

- User authentication (register, login, logout)
- Problem listing with search functionality
- Code editor with syntax highlighting
- Support for multiple programming languages (C++, Java, Python)
- Real-time code execution and judging
- Submission history
- Docker-based code execution for security

## Prerequisites

- Python 3.8 or higher
- Docker
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

3. Build the Docker image for code execution:
```bash
docker build -t coding-judge .
```

4. Initialize the database and add sample problems:
```bash
python add_sample_problems.py
```

## Running the Application

1. Start the Flask application:
```bash
python app.py
```

2. Open your web browser and navigate to:
```
http://localhost:5000
```

## Project Structure

```
.
├── app.py                 # Main Flask application
├── requirements.txt       # Python dependencies
├── Dockerfile            # Docker configuration for code execution
├── judge.py             # Code execution and judging script
├── add_sample_problems.py # Script to add sample problems
├── templates/
│   └── index.html       # Single-page application template
└── static/
    └── css/
        └── style.css    # CSS styles
```

## Security Considerations

- Code execution is sandboxed using Docker containers
- Each submission runs in an isolated environment
- Resource limits (time and memory) are enforced
- User authentication is required for submissions

## Contributing

Feel free to submit issues and enhancement requests! 