# test_repository.py -- tests for repository.py
# Copyright (C) 2007 James Westby <jw+debian@jameswestby.net>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; version 2
# of the License or (at your option) any later version of
# the License.
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

"""Tests for the repository."""

from io import BytesIO
import os
import shutil
import tempfile
import warnings

from dulwich import errors
from dulwich.file import (
    GitFile,
    )
from dulwich.object_store import (
    tree_lookup_path,
    )
from dulwich import objects
from dulwich.repo import (
    check_ref_format,
    DictRefsContainer,
    Repo,
    MemoryRepo,
    read_packed_refs,
    read_packed_refs_with_peeled,
    write_packed_refs,
    _split_ref_line,
    )
from dulwich.tests import (
    TestCase,
    )
from dulwich.tests.utils import (
    open_repo,
    tear_down_repo,
    )

from dulwich.py3k import *

missing_sha = 'b91fa4d900e17e99b433218e988c4eb4a3e9a097'


class CreateRepositoryTests(TestCase):

    def assertFileContentsEqual(self, expected, repo, path):
        f = repo.get_named_file(path)
        if not f:
            self.assertEqual(expected, None)
        else:
            try:
                self.assertEqual(expected, f.read())
            finally:
                f.close()

    def _check_repo_contents(self, repo, expect_bare):
        self.assertEqual(expect_bare, repo.bare)
        self.assertFileContentsEqual(b'Unnamed repository', repo, 'description')
        self.assertFileContentsEqual(b'', repo, os.path.join('info', 'exclude'))
        self.assertFileContentsEqual(None, repo, 'nonexistent file')
        barestr = ('bare = %s' % str(expect_bare).lower()).encode()
        with repo.get_named_file('config') as config:
            self.assertTrue(barestr in config.read())

    def test_create_disk_bare(self):
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir)
        repo = Repo.init_bare(tmp_dir)
        self.assertEqual(tmp_dir, repo._controldir)
        self._check_repo_contents(repo, True)

    def test_create_disk_non_bare(self):
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir)
        repo = Repo.init(tmp_dir)
        self.assertEqual(os.path.join(tmp_dir, '.git'), repo._controldir)
        self._check_repo_contents(repo, False)

    def test_create_memory(self):
        repo = MemoryRepo.init_bare([], {})
        self._check_repo_contents(repo, True)


