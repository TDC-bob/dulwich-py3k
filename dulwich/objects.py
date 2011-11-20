# objects.py -- Access to base git objects
# Copyright (C) 2007 James Westby <jw+debian@jameswestby.net>
# Copyright (C) 2008-2009 Jelmer Vernooij <jelmer@samba.org>
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

"""Access to base git objects."""


import binascii
from io import (
    BytesIO,
    )
import os
import posixpath
import stat
import warnings
import zlib
import hashlib
import re

from collections import namedtuple

from dulwich.errors import (
    ChecksumMismatch,
    NotBlobError,
    NotCommitError,
    NotTagError,
    NotTreeError,
    ObjectFormatException,
    )
from dulwich.file import GitFile


ZERO_SHA = b"0" * 40

# Header fields for commits
_TREE_HEADER = "tree"
_PARENT_HEADER = "parent"
_AUTHOR_HEADER = "author"
_COMMITTER_HEADER = "committer"
_ENCODING_HEADER = "encoding"


# Header fields for objects
_OBJECT_HEADER = "object"
_TYPE_HEADER = "type"
_TAG_HEADER = "tag"
_TAGGER_HEADER = "tagger"

# What's a SHA1 sum look like?
_SREX = re.compile("^[A-Fa-f0-9]{40}$")
_HBREX = re.compile(b"^[A-Fa-f0-9]{40}$")


S_IFGITLINK = 0o160000

def S_ISGITLINK(m):
    return (stat.S_IFMT(m) == S_IFGITLINK)


def _decompress(string):
    dcomp = zlib.decompressobj()
    dcomped = dcomp.decompress(string)
    dcomped += dcomp.flush()
    return dcomped

def sha_to_filename(path, sha):
    """Takes a hex sha and returns its filename relative to the given path."""
    dir = str(sha)[:2]
    file = str(sha)[2:]
    # Check from object dir
    return os.path.join(path, dir, file)

def filename_to_sha(filename):
    """Takes an object filename and returns its corresponding hex sha."""
    # grab the last (up to) two path components
    names = filename.rsplit(os.path.sep, 2)[-2:]
    errmsg = "Invalid object filename: {0}".format(filename)
    assert len(names) == 2, errmsg
    base, rest = names
    assert len(base) == 2 and len(rest) == 38, errmsg
    hex = base + rest
    return Sha1Sum(hex)


def object_header(num_type, length):
    """Return an object header for the given numeric type and text length."""
    return object_class(num_type).type_name.encode('utf-8') + \
      b' ' + str(length).encode('utf-8') + b'\0'


def serializable_property(name, docstring=None):
    def set(obj, value):
        obj._ensure_parsed()
        setattr(obj, "_"+name, value)
        obj._needs_serialization = True
    def get(obj):
        obj._ensure_parsed()
        return getattr(obj, "_"+name)
    return property(get, set, doc=docstring)


def object_class(type):
    """Get the object class corresponding to the given type.

    :param type: Either a type name string or a numeric type.
    :return: The ShaFile subclass corresponding to the given type, or None if
        type is not a valid type name/number.
    """
    return _TYPE_MAP.get(type, None)


def check_hexsha(hex, error_msg):
    try:
        Sha1Sum(hex, error_message=error_msg).bytes
    except (TypeError, AssertionError):
        raise ObjectFormatException("%s %s" % (error_msg, hex))


def check_identity(identity, error_msg):
    """Check if the specified identity is valid.

    This will raise an exception if the identity is not valid.

    :param identity: Identity string
    :param error_msg: Error message to use in exception
    """
    email_start = identity.find("<")
    email_end = identity.find(">")
    if (email_start < 0 or email_end < 0 or email_end <= email_start
        or identity.find("<", email_start + 1) >= 0
        or identity.find(">", email_end + 1) >= 0
        or not identity.endswith(">")):
        raise ObjectFormatException(error_msg)


def _as_sha(obj):
    """Try really hard to get a Sha1Sum representation of an object
    or raise an exception if it can't be done.

    :param obj: Some object that you want represented as a Sha1Sum
    :return: A Sha1Sum object
    """
    if isinstance(obj, Sha1Sum):
        return obj
    else:
        return Sha1Sum(obj)


