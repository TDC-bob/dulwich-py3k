"""
Transparently wraps things to go from bytes <-> str
"""

NOCONVERT = 0
BYTES = 1
STRING = 2
DICT_KEYS_TO_BYTES = 4
DICT_KEYS_TO_STRING = 8
DICT_VALS_TO_BYTES = 16
DICT_VALS_TO_STRING = 32
AGGRESSIVE = 64

class wrap3kstr(object):
    def __init__(self, unnamed_in=NOCONVERT, returns=NOCONVERT, **kwargs):
        self.unnamed_in = self._sanity_check(unnamed_in)
        self.returns = self._sanity_check(returns)
        self.named_in = {}
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
        newdict = param
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

    def toString(self, obj):
        if isinstance(obj, bytes):
            return obj.decode()
        elif isinstance(obj, str):
            return obj
        elif isinstance(obj, tuple):
            return tuple([self.toString(o) for o in obj])
        elif isinstance(obj, list):
            return [self.toString(o) for o in obj]
        else:
            return obj

    def toBytes(self, obj):
        if isinstance(obj, bytes):
            return obj
        elif isinstance(obj, str):
            return obj.encode()
        elif isinstance(obj, tuple):
            return tuple([self.toBytes(o) for o in obj])
        elif isinstance(obj, list):
            return [self.toBytes(o) for o in obj]
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
            return convertDictionary(param)
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
            for (name, val) in nargs:
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

def convert3kstr(obj, mask):
    wrap = wrap3kstr()
    wrap.active_mask = mask
    return wrap.convertParam(obj)
