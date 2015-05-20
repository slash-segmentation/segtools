#pylint: disable=protected-access

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from abc import ABCMeta, abstractmethod
from collections import Iterable, OrderedDict
from numbers import Integral
from itertools import repeat, izip
from numpy import ndarray, ceil
import functools

from .._stack import ImageStack, HomogeneousImageStack, ImageSlice, Homogeneous
from .._util import String
from ..types import is_image, get_im_dtype
from ..source import ImageSource
from ...imstack import Help
from ...general.datawrapper import DictionaryWrapperWithAttr
from ...general.enum import Enum
from ...general.utils import all_subclasses

__all__ = ['FileImageStack','HomogeneousFileImageStack','FileImageSlice','FileImageStackHeader','Field','FixedField','NumericField','MatchQuality']

def slice_len(start, stop, step): return max(int(ceil((stop-start)/step)), 0) #max((stop-start+step-(1 if step>0 else -1))//step, 0)
def check_int(i):
    if int(i) == i: return int(i)
    raise ValueError()

class MatchQuality(int, Enum):
    NotAtAll = 0
    Unlikely = 25
    Maybe = 50
    Likely = 75
    Definitely = 100

class _FileImageStackMeta(ABCMeta):
    """The meta-class for file image stacks, which extends ABCMeta and calls Help.register if applicable"""
    def __new__(cls, clsname, bases, dct):
        c = super(_FileImageStackMeta, cls).__new__(cls, clsname, bases, dct)
        n = c.name()
        if n is not None: Help.register((n,c.__name__), c.print_help)
        return c