class Sha1Sum(object):
    """
    Represent a sha-1 sum as bytes, ascii bytes, and a unicode string
    """

    __slots__ = ('_string', '_hex_bytes', '_bytes',
                 '_get_string', '_get_bytes', '_get_hex_bytes')

    def __init__(self, sha, error_message=None, resolve=False,
                 lazy_errors=False):
        """Create a SHA-1 sum

        :param sha: One of (a) a length-20 raw bytes object, (b) a length-40
            bytes object consisting of ascii characters only, or (c) a
            length-40 python string.
        :param error_message: An error message to return if there's a problem
        :param resolve: Automatically fill in the other representations of this
            sha1-sum. If False, the other representations get computed on
            demand.
        :param lazy_errors: If True, no errors are thrown until someone asks
            for the value. Otherwise the errors are thrown immediately.
        """

        def _exception(default_msg):
            if not error_message:
                ex = ObjectFormatException(
                  "{0}: {1}".format(default_msg, repr(sha)))
            else:
                ex = ObjectFormatException(
                  "{0}: {1}".format(error_message, repr(sha)))

            if not lazy_errors:
                raise ex
            else:
                def _throw():
                    raise ex

                self._string = None
                self._hex_bytes = None
                self._bytes = None
                self._get_string = _throw
                self._get_bytes = _throw
                self._get_hex_bytes = _throw

        if isinstance(sha, str):
            # The only kind of actual strings accepted are len 40 hex strings

            self._string = sha
            self._hex_bytes = None
            self._bytes = None

            # Could be a bit of a performance drain
            if not _SREX.match(sha):
                _exception('invalid sha string')
                return

            self._get_string = None
            self._get_bytes = lambda: binascii.unhexlify(sha.encode('ascii'))
            self._get_hex_bytes = lambda: sha.encode('ascii')

        elif isinstance(sha, bytes):
            # Bytes objects could be a raw digest or an encoded hex string

            if len(sha) == 20:
                # It's a raw digest

                self._string = None
                self._hex_bytes = None
                self._bytes = sha

                self._get_string = \
                  lambda: binascii.hexlify(sha).decode('ascii')
                self._get_hex_bytes = lambda: binascii.hexlify(sha)
                self._get_bytes = None

            elif len(sha) == 40:
                # It's probably an encoded hex string

                self._string = None
                self._hex_bytes = sha
                self._bytes = None

                # Could be a bit of a performance drain
                if not _HBREX.match(sha):
                    _exception('invalid sha byte string')
                    return

                self._get_string = lambda: sha.decode('ascii')
                self._get_hex_bytes = None
                self._get_bytes = lambda: binascii.unhexlify(sha)

            else:
                # It's garbage
                _exception('unrecognized bytes object')
                return

        elif isinstance(sha, Sha1Sum):
            # It's another Sha1Sum object, copy it

            self._string = sha._string
            self._hex_bytes = sha._hex_bytes
            self._bytes = sha._bytes
            self._get_string = sha._get_string
            self._get_hex_bytes = sha._get_hex_bytes
            self._get_bytes = sha._get_bytes

        elif hasattr(sha, 'digest') and callable(sha.digest):
            # It could be a hashlib sha1 object, let's try calling it...

            self._string = None
            self._hex_bytes = None
            self._bytes = sha.digest()

            # digest() should return a bytes object
            if not isinstance(self._bytes, bytes):
                _exception('unrecognized sha object')
                return

            # Damn, it looked so promising
            if len(self._bytes) != 20:
                _exception('unrecognized sha object')
                return

            self._get_string = \
              lambda: binascii.hexlify(self._bytes).decode('ascii')
            self._get_hex_bytes = lambda: binascii.hexlify(self._bytes)
            self._get_bytes = None

        else:
            raise TypeError('Expecting a SHA-1 hash as a bytes or str object')

        if resolve:
            # Go ahead and fill in all the other representations up front
            if self._string is None:
                self._string = self._get_string()
            if self._hex_bytes is None:
                self._hex_bytes = self._get_hex_bytes()
            if self._bytes is None:
                self._bytes = self._get_bytes()
            self._get_string = None
            self._get_hex_bytes = None
            self._get_bytes = None

    @property
    def string(self):
        """Get the sha-1 sum as a hex string

        :return: A hex string representing a sha-1 sum
        """
        if self._string is None:
            self._string = self._get_string()
            self._get_string = None
        return self._string

    @property
    def hex_bytes(self):
        """Get the sha-1 sum as a hex string, encoded into bytes

        :return: A hex byte-string representing a sha-1 sum
        """
        if self._hex_bytes is None:
            self._hex_bytes = self._get_hex_bytes()
            self._get_hex_bytes = None
        return self._hex_bytes

    @property
    def bytes(self):
        """Get the sha-1 sum as raw bytes

        :return: A raw byte-string representing a sha-1 sum
        """
        if self._bytes is None:
            self._bytes = self._get_bytes()
            self._get_bytes = None
        return self._bytes

    def digest(self):
        """Make Sha1Sum behave like hashlib's digest function

        :return: A raw byte-string representing a sha-1 sum
        """
        return self.bytes

    def hexdigest(self):
        """Make Sha1Sum behave like hashlib's hexdigest function

        :return: A hex string representing a sha-1 sum
        """
        return self.string

    def __bytes__(self):
        """Called by bytes(...)

        :return: A raw byte-string representing a sha-1 sum
        """
        return self.bytes

    def __str__(self):
        """Called by str(...)

        :return: A hex string representing a sha-1 sum
        """
        return self.string

    def __repr__(self):
        """Called by repr(...)

        :return: A string representing the Sha1Sum object
        """
        return "{classname}('{hexsha}')".\
            format(classname=self.__class__.__name__, hexsha=self.string)

    def __eq__(self, other):
        """Test two Sha1Sum objects for equality

        :param other: A sha1-like object (Sha1Sum, len 20 bytes, len 40
            string / bytes)
        :return: True if the two Sha1Sum objects are equal, False otherwise
        """
        try:
            return self.bytes == _as_sha(other).bytes
        except (TypeError, ObjectFormatException):
            return False

    def __ne__(self, other):
        """Test two Sha1Sum objects for inequality

        :param other: A sha1-like object (Sha1Sum, len 20 bytes, len 40
            string / bytes)
        :return: False if the two Sha1Sum objects are equal, True otherwise
        """
        return not (self == other)

    def __lt__(self, other):
        """Test to see if this Sha1Sum is less than another one

        :param other: A sha1-like object (Sha1Sum, len 20 bytes, len 40
            string / bytes)
        :return: True if this one's sha1-sum is less than other's, False
            otherwise
        """
        return self.bytes < _as_sha(other).bytes

    def __le__(self, other):
        """Test to see if this Sha1Sum is less than or equal to another one

        :param other: A sha1-like object (Sha1Sum, len 20 bytes, len 40
            string / bytes)
        :return: True if this one's sha1-sum is less than or equal to other's,
            False otherwise
        """
        return self.bytes <= _as_sha(other).bytes

    def __gt__(self, other):
        """Test to see if this Sha1Sum is greater than another one

        :param other: A sha1-like object (Sha1Sum, len 20 bytes, len 40
            string / bytes)
        :return: True if this one's sha1-sum is greater than other's, False
            otherwise
        """
        return self.bytes > _as_sha(other).bytes

    def __ge__(self, other):
        """Test to see if this Sha1Sum is greater than or equal to another one

        :param other: A sha1-like object (Sha1Sum, len 20 bytes, len 40
            string / bytes)
        :return: True if this one's sha1-sum is greater than or equal to
            other's, False otherwise
        """
        return self.bytes >= _as_sha(other).bytes

    def __hash__(self):
        """Get a hashed representation of this object. Since a Sha1Sum object
            is representing something which already *is* a hash, we'll just go
            ahead and use that.

        :return: An integer representation of the hash
        """
        return hash(self.bytes)

    def startswith(self, text):
        """
        """

        if isinstance(text, str):
            return self.string.startswith(text)
        elif isinstance(text, bytes):
            return self.hex_bytes.startswith(text)
        else:
            raise TypeError(text)


