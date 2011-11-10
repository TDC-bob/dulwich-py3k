# sha.py -- Transparently go between bytes, ascii bytes, and string
# Copyright (C) 2007 James Westby <jw+debian@jameswestby.net>
# Copyright (C) 2008-2009 Jelmer Vernooij <jelmer@samba.org>
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

"""Represents a sha-1 sum in all of its many forms"""

import binascii
import re

from dulwich.errors import ObjectFormatException

_srex = re.compile("[A-Fa-f0-9]{40}")
_hbrex = re.compile(b"[A-Fa-f0-9]{40}")

class Sha1Sum(object):
    """
    Represent a sha-1 sum as bytes, ascii bytes, and a unicode string
    """

    __slots__ = ('_string', '_hex_bytes', '_bytes',
                 '_get_string', '_get_bytes', '_get_hex_bytes')

    def __init__(self, sha, error_message=None):
        """Create a sha-1 sum

        :param sha: One of (a) a length-20 raw bytes object, (b) a length-40
            bytes object consisting of ascii characters only, or (c) a
            length-40 python string.
        """
        if isinstance(sha, str):
            self._string = sha
            self._hex_bytes = None
            self._bytes = None

            if not _srex.match(sha):
                if not error_message:
                    raise ObjectFormatException("invalid sha string: '{0}'".format(sha))
                else:
                    raise ObjectFormatException("{0}: '{1}'".format(error_message, sha))

            self._get_string = lambda: sha
            self._get_bytes = lambda: binascii.unhexlify(sha.encode('ascii'))
            self._get_hex_bytes = lambda: sha.encode('ascii')

        elif isinstance(sha, bytes):

            if len(sha) == 20:
                self._string = None
                self._hex_bytes = None
                self._bytes = sha

                self._get_string = lambda: binascii.hexlify(sha).decode('ascii')
                self._get_hex_bytes = lambda: binascii.hexlify(sha)
                self._get_bytes = lambda: sha

            elif len(sha) == 40:
                self._string = None
                self._hex_bytes = sha
                self._bytes = None

                if not _hbrex.match(sha):
                    if not error_message:
                        raise ObjectFormatException("invalid sha byte string: {0}".format(repr(sha)))
                    else:
                        raise ObjectFormatException("{0}: {1}".format(error_message, repr(sha)))

                self._get_string = sha.decode('ascii')
                self._get_hex_bytes = lambda: sha
                self._get_bytes = lambda: binascii.unhexlify(sha)

            else:
                if not error_message:
                    raise ObjectFormatException("unrecognized bytes object: {0}".format(repr(sha)))
                else:
                    raise ObjectFormatException("{0}: {1}".format(error_message, repr(sha)))

        elif isinstance(sha, Sha1Sum):
            self._string = sha._string
            self._hex_bytes = sha._hex_bytes
            self._bytes = sha._bytes

            self._get_string = sha._get_string
            self._get_hex_bytes = sha._get_hex_bytes
            self._get_bytes = sha._get_bytes

        elif hasattr(sha, 'digest') and callable(sha.digest):
            self._string = None
            self._hex_bytes = None
            self._bytes = sha.digest()

            if len(self._bytes) != 20:
                raise ObjectFormatException("unrecognized sha object: {0}".format(repr(sha)))

            self._get_string = lambda: binascii.hexlify(self._bytes).decode('ascii')
            self._get_hex_bytes = lambda: binascii.hexlify(self._bytes)
            self._get_bytes = lambda: self._bytes

        else:
            raise TypeError('Expecting a SHA-1 hash as a bytes or str object')

    @property
    def string(self):
        """Get the sha-1 sum as a hex string

        :return: A hex string representing a sha-1 sum
        """
        if self._string is None:
            self._string = self._get_string()
        return self._string

    @property
    def hex_bytes(self):
        """Get the sha-1 sum as a hex string, encoded into bytes

        :return: A hex byte-string representing a sha-1 sum
        """
        if self._hex_bytes is None:
            self._hex_bytes = self._get_hex_bytes()
        return self._hex_bytes

    @property
    def bytes(self):
        """Get the sha-1 sum as raw bytes

        :return: A raw byte-string representing a sha-1 sum
        """

        if self._bytes is None:
            self._bytes = self._get_bytes()
        return self._bytes

    def __bytes__(self):
        return self.bytes

    def __str__(self):
        return self.string

    def __repr__(self):
        return "{classname}('{hexsha}')".\
            format(classname=self.__class__.__name__, hexsha=self.string)

    def __eq__(self, other):
        if isinstance(other, Sha1Sum):
            return self.bytes == other.bytes
        else:
            try:
                other_sha = Sha1Sum(other)
                return self.bytes == other_sha.bytes
            except (TypeError, ObjectFormatException):
                pass
        return False

    def __ne__(self, other):
        return not (self == other)

    def __hash__(self):
        return hash(self.bytes)
