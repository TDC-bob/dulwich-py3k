# py3k.py -- Utilities for the conversion from python 2 to python 3
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

"""Transparently wraps things to go from bytes <-> str"""

import sys

NOCONVERT = 0
BYTES = 1
STRING = 2
DICT_KEYS_TO_BYTES = 4
DICT_KEYS_TO_STRING = 8
DICT_VALS_TO_BYTES = 16
DICT_VALS_TO_STRING = 32
AGGRESSIVE = 64


def echo(func, write=sys.stdout.write):
    code = func.__code__
    argcount = code.co_argcount
    argnames = code.co_varnames[:argcount]
    fn_defaults = func.__defaults__ or list()
    argdefs = dict(zip(argnames[-len(fn_defaults):], fn_defaults))

    def wrapped_func(*args, **kwargs):
        positional = [av for av in zip(argnames, args)]
        defaulted = [((a, argdefs[a])) for a in argnames[len(args):] if a not in kwargs]
        nameless = [repr(a) for a in args[argcount:]]
        keyword = [av for av in kwargs.items()]
        nargs = positional + defaulted + nameless + keyword

        string = str(func.__name__) + '(' + \
            ", ".join([str(i[0]) + '=' + repr(i[1]) for i in nargs]) + \
            ')'
        write(string + '\n')

        ret = func(*args, **kwargs)
        write(' => ' + repr(ret) + '\n')

        return ret

    wrapped_func.__name__ = func.__name__
    wrapped_func.__doc__ = func.__doc__

    return wrapped_func


class enforce_type(object):

    def __init__(self, **kwargs):
        self._types = kwargs

    def _enforce(self, fname, param_name, param_value, expected_type):
        msg = fname + ": parameter '" + param_name + "' expects type {0}, but got {1}'"
        if param_value is None:
            return

        if isinstance(expected_type, (tuple, list)):
            if not isinstance(param_value, type(expected_type)):
                raise TypeError('#1: ' + msg.format(repr(expected_type), repr(type(param_value))))
            if len(param_value) != len(expected_type):
                raise ValueError(fname + ": parameter '" + param_name + "' is a tuple of size " + \
                  len(param_value) + ', should be size ' + len(expected_type))
            for i in range(len(expected_type)):
                self._enforce(fname, param_name, param_value[i], expected_type[i])

        elif not isinstance(param_value, expected_type):
            raise TypeError(msg.format(repr(expected_type), repr(type(param_value))))

    def __call__(self, func):
        code = func.__code__
        argcount = code.co_argcount
        argnames = code.co_varnames[:argcount]
        fn_defaults = func.__defaults__ or list()
        argdefs = dict(zip(argnames[-len(fn_defaults):], fn_defaults))
        func_name = str(func.__name__)

        def wrapped_func(*args, **kwargs):
            positional = [av for av in zip(argnames, args)]
            defaulted = [((a, argdefs[a])) for a in argnames[len(args):] if a not in kwargs]
            nameless = [repr(a) for a in args[argcount:]]
            keyword = [av for av in kwargs.items()]
            nargs = positional + defaulted + nameless + keyword

            for arg in nargs:
                name = arg[0]
                val = arg[1]
                if name in self._types:
                    self._enforce(func_name, name, val, self._types[name])

            ret = func(*args, **kwargs)
            if 'returns' in self._types:
                expected = self._types['returns']
                if not isinstance(val, expected):
                    msg = str(func.__name__) + ": returned value should be type " + \
                        repr(expected) + ', but got ' + repr(type(val))
                    raise TypeError(msg)

            return ret

        wrapped_func.__name__ = func.__name__
        wrapped_func.__doc__ = func.__doc__

        return wrapped_func