class ShaFile(object):
    """A git SHA file."""

    __slots__ = ('_needs_parsing', '_chunked_text', '_file', '_path',
                 '_sha', '_needs_serialization', '_magic')

    @staticmethod
    def _parse_legacy_object_header(magic, f):
        """Parse a legacy object, creating it but not reading the file."""
        bufsize = 1024
        decomp = zlib.decompressobj()
        header = decomp.decompress(magic)
        start = 0
        end = -1
        while end < 0:
            extra = f.read(bufsize)
            header += decomp.decompress(extra)
            magic += extra
            end = header.find(b"\0", start)
            start = len(header)
        header = header[:end]
        type_name, size = [h.decode('utf-8') for h in header.split(b" ", 1)]
        size = int(size)  # sanity check
        obj_class = object_class(type_name)
        if not obj_class:
            raise ObjectFormatException("Not a known type: %s" % type_name)
        ret = obj_class()
        ret._magic = magic
        return ret

    def _parse_legacy_object(self, map):
        """Parse a legacy object, setting the raw string."""
        text = _decompress(map)
        header_end = text.find(b'\0')
        if header_end < 0:
            raise ObjectFormatException("Invalid object header, no \\0")
        self.set_raw_string(text[header_end+1:])

    def as_legacy_object_chunks(self):
        compobj = zlib.compressobj()
        yield compobj.compress(self._header())
        for chunk in self.as_raw_chunks():
            yield compobj.compress(chunk)
        yield compobj.flush()

    def as_legacy_object(self):
        return b"".join(self.as_legacy_object_chunks())

    def as_raw_chunks(self):
        if self._needs_parsing:
            self._ensure_parsed()
        elif self._needs_serialization:
            self._chunked_text = self._serialize()
        return self._chunked_text

    def as_raw_string(self):
        return b"".join(self.as_raw_chunks())

    def __str__(self):
        s = self.as_raw_string()
        if isinstance(s, bytes):
            return s.decode('utf-8')
        else:
            return s

    def __hash__(self):
        return hash(self.id)

    def as_pretty_string(self):
        return self.as_raw_string()

    def _ensure_parsed(self):
        if self._needs_parsing:
            if not self._chunked_text:
                if self._file is not None:
                    self._parse_file(self._file)
                    self._file = None
                elif self._path is not None:
                    self._parse_path()
                else:
                    raise AssertionError(
                        "ShaFile needs either text or filename")
            self._deserialize(self._chunked_text)
            self._needs_parsing = False

    def set_raw_string(self, text):
        if type(text) != bytes:
            raise TypeError(text)
        self.set_raw_chunks([text])


    def set_raw_chunks(self, chunks):
        self._chunked_text = chunks
        self._deserialize(chunks)
        self._sha = None
        self._needs_parsing = False
        self._needs_serialization = False

    @staticmethod
    def _parse_object_header(magic, f):
        """Parse a new style object, creating it but not reading the file."""
        num_type = (magic[0] >> 4) & 7
        obj_class = object_class(num_type)
        if not obj_class:
            raise ObjectFormatException("Not a known type %d" % num_type)
        ret = obj_class()
        ret._magic = magic
        return ret

    def _parse_object(self, map):
        """Parse a new style object, setting self._text."""
        # skip type and size; type must have already been determined, and
        # we trust zlib to fail if it's otherwise corrupted
        byte = map[0]
        used = 1
        while (byte & 0x80) != 0:
            byte = map[used]
            used += 1
        raw = map[used:]
        self.set_raw_string(_decompress(raw))

    @classmethod
    def _is_legacy_object(cls, magic):
        b0, b1 = magic[0:2]
        word = (b0 << 8) + b1
        return (b0 & 0x8F) == 0x08 and (word % 31) == 0

    @classmethod
    def _parse_file_header(cls, f):
        magic = f.read(2)
        if cls._is_legacy_object(magic):
            return cls._parse_legacy_object_header(magic, f)
        else:
            return cls._parse_object_header(magic, f)

    def __init__(self):
        """Don't call this directly"""
        self._sha = None
        self._path = None
        self._file = None
        self._magic = None
        self._chunked_text = []
        self._needs_parsing = False
        self._needs_serialization = True

    def _deserialize(self, chunks):
        raise NotImplementedError(self._deserialize)

    def _serialize(self):
        raise NotImplementedError(self._serialize)

    def _parse_path(self):
        with GitFile(self._path, 'rb') as f:
            self._parse_file(f)

    def _parse_file(self, f):
        magic = self._magic
        if magic is None:
            magic = f.read(2)
        map = magic + f.read()
        if self._is_legacy_object(magic[:2]):
            self._parse_legacy_object(map)
        else:
            self._parse_object(map)

    @classmethod
    def from_path(cls, path):
        with GitFile(path, 'rb') as f:
            obj = cls.from_file(f)
            obj._path = path
            obj._sha = filename_to_sha(path)
            obj._file = None
            obj._magic = None
            return obj

    @classmethod
    def from_file(cls, f):
        """Get the contents of a SHA file on disk."""
        try:
            obj = cls._parse_file_header(f)
            obj._sha = None
            obj._needs_parsing = True
            obj._needs_serialization = True
            obj._file = f
            return obj
        except (IndexError, ValueError) as e:
            raise ObjectFormatException("invalid object header")

    @staticmethod
    def from_raw_string(type_num, string):
        """Creates an object of the indicated type from the raw string given.

        :param type_num: The numeric type of the object.
        :param string: The raw uncompressed contents.
        """
        obj = object_class(type_num)()
        obj.set_raw_string(string)
        return obj

    @staticmethod
    def from_raw_chunks(type_num, chunks):
        """Creates an object of the indicated type from the raw chunks given.

        :param type_num: The numeric type of the object.
        :param chunks: An iterable of the raw uncompressed contents.
        """
        obj = object_class(type_num)()
        obj.set_raw_chunks(chunks)
        return obj

    @classmethod
    def from_string(cls, string):
        """Create a ShaFile from a string."""
        obj = cls()
        obj.set_raw_string(string)
        return obj

    def _check_has_member(self, member, error_msg):
        """Check that the object has a given member variable.

        :param member: the member variable to check for
        :param error_msg: the message for an error if the member is missing
        :raise ObjectFormatException: with the given error_msg if member is
            missing or is None
        """
        if getattr(self, member, None) is None:
            raise ObjectFormatException(error_msg)

    def check(self):
        """Check this object for internal consistency.

        :raise ObjectFormatException: if the object is malformed in some way
        :raise ChecksumMismatch: if the object was created with a SHA that does
            not match its contents
        """
        # TODO: if we find that error-checking during object parsing is a
        # performance bottleneck, those checks should be moved to the class's
        # check() method during optimization so we can still check the object
        # when necessary.
        old_sha = self.id
        try:
            self._deserialize(self.as_raw_chunks())
            self._sha = None
            new_sha = self.id
        except Exception as e:
            raise ObjectFormatException(e)
        if old_sha != new_sha:
            raise ChecksumMismatch(new_sha, old_sha)

    def _header(self):
        return object_header(self.type, self.raw_length())

    def raw_length(self):
        """Returns the length of the raw string of this object."""
        ret = 0
        for chunk in self.as_raw_chunks():
            ret += len(chunk)
        return ret

    def _make_sha(self):
        ret = hashlib.sha1(b'')
        ret.update(self._header())
        for chunk in self.as_raw_chunks():
            ret.update(chunk)
        return ret

    def sha(self):
        """The SHA1 object that is the name of this object."""
        if self._sha is None or self._needs_serialization:
            # this is a local because as_raw_chunks() overwrites self._sha
            new_sha = hashlib.sha1(b'')
            new_sha.update(self._header())
            for chunk in self.as_raw_chunks():
                new_sha.update(chunk)
            self._sha = new_sha
        return self._sha

    @property
    def id(self):
        return Sha1Sum(self.sha().hexdigest())

    def get_type(self):
        return self.type_num

    def set_type(self, type):
        self.type_num = type

    # DEPRECATED: use type_num or type_name as needed.
    type = property(get_type, set_type)

    def __repr__(self):
        return "<%s %s>" % (self.__class__.__name__, self.id)

    def __ne__(self, other):
        return not isinstance(other, ShaFile) or self.id != other.id

    def __eq__(self, other):
        """Return True if the SHAs of the two objects match.

        It doesn't make sense to talk about an order on ShaFiles, so we don't
        override the rich comparison methods (__le__, etc.).
        """
        return isinstance(other, ShaFile) and self.id == other.id


