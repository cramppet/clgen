#
# Copyright 2016, 2017 Chris Cummins <chrisc.101@gmail.com>.
#
# This file is part of CLgen.
#
# CLgen is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# CLgen is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with CLgen.  If not, see <http://www.gnu.org/licenses/>.
#
import os
import sqlite3
import tarfile
import tensorflow as tf

from labm8 import fs
from labm8 import tar
from unittest import TestCase

import clgen

# Quiet tensorflow. See: http://stackoverflow.com/a/38645250/1318051
tf.logging.set_verbosity(tf.logging.WARN)


class TestData404(Exception):
    pass


def data_path(*components, **kwargs):
    """
    Return absolute path to unittest data file. Data files are located in
    tests/data.

    Args:
        *components (str[]): Relative path.
        **kwargs (dict, optional): If 'exists' True, require that file exists.

    Returns:
        string: Absolute path.

    Raises:
        TestData404: If path doesn"t exist.
    """
    path = fs.path(*components)
    exists = kwargs.get("exists", True)

    abspath = os.path.join(os.path.dirname(__file__), "data", path)
    if exists and not os.path.exists(abspath):
        raise TestData404(abspath)
    return abspath


def data_str(*components):
    """
    Return contents of unittest data file as a string.

    Args:
        *components (str[]): Relative path.

    Returns:
        string: File contents.

    Raises:
        TestData404: If path doesn't exist.
    """
    path = fs.path(*components)

    with open(data_path(path)) as infile:
        return infile.read()


def archive(*components):
    """
    Returns a text archive, unpacking if necessary.

    Arguments:
        *components (str[]): Relative path.

    Returns:
        str: Path to archive.
    """
    path = data_path(*components, exists=False)

    if not fs.isdir(path):
        tar.unpack_archive(path + ".tar.bz2")
    return path


def db_path(path):
    """
    Return absolute path to unittest data file. Data files are located in
    tests/data/db.

    Args:
        path (str): Relative path.

    Returns:
        string: Absolute path.

    Raises:
        TestData404: If path doesn't exist.
    """
    return data_path(os.path.join("db", str(path) + ".db"))


def db(name, **kwargs):
    """
    Return absolute path to unittest data file. Data files are located in
    tests/data/db.

    Args:
        path (str): Relative path.

    Returns:
        sqlite.Connection: Sqlite connection to database.

    Raises:
        TestData404: If path doesn't exist.
    """
    path = data_path(db_path(name), **kwargs)
    return sqlite3.connect(path)


class TestCLgen(TestCase):
    def test_pacakge_data(self):
        with self.assertRaises(clgen.InternalError):
            clgen.package_data("This definitely isn't a real path")
        with self.assertRaises(clgen.File404):
            clgen.package_data("This definitely isn't a real path")

    def test_pacakge_str(self):
        with self.assertRaises(clgen.InternalError):
            clgen.package_str("This definitely isn't a real path")
        with self.assertRaises(clgen.File404):
            clgen.package_str("This definitely isn't a real path")

    def test_sql_script(self):
        with self.assertRaises(clgen.InternalError):
            clgen.sql_script("This definitely isn't a real path")
        with self.assertRaises(clgen.File404):
            clgen.sql_script("This definitely isn't a real path")