class RepositoryTests(TestCase):

    def setUp(self):
        super(RepositoryTests, self).setUp()
        self._repo = None

    def tearDown(self):
        if self._repo is not None:
            tear_down_repo(self._repo)
        super(RepositoryTests, self).tearDown()

    def test_simple_props(self):
        r = self._repo = open_repo('a.git')
        self.assertEqual(r.controldir(), r.path)

    def test_ref(self):
        r = self._repo = open_repo('a.git')
        self.assertEqual(r.ref(b'refs/heads/master'),
                         b'a90fa2d900a17e99b433217e988c4eb4a2e9a097')

    def test_iter(self):
        r = self._repo = open_repo('a.git')
        self.assertRaises(NotImplementedError, r.__iter__)

    def test_setitem(self):
        r = self._repo = open_repo('a.git')
        r["refs/tags/foo"] = 'a90fa2d900a17e99b433217e988c4eb4a2e9a097'
        self.assertEqual(b'a90fa2d900a17e99b433217e988c4eb4a2e9a097',
                          r[b"refs/tags/foo"].id)

    def test_delitem(self):
        r = self._repo = open_repo('a.git')

        del r['refs/heads/master']
        self.assertRaises(KeyError, lambda: r['refs/heads/master'])

        del r['HEAD']
        self.assertRaises(KeyError, lambda: r['HEAD'])

        self.assertRaises(ValueError, r.__delitem__, 'notrefs/foo')

    def test_get_refs(self):
        r = self._repo = open_repo('a.git')
        self.assertEqual({
            b'HEAD': b'a90fa2d900a17e99b433217e988c4eb4a2e9a097',
            b'refs/heads/master': b'a90fa2d900a17e99b433217e988c4eb4a2e9a097',
            b'refs/tags/mytag': b'28237f4dc30d0d462658d6b937b08a0f0b6ef55a',
            b'refs/tags/mytag-packed': b'b0931cadc54336e78a1d980420e3268903b57a50',
            }, r.get_refs())

    def test_head(self):
        r = self._repo = open_repo('a.git')
        self.assertEqual(r.head(), b'a90fa2d900a17e99b433217e988c4eb4a2e9a097')

    def test_get_object(self):
        r = self._repo = open_repo('a.git')
        obj = r.get_object(r.head())
        self.assertEqual(obj.type_name, 'commit')

    def test_get_object_non_existant(self):
        r = self._repo = open_repo('a.git')
        self.assertRaises(KeyError, r.get_object, missing_sha)

    def test_contains_object(self):
        r = self._repo = open_repo('a.git')
        self.assertTrue(r.head() in r)

    def test_contains_ref(self):
        r = self._repo = open_repo('a.git')
        self.assertTrue("HEAD" in r)

    def test_contains_missing(self):
        r = self._repo = open_repo('a.git')
        self.assertFalse("bar" in r)

    def test_commit(self):
        r = self._repo = open_repo('a.git')
        warnings.simplefilter("ignore", DeprecationWarning)
        self.addCleanup(warnings.resetwarnings)
        obj = r.commit(r.head())
        self.assertEqual(obj.type_name, 'commit')

    def test_commit_not_commit(self):
        r = self._repo = open_repo('a.git')
        warnings.simplefilter("ignore", DeprecationWarning)
        self.addCleanup(warnings.resetwarnings)
        self.assertRaises(errors.NotCommitError,
            r.commit, '4f2e6529203aa6d44b5af6e3292c837ceda003f9')

    def test_tree(self):
        r = self._repo = open_repo('a.git')
        commit = r[r.head()]
        warnings.simplefilter("ignore", DeprecationWarning)
        self.addCleanup(warnings.resetwarnings)
        tree = r.tree(commit.tree)
        self.assertEqual(tree.type_name, 'tree')
        self.assertEqual(convert3kstr(tree.sha().hexdigest(), BYTES), commit.tree)

    def test_tree_not_tree(self):
        r = self._repo = open_repo('a.git')
        warnings.simplefilter("ignore", DeprecationWarning)
        self.addCleanup(warnings.resetwarnings)
        self.assertRaises(errors.NotTreeError, r.tree, r.head())

    def test_tag(self):
        r = self._repo = open_repo('a.git')
        tag_sha = '28237f4dc30d0d462658d6b937b08a0f0b6ef55a'
        warnings.simplefilter("ignore", DeprecationWarning)
        self.addCleanup(warnings.resetwarnings)
        tag = r.tag(tag_sha)
        self.assertEqual(tag.type_name, 'tag')
        self.assertEqual(tag.sha().hexdigest(), tag_sha)
        obj_class, obj_sha = tag.object
        self.assertEqual(obj_class, objects.Commit)
        self.assertEqual(obj_sha, r.head())

    def test_tag_not_tag(self):
        r = self._repo = open_repo('a.git')
        warnings.simplefilter("ignore", DeprecationWarning)
        self.addCleanup(warnings.resetwarnings)
        self.assertRaises(errors.NotTagError, r.tag, r.head())

    def test_get_peeled(self):
        # unpacked ref
        r = self._repo = open_repo('a.git')
        tag_sha = '28237f4dc30d0d462658d6b937b08a0f0b6ef55a'
        self.assertNotEqual(r[tag_sha].sha().hexdigest(), r.head())
        self.assertEqual(r.get_peeled('refs/tags/mytag'), r.head())

        # packed ref with cached peeled value
        packed_tag_sha = 'b0931cadc54336e78a1d980420e3268903b57a50'
        parent_sha = r[r.head()].parents[0]
        self.assertNotEqual(r[packed_tag_sha].sha().hexdigest(), parent_sha)
        self.assertEqual(r.get_peeled('refs/tags/mytag-packed'), parent_sha)

        # TODO: add more corner cases to test repo

    def test_get_peeled_not_tag(self):
        r = self._repo = open_repo('a.git')
        self.assertEqual(r.get_peeled('HEAD'), r.head())

    def test_get_blob(self):
        r = self._repo = open_repo('a.git')
        commit = r[r.head()]
        tree = r[commit.tree]
        blob_sha = list(tree.items())[0][2]
        warnings.simplefilter("ignore", DeprecationWarning)
        self.addCleanup(warnings.resetwarnings)
        blob = r.get_blob(blob_sha)
        self.assertEqual(blob.type_name, 'blob')
        self.assertEqual(convert3kstr(blob.sha().hexdigest(), BYTES), blob_sha)

    def test_get_blob_notblob(self):
        r = self._repo = open_repo('a.git')
        warnings.simplefilter("ignore", DeprecationWarning)
        self.addCleanup(warnings.resetwarnings)
        self.assertRaises(errors.NotBlobError, r.get_blob, r.head())

    def test_get_walker(self):
        r = self._repo = open_repo('a.git')
        # include defaults to [r.head()]
        self.assertEqual([e.commit.id for e in r.get_walker()],
                         [r.head(), b'2a72d929692c41d8554c07f6301757ba18a65d91'])
        self.assertEqual(
            [e.commit.id for e in r.get_walker([b'2a72d929692c41d8554c07f6301757ba18a65d91'])],
            [b'2a72d929692c41d8554c07f6301757ba18a65d91'])

    def test_linear_history(self):
        r = self._repo = open_repo('a.git')
        warnings.simplefilter("ignore", DeprecationWarning)
        self.addCleanup(warnings.resetwarnings)
        history = r.revision_history(r.head())
        shas = [convert3kstr(c.sha().hexdigest(), BYTES) for c in history]
        self.assertEqual(shas, [r.head(),
                                b'2a72d929692c41d8554c07f6301757ba18a65d91'])

    def test_clone(self):
        r = self._repo = open_repo('a.git')
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir)
        t = r.clone(tmp_dir, mkdir=False)
        self.assertEqual({
            b'HEAD': b'a90fa2d900a17e99b433217e988c4eb4a2e9a097',
            b'refs/remotes/origin/master':
                b'a90fa2d900a17e99b433217e988c4eb4a2e9a097',
            b'refs/heads/master': b'a90fa2d900a17e99b433217e988c4eb4a2e9a097',
            b'refs/tags/mytag': b'28237f4dc30d0d462658d6b937b08a0f0b6ef55a',
            b'refs/tags/mytag-packed':
                b'b0931cadc54336e78a1d980420e3268903b57a50',
            }, t.refs.as_dict())
        shas = [e.commit.id for e in r.get_walker()]
        self.assertEqual(shas, [t.head(),
                         b'2a72d929692c41d8554c07f6301757ba18a65d91'])

    def test_merge_history(self):
        r = self._repo = open_repo('simple_merge.git')
        shas = [e.commit.id for e in r.get_walker()]
        self.assertEqual(shas, [b'5dac377bdded4c9aeb8dff595f0faeebcc8498cc',
                                b'ab64bbdcc51b170d21588e5c5d391ee5c0c96dfd',
                                b'4cffe90e0a41ad3f5190079d7c8f036bde29cbe6',
                                b'60dacdc733de308bb77bb76ce0fb0f9b44c9769e',
                                b'0d89f20333fbb1d2f3a94da77f4981373d8f4310'])

    def test_revision_history_missing_commit(self):
        r = self._repo = open_repo('simple_merge.git')
        warnings.simplefilter("ignore", DeprecationWarning)
        self.addCleanup(warnings.resetwarnings)
        self.assertRaises(errors.MissingCommitError, r.revision_history,
                          missing_sha)

    def test_out_of_order_merge(self):
        """Test that revision history is ordered by date, not parent order."""
        r = self._repo = open_repo('ooo_merge.git')
        shas = [e.commit.id for e in r.get_walker()]
        self.assertEqual(shas, [b'7601d7f6231db6a57f7bbb79ee52e4d462fd44d1',
                                b'f507291b64138b875c28e03469025b1ea20bc614',
                                b'fb5b0425c7ce46959bec94d54b9a157645e114f5',
                                b'f9e39b120c68182a4ba35349f832d0e4e61f485c'])

    def test_get_tags_empty(self):
        r = self._repo = open_repo('ooo_merge.git')
        self.assertEqual({}, r.refs.as_dict('refs/tags'))

    def test_get_config(self):
        r = self._repo = open_repo('ooo_merge.git')
        self.assertEqual({}, r.get_config())

    def test_common_revisions(self):
        """
        This test demonstrates that ``find_common_revisions()`` actually returns
        common heads, not revisions; dulwich already uses
        ``find_common_revisions()`` in such a manner (see
        ``Repo.fetch_objects()``).
        """

        expected_shas = set([b'60dacdc733de308bb77bb76ce0fb0f9b44c9769e'])

        # Source for objects.
        r_base = open_repo('simple_merge.git')

        # Re-create each-side of the merge in simple_merge.git.
        #
        # Since the trees and blobs are missing, the repository created is
        # corrupted, but we're only checking for commits for the purpose of this
        # test, so it's immaterial.
        r1_dir = tempfile.mkdtemp()
        r1_commits = [b'ab64bbdcc51b170d21588e5c5d391ee5c0c96dfd', # HEAD
                      b'60dacdc733de308bb77bb76ce0fb0f9b44c9769e',
                      b'0d89f20333fbb1d2f3a94da77f4981373d8f4310']

        r2_dir = tempfile.mkdtemp()
        r2_commits = [b'4cffe90e0a41ad3f5190079d7c8f036bde29cbe6', # HEAD
                      b'60dacdc733de308bb77bb76ce0fb0f9b44c9769e',
                      b'0d89f20333fbb1d2f3a94da77f4981373d8f4310']

        try:
            r1 = Repo.init_bare(r1_dir)
            list(map(lambda c: r1.object_store.add_object(r_base.get_object(c)), \
                r1_commits))
            r1.refs[b'HEAD'] = r1_commits[0]

            r2 = Repo.init_bare(r2_dir)
            list(map(lambda c: r2.object_store.add_object(r_base.get_object(c)), \
                r2_commits))
            r2.refs[b'HEAD'] = r2_commits[0]

            # Finally, the 'real' testing!
            shas = r2.object_store.find_common_revisions(r1.get_graph_walker())
            self.assertEqual(set(shas), expected_shas)

            shas = r1.object_store.find_common_revisions(r2.get_graph_walker())
            self.assertEqual(set(shas), expected_shas)
        finally:
            shutil.rmtree(r1_dir)
            shutil.rmtree(r2_dir)