class Blob(ShaFile):
    """A Git Blob object."""

    __slots__ = ()

    type_name = 'blob'
    type_num = 3

    def __init__(self):
        super(Blob, self).__init__()
        self._chunked_text = []
        self._needs_parsing = False
        self._needs_serialization = False

    def _get_data(self):
        return self.as_raw_string()

    def _set_data(self, data):
        self.set_raw_string(data)

    data = property(_get_data, _set_data,
                    "The text contained within the blob object.")

    def _get_chunked(self):
        self._ensure_parsed()
        return self._chunked_text

    def _set_chunked(self, chunks):
        self._chunked_text = chunks

    def _serialize(self):
        if not self._chunked_text:
            self._ensure_parsed()
        self._needs_serialization = False
        return self._chunked_text

    def _deserialize(self, chunks):
        self._chunked_text = chunks

    chunked = property(_get_chunked, _set_chunked,
        "The text within the blob object, as chunks (not necessarily lines).")

    @classmethod
    def from_path(cls, path):
        blob = ShaFile.from_path(path)
        if not isinstance(blob, cls):
            raise NotBlobError(path)
        return blob

    def check(self):
        """Check this object for internal consistency.

        :raise ObjectFormatException: if the object is malformed in some way
        """
        super(Blob, self).check()