class wrap3kstr(object):
    def __init__(self, enforcing=False, unnamed_in=NOCONVERT, returns=NOCONVERT, **kwargs):
        self.unnamed_in = self._sanity_check(unnamed_in)
        self.returns = self._sanity_check(returns)
        self.named_in = {}
        self.enforcing = enforcing
        for key in kwargs:
            self.named_in[key] = self._sanity_check(kwargs[key])

    def _sanity_check(self, bitmask):
        #assert(((bitmask & BYTES) | (bitmask & STRING)) != 3, 'You can only specify BYTES or STRING, not both')
        #assert(((bitmask & DICT_KEYS_TO_BYTES) | (bitmask & DICT_KEYS_TO_STRING)) != 12,
        #       'You can only specify DICT_KEYS_TO_BYTES or DICT_KEYS_TO_STRING, not both')
        #assert(((bitmask & DICT_VALS_TO_BYTES) | (bitmask & DICT_VALS_TO_STRING)) != 48,
        #       'You can only specify DICT_VALS_TO_BYTES or DICT_VALS_TO_STRING, not both')
        if bitmask == AGGRESSIVE:
            return NOCONVERT
        else:
            return bitmask

    def dictKeysToBytes(self, obj):
        nd = {}
        for key in obj.keys():
            nd[self.toBytes(key)] = obj[key]
        return nd

    def dictValuesToBytes(self, obj):
        nd = {}
        for key in obj.keys():
            nd[key] = self.toBytes(obj[key])
        return nd

    def dictAllToBytes(self, obj):
        nd = {}
        for key in obj.keys():
            nd[self.toBytes(key)] = self.toBytes(obj[key])
        return nd

    def dictKeysToString(self, obj):
        nd = {}
        for key in obj.keys():
            nd[self.toString(key)] = obj[key]
        return nd

    def dictKeysToString(self, obj):
        nd = {}
        for key in obj.keys():
            nd[self.toString(key)] = obj[key]
        return nd

    def dictValuesToString(self, obj):
        nd = {}
        for key in obj.keys():
            nd[key] = self.toString(obj[key])
        return nd

    def dictAllToString(self, obj):
        nd = {}
        for key in obj.keys():
            nd[self.toString(key)] = self.toString(obj[key])
        return nd

    def convertDictionary(self, obj):
        newdict = obj
        mask = self.active_mask

        if (mask & DICT_KEYS_TO_BYTES) and (mask & DICT_VALS_TO_BYTES):
            newdict = self.dictAllToBytes(newdict)
        elif mask & DICT_KEYS_TO_BYTES:
            newdict = self.dictKeysToBytes(newdict)
        elif mask & DICT_VALS_TO_BYTES:
            newdict = self.dictValuesToBytes(newdict)

        if (mask & DICT_KEYS_TO_STRING) and (mask & DICT_VALS_TO_STRING):
            newdict = self.dictAllToString(newdict)
        elif mask & DICT_KEYS_TO_STRING:
            newdict = self.dictKeysToString(newdict)
        elif mask & DICT_VALS_TO_STRING:
            newdict = self.dictValuesToString(newdict)

        return newdict

    def toString(self, obj):
        if isinstance(obj, tuple):
            return tuple([self.toString(o) for o in obj])
        elif isinstance(obj, list):
            return [self.toString(o) for o in obj]
        elif isinstance(obj, set):
            return {self.toString(o) for o in obj}
        elif isinstance(obj, dict):
            return self.convertDictionary(obj)

        if self.enforcing:
            assert isinstance(obj, str), 'Expected string, got %s' % str(type(obj))
            return obj
        else:
            if isinstance(obj, bytes):
                return obj.decode()
            elif isinstance(obj, str):
                return obj
            elif self.active_mask & AGGRESSIVE:
                if hasattr(obj, __str__):
                    return str(obj)
                else:
                    return obj
            else:
                return obj

    def toBytes(self, obj):
        if isinstance(obj, tuple):
            return tuple([self.toBytes(o) for o in obj])
        elif isinstance(obj, list):
            return [self.toBytes(o) for o in obj]
        elif isinstance(obj, set):
            return {self.toBytes(o) for o in obj}
        elif isinstance(obj, dict):
            return self.convertDictionary(obj)

        if self.enforcing:
            assert isinstance(obj, bytes), 'Expected bytes, got %s' % str(type(obj))
            return obj
        else:
            if isinstance(obj, bytes):
                return obj
            elif isinstance(obj, str):
                return obj.encode()
            elif self.active_mask & AGGRESSIVE:
                if isinstance(obj, int):
                    return bytes((obj,))
                else:
                    return obj
            else:
                return obj

    def convertParam(self, param):
        mask = self.active_mask
        if mask == NOCONVERT or param is None:
            return param
        elif isinstance(param, dict):
            return self.convertDictionary(param)
        elif mask & BYTES:
            return self.toBytes(param)
        elif mask & STRING:
            return self.toString(param)
        else:
            return param

    def __call__(self, func):
        code = func.__code__
        argcount = code.co_argcount
        argnames = code.co_varnames[:argcount]
        fn_defaults = func.__defaults__ or list()
        argdefs = dict(zip(argnames[-len(fn_defaults):], fn_defaults))

        def wrapped_func(*args, **kwargs):
            positional = [av for av in zip(argnames, args)]
            defaulted = [((a, argdefs[a])) for a in argnames[len(args):] if a not in kwargs]
            nameless = [repr(a) for a in args[argcount:]]
            keyword = [av for av in kwargs.items()]
            nargs = positional + defaulted + nameless + keyword

            newkwargs = {}
            for arg in nargs:
                name = arg[0]
                val = arg[1]
                if name in self.named_in:
                    self.active_mask = self.named_in[name]
                else:
                    self.active_mask = self.unnamed_in
                newkwargs[name] = self.convertParam(val)

            ret = func(**newkwargs)
            self.active_mask = self.returns
            return self.convertParam(ret)

        wrapped_func.__name__ = func.__name__
        wrapped_func.__doc__ = func.__doc__

        return wrapped_func

def convert3kstr(obj, mask, enforcing=False):
    wrap = wrap3kstr(enforcing=enforcing)
    wrap.active_mask = mask
    return wrap.convertParam(obj)