class BuildRepoTests(TestCase):
    """Tests that build on-disk repos from scratch.

    Repos live in a temp dir and are torn down after each test. They start with
    a single commit in master having single file named 'a'.
    """

    def setUp(self):
        super(BuildRepoTests, self).setUp()
        repo_dir = os.path.join(tempfile.mkdtemp(), 'test')
        os.makedirs(repo_dir)
        r = self._repo = Repo.init(repo_dir)
        self.assertFalse(r.bare)
        self.assertEqual(b'ref: refs/heads/master', r.refs.read_ref(b'HEAD'))
        self.assertRaises(KeyError, lambda: r.refs[b'refs/heads/master'])

        with open(os.path.join(r.path, 'a'), 'wb') as f:
            f.write(b'file contents')
        r.stage(['a'])
        commit_sha = r.do_commit('msg',
                                 committer='Test Committer <test@nodomain.com>',
                                 author='Test Author <test@nodomain.com>',
                                 commit_timestamp=12345, commit_timezone=0,
                                 author_timestamp=12345, author_timezone=0)
        self.assertEqual([], r[commit_sha].parents)
        self._root_commit = commit_sha

    def tearDown(self):
        tear_down_repo(self._repo)
        super(BuildRepoTests, self).tearDown()

    def test_build_repo(self):
        r = self._repo
        self.assertEqual(b'ref: refs/heads/master', r.refs.read_ref(b'HEAD'))
        self.assertEqual(self._root_commit, r.refs['refs/heads/master'])
        expected_blob = objects.Blob.from_string('file contents')
        self.assertEqual(expected_blob.data, r[expected_blob.id].data)
        actual_commit = r[self._root_commit]
        self.assertEqual('msg', actual_commit.message)

    def test_commit_modified(self):
        r = self._repo
        f = open(os.path.join(r.path, 'a'), 'wb')
        try:
            f.write(b'new contents')
        finally:
            f.close()
        r.stage(['a'])
        commit_sha = r.do_commit('modified a',
                                 committer='Test Committer <test@nodomain.com>',
                                 author='Test Author <test@nodomain.com>',
                                 commit_timestamp=12395, commit_timezone=0,
                                 author_timestamp=12395, author_timezone=0)
        self.assertEqual([self._root_commit], r[commit_sha].parents)
        _, blob_id = tree_lookup_path(r.get_object, r[commit_sha].tree, 'a')
        self.assertEqual(b'new contents', r[blob_id].data)

    def test_commit_deleted(self):
        r = self._repo
        os.remove(os.path.join(r.path, 'a'))
        r.stage(['a'])
        commit_sha = r.do_commit('deleted a',
                                 committer='Test Committer <test@nodomain.com>',
                                 author='Test Author <test@nodomain.com>',
                                 commit_timestamp=12395, commit_timezone=0,
                                 author_timestamp=12395, author_timezone=0)
        self.assertEqual([self._root_commit], r[commit_sha].parents)
        self.assertEqual([], list(r.open_index()))
        tree = r[r[commit_sha].tree]
        self.assertEqual([], list(tree.items()))

    def test_commit_encoding(self):
        r = self._repo
        commit_sha = r.do_commit('commit with strange character \xee',
             committer='Test Committer <test@nodomain.com>',
             author='Test Author <test@nodomain.com>',
             commit_timestamp=12395, commit_timezone=0,
             author_timestamp=12395, author_timezone=0,
             encoding="iso8859-1")
        self.assertEqual("iso8859-1", r[commit_sha].encoding)

    def test_commit_fail_ref(self):
        r = self._repo

        def set_if_equals(name, old_ref, new_ref):
            return False
        r.refs.set_if_equals = set_if_equals

        def add_if_new(name, new_ref):
            self.fail('Unexpected call to add_if_new')
        r.refs.add_if_new = add_if_new

        old_shas = set(r.object_store)
        self.assertRaises(errors.CommitError, r.do_commit, 'failed commit',
                          committer='Test Committer <test@nodomain.com>',
                          author='Test Author <test@nodomain.com>',
                          commit_timestamp=12345, commit_timezone=0,
                          author_timestamp=12345, author_timezone=0)
        new_shas = set(r.object_store) - old_shas
        self.assertEqual(1, len(new_shas))
        # Check that the new commit (now garbage) was added.
        new_commit = r[new_shas.pop()]
        self.assertEqual(r[self._root_commit].tree, new_commit.tree)
        self.assertEqual('failed commit', new_commit.message)

    def test_commit_branch(self):
        r = self._repo

        commit_sha = r.do_commit('commit to branch',
             committer='Test Committer <test@nodomain.com>',
             author='Test Author <test@nodomain.com>',
             commit_timestamp=12395, commit_timezone=0,
             author_timestamp=12395, author_timezone=0,
             ref="refs/heads/new_branch")
        self.assertEqual(self._root_commit, r["HEAD"].id)
        self.assertEqual(commit_sha, r["refs/heads/new_branch"].id)
        self.assertEqual([], r[commit_sha].parents)
        self.assertTrue("refs/heads/new_branch" in r)

        new_branch_head = commit_sha

        commit_sha = r.do_commit('commit to branch 2',
             committer='Test Committer <test@nodomain.com>',
             author='Test Author <test@nodomain.com>',
             commit_timestamp=12395, commit_timezone=0,
             author_timestamp=12395, author_timezone=0,
             ref="refs/heads/new_branch")
        self.assertEqual(self._root_commit, r["HEAD"].id)
        self.assertEqual(commit_sha, r["refs/heads/new_branch"].id)
        self.assertEqual([new_branch_head], r[commit_sha].parents)

    def test_commit_merge_heads(self):
        r = self._repo
        merge_1 = r.do_commit('commit to branch 2',
             committer='Test Committer <test@nodomain.com>',
             author='Test Author <test@nodomain.com>',
             commit_timestamp=12395, commit_timezone=0,
             author_timestamp=12395, author_timezone=0,
             ref="refs/heads/new_branch")
        commit_sha = r.do_commit('commit with merge',
             committer='Test Committer <test@nodomain.com>',
             author='Test Author <test@nodomain.com>',
             commit_timestamp=12395, commit_timezone=0,
             author_timestamp=12395, author_timezone=0,
             merge_heads=[merge_1])
        self.assertEqual(
            [self._root_commit, merge_1],
            r[commit_sha].parents)

    def test_stage_deleted(self):
        r = self._repo
        os.remove(os.path.join(r.path, 'a'))
        r.stage(['a'])
        r.stage(['a'])  # double-stage a deleted path


