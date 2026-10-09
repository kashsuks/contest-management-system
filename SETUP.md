# Coding Contest Platform Setup Guide

This guide will help you set up and run the coding contest platform locally.

## Prerequisites

- Python 3.11+ (dependency versions in `requirements.txt` are pinned to what the project is tested with)
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


## Running on a contest network

The server listens on all interfaces (`HOST`/`PORT` environment variables, default `0.0.0.0:5000`) and contestants open `http://<server-ip>:5000`.

- Give the server machine a static or reserved IP so the address doesn't change mid-contest.
- Allow inbound TCP port 5000 in the host firewall.
- Some school/guest Wi-Fi networks use client isolation, which blocks device-to-device traffic. Test from a second device on the same network beforehand.
- All front-end libraries are served from `static/vendor/`, so no internet access is needed during the contest (see `static/vendor/README.md` for versions).
- The site uses plain HTTP, so don't let contestants reuse real passwords.
- Keep the machine plugged in and disable sleep.

## Security Considerations

1. Change the default SECRET_KEY in .env
2. Set up proper CORS configuration in production
3. Use HTTPS in production
4. Set up proper database backups
5. Monitor system resources

## Support

For issues and feature requests, please create an issue in the repository. 