class FileImageStack(ImageStack):
    """
    A stack of 2D image slices on disk. This is either backed by a file format that already has
    multiple slices (like MRC, MHA/MHD, and TIFF) or a collection of seperate 2D image files.

    When loading an image stack only the header(s) is/are loaded. THe image data is not read until
    accessed.

    In addtion to getting slices in ImageStack we add setting, inserting, and deleting slices.

    When writing, slices are typically saved immediately but the header typically is not. Call
    the save() function to force all data including the header to be saved.
    """

    __metaclass__ = _FileImageStackMeta

    @classmethod
    def open(cls, filename, readonly=False, **options):
        """
        Opens an existing image-stack file or series of images as a stack. If 'filename' is a
        string then it is treated as an existing file. Otherwise it needs to be an iterable of
        file names. Extra options are only supported by some file formats.
        """
        if isinstance(filename, String):
            highest_cls, highest_mq = None, MatchQuality.NotAtAll
            for cls in all_subclasses(cls):
                if not cls._can_read() or not (readonly or cls._can_write()): continue
                with open(filename, 'r') as f: mq = cls._openable(f, **options)
                if mq > highest_mq: highest_cls, highest_mq = cls, mq
                if mq == MatchQuality.Definitely: break
            if highest_mq == MatchQuality.NotAtAll: raise ValueError('Unknown file format')
            return highest_cls.open(filename, readonly, **options)
        elif isinstance(filename, Iterable):
            from ._collection import FileCollectionStack
            return FileCollectionStack.open(filename, readonly, **options)
        else: raise ValueError()

    @classmethod
    def openable(cls, filename, readonly=False, **options):
        """
        Checks if an existing image-stack file or series of images as a stack can be opened with
        the given arguments. If 'filename' is a string then it is treated as an existing file.
        Otherwise it needs to be an iterable of file names. Extra options are only supported by
        some file formats.
        """
        try:
            if isinstance(filename, String):
                for cls in all_subclasses(cls):
                    with open(filename, 'r') as f:
                        if cls._can_read() and (readonly or cls._can_write()) \
                           and cls._openable(f, **options) > MatchQuality.NotAtAll: return True
            elif isinstance(filename, Iterable): return True
        except StandardError: pass
        return False

    @classmethod
    def create(cls, filename, ims, **options):
        """
        Creates an image-stack file or writes to a series of images as a stack. If 'filename' is a
        string then it is treated as a new file. Otherwise it needs to be an iterable of file names
        (even empty) or None in which case a collection of files are used to write to. Extra options
        are only supported by some file formats. When filenames is None or an empty iterable
        then you need to give a "pattern" option with an extension and %d in it.

        The new stack is created from the given iterable of ndarrays or ImageSources. While some
        formats can be created with no images given, many do require at least one image to be
        created so that at least the dtype and shape is known. Some formats may only allow
        homogeneous image stacks, however selection of a format is purely on file extension and
        options given.
        """
        ims = [ImageSource.as_image_source(im) for im in ims]
        if isinstance(filename, String):
            from os.path import splitext
            ext = splitext(filename)[1].lower().lstrip('.')
            highest_cls, highest_mq = None, MatchQuality.NotAtAll
            for cls in all_subclasses(cls):
                if not cls._can_write(): continue
                mq = cls._creatable(filename, ext, **options)
                if mq > highest_mq: highest_cls, highest_mq = cls, mq
                if mq == MatchQuality.Definitely: break
            if highest_mq == MatchQuality.NotAtAll: raise ValueError('Unknown file extension')
            return highest_cls.create(filename, ims, **options)
        elif filename is None or isinstance(filename, Iterable):
            from ._collection import FileCollectionStack
            return FileCollectionStack.create(filename, ims, **options)
        else: raise ValueError()

    @classmethod
    def creatable(cls, filename, **options):
        """
        Checks if a filename can written to as a new image stack. The filename needs to either be a
        string or an iterable of file names (even empty) or None. Extra options are only supported
        by some file formats. When filenames is None or an empty iterable then you need to give a
        "pattern" option with an extension and %d in it.
        """
        try:
            if isinstance(filename, String):
                from os.path import splitext
                ext = splitext(filename)[1].lower().lstrip('.')
                return any(cls._can_write() and cls._creatable(filename, ext, **options)>MatchQuality.NotAtAll #pylint: disable=star-args
                           for cls in all_subclasses(cls))
            elif filename is None or isinstance(filename, Iterable): return True
        except StandardError: pass
        return False

    @classmethod
    def formats(cls, read=True):
        frmts = []
        for cls in all_subclasses(cls):
            f = cls.name()
            if f is not None and (read and cls._can_read or not read and cls._can_write):
                frmts.append(f)
        return frmts

    @classmethod
    def _openable(cls, f, **opts): #pylint: disable=unused-argument
        """
        [To be implemented by format, default is nothing is openable]

        Return how likely a readable file-like object is openable as a FileImageStack given the
        dictionary of options. Returns a MatchQuality rating. If this returns anything besides
        NotAtAll then the class must provide a static/class method like:
            `open(filename_or_file, readonly, **options)`
        Option keys are always strings, values can be either strings or other values (but strings
        must be accepted for any value and you must convert, if possible). An exception should be
        thrown if there any unknown option keys or option values cannot be used.
        """
        return MatchQuality.NotAtAll

    @classmethod
    def _creatable(cls, filename, ext, **opts): #pylint: disable=unused-argument
        """
        [To be implemented by format, default is nothing is creatable]

        Return how likely a filename/ext (without .) is creatable as a FileImageStack given the
        dictionary of options. Returns a MatchQuality rating. If this returns anything besides
        NotAtAll then the class must provide a static/class method like:
            `create(filename, list_of_ImageSources, **options)`
        Option keys are always strings, values can be either strings or other values (but strings
        must be accepted for any value and you must convert, if possible). An exception should be
        thrown if there any unknown option keys or option values cannot be used.
        """
        return MatchQuality.NotAtAll

    @classmethod
    def _can_read(cls):
        """[To be implemented by format, default is readable]"""
        return True

    @classmethod
    def _can_write(cls):
        """[To be implemented by format, default is writable]"""
        return True

    @classmethod
    def name(cls):
        """
        [To be implemented by format, default causes the format to not be registered]

        Return the name of this image stack handler to be displayed in help outputs.
        """
        return None

    @classmethod
    def print_help(cls, width):
        """
        [To be implemented by format, default prints nothing]

        Prints the help page of this image stack handler.
        """
        pass

    def __init__(self, header, slices, readonly=False):
        super(FileImageStack, self).__init__(slices)
        self._header = header
        header._imstack = self
        self._readonly = bool(readonly)

    # General
    def save(self):
        if self._readonly: raise AttributeError('readonly')
        self._header.save()
    def close(self): pass
    def __delete__(self): self.close()
    @property
    def readonly(self): return self._readonly
    @property
    def header(self): return self._header
    def print_detailed_info(self, width=None):
        fill = ImageStack._get_print_fill(width)
        super(FileImageStack, self).print_detailed_info()
        if not self.header or len(self.header) == 0: print(fill("No header information"))
        else:
            print(fill("Header:"))
            for k,v in self._header.iteritems(): print(fill("  %s = %s" % (k,v)))

    # Internal slice maniplutions - primary functions to be implemented by base classes
    # Getting and setting individual slices is done in the FileImageSlice objects
    @abstractmethod
    def _delete(self, idxs):
        """
        Internal slice deletion function to be implemented by sub-classes. The given idxs is a list
        of tuples each with two values: start and stop. Each tuple represents a continous (step 1)
        range of values from start to stop-1. The start value is always less than the stop value.
        The tuples themselves are in descending order. This will usually be only called with a list
        of a single tuple. If stop == self._d we are removing from the end.

        This method must call _delete_slices(start, stop) which updates the internal slices list,
        the cache, and the stack depth. It should be called when appropiate (as soon as the data is
        deleted). Also note that after that function is called, the indices of all slices after
        "stop" change, so care must be taken to call it in the right order.
        """
        pass

    @abstractmethod
    def _insert(self, idx, ims):
        """
        Internal slice insertion function to be implemented by sub-classes. The idx is the start of
        the insertion (what currently is at idx will end up after the inserted images). If idx is
        equal to the current number of slices then the images are appended. The argument ims is
        always a list of ImageSource objects.

        The function must call _insert_slices(idx, slices) which updates the internal slices list,
        the cache (in part), and the stack depth. It should be called when appropiate (after
        "space" has been made but preferrably before the data is saved which may not always be
        possible).

        This must call FileImageSlice._cache_data(im) after a slice is written.
        """
        pass


    # Caching of slices
    def __update_cache(self, c): self._cache = OrderedDict(izip(c, repeat(True)))

    def _delete_slices(self, start, stop):
        ss = stop - start

        # Update cache
        if self._cache_size: self.__update_cache(i-ss if i>=stop else i for i in self._cache if i<start or i>=stop)

        # Update slices and depth
        del self._slices[start:stop]
        self._d -= ss
        for z in xrange(start, self._d): self._slices[z]._update(z)
        self._header._update_depth(self._d)
        if self._d <= 1: self._homogeneous = Homogeneous.Both
        elif self._homogeneous != Homogeneous.Both: self._homogeneous = None # may have become homogeneous with the deletion

    def _insert_slices(self, idx, slices):
        ln = len(slices)

        # Update slices and depth
        self._slices[idx:idx] = slices
        self._d += ln
        for z in xrange(idx+ln, self._d): self._slices[z]._update(z)
        self._header._update_depth(self._d)

        # Update cache
        if self._cache_size: self.__update_cache(i+ln if i>=idx else i for i in self._cache)

    # Setting and adding slices
    def __setitem__(self, idx, ims):
        """
        Sets slices to new images, writing them to disk. The images can be either ndarrays or
        ImageSource. Accepts advanced indexing as follows:

        * Integer index: accepts integers in [-N, N] where negative values are relative to the end
        of the stack. If N is given than the image is appended. You can only set single images with
        this method.

        * Slice index: accepts all slices and they and converted into indices like is done for
        lists. You must set an iterable of images, with some restricitions:
          - If step is +1 or -1 any length iterable is allowed, if the iterable is smaller than the
            slice then extra entries are removed from the stack, if the iterable is larger than the
            slice than extra entries are inserted into the stack at the last value of the slice.
          - If step is any other value than the iterable must have exactly the same length as the
            slice.

        * Iterable index of integers: accepts an iterable integers each in [-N, N] as per integer
        indices. Note that since an index of N appends, the acceptable range will possibly be
        different as the indices are read. You must set an iterable of images which is the same
        length as the number of indices.

        In general, to conserve memory, when setting a long list of images it is preferable to use
        ImageSource objects which can dynamically load or create the image data.

        Notes on exceptions: any set is broken down into individual operation of set, delete, and
        "create space" (for inserting). If any operation causes an exception, it should
        happen before it has caused any changes to the stack or data on disk. Thus, when an
        exception does occur the stack will still be valid but only some of the requested operations
        will have been completed. The strange one is "create space" which may leave slices of
        garbage data if a subsequent set operation raises an exception.
        """
        if self._readonly: raise Exception('readonly')
        if isinstance(idx, Integral): self.__set_int(idx, ims)
        elif isinstance(idx, slice): self.__set_slice(idx, ims)
        elif isinstance(idx, Iterable): self.__set_iter(idx, ims)
        else: raise TypeError('index')
    def __set_int(self, idx, ims):
        if idx < 0: idx += self._d
        if not (0 <= idx <= self._d): raise IndexError()
        if idx == self._d: self._insert(idx, [ImageSource.as_image_source(ims)])
        else:              self._slices[idx].data = ims
    def __set_slice(self, idx, ims):
        start, stop, step = idx.indices(self._d)
        length = slice_len(start, stop, step)
        ims = [ImageSource.as_image_source(im) for im in ims]
        if step == +1:
            for i in xrange(min(length, len(ims))): self._slices[start+i].data = ims[i]
            if   len(ims) < length: self._delete([(start+len(ims), stop)])
            elif len(ims) > length: self._insert(stop, ims[length:])
        elif step == -1:
            for i in xrange(min(length, len(ims))): self._slices[start-i].data = ims[i]
            if   len(ims) < length: self._delete([(stop, start-len(ims)+1)])
            elif len(ims) > length: self._insert(stop+1, ims[:length-1:-1])
        elif len(ims) != length:
            raise ValueError("setting slices with |step|>1 requires an iterable of the same length as the indices")
        else:
            for i, im in enumerate(ims): self._slices[start+i*step].data = im
    def __set_iter(self, idx, ims):
        idx = [check_int(i+self._d) if i < 0 else i for i in idx]
        functools.reduce(lambda d,i: (d+1 if i==d else d) if 0<=i<=d else [][0], idx, self._d) # check if any indicies will be out of range - [][0] causes an IndexError
        ims = [ImageSource.as_image_source(im) for im in ims]
        if len(ims) != len(idx):
            raise ValueError("setting iterable indices requires an iterable of the same length as the indices")
        for i, im in izip(idx, ims):
            if i == self._d: self._insert(self._d, [im])
            else:            self._slices[i].data = im
    def append(self, im):
        """Appends a single slice, writing it to disk."""
        # equivilient to self[len(self)] = im
        if self._readonly: raise Exception('readonly')
        self._insert(self._d, [ImageSource.as_image_source(im)])
    def extend(self, ims):
        """Appends many slices, writing them to disk."""
        # equivilient to self[len(self):] = ims
        if self._readonly: raise Exception('readonly')
        ims = [ImageSource.as_image_source(im) for im in ims]
        self._insert(self._d, ims)
    def __iadd__(self, im):
        """Either appends or extends depending on the data type."""
        if isinstance(im, ImageSource):
            self.append(im)
        elif isinstance(im, ndarray):
            if is_image(im): self.append(im)
            else: self.extend(im)
        else: self.extend(im)
    def insert(self, i, im):
        """Insert a single slice, writing it to disk."""
        # equivilent to ims[i:i] = im
        if self._readonly: raise Exception('readonly')
        self._insert(i, [ImageSource.as_image_source(im)])

    # Removing slices
    def __delitem__(self, idx):
        """
        Removes slices. Accepts integers, index slices, or iterable indices. Updates the disk
        immediately. Typically only efficient when removing from the end (e.g. del ims[-1] or
        del ims[x:]). You can also use shorten to make sure you are removing from the end.
        """
        # We take the indices given to us and convert them into a "standard" format. The standard
        # format is a list of tuples of start/stop indices of a continous range. The continous
        # ranges are always specified with low-number first then high number (which is +1 the actual
        # range end, like what you woudl give to the range function). The tuples themselves are
        # sorted such that the start index is decreasing.
        if self._readonly: raise Exception('readonly')
        if isinstance(idx, Integral):
            if idx < 0: idx += self._d
            if not (0 <= idx < self._d): raise IndexError()
            idx = [(idx,idx+1)]
        elif isinstance(idx, slice):
            start, stop, step = idx.indices(self._d)
            count = slice_len(start, stop, step)
            if count == 0: return
            # make step negative so we always move from the end towards the beginning
            if step > 0: start, stop, step = stop-1, start-1, -step
            idx = [(stop+1,start+1)] if step == -1 else [(i,i+1) for i in xrange(start, stop, step)]
        elif isinstance(idx, Iterable):
            # get a descending sorted list with all duplicate entries removed and negative values corrected
            idxx = list(sorted(set(check_int(i+self._d if i < 0 else i) for i in idx), reverse=True))
            if len(idxx) == 0: return
            if idxx[-1] < 0 or idxx[0] >= self._d: raise IndexError()
            idx = []
            prev = -1 # i+1 can never be equal to this the first iteration in the loop
            for i in idxx:
                if prev==i+1: idx[-1] = (i, idx[-1][1])
                else:         idx.append((i, i+1))
                prev = i
        else: raise TypeError('index')
        self._delete(idx)
    def shorten(self, count=1):
        """Removes 'count' slices from the end of the stack (default 1)."""
        # equivilient to del self[-count:]
        if self._readonly: raise Exception('readonly')
        if count <= 0 or count > self._d: raise ValueError('count')
        self._delete([(self._d-count,self._d)])
    def clear(self):
        """Remove all slices from the stack."""
        # equivilient to del self[:]
        if self._readonly: raise Exception('readonly')
        self._delete([(0,self._d)])

