# Copyright 2014-2019 Chris Cummins <chrisc.101@gmail.com>.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Python unit test main entry point.

This project uses pytest runner, with a handful of custom configuration options.
Use the Main() function as the entry point to your test files to run pytest
with the proper arguments.
"""
import sys

import contextlib
import inspect
import pathlib
import pytest
import re
import tempfile
import typing
from importlib import util as importutil

from labm8 import app

FLAGS = app.FLAGS

app.DEFINE_boolean('test_color', False, 'Colorize pytest output.')
app.DEFINE_boolean('test_skip_slow', True,
                   'Skip tests that have been marked slow.')
app.DEFINE_integer(
    'test_maxfail', 1,
    'The maximum number of tests that can fail before execution terminates. '
    'If --test_maxfail=0, all tests will execute.')
app.DEFINE_boolean('test_capture_output', True,
                   'Capture stdout and stderr during test execution.')
app.DEFINE_boolean(
    'test_print_durations', True,
    'Print the duration of the slowest tests at the end of execution. Use '
    '--test_durations to set the number of tests to print the durations of.')
app.DEFINE_integer(
    'test_durations', 3,
    'The number of slowest tests to print the durations of after execution. '
    'If --test_durations=0, the duration of all tests is printed.')
app.DEFINE_string(
    'test_coverage_data_dir', None,
    'Run tests with statement coverage and write coverage.py data files to '
    'this directory. The directory is created. Existing files are untouched.')


def AbsolutePathToModule(file_path: str) -> str:
  """Determine module name from an absolute path."""
  match = re.match(r'.+\.runfiles/phd/(.+)', file_path)
  if match:
    # Strip everything up to the root of the project from the path.
    module = match.group(1)
    # Strip the .py suffix.
    module = module[:-len('.py')]
    # Replace path sep with module sep.
    module = module.replace('/', '.')
    return module
  else:
    raise OSError(f"Could not determine runfiles directory: {file_path}")


def GuessModuleUnderTest(file_path: str) -> typing.Optional[str]:
  """Determine the module under test. Returns None if no module under test."""
  # Load the test module and check for a MODULE_UNDER_TEST attribute. If
  # present, this is the name of the module under test. Valid values for
  # MODULE_UNDER_TEST are a string, e.g. 'labm8.app', or None.
  spec = importutil.spec_from_file_location('module', file_path)
  test_module = importutil.module_from_spec(spec)
  spec.loader.exec_module(test_module)
  if hasattr(test_module, 'MODULE_UNDER_TEST'):
    return test_module.MODULE_UNDER_TEST

  # If the module under test was not specified, Guess the module name by
  # stripping the '_test' suffix from the name of the test module.
  return AbsolutePathToModule(file_path)[:-len('_test')]


@contextlib.contextmanager
def CoverageContext(file_path: str,
                    pytest_args: typing.List[str]) -> typing.List[str]:

  # Record coverage of module under test.
  module = GuessModuleUnderTest(file_path)
  if not module:
    app.Log(1, 'Coverage disabled - no module under test')
    yield pytest_args
    return

  with tempfile.TemporaryDirectory(prefix='phd_test_') as d:
    # If we
    if FLAGS.test_coverage_data_dir:
      datadir = pathlib.Path(FLAGS.test_coverage_data_dir)
      datadir.mkdir(parents=True, exist_ok=True)
    else:
      datadir = pathlib.Path(d)
    # Create a coverage.py config file.
    # See: https://coverage.readthedocs.io/en/coverage-4.3.4/config.html
    config_path = f"{d}/converagerc"
    with open(config_path, "w") as f:
      f.write(f"""\
[run]
data_file = {datadir}/.coverage
parallel = True
# disable_warnings =
#   module-not-imported
#   no-data-collected
#   module-not-measured

[report]
ignore_errors = True
# Regexes for lines to exclude from consideration
exclude_lines =
  # Have to re-enable the standard pragma
  pragma: no cover

  # Don't complain about missing debug-only code:
  def __repr__
  if self\.debug

  # Don't complain if tests don't hit defensive assertion code:
  raise AssertionError
  raise NotImplementedError

  # Don't complain if non-runnable code isn't run:
  if 0:
  if __name__ == .__main__.:
""")

    pytest_args += [
        f'--cov={module}',
        f'--cov-config={config_path}',
    ]
    yield pytest_args


def RunPytestOnFileAndExit(file_path: str, argv: typing.List[str]):
  """Run pytest on a file and exit.

  This is invoked by absl.app.RunWithArgs(), and has access to absl flags.

  This function does not return.

  Args:
    file_path: The path of the file to test.
    argv: Positional arguments not parsed by absl. No additional arguments are
      supported.
  """
  if len(argv) > 1:
    raise app.UsageError("Unknown arguments: '{}'.".format(' '.join(argv[1:])))

  # Test files must end with _test.py suffix. This is a code style choice, not
  # a hard requirement.
  if not file_path.endswith('_test.py'):
    app.Fatal("File `%s` does not end in suffix _test.py", file_path)

  # Assemble the arguments to run pytest with. Note that the //:conftest file
  # performs some additional configuration not captured here.
  pytest_args = [
      file_path,
      # Run pytest verbosely.
      '-vv',
      '-p',
      'no:cacheprovider',
  ]

  if FLAGS.test_color:
    pytest_args.append('--color=yes')

  if FLAGS.test_maxfail != 0:
    pytest_args.append(f'--maxfail={FLAGS.test_maxfail}')

  # Print the slowest test durations at the end of execution.
  if FLAGS.test_print_durations:
    pytest_args.append(f'--durations={FLAGS.test_durations}')

  # Capture stdout and stderr by default.
  if not FLAGS.test_capture_output:
    pytest_args.append('-s')

  with CoverageContext(file_path, pytest_args) as pytest_args:
    app.Log(1, 'Running pytest with arguments: %s', pytest_args)
    ret = pytest.main(pytest_args)
  sys.exit(ret)


def Main():
  """Main entry point."""
  app.FLAGS(['argv[0]', '--vmodule=*=5'])

  # Get the file path of the calling function. This is used to identify the
  # script to run the tests of.
  frame = inspect.stack()[1]
  module = inspect.getmodule(frame[0])
  file_path = module.__file__

  app.RunWithArgs(lambda argv: RunPytestOnFileAndExit(file_path, argv))
