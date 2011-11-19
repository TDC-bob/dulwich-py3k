# test_server.py -- Tests for the git server
# Copyright (C) 2010 Google, Inc.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; version 2
# or (at your option) any later version of the License.
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

"""Tests for the smart protocol server."""

from io import BytesIO
import os
import tempfile

from dulwich.errors import (
    GitProtocolError,
    NotGitRepository,
    UnexpectedCommandError,
    )
from dulwich.repo import (
    MemoryRepo,
    Repo,
    )
from dulwich.server import (
    Backend,
    DictBackend,
    FileSystemBackend,
    Handler,
    MultiAckGraphWalkerImpl,
    MultiAckDetailedGraphWalkerImpl,
    _split_proto_line,
    serve_command,
    ProtocolGraphWalker,
    ReceivePackHandler,
    SingleAckGraphWalkerImpl,
    UploadPackHandler,
    )
from dulwich.tests import TestCase
from dulwich.tests.utils import (
    make_commit,
    )
from dulwich.objects import (
    Sha1Sum,
)
from dulwich.py3k import *

ONE = Sha1Sum('1' * 40)
TWO = Sha1Sum('2' * 40)
THREE = Sha1Sum('3' * 40)
FOUR = Sha1Sum('4' * 40)
FIVE = Sha1Sum('5' * 40)
SIX = Sha1Sum('6' * 40)


class TestProto(object):

    def __init__(self):
        self._output = []
        self._received = {0: [], 1: [], 2: [], 3: []}

    def set_output(self, output_lines):
        self._output = [line.rstrip() + b'\n' for line in output_lines]

    def read_pkt_line(self):
        if self._output:
            return self._output.pop(0)
        else:
            return None

    def write_sideband(self, band, data):
        self._received[band].append(data)

    def write_pkt_line(self, data):
        self._received[0].append(data)

    def get_received_line(self, band=0):
        lines = self._received[band]
        return lines.pop(0)


class TestGenericHandler(Handler):

    def __init__(self):
        Handler.__init__(self, Backend(), None)

    @classmethod
    def capabilities(cls):
        return (b'cap1', b'cap2', b'cap3')

    @classmethod
    def required_capabilities(cls):
        return (b'cap2',)


class HandlerTestCase(TestCase):

    def setUp(self):
        super(HandlerTestCase, self).setUp()
        self._handler = TestGenericHandler()

    def assertSucceeds(self, func, *args, **kwargs):
        try:
            func(*args, **kwargs)
        except GitProtocolError as e:
            self.fail(e)

    def test_capability_line(self):
        self.assertEqual(b'cap1 cap2 cap3', self._handler.capability_line())

    def test_set_client_capabilities(self):
        set_caps = self._handler.set_client_capabilities
        self.assertSucceeds(set_caps, [b'cap2'])
        self.assertSucceeds(set_caps, [b'cap1', b'cap2'])

        # different order
        self.assertSucceeds(set_caps, [b'cap3', b'cap1', b'cap2'])

        # error cases
        self.assertRaises(GitProtocolError, set_caps, [b'capxxx', b'cap2'])
        self.assertRaises(GitProtocolError, set_caps, [b'cap1', b'cap3'])

        # ignore innocuous but unknown capabilities
        self.assertRaises(GitProtocolError, set_caps, [b'cap2', b'ignoreme'])
        self.assertFalse(b'ignoreme' in self._handler.capabilities())
        self._handler.innocuous_capabilities = lambda: (b'ignoreme',)
        self.assertSucceeds(set_caps, [b'cap2', b'ignoreme'])

    def test_has_capability(self):
        self.assertRaises(GitProtocolError, self._handler.has_capability, b'cap')
        caps = self._handler.capabilities()
        self._handler.set_client_capabilities(caps)
        for cap in caps:
            self.assertTrue(self._handler.has_capability(cap))
        self.assertFalse(self._handler.has_capability(b'capxxx'))


