"""PIL (Python Imaging Library) supported image stacks and slices"""
# This uses PIL to read images. For some formats PIL supports image stacks and these are exposed as
# stacks. All formats are exposed as image sources. Some formats require special handling since they
# aren't implemented well. Hopefully in newer versions of PIL these special handlings won't get in
# the way.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import struct
from sys import byteorder
from os import SEEK_CUR
from io import open
from types import ClassType
from abc import ABCMeta, abstractproperty, abstractmethod

from PIL import Image, ImageFile

from numpy import dtype, sctypes, array
from numpy import bool_, uint8,uint16,uint32, int8,int16,int32, float32,float64 #float16

from .._stack import FileImageStack, FileImageSlice, FileImageStackHeader, FixedField
from .._single import FileImageSource
from .._util import check_file_obj
from ...types import check_image, get_im_dtype_and_nchan
from ..._util import String

from distutils.version import StrictVersion
if not hasattr(Image, 'PILLOW_VERSION') or StrictVersion(Image.PILLOW_VERSION) < StrictVersion('2.0'):
    raise ImportError

########## PIL dtypes ##########
_native = byteorder!='little'
_mode2dtype = None
_dtype2mode = None
def _init_pil():
    from ...types import create_im_dtype as d
    global _mode2dtype, _dtype2mode
    Image.init()
    # Add common extensions for the SPIDER format
    Image.register_extension("SPIDER", ".spi")
    Image.register_extension("SPIDER", ".stk")
    
    # TODO: could use len(PIL.ImageMode.getmode(mode).bands) and PIL.ImageMode.getmode(mode).basetype to auto-generate these conversions
    _mode2dtype = {
        #'P' is a special case
        # Some of these modes will actually never show up because they are raw modes.

        'RGB': d(uint8,False,3), 'RGBX':d(uint8,False,4), # however the fourth one is "padding"
        'RGBA':d(uint8,False,4), 'RGBa':d(uint8,False,4), # non-premultiplied and pre-multiplied
        'CMYK':d(uint8,False,4), 'YCbCr':d(uint8,False,3),
        'LAB': d(uint8,False,3), 'HSV':d(uint8,False,3),
        'LA':  d(uint8,False,2), # grayscale with alpha

        '1':d(bool_),'L':d(uint8),'I':d(int32,_native),
        'I;8':d(uint8),'I;8S':d(int8),
        'I;16':d(uint16),'I;16L':d(uint16),'I;16B':d(uint16,True),'I;16N':d(uint16,_native),
        'I;16S':d(int16),'I;16LS':d(int16),'I;16BS':d(int16,True),'I;16NS':d(int16,_native),
        'I;32':d(uint32),'I;32L':d(uint32),'I;32B':d(uint32,True),'I;32N':d(uint32,_native),
        'I;32S':d(int32),'I;32LS':d(int32),'I;32BS':d(int32,True),'I;32NS':d(int32,_native),

        'F':d(float32,_native),
        #'F;16F':d(float16),'F;16BF':dt(float16,True),'F;16NF':dt(float16,_native),
        'F;32F':d(float32),'F;32BF':d(float32,True),'F;32NF':d(float32,_native),
        'F;64F':d(float64),'F;64BF':d(float64,True),'F;64NF':d(float64,_native),
    }
    _dtype2mode = {
        # mode, rawmode (little endian), rawmode (big endian)
        # Multi-channel and bit images are special cases
        #float16:('F','F;16F','F;16BF'), # F;16 can only come from integers...
    }
    # Build _dtype2mode
    for t in sctypes['uint']:
        nb = dtype(t).itemsize
        if   nb == 1: _dtype2mode[t] = ('L','L','L')
        elif nb == 2: _dtype2mode[t] = ('I;16','I;16','I;16B')
        elif nb == 4: _dtype2mode[t] = ('I','I;32','I;32B')
        else: nb = str(nb*8); _dtype2mode[t] = ('I','I;'+nb,'I;'+nb+'B')
    for t in sctypes['int']:
        nb = dtype(t).itemsize
        if nb == 1: _dtype2mode[t] = ('I','I;8S','I;8S')
        else: nb = str(nb*8); _dtype2mode[t] = ('I','I;'+nb+'S','I;'+nb+'BS')
    for t in sctypes['float']:
        nb = dtype(t).itemsize
        if nb < 4: continue
        nb = str(nb*8); _dtype2mode[t] = ('F','F;'+nb+'F','F;'+nb+'BF')