class CheckRefFormatTests(TestCase):
    """Tests for the check_ref_format function.

    These are the same tests as in the git test suite.
    """

    def test_valid(self):
        self.assertTrue(check_ref_format(b'heads/foo'))
        self.assertTrue(check_ref_format(b'foo/bar/baz'))
        self.assertTrue(check_ref_format(b'refs///heads/foo'))
        self.assertTrue(check_ref_format(b'foo./bar'))
        self.assertTrue(check_ref_format(b'heads/foo@bar'))
        self.assertTrue(check_ref_format(b'heads/fix.lock.error'))

    def test_invalid(self):
        self.assertFalse(check_ref_format(b'foo'))
        self.assertFalse(check_ref_format(b'heads/foo/'))
        self.assertFalse(check_ref_format(b'./foo'))
        self.assertFalse(check_ref_format(b'.refs/foo'))
        self.assertFalse(check_ref_format(b'heads/foo..bar'))
        self.assertFalse(check_ref_format(b'heads/foo?bar'))
        self.assertFalse(check_ref_format(b'heads/foo.lock'))
        self.assertFalse(check_ref_format(b'heads/v@{ation'))
        self.assertFalse(check_ref_format(b'heads/foo\bar'))


ONES = b"1" * 40
TWOS = b"2" * 40
THREES = b"3" * 40
FOURS = b"4" * 40

