# Copyright 2017 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# =============================================================================
"""Generates and prints out imports and constants for new TensorFlow python api.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import argparse
import collections
import os
import sys

from tensorflow.python.util import tf_decorator


_API_CONSTANTS_ATTR = '_tf_api_constants'
_API_NAMES_ATTR = '_tf_api_names'
_API_DIR = '/api/'
_OUTPUT_MODULE = 'tensorflow.tools.api.generator.api'
_GENERATED_FILE_HEADER = """\"\"\"Imports for Python API.

This file is MACHINE GENERATED! Do not edit.
Generated by: tensorflow/tools/api/generator/create_python_api.py script.
\"\"\"
"""


class SymbolExposedTwiceError(Exception):
  """Raised when different symbols are exported with the same name."""
  pass


def format_import(source_module_name, source_name, dest_name):
  """Formats import statement.

  Args:
    source_module_name: (string) Source module to import from.
    source_name: (string) Source symbol name to import.
    dest_name: (string) Destination alias name.

  Returns:
    An import statement string.
  """
  if source_module_name:
    if source_name == dest_name:
      return 'from %s import %s' % (source_module_name, source_name)
    else:
      return 'from %s import %s as %s' % (
          source_module_name, source_name, dest_name)
  else:
    if source_name == dest_name:
      return 'import %s' % source_name
    else:
      return 'import %s as %s' % (source_name, dest_name)


class _ModuleImportsBuilder(object):
  """Builds a map from module name to imports included in that module."""

  def __init__(self):
    self.module_imports = collections.defaultdict(list)
    self._seen_api_names = set()

  def add_import(
      self, dest_module_name, source_module_name, source_name, dest_name):
    """Adds this import to module_imports.

    Args:
      dest_module_name: (string) Module name to add import to.
      source_module_name: (string) Module to import from.
      source_name: (string) Name of the symbol to import.
      dest_name: (string) Import the symbol using this name.

    Raises:
      SymbolExposedTwiceError: Raised when an import with the same
        dest_name has already been added to dest_module_name.
    """
    import_str = format_import(source_module_name, source_name, dest_name)
    if import_str in self.module_imports[dest_module_name]:
      return

    # Check if we are trying to expose two different symbols with same name.
    full_api_name = dest_name
    if dest_module_name:
      full_api_name = dest_module_name + '.' + full_api_name
    if full_api_name in self._seen_api_names:
      raise SymbolExposedTwiceError(
          'Trying to export multiple symbols with same name: %s.' %
          full_api_name)
    self._seen_api_names.add(full_api_name)

    self.module_imports[dest_module_name].append(import_str)


def get_api_imports():
  """Get a map from destination module to formatted imports.

  Returns:
    A dictionary where
      key: (string) destination module (for e.g. tf or tf.consts).
      value: List of strings representing module imports
          (for e.g. 'from foo import bar') and constant
          assignments (for e.g. 'FOO = 123').
  """
  module_imports_builder = _ModuleImportsBuilder()
  visited_symbols = set()

  # Traverse over everything imported above. Specifically,
  # we want to traverse over TensorFlow Python modules.
  for module in sys.modules.values():
    # Only look at tensorflow modules.
    if not module or 'tensorflow.' not in module.__name__:
      continue
    # Do not generate __init__.py files for contrib modules for now.
    if '.contrib.' in module.__name__ or module.__name__.endswith('.contrib'):
      continue

    for module_contents_name in dir(module):
      attr = getattr(module, module_contents_name)
      if id(attr) in visited_symbols:
        continue

      # If attr is _tf_api_constants attribute, then add the constants.
      if module_contents_name == _API_CONSTANTS_ATTR:
        for exports, value in attr:
          for export in exports:
            names = export.split('.')
            dest_module = '.'.join(names[:-1])
            module_imports_builder.add_import(
                dest_module, module.__name__, value, names[-1])
        continue

      _, attr = tf_decorator.unwrap(attr)
      # If attr is a symbol with _tf_api_names attribute, then
      # add import for it.
      if hasattr(attr, '__dict__') and _API_NAMES_ATTR in attr.__dict__:
        # If the same symbol is available using multiple names, only create
        # imports for it once.
        if id(attr) in visited_symbols:
          continue
        visited_symbols.add(id(attr))

        for export in attr._tf_api_names:  # pylint: disable=protected-access
          names = export.split('.')
          dest_module = '.'.join(names[:-1])
          module_imports_builder.add_import(
              dest_module, module.__name__, module_contents_name, names[-1])

  # Import all required modules in their parent modules.
  # For e.g. if we import 'foo.bar.Value'. Then, we also
  # import 'bar' in 'foo'.
  imported_modules = set(module_imports_builder.module_imports.keys())
  for module in imported_modules:
    if not module:
      continue
    module_split = module.split('.')
    parent_module = ''  # we import submodules in their parent_module

    for submodule_index in range(len(module_split)):
      import_from = _OUTPUT_MODULE
      if submodule_index > 0:
        parent_module += ('.' + module_split[submodule_index-1] if parent_module
                          else module_split[submodule_index-1])
        import_from += '.' + parent_module
      module_imports_builder.add_import(
          parent_module, import_from, module_split[submodule_index],
          module_split[submodule_index])

  return module_imports_builder.module_imports


def create_api_files(output_files):
  """Creates __init__.py files for the Python API.

  Args:
    output_files: List of __init__.py file paths to create.
      Each file must be under api/ directory.

  Raises:
    ValueError: if an output file is not under api/ directory,
      or output_files list is missing a required file.
  """
  module_name_to_file_path = {}
  for output_file in output_files:
    # Convert path separators to '/' for easier parsing below.
    normalized_output_file = output_file.replace(os.sep, '/')
    if _API_DIR not in output_file:
      raise ValueError(
          'Output files must be in api/ directory, found %s.' % output_file)
    # Get the module name that corresponds to output_file.
    # First get module directory under _API_DIR.
    module_dir = os.path.dirname(
        normalized_output_file[
            normalized_output_file.rfind(_API_DIR)+len(_API_DIR):])
    # Convert / to .
    module_name = module_dir.replace('/', '.').strip('.')
    module_name_to_file_path[module_name] = os.path.normpath(output_file)

  # Create file for each expected output in genrule.
  for module, file_path in module_name_to_file_path.items():
    if not os.path.isdir(os.path.dirname(file_path)):
      os.makedirs(os.path.dirname(file_path))
    open(file_path, 'a').close()

  module_imports = get_api_imports()

  # Add imports to output files.
  missing_output_files = []
  for module, exports in module_imports.items():
    # Make sure genrule output file list is in sync with API exports.
    if module not in module_name_to_file_path:
      module_file_path = '"api/%s/__init__.py"' %  (
          module.replace('.', '/'))
      missing_output_files.append(module_file_path)
      continue
    with open(module_name_to_file_path[module], 'w') as fp:
      fp.write(_GENERATED_FILE_HEADER + '\n'.join(exports))

  if missing_output_files:
    raise ValueError(
        'Missing outputs for python_api_gen genrule:\n%s.'
        'Make sure all required outputs are in the '
        'tensorflow/tools/api/generator/BUILD file.' %
        ',\n'.join(sorted(missing_output_files)))


def main(output_files):
  create_api_files(output_files)

if __name__ == '__main__':
  parser = argparse.ArgumentParser()
  parser.add_argument(
      'outputs', metavar='O', type=str, nargs='+',
      help='If a single file is passed in, then we we assume it contains a '
      'semicolon-separated list of Python files that we expect this script to '
      'output. If multiple files are passed in, then we assume output files '
      'are listed directly as arguments.')
  args = parser.parse_args()
  if len(args.outputs) == 1:
    # If we only get a single argument, then it must be a file containing
    # list of outputs.
    with open(args.outputs[0]) as output_list_file:
      outputs = [line.strip() for line in output_list_file.read().split(';')]
  else:
    outputs = args.outputs
  main(outputs)