class HomogeneousFileImageStack(HomogeneousImageStack, FileImageStack):
    """
    An file-based image stack where every slice has the same shape and data type.
    """
    def __init__(self, header, slices, w, h, dtype, readonly=False):
        super(HomogeneousFileImageStack, self).__init__(w, h, dtype, slices, {'header':header,'readonly':readonly})
    def print_detailed_info(self, width=None):
        fill = ImageStack._get_print_fill(width)
        super(HomogeneousFileImageStack, self).print_detailed_info()
        if len(self.header) == 0: print(fill("No header information"))
        else:
            print(fill("Header:"))
            for k,v in self._header.iteritems(): print(fill("  %s = %s" % (k,v)))
    @abstractmethod
    def _delete(self, idxs): pass
    @abstractmethod
    def _insert(self, idx, ims): pass

class FileImageSlice(ImageSlice):
    """
    A image slice from an image stack. These must be implemented for specific formats. The
    implementor must either call _set_props during initialization or implement a non-trivial
    _get_props function (the trivial one would be def _get_props(self): pass).
    """
    def __init__(self, stack, z): super(FileImageSlice, self).__init__(stack, z)

    @ImageSlice.data.setter
    def set_data(self, im):
        self._cache_data(self._set_data(ImageSource.as_image_source(im)))

    def _cache_data(self, im):
        if self._stack._cache_size:
            im.flags.writeable = False
            self._stack._cache_it(self._z)
            self._cache = im
        self._stack._update_homogeneous_set(self._z, im.shape[:2], get_im_dtype(im))

    @abstractmethod
    def _set_data(self, im):
        """
        Internal function for setting image data. The image is an ImageSource object. If the image
        data is not acceptable (for example, the shape or dtype is not acceptable for this format)
        then an exception should be thrown. In the case of an exception, it must be thrown before
        any changes are made to this FileImageSlice properties or the data on disk.

        This method can optionally copy any metadata it is aware of from the image to this slice.

        This must return what self._get_data() would return.
        """
        pass

    def _update(self, z):
        """Update this slice when the Z value changes. By default this just sets the _z field."""
        self._z = z

