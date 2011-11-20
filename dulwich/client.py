# client.py -- Implementation of the server side git protocols
# Copyright (C) 2008-2009 Jelmer Vernooij <jelmer@samba.org>
# Copyright (C) 2008 John Carr
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# or (at your option) a later version of the License.
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

"""Client side support for the Git protocol."""

__docformat__ = 'restructuredText'

from io import BytesIO
import select
import socket
import subprocess
import urllib.request, urllib.error, urllib.parse
import urllib.parse

from dulwich.errors import (
    GitProtocolError,
    NotGitRepository,
    SendPackError,
    UpdateRefsError,
    )
from dulwich.protocol import (
    _RBUFSIZE,
    PktLineParser,
    Protocol,
    TCP_GIT_PORT,
    ZERO_SHA,
    extract_capabilities,
    )
from dulwich.pack import (
    write_pack_objects,
    )
from dulwich.objects import (
    Sha1Sum
)
from dulwich.py3k import *

# Python 2.6.6 included these in urlparse.uses_netloc upstream. Do
# monkeypatching to enable similar behaviour in earlier Pythons:
for scheme in ('git', 'git+ssh'):
    if scheme not in urllib.parse.uses_netloc:
        urllib.parse.uses_netloc.append(scheme)

def _fileno_can_read(fileno):
    """Check if a file descriptor is readable."""
    return len(select.select([fileno], [], [], 0)[0]) > 0

COMMON_CAPABILITIES = [b'ofs-delta', b'side-band-64k']
FETCH_CAPABILITIES = [b'multi_ack', b'multi_ack_detailed'] + COMMON_CAPABILITIES
SEND_CAPABILITIES = [b'report-status'] + COMMON_CAPABILITIES


class ReportStatusParser(object):
    """Handle status as reported by servers with the 'report-status' capability.
    """

    def __init__(self):
        self._done = False
        self._pack_status = None
        self._ref_status_ok = True
        self._ref_statuses = []

    def check(self):
        """Check if there were any errors and, if so, raise exceptions.

        :raise SendPackError: Raised when the server could not unpack
        :raise UpdateRefsError: Raised when refs could not be updated
        """
        if self._pack_status not in (b'unpack ok', None):
            raise SendPackError(self._pack_status)
        if not self._ref_status_ok:
            ref_status = {}
            ok = set()
            for status in self._ref_statuses:
                if b' ' not in status:
                    # malformed response, move on to the next one
                    continue
                status, ref = status.split(b' ', 1)

                if status == b'ng':
                    if b' ' in ref:
                        ref, status = ref.split(b' ', 1)
                else:
                    ok.add(ref)
                ref_status[ref] = status
            raise UpdateRefsError('%s failed to update' %
                                  ', '.join([convert3kstr(ref, STRING) for ref in ref_status
                                             if ref not in ok]),
                                  ref_status=ref_status)

    def handle_packet(self, pkt):
        """Handle a packet.

        :raise GitProtocolError: Raised when packets are received after a
            flush packet.
        """
        if self._done:
            raise GitProtocolError("received more data after status report")
        if pkt is None:
            self._done = True
            return
        if self._pack_status is None:
            self._pack_status = pkt.strip()
        else:
            ref_status = pkt.strip()
            self._ref_statuses.append(ref_status)
            if not ref_status.startswith(b'ok '):
                self._ref_status_ok = False


