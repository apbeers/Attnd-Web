"""Run the live integration tests and summarise them (also into $GITHUB_STEP_SUMMARY when set).

    ATTND_IT_SERVER=<host> ATTND_IT_KEY=<key> python3 live_tests/run.py [-k pattern]

Exits non-zero when a test fails or errors; when ATTND_IT_SERVER / ATTND_IT_KEY are missing everything is skipped.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv):
    loader = unittest.TestLoader()
    if '-k' in argv:
        loader.testNamePatterns = [f'*{argv[argv.index("-k") + 1]}*']
    suite = loader.discover(HERE, pattern='live_*.py', top_level_dir=HERE)
    result = unittest.TextTestRunner(verbosity=2).run(suite)

    import live_api
    server = live_api.SERVER or '(not set)'
    problems = [(t, 'failed') for t, _ in result.failures] + [(t, 'error') for t, _ in result.errors]
    passed = result.testsRun - len(problems) - len(result.skipped)
    icon = '✅' if not problems else '❌'
    lines = [f'### {icon} Live integration tests · `{server}`', '',
             f'{passed} passed, {len(problems)} failed, {len(result.skipped)} skipped (of {result.testsRun}).']
    if problems:
        lines += ['', '| Test | Result | First line |', '|---|---|---|']
        details = dict((id(t), tb) for t, tb in result.failures + result.errors)
        for test, kind in problems:
            last = [l for l in details[id(test)].strip().splitlines() if l.strip()][-1][:200].replace('|', '\\|')
            lines.append(f'| `{test.id()}` | {kind} | {last} |')
    summary = '\n'.join(lines) + '\n'
    print('\n' + summary)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as fh:
            fh.write(summary)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
