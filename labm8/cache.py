# Copyright (C) 2015, 2016 Chris Cummins.
#
# Labm8 is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your
# option) any later version.
#
# Labm8 is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
# or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public
# License for more details.
#
# You should have received a copy of the GNU General Public License
# along with labm8.  If not, see <http://www.gnu.org/licenses/>.
#
"""
Transient and persistent caching mechanisms.
"""
import atexit
import json
import six

import labm8 as lab
from labm8 import crypto
from labm8 import fs
from labm8 import io


class Cache(object):
    """
    Cache for storing (key,value) relational data.

    A cache is a dictionary with a limited subset of a the
    functionality.
    """

    def get(self, key, default=None):
        """
        Retrieve an item from cache.

        Arguments:
            key: Item key.
            default (optional): Default value if item not found.
        """
        raise NotImplementedError

    def clear(self):
        """
        Remove all items from cache.
        """
        raise NotImplementedError

    def items(self):
        """
        Returns a generator for iterating over (key, value) pairs.
        """
        raise NotImplementedError

    def __getitem__(self, key):
        """
        Retrieve an item from cache.

        Arguments:
           key: Item key.

        Raises:
           KeyError: If key is not in cache.
        """
        raise NotImplementedError

    def __setitem__(self, key, value):
        """
        Set (key, value) pair.
        """
        raise NotImplementedError

    def __contains__(self, key):
        """
        Returns whether key is in cache.
        """
        raise NotImplementedError

    def __delitem__(self, key):
        """
        Remove (key, value) pair.
        """
        raise NotImplementedError


class TransientCache(Cache):
    """
    An in-memory only cache.
    """

    def __init__(self, basecache=None):
        """
        Create a new transient cache.

        Optionally supports populating the cache with values of an
        existing cache.

        Arguments:
           basecache (TransientCache, optional): Cache to populate this new
             cache with.
        """
        self._data = {}

        if basecache is not None:
            for key,val in basecache.items():
                self._data[key] = val

    def get(self, key, default=None):
        if key in self._data:
            return self._data[key]
        else:
            return default

    def clear(self):
        self._data.clear()

    def items(self):
        return six.iteritems(self._data)

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value
        return value

    def __contains__(self, key):
        return key in self._data

    def __delitem__(self, key):
        del self._data[key]


class JsonCache(TransientCache):
    """
    A persistent, JSON-backed cache.

    Requires that (key, value) pairs are JSON serialisable.
    """

    def __init__(self, path, basecache=None):
        """
        Create a new JSON cache.

        Optionally supports populating the cache with values of an
        existing cache.

        Arguments:
           basecache (TransientCache, optional): Cache to populate this new
             cache with.
        """

        super(JsonCache, self).__init__()
        self.path = fs.abspath(path)

        if fs.exists(self.path):
            io.debug(("Loading cache '{0}'".format(self.path)))
            with open(self.path) as file:
                self._data = json.load(file)

        if basecache is not None:
            for key,val in basecache.items():
                self._data[key] = val

        # Register exit handler
        atexit.register(self.write)

    def write(self):
        """
        Write contents of cache to disk.
        """
        io.debug("Storing cache '{0}'".format(self.path))
        with open(self.path, "w") as file:
            json.dump(self._data, file, sort_keys=True, indent=2,
                      separators=(',', ': '))


class FSCache(Cache):
    """
    Persistent filesystem cache.

    Each key uniquely identifies a file.
    Each value is a file path.

    Adding a file to the cache moves it into the cahce directory.
    """
    def __init__(self, root):
        """
        Create filesystem cache.

        Arguments:
            root (str): String.
        """
        self.path = root

        fs.mkdir(self.path)

    def clear(self):
        """
        Empty the filesystem cache.

        This deletes the entire cache directory.
        """
        fs.rm(self.path)

    def _keypath(self, key):
        """
        Convert key to relative cache path.

        Arguments:
            key: Key.

        Returns:
            str: Absolute path.
        """
        hash = crypto.sha1(json.dumps(key, sort_keys=True))
        return fs.path(self.path, hash)

    def __getitem__(self, key):
        """
        Get path to file in cache.

        Arguments:
            key: Key.

        Returns:
            str: Path to cache value.

        Raises:
            KeyErorr: If key not in cache.
        """
        path = self._keypath(key)
        if fs.exists(path):
            return path
        else:
            raise KeyError(key)

    def __setitem__(self, key, value):
        """
        Emplace file in cache.

        Arguments:
            key: Key.
            value (str): Path of file to insert in cache.

        Raises:
            ValueError: If no "value" does nto exist.
        """
        if not fs.exists(value):
            raise ValueError(value)

        path = self._keypath(key)
        fs.mv(value, path)

    def __contains__(self, key):
        """
        Check cache contents.

        Arguments:
            key: Key.

        Returns:
            bool: True if key in cache, else false.
        """
        path = self._keypath(key)
        return fs.exists(path)

    def __delitem__(self, key):
        """
        Delete cached file.

        Arguments:
            key: Key.

        Raises:
            KeyError: If file not in cache.
        """
        path = self._keypath(key)
        if fs.exists(path):
            fs.rm(path)
        else:
            raise KeyError(key)

    def get(self, key, default=None):
        """
        Fetch from cache.

        Arguments:
            key: Key.
            default (optional): Value returned if key not found.

        Returns:
            str: Path to cached file.
        """
        if key in self:
            return self[key]
        else:
            return default