# TODO(durin42): this doesn't correctly degrade if the server doesn't
# support some capabilities. This should work properly with servers
# that don't support multi_ack.
class GitClient(object):
    """Git smart server client.

    """

    def __init__(self, thin_packs=True, report_activity=None):
        """Create a new GitClient instance.

        :param thin_packs: Whether or not thin packs should be retrieved
        :param report_activity: Optional callback for reporting transport
            activity.
        """
        self._report_activity = report_activity
        self._fetch_capabilities = list(FETCH_CAPABILITIES)
        self._send_capabilities = list(SEND_CAPABILITIES)
        if thin_packs:
            self._fetch_capabilities.append(b'thin-pack')

    def _read_refs(self, proto):
        server_capabilities = None
        refs = {}
        # Receive refs from server
        for pkt in proto.read_pkt_seq():
            (sha, ref) = pkt.rstrip(b'\n').split(b' ', 1)
            if sha == b'ERR':
                raise GitProtocolError(ref)
            if server_capabilities is None:
                (ref, server_capabilities) = extract_capabilities(ref)
            refs[ref] = Sha1Sum(sha)
        return refs, server_capabilities

    def send_pack(self, path, determine_wants, generate_pack_contents,
                  progress=None):
        """Upload a pack to a remote repository.

        :param path: Repository path
        :param generate_pack_contents: Function that can return a sequence of the
            shas of the objects to upload.
        :param progress: Optional progress function

        :raises SendPackError: if server rejects the pack data
        :raises UpdateRefsError: if the server supports report-status
                                 and rejects ref updates
        """
        raise NotImplementedError(self.send_pack)

    def fetch(self, path, target, determine_wants=None, progress=None):
        """Fetch into a target repository.

        :param path: Path to fetch from
        :param target: Target repository to fetch into
        :param determine_wants: Optional function to determine what refs
            to fetch
        :param progress: Optional progress function
        :return: remote refs
        """
        if determine_wants is None:
            determine_wants = target.object_store.determine_wants_all
        f, commit = target.object_store.add_pack()
        try:
            return self.fetch_pack(path, determine_wants,
                target.get_graph_walker(), f.write, progress)
        finally:
            pack = commit()
            if pack and hasattr(pack, 'close'):
                pack.close()

    def fetch_pack(self, path, determine_wants, graph_walker, pack_data,
                   progress):
        """Retrieve a pack from a git smart server.

        :param determine_wants: Callback that returns list of commits to fetch
        :param graph_walker: Object with next() and ack().
        :param pack_data: Callback called for each bit of data in the pack
        :param progress: Callback for progress reports (strings)
        """
        raise NotImplementedError(self.fetch_pack)

    def _parse_status_report(self, proto):
        unpack = proto.read_pkt_line().strip()
        if unpack != 'unpack ok':
            st = True
            # flush remaining error data
            while st is not None:
                st = proto.read_pkt_line()
            raise SendPackError(unpack)
        statuses = []
        errs = False
        ref_status = proto.read_pkt_line()
        while ref_status:
            ref_status = ref_status.strip()
            statuses.append(ref_status)
            if not ref_status.startswith('ok '):
                errs = True
            ref_status = proto.read_pkt_line()

        if errs:
            ref_status = {}
            ok = set()
            for status in statuses:
                if ' ' not in status:
                    # malformed response, move on to the next one
                    continue
                status, ref = status.split(' ', 1)

                if status == 'ng':
                    if ' ' in ref:
                        ref, status = ref.split(' ', 1)
                else:
                    ok.add(ref)
                ref_status[ref] = status
            raise UpdateRefsError('%s failed to update' %
                                  ', '.join([ref for ref in ref_status
                                             if ref not in ok]),
                                  ref_status=ref_status)

    def _read_side_band64k_data(self, proto, channel_callbacks):
        """Read per-channel data.

        This requires the side-band-64k capability.

        :param proto: Protocol object to read from
        :param channel_callbacks: Dictionary mapping channels to packet
            handlers to use. None for a callback discards channel data.
        """
        for pkt in proto.read_pkt_seq():
            channel = pkt[0]
            pkt = pkt[1:]
            try:
                cb = channel_callbacks[channel]
            except KeyError:
                raise AssertionError('Invalid sideband channel %d' % channel)
            else:
                if cb is not None:
                    cb(pkt)

    def _handle_receive_pack_head(self, proto, capabilities, old_refs, new_refs):
        """Handle the head of a 'git-receive-pack' request.

        :param proto: Protocol object to read from
        :param capabilities: List of negotiated capabilities
        :param old_refs: Old refs, as received from the server
        :param new_refs: New refs
        :return: (have, want) tuple
        """
        want = []
        have = [x for x in list(old_refs.values()) if not x == ZERO_SHA]
        sent_capabilities = False
        for refname in set(list(new_refs.keys()) + list(old_refs.keys())):
            old_sha1 = old_refs.get(refname, ZERO_SHA)
            new_sha1 = new_refs.get(refname, ZERO_SHA)
            if old_sha1 != new_sha1:
                if sent_capabilities:
                    proto.write_pkt_line(
                      old_sha1.hex_bytes + b' ' + new_sha1.hex_bytes + b' ' + refname)
                else:
                    proto.write_pkt_line(
                      old_sha1.hex_bytes + b' ' + new_sha1.hex_bytes + b' ' + refname + \
                      b'\0' + b' '.join(capabilities))

                    sent_capabilities = True
            if new_sha1 not in have and new_sha1 != ZERO_SHA:
                want.append(new_sha1)
        proto.write_pkt_line(None)
        return (have, want)

    def _handle_receive_pack_tail(self, proto, capabilities, progress):
        """Handle the tail of a 'git-receive-pack' request.

        :param proto: Protocol object to read from
        :param capabilities: List of negotiated capabilities
        :param progress: Optional progress reporting function
        """
        if b'report-status' in capabilities:
            report_status_parser = ReportStatusParser()
        else:
            report_status_parser = None
        if b"side-band-64k" in capabilities:
            channel_callbacks = { 2: progress }
            if b'report-status' in capabilities:
                channel_callbacks[1] = PktLineParser(
                    report_status_parser.handle_packet).parse
            self._read_side_band64k_data(proto, channel_callbacks)
        else:
            if b'report-status' in capabilities:
                for pkt in proto.read_pkt_seq():
                    report_status_parser.handle_packet(pkt)
        if report_status_parser is not None:
            report_status_parser.check()
        # wait for EOF before returning
        data = proto.read()
        if data:
            raise SendPackError('Unexpected response %r' % data)

    def _handle_upload_pack_head(self, proto, capabilities, graph_walker,
                                 wants, can_read):
        """Handle the head of a 'git-upload-pack' request.

        :param proto: Protocol object to read from
        :param capabilities: List of negotiated capabilities
        :param graph_walker: GraphWalker instance to call .ack() on
        :param wants: List of commits to fetch
        :param can_read: function that returns a boolean that indicates
            whether there is extra graph data to read on proto
        """

        assert isinstance(wants, list) and type(wants[0]) == Sha1Sum
        proto.write_pkt_line(b'want ' + wants[0].hex_bytes + b' ' +
                             b' '.join(capabilities) + b'\n')
        for want in wants[1:]:
            proto.write_pkt_line(b'want ' + want.hex_bytes + b'\n')
        proto.write_pkt_line(None)
        have = next(graph_walker)
        while have:
            assert isinstance(have, Sha1Sum)
            proto.write_pkt_line(b'have ' + have.hex_bytes + b'\n')
            if can_read():
                pkt = proto.read_pkt_line()
                parts = pkt.rstrip(b'\n').split(b' ')
                if parts[0] == b'ACK':
                    graph_walker.ack(parts[1])
                    if parts[2] in (b'continue', b'common'):
                        pass
                    elif parts[2] == b'ready':
                        break
                    else:
                        raise AssertionError(
                            "%s not in ('continue', 'ready', 'common)" %
                            convert3kstr(parts[2], STRING))
            have = next(graph_walker)
        proto.write_pkt_line(b'done\n')

    def _handle_upload_pack_tail(self, proto, capabilities, graph_walker,
                                 pack_data, progress, rbufsize=_RBUFSIZE):
        """Handle the tail of a 'git-upload-pack' request.

        :param proto: Protocol object to read from
        :param capabilities: List of negotiated capabilities
        :param graph_walker: GraphWalker instance to call .ack() on
        :param pack_data: Function to call with pack data
        :param progress: Optional progress reporting function
        :param rbufsize: Read buffer size
        """
        pkt = proto.read_pkt_line()
        while pkt:
            parts = pkt.rstrip(b'\n').split(b' ')
            if parts[0] == b'ACK':
                graph_walker.ack(pkt.split(b' ')[1])
            if len(parts) < 3 or parts[2] not in (
                    b'ready', b'continue', b'common'):
                break
            pkt = proto.read_pkt_line()
        if b"side-band-64k" in capabilities:
            self._read_side_band64k_data(proto, {1: pack_data, 2: progress})
            # wait for EOF before returning
            data = proto.read()
            if data:
                raise Exception('Unexpected response %r' % convert3kstr(data, STRING))
        else:
            while True:
                data = self.read(rbufsize)
                if data == "":
                    break
                pack_data(data)

    def __enter__(self):
        return self

    def __exit__(self, type, value, tb):
        self.close()

    def close(self):
        raise NotImplementedError()


