# sha1.py -- Transparently go between bytes, ascii bytes, and string
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

_SREX = re.compile("^[A-Fa-f0-9]{40}$")
_HBREX = re.compile(b"^[A-Fa-f0-9]{40}$")

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