########## PIL interaction class ##########
def imsrc2pil(im):
    im = im.data
    st, sh = im.strides[0], im.shape[1::-1]
    dt, nchan = get_im_dtype_and_nchan(im)
    if nchan > 1:
        if dt.type != uint8 or nchan > 4: raise ValueError
        mode = ('LA','RGB','RGBA')[nchan-2]
        return Image.frombuffer(mode, sh, im.data, 'raw', mode, st, 1)
    elif dt.kind == 'b':
        # Make sure data is actually saved as 1-bit data (both SciPy and PIL seem to be broken with this)
        im = im * uint8(255)
        return Image.frombuffer('L', sh, im.data, 'raw', 'L', st, 1).convert('1')
    else:
        mode = _dtype2mode.get(dt.type)
        if mode is None: raise ValueError
        return Image.frombuffer(mode[0], sh, im.data, 'raw', mode[2 if _native else 1], st, 1)
def _accept_all(im): return True
def _accept_none(im): return False
class _PILSource(object):
    """
    This is the class that does most of the work interacting with the PIL library. It is
    implemented this way so specific formats can derive from this class to change some of the
    behaviors. When first constructed this simply has references to the format, opener class,
    accepting function, and saving function. Upon opening or creating, a copy is returned which has
    various other attributes.
    """
    def __init__(self, frmt, open, accept, save):
        self.format = frmt
        self._open = open
        self.accept = (_accept_none if open is None else _accept_all) if accept is None else accept
        self._save = save
    @property
    def readable(self): return self._open is not None
    @property
    def writable(self): return self._save is not None
    
    def _open_pil_image(self, f, filename, **options):
        """
        Opens a PIL image object from the file object and filename with the given options. Can be
        overriden by subclasses to handle options. Default implementation does not support any
        options.
        """
        if len(options) > 0: raise ValueError("No options are supported")
        return self._open(f, filename)
    def _save_pil_image(self, f, filename, im, **options):
        """
        Saves a PIL image object to the file object and filename with the given options. Subclasses
        can override this to deal with the options. Otherwise options are just passed to the image's
        "encoderinfo".
        """
        im.encoderinfo = options
        im.encoderconfig = ()
        self._save(im, f, filename)

    def _copy(self, im, filename, readonly, open_options, save_options):
        """
        Copies the format, _open, accept, and _save properties of this object into a new object of
        the same type and adds the new properties.
        """
        c = type(self)(self.format, self._open, self.accept, self._save)
        c.im = im
        c.filename = filename
        c.readonly = readonly
        c.open_options = open_options
        c.save_options = save_options
        return c
    
    def open(self, file, readonly, open_options={}, save_options={}, **options):
        """
        Opens a file object/filename image with the given options. If subclasses support options
        they must override this function and split the options into options that are meant for
        _open_pil_image and _save_pil_image function calls. No options may be left outside of those
        groups. Returns a copy of this template object that has the image object and other
        attributes.
        """
        if hasattr(self, 'im'): raise RuntimeError("Not a template PILSource object")
        if not self.readable: raise ValueError("Cannot read from file format")
        if not readonly and not self.writable: raise ValueError("Cannot write to file format")
        if len(options) > 0: raise ValueError("No options are supported")
        if isinstance(file, String):
            filename, f = file, open(file, 'rb')
            try:
                return self._copy(self._open_pil_image(f, filename, **open_options),
                                  filename, readonly, open_options, save_options)
            except StandardError: f.close(); raise
        
        # File-like object
        if not check_file_obj(file, True, not readonly, True): raise ValueError('Unusable file object')
        start = file.tell()
        filename = file.name if (hasattr(file, 'name') and isinstance(file.name, String) and
                                 len(file.name) > 0 and file.name[0] != '<' and file.name[-1] != '>') else ''
        try:
            return self._copy(self._open_pil_image(file, filename, **open_options),
                              filename, readonly, open_options, save_options)
        except StandardError: f.seek(start); raise
    
    def openable(self, filename, prefix, readonly, **options):
        """
        Checks if a file is openable. Prefix is the first 16 bytes from the file. This should not be
        overriden. Instead override open or _open_pil_image.
        """
        if not (self.readable and (readonly or self.writable) and self.accept(prefix)): return False
        try:
            self.open(filename, readonly, **options).close()
            return True
        except (SyntaxError, IndexError, TypeError, ValueError, EOFError, struct.error): return False

    def create(self, filename, im, writeonly, open_options={}, save_options={}, **options):
        """
        Creates a new image file. If subclasses support options they must override this function and
        split the options into options that are meant for _open_pil_image and _save_pil_image
        function calls. No options may be left outside of those groups. Returns a copy of this
        template object that has the image object and other attributes. im is an ImageSource.
        """
        if hasattr(self, 'im'): raise RuntimeError("Not a template PILSource object")
        if not self.writable: raise ValueError("Cannot write to file format")
        if not writeonly and not self.readable: raise ValueError("Cannot read from file format")
        if len(options) > 0: raise ValueError("No options are supported")

        # Save image
        pil = imsrc2pil(im)
        with open(filename, 'wb') as f: self._save_pil_image(f, filename, pil, **save_options)
        
        # Open image
        if writeonly:
            # if writeonly we have to cache the dtype and shape properties
            s = self._copy(None, filename, False, open_options, save_options)
            s._dtype = im.dtype
            s._shape = im.shape
            return s
        f = open(filename, 'rb')
        try:
            return self._copy(self._open_pil_image(f, filename, **open_options),
                              filename, False, open_options, save_options)
        except StandardError: f.close(); raise
    
    def creatable(self, writeonly, **options):
        """
        Checks if a file is creatable. Unlike openable, subclasses must override this to support
        options.
        """
        return self.writable and (not writeonly or self.readable) and len(options) == 0

    # Only available after open/create
    def close(self):
        if hasattr(self, 'im') and hasattr(self.im, 'close'): self.im.close()
        del self.im
    @property
    def header_info(self):
        h = {'format':self.im.format}
        h.update(self.im.info)
        return h
    @property
    def is_stack(self): return False
    @property
    def dtype(self): return (self._dtype if self.im is None else
                             _mode2dtype[self.im.palette.mode if self.im.mode=='P' else self.im.mode])
    @property
    def shape(self): return self._shape if self.im is None else tuple(reversed(self.im.size))
    @property
    def data(self): # return ndarray
        dt = self.dtype
        return array(self.im.getdata(), dtype=dt).reshape(tuple(reversed(dt.shape+self.im.size)))
    def set_data(self, im): # im is an ImageSource
        reopen = self.im is not None # if writeonly don't reopen image
        if reopen: self.im.close()
        else:
            self._dtype = im.dtype
            self._shape = im.shape
        im = imsrc2pil(im)
        if self.filename:
            with open(self.filename, 'wb') as f: self._save_pil_image(f, self.filename, im, **self.save_options)
            if reopen:
                f = open(self.filename, 'rb')
                try: self.im = self._open_pil_image(f, self.filename, **self.open_options)
                except StandardError: f.close(); raise
        else:
            self.file.seek(0)
            self._save_pil_image(f, "", im, **self.save_options)
            if reopen:
                self.file.seek(0)
                self.im = self._open_pil_image(self.file, "", **self.open_options)