class TraditionalGitClient(GitClient):
    """Traditional Git client."""

    def _connect(self, cmd, path):
        """Create a connection to the server.

        This method is abstract - concrete implementations should
        implement their own variant which connects to the server and
        returns an initialized Protocol object with the service ready
        for use and a can_read function which may be used to see if
        reads would block.

        :param cmd: The git service name to which we should connect.
        :param path: The path we should pass to the service.
        """
        raise NotImplementedError()

    def send_pack(self, path, determine_wants, generate_pack_contents,
                  progress=None):
        """Upload a pack to a remote repository.

        :param path: Repository path
        :param generate_pack_contents: Function that can return a sequence of the
            shas of the objects to upload.
        :param progress: Optional callback called with progress updates

        :raises SendPackError: if server rejects the pack data
        :raises UpdateRefsError: if the server supports report-status
                                 and rejects ref updates
        """
        proto, unused_can_read = self._connect('receive-pack', path)
        with proto:
            old_refs, server_capabilities = self._read_refs(proto)
            negotiated_capabilities = list(self._send_capabilities)
            if b'report-status' not in server_capabilities:
                negotiated_capabilities.remove(b'report-status')
            new_refs = determine_wants(old_refs)
            if new_refs is None:
                proto.write_pkt_line(None)
                return old_refs
            (have, want) = self._handle_receive_pack_head(proto,
                negotiated_capabilities, old_refs, new_refs)
            if not want and old_refs == new_refs:
                return new_refs
            objects = generate_pack_contents(have, want)
            if len(objects) > 0:
                entries, sha = write_pack_objects(proto.write_file(), objects)
            self._handle_receive_pack_tail(proto, negotiated_capabilities,
                progress)
        return new_refs

    def fetch_pack(self, path, determine_wants, graph_walker, pack_data,
                   progress=None):
        """Retrieve a pack from a git smart server.

        :param determine_wants: Callback that returns list of commits to fetch
        :param graph_walker: Object with next() and ack().
        :param pack_data: Callback called for each bit of data in the pack
        :param progress: Callback for progress reports (strings)
        """
        proto, can_read = self._connect('upload-pack', path)
        with proto:
            (refs, server_capabilities) = self._read_refs(proto)
            negotiated_capabilities = list(self._fetch_capabilities)
            wants = determine_wants(refs)
            if not wants:
                proto.write_pkt_line(None)
                return refs
            self._handle_upload_pack_head(proto, negotiated_capabilities,
                graph_walker, wants, can_read)
            self._handle_upload_pack_tail(proto, negotiated_capabilities,
                graph_walker, pack_data, progress)
        return refs