class FileImageStackHeader(DictionaryWrapperWithAttr):
    """
    The header of an image stack. This is primarily a dictionary with built-in checking of names and
    values based on the image stack type. In general this provides image-format specific information
    and cannot be reliably queried between image stack types. One day a generalized "converter" may
    be created for common header values between different types.

    **Implementation Notes**
    The implementor must set the class fields _fields before calling super().__init__(). This
    contains the image stack for which this header is connected to, the known fields as a
    dictionary or ordered dictionary containing field-name to Field object, and the data for
    those fields as a dictionary of field-name to data. Whenever _data or _fields are changed
    directly you must call super()._check(). This is called for you automatically in
    super().__init__() if check is not False.

    The functions self.save(), self._update_depth(d), and self._get_field_name(f) must also be
    implemented. More information about those is provided in the abstract definitions.

    As a reminder, since this is an exension of DictionaryWrapperWithAttr, to set class fields that
    are not header fields you must either have them in the class definition or interact with
    self.__dict__.
    """
    __metaclass__ = ABCMeta
    def __init__(self, data=None, check=True):
        if '_data' not in self.__dict__: self.__dict__['_data'] = None
        if '_imstack' not in self.__dict__: self.__dict__['_imstack'] = None
        super(FileImageStackHeader, self).__init__({} if data is None else data)
        if '_fields' not in self.__dict__: raise TypeError
        if check: self._check()

    def _check(self):
        for _key,value in self._data.items():
            key = self._get_field_name(_key)
            if key is None: raise KeyError('%s cannot be in header' % _key)
            if key != _key: # key was changed, update data
                self._data[key] = value
                del self._data[_key]
        for key,field in self._fields.iteritems():
            if key in self._data: self._data[key] = field.cast(self._data[key], self)
            elif not field.opt: raise KeyError('%s must be in header' % key)


    @abstractmethod
    def save(self): pass
    @abstractmethod
    def _update_depth(self, d):
        """
        This is called whenever the depth of the associated image stack is changed. This may be
        because the image stack has shrunk or grown. Any header properties that need to be updated
        because of the this change should be updated here. The new depth is given. The image stack
        depth has already been changed as well (so the old depth is not directly queriable).
        """
        pass
    @abstractmethod
    def _get_field_name(self, f):
        """
        Get the field name given a requested name. This should tranform the field name if applicable
        (for example, if the format treats field names in a case-insensitive way this should change
        the name to lower-case, or if there are multiple names for the same field this should pick
        one). If the field name is illegal, None should be returned. If the returned field-name is
        not in self._fields then a new field is created with that name that is optional and has no
        restrictions on value. Some examples:

        Formats that have no fields:
            return None
        Formats that have a fixed set of fields, case-sensitive:
            return f if f in self._fields else None
        Formats that accept any field name, case-insensitive:
            return f.lower()
        """
        pass


    # Retrieval
    def __getitem__(self, key):  return self._data[self._get_field_name(key)]
    def __contains__(self, key): return self._get_field_name(key) in self._data

    # Deleting functions
    def __delitem__(self, key):
        if self._imstack._readonly: raise AttributeError('header is readonly')
        _key, key = key, self._get_field_name(key)
        if key not in self._data: raise KeyError('%s not in header' % _key)
        if key in self._fields and not self._fields[key].opt: raise AttributeError('%s cannot be removed from header' % _key)
        del self._data[key]
    def clear(self):
        # Only clears optional and custom values
        if self._imstack._readonly: raise AttributeError('header is readonly')
        for k in self._data:
            if k not in self._fields or self._fields[k].opt:
                del self._data[k]
            else:
                from warnings import warn
                warn('attempting to clear required field', RuntimeWarning)
    def pop(self, key, default=None):
        if self._imstack._readonly: raise AttributeError('header is readonly')
        _key, key = key, self._get_field_name(key)
        if key not in self._data: raise KeyError('%s not in header' % _key)
        if key in self._fields and not self._fields[key].opt: raise AttributeError('%s cannot be removed from header' % _key)
        value = self._data[key]
        del self._data[key]
        return value
    def popitem(self): raise AttributeError('Cannot pop fields from header')

    # Setting/inserting functions
    def __setitem__(self, key, value):
        if self._imstack._readonly: raise AttributeError('header is readonly')
        _key, key = key, self._get_field_name(key)
        if key is None: raise KeyError('%s cannot be added to header' % _key)
        f = self._fields.get(key, None)
        if f is None: self._data[key] = value
        elif f.ro: raise AttributeError('%s cannot be edited in header' % _key)
        else: self._data[key] = f.cast(value, self)
    def setdefault(self, key, default = DictionaryWrapperWithAttr._marker):
        if self._imstack._readonly: raise AttributeError('header is readonly')
        _key, key = key, self._get_field_name(key)
        if key is None: raise KeyError('%s cannot be added to header' % _key)
        if key not in self._data:
            if default is DictionaryWrapperWithAttr._marker: raise KeyError
            f = self._fields.get(key, None)
            if f is None: self._data[key] = default
            elif f.ro: raise AttributeError('%s cannot be edited in header' % _key)
            else: self._data[key] = f.cast(default, self)
        return self._data[key]
    def update(self, d=None, **kwargs):
        if self._imstack._readonly: raise AttributeError('header is readonly')
        if d is None: d = kwargs
        itr = d.iteritems() if isinstance(d, dict) else d
        for k,v in itr: self[k] = v