def _get_prefix(file):
    if isinstance(file, String):
        with open(file, 'rb') as f: return f.read(16)
    data = file.read(16)
    file.seek(-len(data), SEEK_CUR)
    return data
def _open_source(sources, frmt, f, readonly, **options):
    if frmt is not None:
        if frmt not in sources: raise ValueError('Unknown format')
        return sources[frmt].open(f, readonly, **options)
    prefix = _get_prefix(f)
    for s in sources.itervalues():
        if s.readable and (readonly or s.writable) and s.accept(prefix):
            try: return s.open(f, readonly, **options)
            except (SyntaxError, IndexError, TypeError, ValueError, struct.error): pass
    raise ValueError('Unknown format')
def _openable_source(sources, frmt, f, filename, readonly, **options):
    prefix = _get_prefix(f)
    return (any(s.openable(filename, prefix, readonly, **options) for s in sources.itervalues())
            if frmt is None else
            (frmt in sources and sources[frmt].openable(filename, prefix, readonly, **options)))


########## Image Source ##########
_sources, _read_formats, _write_formats = None, None, None
def _init_source():
    global _sources, _read_formats, _write_formats
    if _mode2dtype is None: _init_pil()
    _source_classes = { }

    stub_formats = set(frmt for frmt,(clazz,accept) in Image.OPEN.iteritems() if
                       isinstance(clazz,(type,ClassType)) and issubclass(clazz,ImageFile.StubImageFile))
    stub_formats.add('MPEG') # MPEG is not registered properly as a stub
    _read_formats = frozenset(Image.OPEN) - stub_formats
    _write_formats = frozenset(Image.SAVE) - stub_formats
    _sources = {
        frmt:_source_classes.get(frmt,_PILSource)(frmt,clazz,accept,Image.SAVE.get(frmt))
        for frmt,(clazz,accept) in Image.OPEN.iteritems()
        if not isinstance(clazz,(type,ClassType)) or not issubclass(clazz,ImageFile.StubImageFile)
        }
    # Add write-only formats
    _sources.update({frmt:_source_classes.get(frmt,_PILSource)(frmt,None,None,Image.SAVE[frmt])
                     for frmt in (_write_formats-_read_formats)})

