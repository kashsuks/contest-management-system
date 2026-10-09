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

Contestant code is compiled and run inside a throwaway Docker container (no network, read-only filesystem, dropped capabilities, non-root user, memory / pid / CPU limits). Python, C++ (g++) and Java (JDK) are provided by the image, so you only need Docker on the machine running the server.

1. Install and start Docker (Docker Desktop on macOS/Windows, `docker.io` on Linux).
2. Build the judge image once:
```bash
docker build -t cms-judge judge/
```
3. Start the server as usual. If Docker or the image is missing, submissions fail with a judge error; they are never run unsandboxed.

Environment variables:
- `JUDGE_IMAGE` - image name (default `cms-judge`)
- `JUDGE_SANDBOX=local` - **development only**: run submissions directly on this machine (needs local `g++` / JDK). Never use this for a real contest.

## Security Considerations

1. Change the default SECRET_KEY in .env
2. Set up proper CORS configuration in production
3. Use HTTPS in production
4. Set up proper database backups
5. Monitor system resources

## Support

For issues and feature requests, please create an issue in the repository. 