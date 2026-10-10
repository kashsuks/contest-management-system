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