class PIL(FileImageSource):
    @staticmethod
    def __parse_opts(slice, options): # no ** here on options because we want to modify them
        if slice is not None:
            # Slice was given, must be a stack-able type
            if _stacks is None: _init_stacks()
            slice = int(slice)
            if slice < 0: raise ValueError('Slice must be a non-negative integers')
            options['slice'] = slice
            return _stacks
        if _sources is None: _init_source()
        return _sources
            
    @classmethod
    def open(cls, f, readonly, format=None, slice=None, **options):
        sources = PIL.__parse_opts(slice, options)
        return PIL(_open_source(sources, format, f, readonly, **options))

    @classmethod
    def _openable(cls, filename, f, readonly, format=None, slice=None, **options):
        try: sources = PIL.__parse_opts(slice, options)
        except ValueError: return False
        return _openable_source(sources, format, f, filename, readonly, **options)

    @classmethod
    def create(cls, filename, im, writeonly, format=None, **options):
        if _sources is None: _init_source()
        if format is None:
            from os.path import splitext
            format = Image.EXTENSION.get(splitext(filename)[1].lower())
            if format is None: raise ValueError('Unknown file extension')
        return _sources[format].create(filename, im, writeonly, **options)

    @classmethod
    def _creatable(cls, filename, ext, writeonly, format=None, **options):
        if _sources is None: _init_source()
        if format is None:
            format = Image.EXTENSION.get(ext)
            if format is None: return False
        return _sources[format].creatable(writeonly, **options)

    @classmethod
    def name(cls): return "PIL"

    @classmethod
    def print_help(cls, width):
        from ....imstack import Help
        p = Help(width)
        p.title("Python Imaging Library (PIL) Image Handler")
        p.text("""
PIL is a common library for reading various image formats in Python. Technically we use the PILLOW
fork of PIL which is the standard replacement for PIL. This requires PILLOW v2.0 or newer.

This supports the option 'format' to force one of the supported formats listed below. Some formats
support multiple images in a single file (see 'PIL-Stack' for more information). For these formats
you may specify the option 'slice' to select which frame to use when loading them but not saving
them. If this option is given for a format that doesn't support slices, the slice is out of bounds,
or when saving, the file will fail to load.

When saving, only a small amount of effort will be made to convert the image to a data-type that the
format supports (mainly making RGB into palletted). If the format does not support the data-type it
will fail.

Extensions listed below are used to determine the format to save as if not explicit, during loading
the contents of the file are always used to determine the format.

Supported image formats (read/write):""")
        p.list(*sorted(cls.__add_exts(cls.formats(True, True))))
        p.newline()
        p.text("""Supported image formats [read-only]:""")
        p.list(*sorted(cls.formats(True, False)))
        p.newline()
        p.text("""Supported image formats [write-only]:""")
        p.list(*sorted(cls.__add_exts(cls.formats(False, True))))
        p.newline()
        p.text("See also:")
        p.list('PIL-Stack')

    @classmethod
    def __add_exts(cls, formats):
        frmt2exts = {}
        for ext,frmt in Image.EXTENSION.iteritems(): frmt2exts.setdefault(frmt,[]).append(ext)
        return [frmt+((' ('+(', '.join(frmt2exts[frmt])) + ')') if frmt in frmt2exts else '')
                for frmt in formats]

    @classmethod
    def formats(cls, read, write):
        if _sources is None: _init_source()
        if read: return (_read_formats & _write_formats) if write else (_read_formats - _write_formats)
        elif write: return _write_formats - _read_formats
        else: return frozenset()

    def __init__(self, source):
        self._source = source
        super(PIL, self).__init__(source.readonly)
    def close(self): self._source.close()
    def _get_props(self): self._set_props(self._source.dtype, self._source.shape)
    def _get_data(self): return self._source.data
    def _set_data(self, im):
        self._source.set_data(im)
        self._set_props(self._source.dtype, self._source.shape)