class PackedRefsFileTests(TestCase):

    def test_split_ref_line_errors(self):
        self.assertRaises(errors.PackedRefsException, _split_ref_line,
                          b'singlefield')
        self.assertRaises(errors.PackedRefsException, _split_ref_line,
                          b'badsha name')
        self.assertRaises(errors.PackedRefsException, _split_ref_line,
                          ONES + b' bad/../refname')

    def test_read_without_peeled(self):
        f = BytesIO(b'# comment\n' + ONES + b' ref/1\n' + TWOS + b' ref/2')
        self.assertEqual([(ONES, b'ref/1'), (TWOS, b'ref/2')],
                         list(read_packed_refs(f)))

    def test_read_without_peeled_errors(self):
        f = BytesIO(ONES + b' ref/1\n^' + TWOS)
        self.assertRaises(errors.PackedRefsException, list, read_packed_refs(f))

    def test_read_with_peeled(self):
        f = BytesIO(ONES + b' ref/1\n' + TWOS + b' ref/2\n^' +
                    THREES + b'\n' + FOURS + b' ref/4')

        self.assertEqual([
          (ONES, b'ref/1', None),
          (TWOS, b'ref/2', THREES),
          (FOURS, b'ref/4', None),
          ], list(read_packed_refs_with_peeled(f)))

    def test_read_with_peeled_errors(self):
        f = BytesIO(b'^' + TWOS + b'\n' + ONES + b' ref/1')
        self.assertRaises(errors.PackedRefsException, list, read_packed_refs(f))

        f = BytesIO(ONES + b' ref/1\n^' + TWOS + b'\n^' + THREES)
        self.assertRaises(errors.PackedRefsException, list, read_packed_refs(f))

    def test_write_with_peeled(self):
        f = BytesIO()
        write_packed_refs(f, {b'ref/1': ONES, b'ref/2': TWOS},
                          {b'ref/1': THREES})
        self.assertEqual(
          b'# pack-refs with: peeled\n' + ONES + b' ref/1\n^' + 
          THREES + b'\n' + TWOS + b' ref/2\n', f.getvalue())

    def test_write_without_peeled(self):
        f = BytesIO()
        write_packed_refs(f, {b'ref/1': ONES, b'ref/2': TWOS})
        self.assertEqual(ONES + b' ref/1\n' + TWOS + b' ref/2\n', f.getvalue())