class Field(object):
    # TODO: re-use the casting system from the command-line parsing
    """
    A image stack header field. The base class takes a casting function, if the value is read-only
    (to the external world, default False), if the field is optional (default True), and a default
    value (which is currently not used).

    The cast function should take a wide array of values and convert them if possible to the true
    type. If the input value cannot be converted, a TypeError or ValueError should be raised.
    """
    def __init__(self, cast=None, ro=False, opt=True, default=None):
        self._cast = cast
        self.ro    = ro
        self.opt   = opt
        # TODO: use default value somewhere
    def cast(self, v, h): return self._cast(v) #pylint: disable=unused-argument
class FixedField(Field):
    """
    This is an image stack header field that can have only one value ever. It is readonly. We still
    take a casting function to do type conversion, but we also do an automatic check on the return
    from cast to see if it is identical to the value we are fixed to.
    """
    def __init__(self, cast, value, opt=True):
        super(FixedField, self).__init__(cast, True, opt, value)
        self.value = cast(value)
    def cast(self, v, h):
        if self._cast(v) != self.value: raise ValueError
        return self.value
class NumericField(Field):
    """
    A numeric-based field. By default the casting operator is "int" and no upper or lower bound is
    placed on the value. You can set cast, lower, and upper to change this behavior.
    """
    def __init__(self, cast=int, lower=None, upper=None, ro=False, opt=True, default=None):
        # Note: lower/min and upper/max are inclusive, if None no restriction on that end
        super(NumericField, self).__init__(cast, ro, opt, default)
        self.min = cast(lower) if lower is not None else None
        self.max = cast(upper) if upper is not None else None
    def cast(self, v, h):
        v = self._cast(v)
        if (self.min is not None and v < self.min) or (self.max is not None and v > self.max):
            raise ValueError('value not in range')
        return v

# Import formats and commands
#from . import formats (doesn't work, next line is roughly equivalent) 
__import__(('.'.join(__name__.split('.')[:-1]+['formats'])), globals(), locals()) #pylint: disable=unused-import
from . import _commands             #pylint: disable=unused-import