########## Image Stack ##########
## TODO: PSD: random access except can never return to 0 (which is the "full image"?)
# TODO: header can change per-slice:
# Definitely: DCX, MIC, TIFF
# Maybe: GIF, SPIDER
class _PILStack(_PILSource):
    def __init__(self, frmt, open, accept, save=None):
        super(_PILStack, self).__init__(frmt, open, accept, None)
    def open(self, f, filename, readonly, slice=None, **options):
        s = super(_PILStack, self).open(f, filename, readonly, **options)
        if not s.is_stack: raise ValueError("File is not a stack")
        if slice is not None: s.seek(slice)
        return s
        
    @property
    def is_stack(self): return True
    def seek(self, idx):
        z = 0
        while z != idx:
            try: pil.seek(z); z += 1
            except EOFError: break
        if z != idx: raise ValueError('Slice index out of range')
    def slices(self, stack):
        # Default behavior for slices is to read all of them store them. This is pretty bad, but
        # nothing we can really do. The good thing is many formats have a better solution.
        slices, z = [], 0
        while True:
            try:
                pil.seek(z)
                slices.append(_PILSlice(stack, self, z))
                z += 1
            except EOFError: break
        return slices
class _PILSlice(FileImageSlice):
    def __init__(self, stack, pil, z):
        super(_PILSlice, self).__init__(stack, z)
        self._set_props(pil.dtype, pil.shape)
        self._data = pil.data
    def _get_props(self): pass
    def _get_data(self): return self._data

class _RandomAccessPILStack(_PILStack):
    # A PIL stack for formats that allow random-access of slices. Each format exposes the total
    # number of slices differently though, so there are subclasses for that.
    __metaclass__ = ABCMeta
    @abstractproperty
    def depth(self): return 0
    def seek(self, idx):
        if idx >= self.depth: raise ValueError('Slice index out of range')
        try: pil.seek(idx)
        except EOFError: raise ValueError('Slice index out of range')
    def slices(self, stack):
        return [_RandomAccessPILSlice(stack, self, z) for z in xrange(self.depth)]
class _RandomAccessPILSlice(FileImageSlice):
    def __init__(self, stack, pil, z):
        super(_RandomAccessPILSlice, self).__init__(stack, z)
        pil.seek(z)
        self._set_props(pil.dtype, pil.shape)
        self._pil = pil
    def _get_props(self): pass
    def _get_data(self):
        self._pil.seek(self._z)
        return self._pil.data

class _IMStack(_RandomAccessPILStack):
    @property
    def depth(self): return self.im.info["File size (no of images)"]
class _SPIDERStack(_RandomAccessPILStack):
    @property
    def depth(self): return self.im.nimages
    @property
    def is_stack(self): return self.im.istack != 0
