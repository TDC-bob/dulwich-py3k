# _compat.py -- For dealing with python2.4 oddness
# Copyright (C) 2008 Canonical Ltd.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; version 2
# of the License or (at your option) a later version.
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

import hashlib
from urllib.parse import parse_qs
from urllib.parse import parse_qs
from os import SEEK_CUR, SEEK_END

import struct
from dulwich.py3k import *

@enforce_type(source=bytes)
def make_sha(source=b''):
    """A python2.4 workaround for the hashlib module fiasco."""
    return hashlib.sha1(source)


def unpack_from(fmt, buf, offset=0):
    """A python2.4 workaround for struct missing unpack_from."""
    try:
        return struct.unpack_from(fmt, buf, offset)
    except AttributeError:
        b = buf[offset:offset+struct.calcsize(fmt)]
        return struct.unpack(fmt, b)

from itertools import permutations
from collections import namedtuple
