# Coding Contest Platform Setup Guide

This guide will help you set up and run the coding contest platform locally.

## Prerequisites

- Python 3.8+
- npm or yarn
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
pip install psutil  # Required for the judge system
```

3. Set up environment variables:
```bash
cp .env.example .env
# Edit .env with your configuration:
# - DATABASE_URL
# - SECRET_KEY
# - JWT_ALGORITHM
# - ACCESS_TOKEN_EXPIRE_MINUTES
```

4. Initialize the database:
```bash
python scripts/init_db.py
```

5. Start the backend server:
```bash
python app.py
```

## Frontend Setup

1. Navigate to the frontend directory:
```bash
cd frontend
```

2. Install frontend dependencies:
```bash
npm install
```

3. Start the development server:
```bash
npm start
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

## Testing the Platform

1. Create an admin account:
```bash
curl -X POST http://localhost:5000/register \
  -H "Content-Type: application/json" \
  -d '{
    "username": "admin",
    "email": "admin@example.com",
    "password": "admin123"
  }'
```

2. Create a problem:
```bash
curl -X POST http://localhost:5000/create_problem \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Hello World",
    "description": "Print the input string.",
    "difficulty": "Easy",
    "time_limit": 1000,
    "memory_limit": 256,
    "test_cases": [
      {
        "input": "Hello, World!",
        "output": "Hello, World!"
      }
    ]
  }'
```

## Security Considerations

1. Change the default SECRET_KEY in .env
2. Set up proper CORS configuration in production
3. Use HTTPS in production
4. Set up proper database backups
5. Monitor system resources

## Production Deployment

1. Build the frontend:
```bash
cd frontend
npm run build
```

2. Set up a production-grade server (e.g., Nginx)
3. Use a process manager (e.g., Supervisor) for the backend
4. Set up SSL certificates
5. Configure proper logging and monitoring

## Troubleshooting

1. Database connection issues:
   - Check database service is running
   - Verify DATABASE_URL in .env
   - Check database user permissions

2. Judge issues:
   - For C++: Verify g++ is installed and in PATH
   - For Java: Verify JDK is installed and JAVA_HOME is set
   - Check file permissions in the judge directory
   - Verify test case format

3. Frontend issues:
   - Clear browser cache
   - Check browser console for errors
   - Verify API endpoint configuration

## Support

For issues and feature requests, please create an issue in the repository. 