class _DCXStack(_RandomAccessPILStack):
    @property
    def depth(self): return len(self.im._offset)
class _MICStack(_RandomAccessPILStack):
    @classmethod
    def depth(self): return len(self.im.images)
    @classmethod
    def is_stack(self): return self.im.category == Image.CONTAINER
class _TIFFStack(_RandomAccessPILStack):
    # Not quite random-access because we don't know the depth until we have gone all the way
    # through once. Also, internally, it does use increment and reset but is fast since it can skip
    # all of the image data.
    _depth = None
    @property
    def depth(self):
        if self._depth is None:
            z = 0
            while True:
                try: pil.seek(z); z += 1
                except EOFError: break
            self._depth = z
        return self._depth
    @property
    def header_info(self):
        h = {'format':self.im.format}
        h.update(self.im.tag)
        h.update(self.im.info)
        return h


class _PSDStack(_PILStack):
    @classmethod
    def depth(self): return len(self.im.layers)+1

_stacks = None
def _init_stack():
    global _stacks
    if _mode2dtype is None: _init_pil()
    _stack_classes = {
        'IM': _IMStack,
        'SPIDER': _SPIDERStack,
        'TIFF': _TIFFStack,
        'DCX': _DCXStack,
        'MIC': _MICStack,
        'PSD': _PSDStack,
    }
    _stacks = {
        frmt:_stack_classes.get(frmt,_PILStack)(frmt,clazz,accept)
        for frmt,(clazz,accept) in Image.OPEN.iteritems()
        if isinstance(clazz,(type,ClassType)) and clazz.seek != Image.Image.seek
        }

class PILStack(FileImageStack):
    @classmethod
    def open(cls, f, readonly=False, format=None, **options):
        if _stacks is None: _init_stack()
        return PIL(_open_source(_stacks, format, f, readonly, **options))
    
    @classmethod
    def _openable(cls, filename, f, readonly, format=None, **options):
        if _stacks is None: _init_stack()
        return _openable_source(_stacks, format, f, filename, readonly, **options)

    # TODO: support writing
    # Need to add create/creatable, add _insert/_delete methods, and update many other things
    # Possibly save-able:
    #    IM:     supports "frames" param but I don't see how it actually saves multiple frames
    #    GIF:    see gifmaker.py in the Pillow module
    #    SPIDER: maybe
    #    TIFF:   maybe
    @classmethod
    def _can_write(cls): return False

    @classmethod
    def name(cls): return "PIL-Stack"
    @classmethod
    def print_help(cls, width):
        from ....imstack import Help
        p = Help(width)
        p.title("Python Imaging Library (PIL) Image Stack")
        p.text("""
PIL is a common library for reading various image formats in Python. Technically we use the PILLOW
fork of PIL which is the standard replacement for PIL. This requires PILLOW v2.0 or newer.

PIL is a common library for reading various image formats in Python. Some of those formats support
several image slices in a single file, including TIFF, IM, DCX, and GIF. The PIL formats that
support several image slices can be loaded as a stack. Several of these formats have limitations
such as being only able to read sequentially and may incure higher overheads when not being read
in the manner they were intended to be.

Currently there is no support for writing a PIL-supported image stack format.

This supports the option 'format' to force one of the supported formats listed below.

Supported image formats:""")
        p.list(*sorted(cls.formats()))
        p.newline()
        p.text("See also:")
        p.list('PIL')
        

    @classmethod
    def formats(cls):
        if _stacks is None: _init_stack()
        return _stacks.keys()

    def __init__(self, stack):
        self._stack = stack
        super(PILStack, self).__init__(PILHeader(stack), stack.slices(self), True)
    def close(self): self._stack.close()
    
class PILHeader(FileImageStackHeader):
    _fields = None
    def __init__(self, stack, **options):
        data = stack.header_info()
        data['options'] = options
        self._fields = {k:FixedField(lambda x:x,v,False) for k,v in data.iteritems()}
        super(PILHeader, self).__init__(data)
    def save(self):
        if self._imstack._readonly: raise AttributeError('header is readonly')
    def _update_depth(self, d): pass # not possible since it is always read-only
    def _get_field_name(self, f):
        return f if f in self._fields else None