def _parse_tag_or_commit(text):
    """Parse tag or commit text.

    :param text: the raw text of the tag or commit object.
    :return: iterator of tuples of (field, value), one per header line, in the
        order read from the text, possibly including duplicates. Includes a
        field named None for the freeform tag/commit text.
    """
    with BytesIO(text) as f:
        for l in f:
            l = l.rstrip(b"\n")
            if l == b"":
                # Empty line indicates end of headers
                break

            parts = l.split(b" ", 1)
            assert len(parts) == 2
            yield parts
        yield (None, f.read())


def parse_tag(text):
    return _parse_tag_or_commit(text)


class Tag(ShaFile):
    """A Git Tag object."""

    type_name = 'tag'
    type_num = 4

    __slots__ = ('_tag_timezone_neg_utc', '_name', '_object_sha',
                 '_object_class', '_tag_time', '_tag_timezone',
                 '_tagger', '_message')

    def __init__(self):
        super(Tag, self).__init__()
        self._tag_timezone_neg_utc = False

    @classmethod
    def from_path(cls, filename):
        tag = ShaFile.from_path(filename)
        if not isinstance(tag, cls):
            raise NotTagError(filename)
        return tag

    def check(self):
        """Check this object for internal consistency.

        :raise ObjectFormatException: if the object is malformed in some way
        """
        super(Tag, self).check()
        self._check_has_member("_object_sha", "missing object sha")
        self._check_has_member("_object_class", "missing object type")
        self._check_has_member("_name", "missing tag name")

        if not self._name:
            raise ObjectFormatException("empty tag name")

        check_hexsha(self._object_sha, "invalid object sha")

        if getattr(self, "_tagger", None):
            check_identity(self._tagger, "invalid tagger")

        last = None
        for field, _ in parse_tag(b"".join(self._chunked_text)):
            if field:
                field = field.decode('utf-8')
            if field == _OBJECT_HEADER and last is not None:
                raise ObjectFormatException("unexpected object")
            elif field == _TYPE_HEADER and last != _OBJECT_HEADER:
                raise ObjectFormatException("unexpected type")
            elif field == _TAG_HEADER and last != _TYPE_HEADER:
                raise ObjectFormatException("unexpected tag name")
            elif field == _TAGGER_HEADER and last != _TAG_HEADER:
                raise ObjectFormatException("unexpected tagger")
            last = field

    def _serialize(self):
        chunks = []
        chunks.append(_OBJECT_HEADER.encode('utf-8') + b' ' +
                      self._object_sha.hex_bytes + b'\n')
        chunks.append(_TYPE_HEADER.encode('utf-8') + b' ' +
                      self._object_class.type_name.encode('utf-8') + b'\n')
        chunks.append(_TAG_HEADER.encode('utf-8') + b' ' +
                      self._name.encode('utf-8') + b'\n')
        if self._tagger:
            if self._tag_time is None:
                chunks.append(_TAGGER_HEADER.encode('utf-8') + b' ' +
                              self._tagger.encode('utf-8') + b'\n')
            else:
                chunks.append(_TAGGER_HEADER.encode('utf-8') + b' ' +
                              self._tagger.encode('utf-8') + b' ' +
                              str(self._tag_time).encode('utf-8') + b' ' +
                              format_timezone(self._tag_timezone, self._tag_timezone_neg_utc) + b'\n')

        chunks.append(b'\n') # To close headers
        chunks.append(self._message.encode('utf-8'))
        return chunks

    def _deserialize(self, chunks):
        """Grab the metadata attached to the tag"""
        self._tagger = None
        for field, value in parse_tag(b"".join(chunks)):
            if field:
                field = field.decode('utf-8')
            if field == _OBJECT_HEADER:
                self._object_sha = Sha1Sum(value, lazy_errors=True)
            elif field == _TYPE_HEADER:
                obj_class = object_class(value.decode('utf-8'))
                if not obj_class:
                    raise ObjectFormatException("Not a known type: %s" % value.decode('utf-8'))
                self._object_class = obj_class
            elif field == _TAG_HEADER:
                self._name = value.decode('utf-8')
            elif field == _TAGGER_HEADER:
                value = value.decode('utf-8')
                try:
                    sep = value.index('> ')
                except ValueError:
                    self._tagger = value
                    self._tag_time = None
                    self._tag_timezone = None
                    self._tag_timezone_neg_utc = False
                else:
                    self._tagger = value[0:sep+1]
                    try:
                        (timetext, timezonetext) = value[sep+2:].rsplit(" ", 1)
                        self._tag_time = int(timetext)
                        self._tag_timezone, self._tag_timezone_neg_utc = \
                                parse_timezone(timezonetext)
                    except ValueError as e:
                        raise ObjectFormatException(e)
            elif field is None:
                self._message = value.decode('utf-8')
            else:
                raise ObjectFormatException("Unknown field %s" % field)

    def _get_object(self):
        """Get the object pointed to by this tag.

        :return: tuple of (object class, sha).
        """
        self._ensure_parsed()
        return (self._object_class, self._object_sha)

    def _set_object(self, value):
        self._ensure_parsed()
        (self._object_class, self._object_sha) = value
        self._needs_serialization = True

    object = property(_get_object, _set_object)

    name = serializable_property("name", "The name of this tag")
    tagger = serializable_property("tagger",
        "Returns the name of the person who created this tag")
    tag_time = serializable_property("tag_time",
        "The creation timestamp of the tag.  As the number of seconds since the epoch")
    tag_timezone = serializable_property("tag_timezone",
        "The timezone that tag_time is in.")
    message = serializable_property("message", "The message attached to this tag")