class UploadPackHandlerTestCase(TestCase):

    def setUp(self):
        super(UploadPackHandlerTestCase, self).setUp()
        self._repo = MemoryRepo.init_bare([], {})
        backend = DictBackend({'/': self._repo})
        self._handler = UploadPackHandler(
          backend, ['/', 'host=lolcathost'], TestProto())

    def test_progress(self):
        caps = self._handler.required_capabilities()
        self._handler.set_client_capabilities(caps)
        self._handler.progress(b'first message')
        self._handler.progress(b'second message')
        self.assertEqual(b'first message',
                         self._handler.proto.get_received_line(2))
        self.assertEqual(b'second message',
                         self._handler.proto.get_received_line(2))
        self.assertRaises(IndexError, self._handler.proto.get_received_line, 2)

    def test_no_progress(self):
        caps = list(self._handler.required_capabilities()) + [b'no-progress']
        self._handler.set_client_capabilities(caps)
        self._handler.progress(b'first message')
        self._handler.progress(b'second message')
        self.assertRaises(IndexError, self._handler.proto.get_received_line, 2)

    def test_get_tagged(self):
        refs = {
            b'refs/tags/tag1': ONE,
            b'refs/tags/tag2': TWO,
            b'refs/heads/master': FOUR,  # not a tag, no peeled value
            }
        # repo needs to peel this object
        self._repo.object_store.add_object(make_commit(id=FOUR))
        self._repo.refs._update(refs)
        peeled = {
            b'refs/tags/tag1': Sha1Sum('1234' * 10),
            b'refs/tags/tag2': Sha1Sum('5678' * 10),
            }
        self._repo.refs._update_peeled(peeled)

        caps = list(self._handler.required_capabilities()) + [b'include-tag']
        self._handler.set_client_capabilities(caps)
        self.assertEqual({Sha1Sum('1234' * 10): ONE, Sha1Sum('5678' * 10): TWO},
                          self._handler.get_tagged(refs, repo=self._repo))

        # non-include-tag case
        caps = self._handler.required_capabilities()
        self._handler.set_client_capabilities(caps)
        self.assertEqual({}, self._handler.get_tagged(refs, repo=self._repo))


class TestUploadPackHandler(UploadPackHandler):
    @classmethod
    def required_capabilities(self):
        return ()