class TCPGitClient(TraditionalGitClient):
    """A Git Client that works over TCP directly (i.e. git://)."""

    def __init__(self, host, port=None, *args, **kwargs):
        if port is None:
            port = TCP_GIT_PORT
        self._host = host
        self._port = port
        GitClient.__init__(self, *args, **kwargs)

    def _connect(self, cmd, path):
        sockaddrs = socket.getaddrinfo(self._host, self._port,
            socket.AF_UNSPEC, socket.SOCK_STREAM)
        s = None
        err = socket.error("no address found for %s" % self._host)
        for (family, socktype, proto, canonname, sockaddr) in sockaddrs:
            s = socket.socket(family, socktype, proto)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            try:
                s.connect(sockaddr)
                break
            except socket.error as err:
                if s is not None:
                    s.close()
                s = None
        if s is None:
            raise err
        # -1 means system default buffering
        rfile = s.makefile('rb', -1)
        # 0 means unbuffered
        wfile = s.makefile('wb', 0)
        def _closeit():
            rfile.close()
            wfile.close()
            s.close()

        proto = Protocol(rfile.read, wfile.write, _closeit,
                         report_activity=self._report_activity)
        if path.startswith("/~"):
            path = path[1:]
        proto.send_cmd(b'git-' + cmd.encode('utf-8'), path, b'host=%s' + convert3kstr(self._host, BYTES))
        return proto, lambda: _fileno_can_read(s)

    def close(self):
        pass