class TreeEntry(namedtuple('TreeEntry', ['path', 'mode', 'sha'])):
    """Named tuple encapsulating a single tree entry."""

    def in_path(self, path):
        """Return a copy of this entry with the given path prepended."""

        if not isinstance(self.path, bytes) or not isinstance(path, bytes):
            raise TypeError
        return TreeEntry(posixpath.join(path.decode('utf-8'), self.path.decode('utf-8')).encode('utf-8'), self.mode, self.sha)

def parse_tree(text, strict=False):
    """Parse a tree text.

    :param text: Serialized text to parse
    :return: iterator of tuples of (name, mode, sha)
    :raise ObjectFormatException: if the object was malformed in some way
    """

    count = 0
    l = len(text)
    while count < l:
        mode_end = text.index(b' ', count)
        mode_text = text[count:mode_end]
        if strict and mode_text.startswith(b'0'):
            raise ObjectFormatException("Invalid mode '%s'" % mode_text)
        try:
            mode = int(mode_text, 8)
        except ValueError:
            raise ObjectFormatException("Invalid mode '%s'" % mode_text)
        name_end = text.index(b'\0', mode_end)
        name = text[mode_end+1:name_end]
        count = name_end+21
        sha = text[name_end+1:count]
        if len(sha) != 20:
            raise ObjectFormatException("Sha has invalid length")
        yield (name, mode, Sha1Sum(sha))


def serialize_tree(items):
    """Serialize the items in a tree to a text.

    :param items: Sorted iterable over (name, mode, sha) tuples
    :return: Serialized tree text as chunks
    """
    for name, mode, sha in items:
        yield ("%04o " % mode).encode('utf-8') + name + b'\0' + bytes(sha)


def cmp_to_key(mycmp):
    """Convert a cmp= function into a key= function"""
    class K(object):
        def __init__(self, obj, *args):
            self.obj = obj
        def __lt__(self, other):
            return mycmp(self.obj, other.obj) < 0
        def __gt__(self, other):
            return mycmp(self.obj, other.obj) > 0
        def __eq__(self, other):
            return mycmp(self.obj, other.obj) == 0
        def __le__(self, other):
            return mycmp(self.obj, other.obj) <= 0
        def __ge__(self, other):
            return mycmp(self.obj, other.obj) >= 0
        def __ne__(self, other):
            return mycmp(self.obj, other.obj) != 0
    return K

def sorted_tree_items(entries, name_order):
    """Iterate over a tree entries dictionary.

    :param name_order: If True, iterate entries in order of their name. If
        False, iterate entries in tree order, that is, treat subtree entries as
        having '/' appended.
    :param entries: Dictionary mapping names to (mode, sha) tuples
    :return: Iterator over (name, mode, hexsha)
    """
    cmp_func = name_order and cmp_entry_name_order or cmp_entry
    for name, entry in sorted(iter(entries.items()), key=cmp_to_key(cmp_func)):
        mode, hexsha = entry
        # Stricter type checks than normal to mirror checks in the C version.
        if not isinstance(mode, int):
            raise TypeError('Expected integer/long for mode, got %r' % mode)
        mode = int(mode)
        if not isinstance(hexsha, Sha1Sum):
            raise TypeError('Expected a Sha1Sum for SHA, got %r' % hexsha)
        yield TreeEntry(name, mode, hexsha)


def cmp_entry(tuple_1, tuple_2):
    """Compare two tree entries in tree order."""
    (name1, value1) = tuple_1
    (name2, value2) = tuple_2

    if stat.S_ISDIR(value1[0]):
        name1 += b"/"
    if stat.S_ISDIR(value2[0]):
        name2 += b"/"
    return (name1 > name2) - (name1 < name2)


def cmp_entry_name_order(entry1, entry2):
    """Compare two tree entries in name order."""
    return (entry1[0] > entry2[0]) - (entry1[0] < entry2[0])


