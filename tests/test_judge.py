"""Judge behaviour that doesn't need Docker (local mode, Python submissions)."""
import pytest

from judge.judge import judge_submission, normalize_output

ECHO = [{'points': 10, 'test_cases': [{'input': '1\n', 'output': '1 2\n3\n'}]}]


def judge(code, expected='1 2\n3\n'):
    batches = [{'points': 10, 'test_cases': [{'input': '', 'output': expected}]}]
    return judge_submission(code, 'python', batches, 2000, 256)['batch_results'][0]['test_case_results'][0]['status']


@pytest.mark.parametrize('printed', [
    "print('1 2'); print('3')",            # exact
    "print('1 2   '); print('3  ')",       # trailing spaces on lines
    "print('1 2\\t'); print('3')",         # trailing tab
    "print('\\n\\n1 2'); print('3\\n\\n')",  # blank lines around the output
    "import sys; sys.stdout.write('1 2\\r\\n3\\r\\n')",  # Windows line endings
])
def test_trailing_whitespace_is_ignored(printed):
    assert judge(printed) == 'AC'


@pytest.mark.parametrize('printed', [
    "print('1  2'); print('3')",            # extra space inside a line
    "print('1 2 3')",                       # different line structure
    "print('1 2'); print(); print('3')",    # a blank line in the middle
    "print('1 2'); print(' 3')",            # leading space on a later line
    "print('1 2')",                         # missing line
])
def test_real_differences_still_fail(printed):
    assert judge(printed) == 'WA'


def test_expected_output_is_normalised_too():
    assert judge("print('1 2'); print('3')", expected='1 2   \r\n3\r\n\r\n') == 'AC'


def test_normalize_output_examples():
    assert normalize_output('a  \nb\t\n\n') == 'a\nb'
    assert normalize_output('\n\n  a b \r\nc') == 'a b\nc'
    assert normalize_output('a  b') == 'a  b'
