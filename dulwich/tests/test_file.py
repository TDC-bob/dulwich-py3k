# test_file.py -- Test for git files
# Copyright (C) 2010 Google, Inc.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; version 2
# of the License or (at your option) a later version of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA  02110-1301, USA.

import errno
import os
import shutil
import sys
import tempfile

from dulwich.file import GitFile, fancy_rename
from dulwich.tests import (
    SkipTest,
    TestCase,
    )

class FancyRenameTests(TestCase):

    def setUp(self):
        super(FancyRenameTests, self).setUp()
        self._tempdir = tempfile.mkdtemp()
        self.foo = self.path('foo')
        self.bar = self.path('bar')
        self.create(self.foo, b'foo contents')

    def tearDown(self):
        shutil.rmtree(self._tempdir)
        super(FancyRenameTests, self).tearDown()

    def path(self, filename):
        return os.path.join(self._tempdir, filename)

    def create(self, path, contents):
        with open(path, 'wb') as f:
            f.write(contents)

    def test_no_dest_exists(self):
        self.assertFalse(os.path.exists(self.bar))
        fancy_rename(self.foo, self.bar)
        self.assertFalse(os.path.exists(self.foo))

        with open(self.bar, 'rb') as new_f:
            self.assertEqual(b'foo contents', new_f.read())
         
    def test_dest_exists(self):
        self.create(self.bar, b'bar contents')
        fancy_rename(self.foo, self.bar)
        self.assertFalse(os.path.exists(self.foo))

        with open(self.bar, 'rb') as new_f:
            self.assertEqual(b'foo contents', new_f.read())

    def test_dest_opened(self):
        if sys.platform != "win32":
            raise SkipTest("platform allows overwriting open files")
        self.create(self.bar, b'bar contents')
        with open(self.bar, 'rb') as dest_f:
            self.assertRaises(OSError, fancy_rename, self.foo, self.bar)
        self.assertTrue(os.path.exists(self.path('foo')))

        with open(self.foo, 'rb') as new_f:
            self.assertEqual('foo contents', new_f.read())

        with open(self.bar, 'rb') as new_f:
            self.assertEqual('bar contents', new_f.read())


class GitFileTests(TestCase):

    def setUp(self):
        super(GitFileTests, self).setUp()
        self._tempdir = tempfile.mkdtemp()
        with open(self.path('foo'), 'wb') as f:
            f.write(b'foo contents')

    def tearDown(self):
        shutil.rmtree(self._tempdir)
        super(GitFileTests, self).tearDown()

    def path(self, filename):
        return os.path.join(self._tempdir, filename)

    def test_invalid(self):
        foo = self.path('foo')
        self.assertRaises(IOError, GitFile, foo, mode='r')
        self.assertRaises(IOError, GitFile, foo, mode='ab')
        self.assertRaises(IOError, GitFile, foo, mode='r+b')
        self.assertRaises(IOError, GitFile, foo, mode='w+b')
        self.assertRaises(IOError, GitFile, foo, mode='a+bU')

    def test_readonly(self):
        import _io
        f = GitFile(self.path('foo'), 'rb')
        self.assertTrue(isinstance(f, _io.BufferedReader))
        self.assertEqual(b'foo contents', f.read())
        self.assertEqual(b'', f.read())
        f.seek(4)
        self.assertEqual(b'contents', f.read())
        f.close()

    def test_default_mode(self):
        f = GitFile(self.path('foo'))
        self.assertEqual(b'foo contents', f.read())
        f.close()

    def test_write(self):
        foo = self.path('foo')
        foo_lock = '%s.lock' % foo

        with open(foo, 'rb') as orig_f:
            self.assertEqual(orig_f.read(), b'foo contents')

        self.assertFalse(os.path.exists(foo_lock))
        f = GitFile(foo, 'wb')
        self.assertFalse(f.closed)
        self.assertRaises(AttributeError, getattr, f, 'not_a_file_property')

        self.assertTrue(os.path.exists(foo_lock))
        f.write(b'new stuff')
        f.seek(4)
        f.write(b'contents')
        f.close()
        self.assertFalse(os.path.exists(foo_lock))

        with open(foo, 'rb') as new_f:
            self.assertEqual(b'new contents', new_f.read())

    def test_open_twice(self):
        foo = self.path('foo')
        f1 = GitFile(foo, 'wb')
        f1.write(b'new')
        try:
            f2 = GitFile(foo, 'wb')
            self.fail()
        except OSError as e:
            self.assertEqual(errno.EEXIST, e.errno)
        f1.write(b' contents')
        f1.close()

        # Ensure trying to open twice doesn't affect original.
        with open(foo, 'rb') as f:
            self.assertEqual(b'new contents', f.read())

    def test_abort(self):
        foo = self.path('foo')
        foo_lock = '%s.lock' % foo

        with open(foo, 'rb') as orig_f:
            self.assertEqual(orig_f.read(), b'foo contents')

        f = GitFile(foo, 'wb')
        f.write(b'new contents')
        f.abort()
        self.assertTrue(f.closed)
        self.assertFalse(os.path.exists(foo_lock))

        with open(foo, 'rb') as new_orig_f:
            self.assertEqual(new_orig_f.read(), b'foo contents')

    def test_abort_close(self):
        foo = self.path('foo')
        f = GitFile(foo, 'wb')
        f.abort()
        try:
            f.close()
        except (IOError, OSError):
            self.fail()

        f = GitFile(foo, 'wb')
        f.close()
        try:
            f.abort()
        except (IOError, OSError):
            self.fail()

    def test_abort_close_removed(self):
        foo = self.path('foo')
        f = GitFile(foo, 'wb')

        f._file.close()
        os.remove(foo+".lock")

        f.abort()
        self.assertTrue(f._closed)