class Tree(ShaFile):
    """A Git tree object"""

    type_name = 'tree'
    type_num = 2

    __slots__ = ('_entries')

    def __init__(self):
        super(Tree, self).__init__()
        self._entries = {}

    @classmethod
    def from_path(cls, filename):
        tree = ShaFile.from_path(filename)
        if not isinstance(tree, cls):
            raise NotTreeError(filename)
        return tree

    def __contains__(self, name):
        self._ensure_parsed()
        return name in self._entries

    def __getitem__(self, name):
        self._ensure_parsed()
        return self._entries[name]

    def __setitem__(self, name, value):
        """Set a tree entry by name.

        :param name: The name of the entry, as a string.
        :param value: A tuple of (mode, sha), where mode is the mode of the
            entry as an integral type and sha is the Sha1Sum of the entry as
            a string.
        """
        mode, sha = value
        self._ensure_parsed()
        self._entries[name] = (mode, sha)
        self._needs_serialization = True

    def __delitem__(self, name):
        self._ensure_parsed()
        del self._entries[name]
        self._needs_serialization = True

    def __len__(self):
        self._ensure_parsed()
        return len(self._entries)

    def __iter__(self):
        self._ensure_parsed()
        return iter(self._entries)

    def add(self, name, mode, sha):
        """Add an entry to the tree.

        :param mode: The mode of the entry as an integral type. Not all 
            possible modes are supported by git; see check() for details.
        :param name: The name of the entry, as a string.
        :param hexsha: The hex SHA of the entry as a string.
        """
        self._ensure_parsed()
        self._entries[name] = mode, sha
        self._needs_serialization = True

    def entries(self):
        """Return a list of tuples describing the tree entries.

        :note: The order of the tuples that are returned is different from that
            returned by the items and iteritems methods. This function will be
            deprecated in the future.
        """
        warnings.warn("Tree.entries() is deprecated. Use Tree.items() or"
            " Tree.iteritems() instead.", category=DeprecationWarning,
            stacklevel=2)
        self._ensure_parsed()
        # The order of this is different from iteritems() for historical
        # reasons
        return [
            (mode, name, hexsha) for (name, mode, hexsha) in self.items()]

    def iteritems(self, name_order=False):
        """Iterate over entries.

        :param name_order: If True, iterate in name order instead of tree order.
        :return: Iterator over (name, mode, sha) tuples
        """
        self._ensure_parsed()
        return sorted_tree_items(self._entries, name_order)

    def items(self):
        """Return the sorted entries in this tree.

        :return: List with (name, mode, sha) tuples
        """
        return list(self.iteritems())

    def _deserialize(self, chunks):
        """Grab the entries in the tree"""
        try:
            parsed_entries = parse_tree(b"".join(chunks))
        except ValueError as e:
            raise ObjectFormatException(e)
        # TODO: list comprehension is for efficiency in the common (small) case;
        # if memory efficiency in the large case is a concern, use a genexp.
        self._entries = dict([(n, (m, s)) for n, m, s in parsed_entries])

    def check(self):
        """Check this object for internal consistency.

        :raise ObjectFormatException: if the object is malformed in some way
        """
        super(Tree, self).check()
        last = None
        allowed_modes = (stat.S_IFREG | 0o755, stat.S_IFREG | 0o644,
                         stat.S_IFLNK, stat.S_IFDIR, S_IFGITLINK,
                         # TODO: optionally exclude as in git fsck --strict
                         stat.S_IFREG | 0o664)
        for name, mode, sha in parse_tree(b''.join(self._chunked_text),
                                          True):
            check_hexsha(sha, 'invalid sha %s' % sha)
            if b'/' in name or name in (b'', b'.', b'..'):
                raise ObjectFormatException('invalid name %s' % name)

            if mode not in allowed_modes:
                raise ObjectFormatException('invalid mode %06o' % mode)

            entry = (name, (mode, sha))
            if last:
                if cmp_entry(last, entry) > 0:
                    raise ObjectFormatException('entries not sorted')
                if name == last[0]:
                    raise ObjectFormatException('duplicate entry %s' % name)
            last = entry

    def _serialize(self):
        return list(serialize_tree(iter(self.items())))

    def as_pretty_string(self):
        text = []
        for name, mode, sha in self.items():
            if mode & stat.S_IFDIR:
                kind = "tree"
            else:
                kind = "blob"
            text.append("%04o %s %s\t%s\n" % (mode, kind, str(sha), name.decode('utf-8')))
        return "".join(text)

    def lookup_path(self, lookup_obj, path):
        """Look up an object in a Git tree.

        :param lookup_obj: Callback for retrieving object by SHA1
        :param path: Path to lookup
        :return: A tuple of (mode, SHA) of the resulting path.
        """
        parts = path.split('/')
        sha = self.id
        mode = None
        for p in parts:
            if not p:
                continue
            obj = lookup_obj(sha)
            if not isinstance(obj, Tree):
                raise NotTreeError(sha)
            mode, sha = obj[p.encode('utf-8')]
        return mode, sha


def parse_timezone(text):
    """Parse a timezone text fragment (e.g. '+0100').

    :param text: Text to parse.
    :return: Tuple with timezone as seconds difference to UTC
        and a boolean indicating whether this was a UTC timezone
        prefixed with a negative sign (-0000).
    """
    if isinstance(text, bytes):
        text = text.decode('utf-8')
    offset = int(text)
    negative_utc = (offset == 0 and text[0] == '-')
    signum = (offset < 0) and -1 or 1
    offset = abs(offset)
    hours = int(offset / 100)
    minutes = (offset % 100)
    return signum * (hours * 3600 + minutes * 60), negative_utc


def format_timezone(offset, negative_utc=False):
    """Format a timezone for Git serialization.

    :param offset: Timezone offset as seconds difference to UTC
    :param negative_utc: Whether to use a minus sign for UTC
        (-0000 rather than +0000).
    """
    if offset % 60 != 0:
        raise ValueError("Unable to handle non-minute offset.")
    if offset < 0 or (offset == 0 and negative_utc):
        sign = '-'
    else:
        sign = '+'
    offset = abs(offset)
    return ('%c%02d%02d' % (sign, offset / 3600, (offset / 60) % 60)).encode('utf-8')


def parse_commit(text):
    return _parse_tag_or_commit(text)