class SubprocessWrapper(object):
    """A socket-like object that talks to a subprocess via pipes."""

    def __init__(self, proc, close_stdin=True, close_stdout=True, close_stderr=False):
        self.proc = proc
        self.read = proc.stdout.read
        self.write = proc.stdin.write
        self.close_stdin = close_stdin
        self.close_stdout = close_stdout
        self.close_stderr = close_stderr

    def can_read(self):
        if subprocess.mswindows:
            from msvcrt import get_osfhandle
            from win32pipe import PeekNamedPipe
            handle = get_osfhandle(self.proc.stdout.fileno())
            return PeekNamedPipe(handle, 0)[2] != 0
        else:
            return _fileno_can_read(self.proc.stdout.fileno())

    def close(self):
        if self.close_stdin:
            self.proc.stdin.close()
        if self.close_stdout:
            self.proc.stdout.close()
        if self.close_stderr:
            self.proc.stderr.close()
        self.proc.wait()


class SubprocessGitClient(TraditionalGitClient):
    """Git client that talks to a server using a subprocess."""

    def __init__(self, *args, **kwargs):
        self._connection = None
        GitClient.__init__(self, *args, **kwargs)

    def _connect(self, service, path):
        import subprocess
        argv = ['git', service, path]
        p = SubprocessWrapper(
            subprocess.Popen(argv, bufsize=0, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE))
        return Protocol(p.read, p.write, p.close,
                        report_activity=self._report_activity), p.can_read

    def close(self):
        pass

class SSHVendor(object):

    def connect_ssh(self, host, command, username=None, port=None):
        import subprocess
        #FIXME: This has no way to deal with passwords..
        args = ['ssh', '-x']
        if port is not None:
            args.extend(['-p', str(port)])
        if username is not None:
            host = '%s@%s' % (username, host)
        args.append(host)
        proc = subprocess.Popen(args + command,
                                stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE)
        return SubprocessWrapper(proc)

# Can be overridden by users
get_ssh_vendor = SSHVendor

class SSHGitClient(TraditionalGitClient):

    def __init__(self, host, port=None, username=None, *args, **kwargs):
        self.host = host
        self.port = port
        self.username = username
        GitClient.__init__(self, *args, **kwargs)
        self.alternative_paths = {}

    def _get_cmd_path(self, cmd):
        return self.alternative_paths.get(cmd, 'git-%s' % cmd)

    def _connect(self, cmd, path):
        con = get_ssh_vendor().connect_ssh(
            self.host, ["%s '%s'" % (self._get_cmd_path(cmd), path)],
            port=self.port, username=self.username)
        return (Protocol(con.read, con.write, con.close,
                report_activity=self._report_activity),
                con.can_read)

    def close(self):
        pass