class ProtocolGraphWalkerTestCase(TestCase):

    def setUp(self):
        super(ProtocolGraphWalkerTestCase, self).setUp()
        # Create the following commit tree:
        #   3---5
        #  /
        # 1---2---4
        commits = [
          make_commit(id=ONE, parents=[], commit_time=111),
          make_commit(id=TWO, parents=[ONE], commit_time=222),
          make_commit(id=THREE, parents=[ONE], commit_time=333),
          make_commit(id=FOUR, parents=[TWO], commit_time=444),
          make_commit(id=FIVE, parents=[THREE], commit_time=555),
          ]
        self._repo = MemoryRepo.init_bare(commits, {})
        backend = DictBackend({'/': self._repo})
        self._walker = ProtocolGraphWalker(
            TestUploadPackHandler(backend, ['/', 'host=lolcats'], TestProto()),
            self._repo.object_store, self._repo.get_peeled)

    def test_is_satisfied_no_haves(self):
        self.assertFalse(self._walker._is_satisfied([], ONE, 0))
        self.assertFalse(self._walker._is_satisfied([], TWO, 0))
        self.assertFalse(self._walker._is_satisfied([], THREE, 0))

    def test_is_satisfied_have_root(self):
        self.assertTrue(self._walker._is_satisfied([ONE], ONE, 0))
        self.assertTrue(self._walker._is_satisfied([ONE], TWO, 0))
        self.assertTrue(self._walker._is_satisfied([ONE], THREE, 0))

    def test_is_satisfied_have_branch(self):
        self.assertTrue(self._walker._is_satisfied([TWO], TWO, 0))
        # wrong branch
        self.assertFalse(self._walker._is_satisfied([TWO], THREE, 0))

    def test_all_wants_satisfied(self):
        self._walker.set_wants([FOUR, FIVE])
        # trivial case: wants == haves
        self.assertTrue(self._walker.all_wants_satisfied([FOUR, FIVE]))
        # cases that require walking the commit tree
        self.assertTrue(self._walker.all_wants_satisfied([ONE]))
        self.assertFalse(self._walker.all_wants_satisfied([TWO]))
        self.assertFalse(self._walker.all_wants_satisfied([THREE]))
        self.assertTrue(self._walker.all_wants_satisfied([TWO, THREE]))

    def test_split_proto_line(self):
        allowed = (b'want', b'done', None)
        self.assertEqual((b'want', ONE),
                          _split_proto_line(b'want ' + ONE.hex_bytes + b'\n', allowed))
        self.assertEqual((b'want', TWO),
                          _split_proto_line(b'want ' + TWO.hex_bytes + b'\n', allowed))
        self.assertRaises(GitProtocolError, _split_proto_line,
                          b'want xxxx\n', allowed)
        self.assertRaises(UnexpectedCommandError, _split_proto_line,
                          b'have ' + THREE.hex_bytes + b'\n', allowed)
        self.assertRaises(GitProtocolError, _split_proto_line,
                          b'foo ' + FOUR.hex_bytes + b'\n', allowed)
        self.assertRaises(GitProtocolError, _split_proto_line, b'bar', allowed)
        self.assertEqual((b'done', None), _split_proto_line(b'done\n', allowed))
        self.assertEqual((None, None), _split_proto_line('', allowed))

    def test_determine_wants(self):
        self.assertEqual(None, self._walker.determine_wants({}))
        self.assertEqual(None, self._walker.proto.get_received_line())

        self._walker.proto.set_output([
          b'want ' + ONE.hex_bytes + b' multi_ack',
          b'want ' + TWO.hex_bytes,
          ])
        heads = {
          b'refs/heads/ref1': ONE,
          b'refs/heads/ref2': TWO,
          b'refs/heads/ref3': THREE,
          }
        self._repo.refs._update(heads)
        self.assertEqual([ONE, TWO], self._walker.determine_wants(heads))

        self._walker.proto.set_output([b'want ' + FOUR.hex_bytes + b' multi_ack'])
        self.assertRaises(GitProtocolError, self._walker.determine_wants, heads)

        self._walker.proto.set_output([])
        self.assertEqual([], self._walker.determine_wants(heads))

        self._walker.proto.set_output([b'want ' + ONE.hex_bytes + b' multi_ack', b'foo'])
        self.assertRaises(GitProtocolError, self._walker.determine_wants, heads)

        self._walker.proto.set_output([b'want ' + FOUR.hex_bytes + b' multi_ack'])
        self.assertRaises(GitProtocolError, self._walker.determine_wants, heads)

    def test_determine_wants_advertisement(self):
        self._walker.proto.set_output([])
        # advertise branch tips plus tag
        heads = {
          b'refs/heads/ref4': FOUR,
          b'refs/heads/ref5': FIVE,
          b'refs/heads/tag6': SIX,
          }
        self._repo.refs._update(heads)
        self._repo.refs._update_peeled(heads)
        self._repo.refs._update_peeled({b'refs/heads/tag6': FIVE})
        self._walker.determine_wants(heads)
        lines = []
        while True:
            line = self._walker.proto.get_received_line()
            if line is None:
                break
            # strip capabilities list if present
            if b'\x00' in line:
                line = line[:line.index(b'\x00')]
            lines.append(line.rstrip())

        self.assertEqual([
          FOUR.hex_bytes + b' refs/heads/ref4',
          FIVE.hex_bytes + b' refs/heads/ref5',
          FIVE.hex_bytes + b' refs/heads/tag6^{}',
          SIX.hex_bytes + b' refs/heads/tag6',
          ], sorted(lines))

        # ensure peeled tag was advertised immediately following tag
        for i, line in enumerate(lines):
            if line.endswith(b' refs/heads/tag6'):
                self.assertEqual(FIVE.hex_bytes + b' refs/heads/tag6^{}', lines[i+1])

    # TODO: test commit time cutoff