class Commit(ShaFile):
    """A git commit object"""

    type_name = 'commit'
    type_num = 1

    __slots__ = ('_parents', '_encoding', '_extra', '_author_timezone_neg_utc',
                 '_commit_timezone_neg_utc', '_commit_time',
                 '_author_time', '_author_timezone', '_commit_timezone',
                 '_author', '_committer', '_parents', '_extra',
                 '_encoding', '_tree', '_message')

    def __init__(self):
        super(Commit, self).__init__()
        self._parents = []
        self._encoding = None
        self._extra = {}
        self._author_timezone_neg_utc = False
        self._commit_timezone_neg_utc = False

    @classmethod
    def from_path(cls, path):
        commit = ShaFile.from_path(path)
        if not isinstance(commit, cls):
            raise NotCommitError(path)
        return commit

    def _deserialize(self, chunks):
        self._parents = []
        self._extra = []
        self._author = None

        for field, value in parse_commit(b''.join(self._chunked_text)):
            if field:
                fieldname = field.decode('utf-8')
            else:
                fieldname = None
            if fieldname == _TREE_HEADER:
                self._tree = Sha1Sum(value)
            elif fieldname == _PARENT_HEADER:
                self._parents.append(Sha1Sum(value))
            elif fieldname == _AUTHOR_HEADER:
                self._author, timetext, timezonetext = value.decode('utf-8').rsplit(" ", 2)
                self._author_time = int(timetext)
                self._author_timezone, self._author_timezone_neg_utc =\
                    parse_timezone(timezonetext)
            elif fieldname == _COMMITTER_HEADER:
                self._committer, timetext, timezonetext = value.decode('utf-8').rsplit(" ", 2)
                self._commit_time = int(timetext)
                self._commit_timezone, self._commit_timezone_neg_utc =\
                    parse_timezone(timezonetext)
            elif fieldname == _ENCODING_HEADER:
                self._encoding = value.decode('utf-8')
            elif fieldname is None:
                self._message = value.decode('utf-8')
            else:
                self._extra.append((field, value))

    def check(self):
        """Check this object for internal consistency.

        :raise ObjectFormatException: if the object is malformed in some way
        """
        super(Commit, self).check()
        self._check_has_member("_tree", "missing tree")
        self._check_has_member("_author", "missing author")
        self._check_has_member("_committer", "missing committer")
        # times are currently checked when set

        for parent in self._parents:
            check_hexsha(parent, "invalid parent sha")
        check_hexsha(self._tree, "invalid tree sha")

        check_identity(self._author, "invalid author")
        check_identity(self._committer, "invalid committer")

        last = None
        for field, _ in parse_commit(b"".join(self._chunked_text)):
            if field:
                field = field.decode('utf-8')
            if field == _TREE_HEADER and last is not None:
                raise ObjectFormatException("unexpected tree")
            elif field == _PARENT_HEADER and last not in (_PARENT_HEADER,
                                                          _TREE_HEADER):
                raise ObjectFormatException("unexpected parent")
            elif field == _AUTHOR_HEADER and last not in (_TREE_HEADER,
                                                          _PARENT_HEADER):
                raise ObjectFormatException("unexpected author")
            elif field == _COMMITTER_HEADER and last != _AUTHOR_HEADER:
                raise ObjectFormatException("unexpected committer")
            elif field == _ENCODING_HEADER and last != _COMMITTER_HEADER:
                raise ObjectFormatException("unexpected encoding")
            last = field

        # TODO: optionally check for duplicate parents

    def _serialize(self):
        chunks = []
        chunks.append(_TREE_HEADER.encode('utf-8') + b' ' + self._tree.hex_bytes + b'\n')
        for p in self._parents:
            chunks.append(_PARENT_HEADER.encode('utf-8') + b' ' + p.hex_bytes + b'\n')
        chunks.append(_AUTHOR_HEADER.encode('utf-8') + b' ' +
                      self._author.encode('utf-8') + b' ' +
                      str(self._author_time).encode('utf-8') + b' ' +
                      format_timezone(self._author_timezone, self._author_timezone_neg_utc) + b'\n')
        chunks.append(_COMMITTER_HEADER.encode('utf-8') + b' ' +
                      self._committer.encode('utf-8') + b' ' +
                      str(self._commit_time).encode('utf-8') + b' ' +
                      format_timezone(self._commit_timezone, self._commit_timezone_neg_utc) + b'\n')
        if self.encoding:
            chunks.append(_ENCODING_HEADER.encode('utf-8') + b' ' + 
                          self.encoding.encode('utf-8') + b'\n')
        for k, v in self.extra:
            assert isinstance(k, bytes) and isinstance(v, bytes)
            if b'\n' in k or b'\n' in v:
                raise AssertionError("newline in extra data: %r -> %r" % (k, v))
            chunks.append(k + b' ' + v + b'\n')
        chunks.append(b'\n') # There must be a new line after the headers
        chunks.append(self._message.encode('utf-8'))
        return chunks

    tree = serializable_property("tree", "Tree that is the state of this commit")

    def _get_parents(self):
        """Return a list of parents of this commit."""
        self._ensure_parsed()
        return self._parents

    def _set_parents(self, value):
        """Set a list of parents of this commit."""
        self._ensure_parsed()
        self._needs_serialization = True
        self._parents = value

    parents = property(_get_parents, _set_parents)

    def _get_extra(self):
        """Return extra settings of this commit."""
        self._ensure_parsed()
        return self._extra

    extra = property(_get_extra)

    author = serializable_property("author",
        "The name of the author of the commit")

    committer = serializable_property("committer",
        "The name of the committer of the commit")

    message = serializable_property("message",
        "The commit message")

    commit_time = serializable_property("commit_time",
        "The timestamp of the commit. As the number of seconds since the epoch.")

    commit_timezone = serializable_property("commit_timezone",
        "The zone the commit time is in")

    author_time = serializable_property("author_time",
        "The timestamp the commit was written. as the number of seconds since the epoch.")

    author_timezone = serializable_property("author_timezone",
        "Returns the zone the author time is in.")

    encoding = serializable_property("encoding",
        "Encoding of the commit message.")


OBJECT_CLASSES = (
    Commit,
    Tree,
    Blob,
    Tag,
    )

_TYPE_MAP = {}

for cls in OBJECT_CLASSES:
    _TYPE_MAP[cls.type_name] = cls
    _TYPE_MAP[cls.type_num] = cls



# Hold on to the pure-python implementations for testing
_parse_tree_py = parse_tree
_sorted_tree_items_py = sorted_tree_items
try:
    # Try to import C versions
    from dulwich._objects import parse_tree, sorted_tree_items
except ImportError:
    pass
