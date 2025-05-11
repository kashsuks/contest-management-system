import os
import json
import psutil
import subprocess
import tempfile
import time
from typing import Dict, Any

def compile_code(code, language):
    """Compile the code based on the language."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=get_file_extension(language)) as f:
        f.write(code)
        source_file = f.name

    if language == 'cpp':
        executable = source_file + '.exe'
        result = subprocess.run(['g++', source_file, '-o', executable], 
                              capture_output=True, text=True)
        if result.returncode != 0:
            return None, result.stderr
        return executable, None
    elif language == 'java':
        result = subprocess.run(['javac', source_file], 
                              capture_output=True, text=True)
        if result.returncode != 0:
            return None, result.stderr
        return source_file.replace('.java', ''), None
    elif language == 'python':
        return source_file, None
    else:
        return None, f"Unsupported language: {language}"

def get_file_extension(language):
    """Get the file extension for the given language."""
    extensions = {
        'cpp': '.cpp',
        'java': '.java',
        'python': '.py'
    }
    return extensions.get(language, '')

def run_code(code: str, language: str, test_case: Dict[str, str], time_limit: int, memory_limit: int) -> Dict[str, Any]:
    """Run the code against a test case and return the result."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Write code to file
        if language == 'python':
            code_file = os.path.join(temp_dir, 'solution.py')
        elif language == 'cpp':
            code_file = os.path.join(temp_dir, 'solution.cpp')
        elif language == 'java':
            code_file = os.path.join(temp_dir, 'Solution.java')
        else:
            return {'status': 'CE', 'error': f'Unsupported language: {language}'}

        with open(code_file, 'w') as f:
            f.write(code)

        # Compile if needed
        if language == 'cpp':
            compile_result = subprocess.run(['g++', code_file, '-o', os.path.join(temp_dir, 'solution')], 
                                         capture_output=True, text=True)
            if compile_result.returncode != 0:
                return {'status': 'CE', 'error': compile_result.stderr}
            executable = os.path.join(temp_dir, 'solution')
        elif language == 'java':
            compile_result = subprocess.run(['javac', code_file], capture_output=True, text=True)
            if compile_result.returncode != 0:
                return {'status': 'CE', 'error': compile_result.stderr}
            executable = ['java', '-cp', temp_dir, 'Solution']
        else:
            executable = ['python', code_file]

        # Run the code
        start_time = time.time()
        process = subprocess.Popen(
            executable,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        try:
            # Get process object for memory tracking
            ps_process = psutil.Process(process.pid)
            
            # Send input
            stdout, stderr = process.communicate(input=test_case['input'], timeout=time_limit/1000)
            execution_time = (time.time() - start_time) * 1000  # Convert to milliseconds

            # Check memory usage
            try:
                memory_used = ps_process.memory_info().rss / 1024  # Convert to KB
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                memory_used = 0  # If we can't get memory info, default to 0

            if process.returncode != 0:
                return {
                    'status': 'RE',
                    'error': stderr,
                    'execution_time': execution_time,
                    'memory_used': memory_used
                }

            # Compare output
            expected_output = test_case['output'].strip()
            actual_output = stdout.strip()

            if actual_output == expected_output:
                return {
                    'status': 'AC',
                    'execution_time': execution_time,
                    'memory_used': memory_used
                }
            else:
                return {
                    'status': 'WA',
                    'expected': expected_output,
                    'got': actual_output,
                    'execution_time': execution_time,
                    'memory_used': memory_used
                }

        except subprocess.TimeoutExpired:
            process.kill()
            return {'status': 'TLE', 'error': 'Time limit exceeded'}
        except Exception as e:
            return {'status': 'RE', 'error': str(e)}

def judge_submission(code, language, batches, time_limit, memory_limit):
    """Judge a submission against batches of test cases."""
    total_earned = 0
    max_execution_time = 0
    max_memory_used = 0
    batch_results = []

    # Run against each batch
    for batch in batches:
        batch_points = batch['points']
        test_cases = batch['test_cases']
        batch_passed = True
        batch_execution_time = 0
        batch_memory_used = 0
        error = ""
        
        current_batch_result = {
            'status': '',
            'batch_points': 0,
            'test_case_results': []
        }

        # Run against each test case in the batch
        for test_case in test_cases:
            if not batch_passed:
                current_batch_result['test_case_results'].append({'status': 'skip'})
                continue
                
            result = run_code(code, language, test_case, time_limit, memory_limit)
            
            if result['status'] != 'AC':
                batch_passed = False
                error = result['status']
                
                # add specific execution time for TLE
                time_taken = round(result.get('execution_time', 0), 2) if error != 'TLE' else f">{time_limit:.2f}"
                
                current_batch_result['test_case_results'].append({
                    'status': result['status'],
                    'error': result.get('error', ''),
                    'expected': result.get('expected', ''),
                    'got': result.get('got', ''),
                    'execution_time': time_taken,
                    'memory_used': result.get('memory_used', 0)
                })
            else:
                current_batch_result['test_case_results'].append({
                    'status': 'AC',
                    'execution_time': round(result.get('execution_time', 0), 2),
                    'memory_used': result.get('memory_used', 0)
                })
            
            batch_execution_time = max(batch_execution_time, result.get('execution_time', 0))
            batch_memory_used = max(batch_memory_used, result.get('memory_used', 0))

        if batch_passed:
            current_batch_result['batch_points'] = batch_points
            current_batch_result['status'] = 'AC'
            total_earned += batch_points
            max_execution_time = max(max_execution_time, batch_execution_time)
            max_memory_used = max(max_memory_used, batch_memory_used)
        else:
            current_batch_result['status'] = error
        
        batch_results.append(current_batch_result)
    
    return {
        'status': 'AC' if total_earned > 0 else 'WA',
        'points_earned': total_earned,
        'execution_time': round(max_execution_time, 2),
        'memory_used': max_memory_used,
        'batch_results': batch_results
    }

def main():
    """Main function to handle the judging process."""
    try:
        # Get input from environment variables
        code = os.environ.get('CODE', '')
        language = os.environ.get('LANGUAGE', '')
        time_limit = int(os.environ.get('TIME_LIMIT', 1000))
        memory_limit = int(os.environ.get('MEMORY_LIMIT', 256))
        batches = json.loads(os.environ.get('BATCHES', '[]'))

        if not all([code, language, batches]):
            print(json.dumps({
                'status': 'CE',
                'error': 'Missing required parameters'
            }))
            return

        # Judge the submission
        result = judge_submission(code, language, batches, time_limit, memory_limit)
        print(json.dumps(result))

    except Exception as e:
        print(json.dumps({
            'status': 'CE',
            'error': str(e)
        }))

if __name__ == '__main__':
    main() 