class TestProtocolGraphWalker(object):

    def __init__(self):
        self.acks = []
        self.lines = []
        self.done = False
        self.http_req = None
        self.advertise_refs = False

    def read_proto_line(self, allowed):
        command, sha = self.lines.pop(0)
        if allowed is not None:
            assert command in allowed
        return command, sha

    def send_ack(self, sha, ack_type=''):
        self.acks.append((sha, ack_type))

    def send_nak(self):
        self.acks.append((None, 'nak'))

    def all_wants_satisfied(self, haves):
        return self.done

    def pop_ack(self):
        if not self.acks:
            return None
        return self.acks.pop(0)


class AckGraphWalkerImplTestCase(TestCase):
    """Base setup and asserts for AckGraphWalker tests."""

    def setUp(self):
        super(AckGraphWalkerImplTestCase, self).setUp()
        self._walker = TestProtocolGraphWalker()
        self._walker.lines = [
          (b'have', TWO),
          (b'have', ONE),
          (b'have', THREE),
          (b'done', None),
          ]
        self._impl = self.impl_cls(self._walker)

    def assertNoAck(self):
        self.assertEqual(None, self._walker.pop_ack())

    def assertAcks(self, acks):
        for sha, ack_type in acks:
            self.assertEqual((sha, ack_type), self._walker.pop_ack())
        self.assertNoAck()

    def assertAck(self, sha, ack_type=''):
        self.assertAcks([(sha, ack_type)])

    def assertNak(self):
        self.assertAck(None, 'nak')

    def assertNextEquals(self, sha):
        self.assertEqual(sha, next(self._impl))


class SingleAckGraphWalkerImplTestCase(AckGraphWalkerImplTestCase):

    impl_cls = SingleAckGraphWalkerImpl

    def test_single_ack(self):
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self._walker.done = True
        self._impl.ack(ONE)
        self.assertAck(ONE)

        self.assertNextEquals(THREE)
        self._impl.ack(THREE)
        self.assertNoAck()

        self.assertNextEquals(None)
        self.assertNoAck()

    def test_single_ack_flush(self):
        # same as ack test but ends with a flush-pkt instead of done
        self._walker.lines[-1] = (None, None)

        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self._walker.done = True
        self._impl.ack(ONE)
        self.assertAck(ONE)

        self.assertNextEquals(THREE)
        self.assertNoAck()

        self.assertNextEquals(None)
        self.assertNoAck()

    def test_single_ack_nak(self):
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self.assertNoAck()

        self.assertNextEquals(THREE)
        self.assertNoAck()

        self.assertNextEquals(None)
        self.assertNak()

    def test_single_ack_nak_flush(self):
        # same as nak test but ends with a flush-pkt instead of done
        self._walker.lines[-1] = (None, None)

        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self.assertNoAck()

        self.assertNextEquals(THREE)
        self.assertNoAck()

        self.assertNextEquals(None)
        self.assertNak()


class MultiAckGraphWalkerImplTestCase(AckGraphWalkerImplTestCase):

    impl_cls = MultiAckGraphWalkerImpl

    def test_multi_ack(self):
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self._walker.done = True
        self._impl.ack(ONE)
        self.assertAck(ONE, b'continue')

        self.assertNextEquals(THREE)
        self._impl.ack(THREE)
        self.assertAck(THREE, b'continue')

        self.assertNextEquals(None)
        self.assertAck(THREE)

    def test_multi_ack_partial(self):
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self._impl.ack(ONE)
        self.assertAck(ONE, b'continue')

        self.assertNextEquals(THREE)
        self.assertNoAck()

        self.assertNextEquals(None)
        # done, re-send ack of last common
        self.assertAck(ONE)

    def test_multi_ack_flush(self):
        self._walker.lines = [
          (b'have', TWO),
          (None, None),
          (b'have', ONE),
          (b'have', THREE),
          (b'done', None),
          ]
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self.assertNak()  # nak the flush-pkt

        self._walker.done = True
        self._impl.ack(ONE)
        self.assertAck(ONE, b'continue')

        self.assertNextEquals(THREE)
        self._impl.ack(THREE)
        self.assertAck(THREE, b'continue')

        self.assertNextEquals(None)
        self.assertAck(THREE)

    def test_multi_ack_nak(self):
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self.assertNoAck()

        self.assertNextEquals(THREE)
        self.assertNoAck()

        self.assertNextEquals(None)
        self.assertNak()