# Dict of refs that we expect all RefsContainerTests subclasses to define.
_TEST_REFS = {
  b'HEAD': b'42d06bd4b77fed026b154d16493e5deab78f02ec',
  b'refs/heads/master': b'42d06bd4b77fed026b154d16493e5deab78f02ec',
  b'refs/heads/packed': b'42d06bd4b77fed026b154d16493e5deab78f02ec',
  b'refs/tags/refs-0.1': b'df6800012397fb85c56e7418dd4eb9405dee075c',
  b'refs/tags/refs-0.2': b'3ec9c43c84ff242e3ef4a9fc5bc111fd780a76a8',
  }


class RefsContainerTests(object):

    def test_keys(self):
        actual_keys = set(self._refs.keys())
        self.assertEqual(set(self._refs.allkeys()), actual_keys)
        # ignore the symref loop if it exists
        actual_keys.discard(b'refs/heads/loop')
        self.assertEqual(set(_TEST_REFS.keys()), actual_keys)

        actual_keys = self._refs.keys(b'refs/heads')
        actual_keys.discard(b'loop')
        self.assertEqual([b'master', b'packed'], sorted(actual_keys))
        self.assertEqual([b'refs-0.1', b'refs-0.2'],
                         sorted(self._refs.keys(b'refs/tags')))

    def test_as_dict(self):
        # refs/heads/loop does not show up even if it exists
        self.assertEqual(_TEST_REFS, self._refs.as_dict())

    def test_setitem(self):
        self._refs[b'refs/some/ref'] = b'42d06bd4b77fed026b154d16493e5deab78f02ec'
        self.assertEqual(b'42d06bd4b77fed026b154d16493e5deab78f02ec',
                         self._refs[b'refs/some/ref'])
        self.assertRaises(errors.RefFormatError, self._refs.__setitem__,
                          b'notrefs/foo', b'42d06bd4b77fed026b154d16493e5deab78f02ec')

    def test_set_if_equals(self):
        nines = b'9' * 40
        self.assertFalse(self._refs.set_if_equals(b'HEAD', b'c0ffee', nines))
        self.assertEqual(b'42d06bd4b77fed026b154d16493e5deab78f02ec',
                         self._refs[b'HEAD'])

        self.assertTrue(self._refs.set_if_equals(
          b'HEAD', b'42d06bd4b77fed026b154d16493e5deab78f02ec', nines))
        self.assertEqual(nines, self._refs[b'HEAD'])

        self.assertTrue(self._refs.set_if_equals(b'refs/heads/master', None,
                                                 nines))
        self.assertEqual(nines, self._refs[b'refs/heads/master'])

    def test_add_if_new(self):
        nines = b'9' * 40
        self.assertFalse(self._refs.add_if_new(b'refs/heads/master', nines))
        self.assertEqual(b'42d06bd4b77fed026b154d16493e5deab78f02ec',
                         self._refs[b'refs/heads/master'])

        self.assertTrue(self._refs.add_if_new(b'refs/some/ref', nines))
        self.assertEqual(nines, self._refs[b'refs/some/ref'])

    def test_set_symbolic_ref(self):
        self._refs.set_symbolic_ref(b'refs/heads/symbolic', b'refs/heads/master')
        self.assertEqual(b'ref: refs/heads/master',
                         self._refs.read_loose_ref(b'refs/heads/symbolic'))
        self.assertEqual(b'42d06bd4b77fed026b154d16493e5deab78f02ec',
                         self._refs[b'refs/heads/symbolic'])

    def test_set_symbolic_ref_overwrite(self):
        nines = b'9' * 40
        self.assertFalse(b'refs/heads/symbolic' in self._refs)
        self._refs[b'refs/heads/symbolic'] = nines
        self.assertEqual(nines, self._refs.read_loose_ref(b'refs/heads/symbolic'))
        self._refs.set_symbolic_ref(b'refs/heads/symbolic', b'refs/heads/master')
        self.assertEqual(b'ref: refs/heads/master',
                         self._refs.read_loose_ref(b'refs/heads/symbolic'))
        self.assertEqual(b'42d06bd4b77fed026b154d16493e5deab78f02ec',
                         self._refs[b'refs/heads/symbolic'])

    def test_check_refname(self):
        self._refs._check_refname(b'HEAD')
        self._refs._check_refname(b'refs/stash')
        self._refs._check_refname(b'refs/heads/foo')

        self.assertRaises(errors.RefFormatError, self._refs._check_refname,
                          b'refs')
        self.assertRaises(errors.RefFormatError, self._refs._check_refname,
                          b'notrefs/foo')

    def test_contains(self):
        self.assertTrue('refs/heads/master' in self._refs)
        self.assertFalse('refs/heads/bar' in self._refs)

    def test_delitem(self):
        self.assertEqual(b'42d06bd4b77fed026b154d16493e5deab78f02ec',
                          self._refs[b'refs/heads/master'])
        del self._refs['refs/heads/master']
        self.assertRaises(KeyError, lambda: self._refs['refs/heads/master'])

    def test_remove_if_equals(self):
        self.assertFalse(self._refs.remove_if_equals('HEAD', 'c0ffee'))
        self.assertEqual(b'42d06bd4b77fed026b154d16493e5deab78f02ec',
                         self._refs[b'HEAD'])
        self.assertTrue(self._refs.remove_if_equals(
          b'refs/tags/refs-0.2', b'3ec9c43c84ff242e3ef4a9fc5bc111fd780a76a8'))
        self.assertFalse(b'refs/tags/refs-0.2' in self._refs)


