# -*- coding: utf-8 -*-

#   Copyright (c) 2010-2016, MIT Probabilistic Computing Project
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

from contextlib import contextmanager
import os
import pytest
from bdbcontrib import verify_notebook as vn
from util import session

PARENT_DIR=os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "satellites")

def test_ma_schools():
  with session(PARENT_DIR):
    msglimit = None if pytest.config.option.verbose else 1000
    vn.run_and_verify_notebook(
      os.path.join(PARENT_DIR, "querying-and-plotting"),
      msglimit=msglimit,
      required=[('pairplot_vars\(', [vn.assert_has_png()]),
                ('pairplot\(', [vn.assert_has_png()]),
                ('barplot\(', [vn.assert_has_png()]),
                ('histogram\(', [vn.assert_has_png()]),
                ('heatmap\(', [vn.assert_has_png()]),
                ],
      warnings_are_errors=False)