class MultiAckDetailedGraphWalkerImplTestCase(AckGraphWalkerImplTestCase):

    impl_cls = MultiAckDetailedGraphWalkerImpl

    def test_multi_ack(self):
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self._walker.done = True
        self._impl.ack(ONE)
        self.assertAcks([(ONE, b'common'), (ONE, b'ready')])

        self.assertNextEquals(THREE)
        self._impl.ack(THREE)
        self.assertAck(THREE, b'ready')

        self.assertNextEquals(None)
        self.assertAck(THREE)

    def test_multi_ack_partial(self):
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self._impl.ack(ONE)
        self.assertAck(ONE, b'common')

        self.assertNextEquals(THREE)
        self.assertNoAck()

        self.assertNextEquals(None)
        # done, re-send ack of last common
        self.assertAck(ONE)

    def test_multi_ack_flush(self):
        # same as ack test but contains a flush-pkt in the middle
        self._walker.lines = [
          (b'have', TWO),
          (None, None),
          (b'have', ONE),
          (b'have', THREE),
          (b'done', None),
          ]
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self.assertNak()  # nak the flush-pkt

        self._walker.done = True
        self._impl.ack(ONE)
        self.assertAcks([(ONE, b'common'), (ONE, b'ready')])

        self.assertNextEquals(THREE)
        self._impl.ack(THREE)
        self.assertAck(THREE, b'ready')

        self.assertNextEquals(None)
        self.assertAck(THREE)

    def test_multi_ack_nak(self):
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self.assertNoAck()

        self.assertNextEquals(THREE)
        self.assertNoAck()

        self.assertNextEquals(None)
        self.assertNak()

    def test_multi_ack_nak_flush(self):
        # same as nak test but contains a flush-pkt in the middle
        self._walker.lines = [
          (b'have', TWO),
          (None, None),
          (b'have', ONE),
          (b'have', THREE),
          (b'done', None),
          ]
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self.assertNak()

        self.assertNextEquals(THREE)
        self.assertNoAck()

        self.assertNextEquals(None)
        self.assertNak()

    def test_multi_ack_stateless(self):
        # transmission ends with a flush-pkt
        self._walker.lines[-1] = (None, None)
        self._walker.http_req = True

        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self.assertNoAck()

        self.assertNextEquals(THREE)
        self.assertNoAck()

        self.assertNextEquals(None)
        self.assertNak()


class FileSystemBackendTests(TestCase):
    """Tests for FileSystemBackend."""

    def setUp(self):
        super(FileSystemBackendTests, self).setUp()
        self.path = tempfile.mkdtemp()
        self.repo = Repo.init(self.path)
        self.backend = FileSystemBackend()

    def test_nonexistant(self):
        self.assertRaises(NotGitRepository,
            self.backend.open_repository, "/does/not/exist/unless/foo")

    def test_absolute(self):
        repo = self.backend.open_repository(self.path)
        self.assertEqual(repo.path, self.repo.path)

    def test_child(self):
        self.assertRaises(NotGitRepository,
            self.backend.open_repository, os.path.join(self.path, "foo"))


class ServeCommandTests(TestCase):
    """Tests for serve_command."""

    def setUp(self):
        super(ServeCommandTests, self).setUp()
        self.backend = DictBackend({})

    def serve_command(self, handler_cls, args, inf, outf):
        return serve_command(handler_cls, [b"test"] + args, backend=self.backend,
            inf=inf, outf=outf)

    def test_receive_pack(self):
        commit = make_commit(id=ONE, parents=[], commit_time=111)
        self.backend.repos['/'] = MemoryRepo.init_bare(
            [commit], {b'refs/heads/master': commit.id})
        outf = BytesIO()
        exitcode = self.serve_command(ReceivePackHandler, ['/'], BytesIO(b'0000'), outf)
        outlines = outf.getvalue().splitlines()

        self.assertEqual(2, len(outlines))
        self.assertEqual(b"1111111111111111111111111111111111111111 refs/heads/master",
            outlines[0][4:].split(b"\x00")[0])
        self.assertEqual(b"0000", outlines[-1])
        self.assertEqual(0, exitcode)
