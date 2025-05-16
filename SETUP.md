# Coding Contest Platform Setup Guide

This guide will help you set up and run the coding contest platform locally.

## Prerequisites

- Python 3.8+
- For C++ submissions: g++ compiler
- For Java submissions: JDK (Java Development Kit)

## Backend Setup

1. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install backend dependencies:
```bash
pip install -r requirements.txt
```

4. Initialize the contest environment
```bash
python setup.py
```

5. Start the backend server:
```bash
python app.py
```

## Judge System Setup

The judge system runs locally and supports multiple programming languages:

1. Python (built-in, no additional setup needed)
2. C++ (requires g++ compiler)
   - Windows: Install MinGW
   - Linux: `sudo apt-get install g++`
   - macOS: `brew install gcc`
3. Java (requires JDK)
   - Download and install JDK from Oracle or OpenJDK
   - Set JAVA_HOME environment variable


## Security Considerations

1. Change the default SECRET_KEY in .env
2. Set up proper CORS configuration in production
3. Use HTTPS in production
4. Set up proper database backups
5. Monitor system resources

## Support

For issues and feature requests, please create an issue in the repository. 