class HttpGitClient(GitClient):

    def __init__(self, base_url, dumb=None, *args, **kwargs):
        self.base_url = base_url.rstrip("/") + "/"
        self.dumb = dumb
        GitClient.__init__(self, *args, **kwargs)

    def _get_url(self, path):
        return urllib.parse.urljoin(self.base_url, path).rstrip("/") + "/"

    def _perform(self, req):
        """Perform a HTTP request.

        This is provided so subclasses can provide their own version.

        :param req: urllib2.Request instance
        :return: matching response
        """
        return urllib.request.urlopen(req)

    def _discover_references(self, service, url):
        assert url[-1] == "/"
        url = urllib.parse.urljoin(url, "info/refs")
        headers = {}
        if self.dumb != False:
            url += "?service=%s" % service
            headers["Content-Type"] = "application/x-%s-request" % service
        req = urllib.request.Request(url, headers=headers)
        resp = self._perform(req)
        if resp.getcode() == 404:
            raise NotGitRepository()
        if resp.getcode() != 200:
            raise GitProtocolError("unexpected http response %d" %
                resp.getcode())
        self.dumb = (not resp.info().get_content_type().startswith("application/x-git-"))
        proto = Protocol(resp.read, None, resp.close)
        if not self.dumb:
            # The first line should mention the service
            pkts = list(proto.read_pkt_seq())
            if pkts != [(('# service=%s\n' % service).encode('utf-8'))]:
                raise GitProtocolError(
                    "unexpected first line %r from smart server" % pkts)
        return self._read_refs(proto)

    def _smart_request(self, service, url, data):
        assert url[-1] == "/"
        url = urllib.parse.urljoin(url, service)
        req = urllib.request.Request(url,
            headers={"Content-Type": "application/x-%s-request" % service},
            data=data)
        resp = self._perform(req)
        if resp.getcode() == 404:
            raise NotGitRepository()
        if resp.getcode() != 200:
            raise GitProtocolError("Invalid HTTP response from server: %d"
                % resp.getcode())
        if resp.info().get_content_type() != ("application/x-%s-result" % service):
            raise GitProtocolError("Invalid content-type from server: %s"
                % resp.info().get_content_type())
        return resp

    def send_pack(self, path, determine_wants, generate_pack_contents,
                  progress=None):
        """Upload a pack to a remote repository.

        :param path: Repository path
        :param generate_pack_contents: Function that can return a sequence of the
            shas of the objects to upload.
        :param progress: Optional progress function

        :raises SendPackError: if server rejects the pack data
        :raises UpdateRefsError: if the server supports report-status
                                 and rejects ref updates
        """
        url = self._get_url(path)
        old_refs, server_capabilities = self._discover_references(
            "git-receive-pack", url)
        negotiated_capabilities = list(self._send_capabilities)
        new_refs = determine_wants(old_refs)
        if new_refs is None:
            return old_refs
        if self.dumb:
            raise NotImplementedError(self.fetch_pack)
        req_data = BytesIO()
        with Protocol(None, req_data.write, req_data.close) as req_proto:
            (have, want) = self._handle_receive_pack_head(
                req_proto, negotiated_capabilities, old_refs, new_refs)
            if not want and old_refs == new_refs:
                return new_refs
            objects = generate_pack_contents(have, want)
            if len(objects) > 0:
                entries, sha = write_pack_objects(req_proto.write_file(), objects)
            resp = self._smart_request("git-receive-pack", url,
                data=req_data.getvalue())
            with Protocol(resp.read, None, resp.close) as resp_proto:
                self._handle_receive_pack_tail(resp_proto, negotiated_capabilities,
                    progress)
        return new_refs

    def fetch_pack(self, path, determine_wants, graph_walker, pack_data,
                   progress):
        """Retrieve a pack from a git smart server.

        :param determine_wants: Callback that returns list of commits to fetch
        :param graph_walker: Object with next() and ack().
        :param pack_data: Callback called for each bit of data in the pack
        :param progress: Callback for progress reports (strings)
        """
        url = self._get_url(path)
        refs, server_capabilities = self._discover_references(
            "git-upload-pack", url)
        negotiated_capabilities = list(server_capabilities)
        wants = determine_wants(refs)
        if not wants:
            return refs
        if self.dumb:
            raise NotImplementedError(self.send_pack)
        req_data = BytesIO()
        with Protocol(None, req_data.write, req_data.close) as req_proto:
            self._handle_upload_pack_head(req_proto,
                negotiated_capabilities, graph_walker, wants,
                lambda: False)
            resp = self._smart_request("git-upload-pack", url,
                data=req_data.getvalue())
            with Protocol(resp.read, None, resp.close) as resp_proto:
                self._handle_upload_pack_tail(resp_proto, negotiated_capabilities,
                    graph_walker, pack_data, progress)
        return refs

    def close(self):
        pass


def get_transport_and_path(uri):
    """Obtain a git client from a URI or path.

    :param uri: URI or path
    :return: Tuple with client instance and relative path.
    """
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme == 'git':
        return TCPGitClient(parsed.hostname, port=parsed.port), parsed.path
    elif parsed.scheme == 'git+ssh':
        return SSHGitClient(parsed.hostname, port=parsed.port,
                            username=parsed.username), parsed.path
    elif parsed.scheme in ('http', 'https'):
        return HttpGitClient(urllib.parse.urlunparse(parsed)), parsed.path

    if parsed.scheme and not parsed.netloc:
        # SSH with no user@, zero or one leading slash.
        return SSHGitClient(parsed.scheme), parsed.path
    elif parsed.scheme:
        raise ValueError('Unknown git protocol scheme: %s' % parsed.scheme)
    elif '@' in parsed.path and ':' in parsed.path:
        # SSH with user@host:foo.
        user_host, path = parsed.path.split(':')
        user, host = user_host.rsplit('@')
        return SSHGitClient(host, username=user), path

    # Otherwise, assume it's a local path.
    return SubprocessGitClient(), uri