class DictRefsContainerTests(RefsContainerTests, TestCase):

    def setUp(self):
        TestCase.setUp(self)
        self._refs = DictRefsContainer(dict(_TEST_REFS))

    def test_invalid_refname(self):
        # FIXME: Move this test into RefsContainerTests, but requires
        # some way of injecting invalid refs.
        self._refs._refs[b"refs/stash"] = b"00" * 20
        expected_refs = dict(_TEST_REFS)
        expected_refs[b"refs/stash"] = b"00" * 20
        self.assertEqual(expected_refs, self._refs.as_dict())


class DiskRefsContainerTests(RefsContainerTests, TestCase):

    def setUp(self):
        TestCase.setUp(self)
        self._repo = open_repo('refs.git')
        self._refs = self._repo.refs

    def tearDown(self):
        tear_down_repo(self._repo)
        TestCase.tearDown(self)

    def test_get_packed_refs(self):
        self.assertEqual({
          b'refs/heads/packed': b'42d06bd4b77fed026b154d16493e5deab78f02ec',
          b'refs/tags/refs-0.1': b'df6800012397fb85c56e7418dd4eb9405dee075c',
          }, self._refs.get_packed_refs())

    def test_get_peeled_not_packed(self):
        # not packed
        self.assertEqual(None, self._refs.get_peeled(b'refs/tags/refs-0.2'))
        self.assertEqual(b'3ec9c43c84ff242e3ef4a9fc5bc111fd780a76a8',
                         self._refs[b'refs/tags/refs-0.2'])

        # packed, known not peelable
        self.assertEqual(self._refs[b'refs/heads/packed'],
                         self._refs.get_peeled(b'refs/heads/packed'))

        # packed, peeled
        self.assertEqual(b'42d06bd4b77fed026b154d16493e5deab78f02ec',
                         self._refs.get_peeled(b'refs/tags/refs-0.1'))

    def test_setitem(self):
        RefsContainerTests.test_setitem(self)
        f = open(os.path.join(self._refs.path, 'refs', 'some', 'ref'), 'rb')
        self.assertEqual(b'42d06bd4b77fed026b154d16493e5deab78f02ec',
                          f.read()[:40])
        f.close()

    def test_setitem_symbolic(self):
        ones = b'1' * 40
        self._refs[b'HEAD'] = ones
        self.assertEqual(ones, self._refs[b'HEAD'])

        # ensure HEAD was not modified
        f = open(os.path.join(self._refs.path, 'HEAD'), 'rb')
        self.assertEqual(b'ref: refs/heads/master', iter(f).__next__().rstrip(b'\n'))
        f.close()

        # ensure the symbolic link was written through
        f = open(os.path.join(self._refs.path, 'refs', 'heads', 'master'), 'rb')
        self.assertEqual(ones, f.read()[:40])
        f.close()

    def test_set_if_equals(self):
        RefsContainerTests.test_set_if_equals(self)

        # ensure symref was followed
        self.assertEqual(b'9' * 40, self._refs[b'refs/heads/master'])

        # ensure lockfile was deleted
        self.assertFalse(os.path.exists(
          os.path.join(self._refs.path, 'refs', 'heads', 'master.lock')))
        self.assertFalse(os.path.exists(
          os.path.join(self._refs.path, 'HEAD.lock')))

    def test_add_if_new_packed(self):
        # don't overwrite packed ref
        self.assertFalse(self._refs.add_if_new(b'refs/tags/refs-0.1', b'9' * 40))
        self.assertEqual(b'df6800012397fb85c56e7418dd4eb9405dee075c',
                         self._refs[b'refs/tags/refs-0.1'])

    def test_add_if_new_symbolic(self):
        # Use an empty repo instead of the default.
        tear_down_repo(self._repo)
        repo_dir = os.path.join(tempfile.mkdtemp(), 'test')
        os.makedirs(repo_dir)
        self._repo = Repo.init(repo_dir)
        refs = self._repo.refs

        nines = b'9' * 40
        self.assertEqual(b'ref: refs/heads/master', refs.read_ref(b'HEAD'))
        self.assertFalse(b'refs/heads/master' in refs)
        self.assertTrue(refs.add_if_new(b'HEAD', nines))
        self.assertEqual(b'ref: refs/heads/master', refs.read_ref(b'HEAD'))
        self.assertEqual(nines, refs[b'HEAD'])
        self.assertEqual(nines, refs[b'refs/heads/master'])
        self.assertFalse(refs.add_if_new(b'HEAD', b'1' * 40))
        self.assertEqual(nines, refs[b'HEAD'])
        self.assertEqual(nines, refs[b'refs/heads/master'])

    def test_follow(self):
        self.assertEqual(
          (b'refs/heads/master', b'42d06bd4b77fed026b154d16493e5deab78f02ec'),
          self._refs._follow(b'HEAD'))
        self.assertEqual(
          (b'refs/heads/master', b'42d06bd4b77fed026b154d16493e5deab78f02ec'),
          self._refs._follow(b'refs/heads/master'))
        self.assertRaises(KeyError, self._refs._follow, b'refs/heads/loop')

    def test_delitem(self):
        RefsContainerTests.test_delitem(self)
        ref_file = os.path.join(self._refs.path, 'refs', 'heads', 'master')
        self.assertFalse(os.path.exists(ref_file))
        self.assertFalse('refs/heads/master' in self._refs.get_packed_refs())

    def test_delitem_symbolic(self):
        self.assertEqual(b'ref: refs/heads/master',
                          self._refs.read_loose_ref(b'HEAD'))
        del self._refs[b'HEAD']
        self.assertRaises(KeyError, lambda: self._refs[b'HEAD'])
        self.assertEqual(b'42d06bd4b77fed026b154d16493e5deab78f02ec',
                         self._refs[b'refs/heads/master'])
        self.assertFalse(os.path.exists(os.path.join(self._refs.path, 'HEAD')))

    def test_remove_if_equals_symref(self):
        # HEAD is a symref, so shouldn't equal its dereferenced value
        self.assertFalse(self._refs.remove_if_equals(
          b'HEAD', b'42d06bd4b77fed026b154d16493e5deab78f02ec'))
        self.assertTrue(self._refs.remove_if_equals(
          b'refs/heads/master', b'42d06bd4b77fed026b154d16493e5deab78f02ec'))
        self.assertRaises(KeyError, lambda: self._refs[b'refs/heads/master'])

        # HEAD is now a broken symref
        self.assertRaises(KeyError, lambda: self._refs[b'HEAD'])
        self.assertEqual(b'ref: refs/heads/master',
                          self._refs.read_loose_ref(b'HEAD'))

        self.assertFalse(os.path.exists(
            os.path.join(self._refs.path, 'refs', 'heads', 'master.lock')))
        self.assertFalse(os.path.exists(
            os.path.join(self._refs.path, 'HEAD.lock')))

    def test_remove_packed_without_peeled(self):
        refs_file = os.path.join(self._repo.path, 'packed-refs')
        with GitFile(refs_file) as f:
            refs_data = f.read()
        with GitFile(refs_file, 'wb') as f:
            f.write(b'\n'.join(l for l in refs_data.split(b'\n')
                               if not l or l[0] not in b'#^'))
        self._repo = Repo(self._repo.path)
        refs = self._repo.refs
        self.assertTrue(refs.remove_if_equals(
          b'refs/heads/packed', b'42d06bd4b77fed026b154d16493e5deab78f02ec'))

    def test_remove_if_equals_packed(self):
        # test removing ref that is only packed
        self.assertEqual(b'df6800012397fb85c56e7418dd4eb9405dee075c',
                         self._refs[b'refs/tags/refs-0.1'])
        self.assertTrue(
          self._refs.remove_if_equals(b'refs/tags/refs-0.1',
          b'df6800012397fb85c56e7418dd4eb9405dee075c'))
        self.assertRaises(KeyError, lambda: self._refs[b'refs/tags/refs-0.1'])

    def test_read_ref(self):
        self.assertEqual(b'ref: refs/heads/master', self._refs.read_ref(b"HEAD"))
        self.assertEqual(b'42d06bd4b77fed026b154d16493e5deab78f02ec',
            self._refs.read_ref(b"refs/heads/packed"))
        self.assertEqual(None,
            self._refs.read_ref(b"nonexistant"))
