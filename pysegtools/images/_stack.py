from abc import ABCMeta, abstractproperty, abstractmethod
from numpy import ndarray
from collections import Iterable, OrderedDict
from numbers import Integral

from ..general.enum import Flags
from .types import imstack_standardize_dtype
from .source import ImageSource, DeferredPropertiesImageSource

__all__ = ["ImageStack", "HomogeneousImageStack", "ImageSlice", "Homogeneous"]

class Homogeneous(int, Flags):
    Shape = 1
    DType = 2
    Both  = 3
    
class ImageStack(object):
    """
    A stack of 2D image slices. This may represent an image on disk or image filter to be/already
    applied to a stack.

    Individual 2D slices are returned with [] or when iterating. Slice image data is only loaded or
    calculated as needed and by default are not cached. The number of slices is available with
    len(). The [] also accepts slice-notation and iterables of indicies and returns a list of
    ImageSlice objects.
    """
    
    __metaclass__ = ABCMeta

    @classmethod
    def _get_all_subclasses(cls):
        subcls = cls.__subclasses__()
        for sc in list(subcls): subcls.extend(sc._get_all_subclasses())
        return subcls

    @classmethod
    def as_image_stack(cls, ims):
        """
        Takes an image-stack like object and makes sure it is an ImageStack object. If it is already
        an ImageStack then it is returned. Other types supported are iterables/sequences of image
        sources or 2D ndarrays or a 3D ndarray.
        """
        if isinstance(ims, ImageStack): return ims
        dtype, check = None, True
        if isinstance(ims, ndarray):
            try: return ImageStackArray(imstack_standardize_dtype(ims)) # 3D ndarray
            except ValueError: return ImageStackCollection((ims,))      # single 2D ndarry
        elif isinstance(ims, ImageSource): return ImageStackCollection((ims,)) # single ImageSource
        elif isinstance(ims, Iterable):    return ImageStackCollection(ims)    # iterable of (presumably) ImageSources/ndarrays
        else: raise ValueError()
    
    def __init__(self, slices):
        self._slices = slices
        self._d = len(slices)
        self._cache_size = 0
        self._homogeneous = Homogeneous.Both if self._d <= 1 else None

    # General
    @property
    def d(self): return self._d
    def __len__(self): return self._d

    # Homogeneous interface
    def _get_homogeneous_info(self):
        if self._d == 0: return Homogeneous.Both, (None, None), None
        shape = self._slices[0].shape
        dtype = self._slices[0].dtype
        if self._homogeneous is None:
            self._homogeneous = Homogeneous._None
            if all(shape == im.shape for im in self._slices): # TODO: skip first
                self._homogeneous |= Homogeneous.Shape
            else: shape = None
            if all(dtype == im.dtype for im in self._slices): # TODO: skip first
                self._homogeneous |= Homogeneous.DType
            else: dtype = None
        else:
            if Homogeneous.Shape not in self._homogeneous: shape = None
            if Homogeneous.DType not in self._homogeneous: dtype = None
        return self._homogeneous, shape, dtype
    def _update_homogeneous_set(self, z, shape, dtype):
        s = self._slices[-1 if z == 0 else 0]
        if Homogeneous.Shape in self._homogeneous and shape != s.shape:
            self._homogeneous &= ~Homogeneous.Shape
        if Homogeneous.DType in self._homogeneous and dtype != s.dtype:
            self._homogeneous &= ~Homogeneous.DType
    @property
    def is_homogeneous(self): return self._get_homogeneous_info()[0] != Homogeneous._None
    @property
    def w(self): return self.shape[1]
    @property
    def h(self): return self.shape[0]
    @property
    def shape(self):
        h = self._get_homogeneous_info()
        if Homogeneous.Shape not in h[0]: raise AttributeError('property unavailable on heterogeneous image stacks')
        return h[1]
    @property
    def dtype(self):
        h = self._get_homogeneous_info()
        if Homogeneous.DType not in h[0]: raise AttributeError('property unavailable on heterogeneous image stacks')
        return h[2]

    ## Caching of slices ##
    # Note that much of the caching is in ImageSlice or subclasses
    @property
    def cache_size(self): return self._cache_size
    @cache_size.setter
    def cache_size(self, value):
        """
        Set the size of the cache. This number of recently accessed or set slices will be available
        without disk reads or calculations. Default is 0 which means no slices are cached. If -1 then
        all slices will be cached as they are accessed.
        """
        # The cache uses the following member variables:
        #  _cache_size        either 0 (cache off), -1 (unlimited cache), or a value >0 (max cache size)
        #  _cache             the LRU cache, an OrderedDict of indices which are cached with popitem(False) as least recently used
        #  ._slices[]._cache  the cached data for a slice (if it exists)
        value = int(value)
        if value < -1: raise ValueError
        if value == 0: # removing cache
            if self._cache_size:
                del self._cache
                for s in self._slices:
                    if hasattr(s, '_cache'): del s._cache
        elif value != 0:
            if not self._cache_size: # creating cache
                self._cache = OrderedDict()
            elif value != -1:
                while len(self._cache) > value: # cache is shrinking
                    del self._slices[self._cache.popitem(False)[0]]._cache
        self._cache_size = value
    # TODO: def set_cache_size_in_bytes(self, bytes): self.cache_size = bytes // self._sec_bytes;
    def _cache_it(self, i):
        # Places an index into the cache list (but doesn't do anything with the cached data itself)
        # Returns True if the index is already cached (in which case it is moved to the back of the LRU)
        # Otherwise if the queue is full then the oldest thing is removed from the cache
        already_in_cache = self._cache.pop(i, False)
        if not already_in_cache and len(self._cache) == self._cache_size: # cache full
            del self._slices[self._cache.popitem(False)]._cache
        self._cache[i] = True
        return already_in_cache

    # Getting Slices
    def __getitem__(self, idx):
        """
        Get image slices. Accepts integers, index slices, or iterable indices. When using an integral
        index this returns an ImageSlice object. For index slice and iterable indices it returns a
        list of ImageSlice objects. Images slice data is not loaded until the data attribute of the
        ImageSlice object is used.
        """
        if isinstance(idx, (Integral, slice)): return self._slices[idx]
        elif isinstance(idx, Iterable):        return [self._slices[i] for i in idx]
        else: raise TypeError('index')
    def __iter__(self):
        for i in xrange(self._d): yield self._slices[i]

class HomogeneousImageStack(ImageStack):
    """
    An image stack where every slice has the same shape and data type. Provides speed ups for many
    of the homogeneous properties and adds the stack property. It also adds some protected
    properties for convience in deriving classes.
    """
    __metaclass__ = ABCMeta

    def __init__(self, slices, w, h, dtype):
        super(HomogeneousImageStack, self).__init__(slices)
        self._init_props(w, h, dtype)
    def _init_props(self, w, h, dtype):
        # Due to multiple-inheritance, this is the function a subclass propabaly wants to call
        self._w = w
        self._h = h
        self._shape = (h, w)
        self._dtype = dtype
        self._slc_pxls  = w * h
        self._slc_bytes = w * h * dtype.itemsize
        self._homogeneous = Homogeneous.Both

    def _get_homogeneous_info(self): return Homogeneous.Both, self._shape, self._dtype
    def _update_homogeneous_set(self, z, shape, dtype): pass

    @property
    def is_homogeneous(self): return True

    @property
    def w(self): return self._w
    @property
    def h(self): return self._h
    @property
    def shape(self): return self._shape
    @property
    def dtype(self): return self._dtype
    
    @property
    def stack(self):
        """Get the entire stack as a single 3D image."""
        from numpy import empty
        stack = empty((self._d,) + self._shape, dtype=self._dtype)
        for i, sec in enumerate(self): stack[i,:,:] = sec
        return stack

class ImageSlice(DeferredPropertiesImageSource):
    """
    A image slice from an image stack. These must be implemented for specific formats and filters.
    The implementor must either call _set_props during initialization or implement a non-trivial
    _get_props function (the trivial one would be def _get_props(self): pass).
    """
    __metaclass__ = ABCMeta
    
    def __init__(self, stack, z):
        self._stack = stack
        self._z = z

    @property
    def stack(self): return self._stack
    @property
    def z(self): return self._z

    @property
    def data(self):
        if not self._stack._cache_size: return self._get_data()
        if not self._stack._cache_it(self._x): self._cache = self._get_data()
        return self._cache # the cache is full on un-writeable copies already, so no .copy()

    @abstractmethod
    def _get_data(self):
        """
        Internal function for getting image data. Must return an ndarray with shape and dtype of
        this slice (which should be a standardized type).
        """
        pass

# Some generic image stacks that are wrappers are other image datas
class ImageStackArray(HomogeneousImageStack):
    """
    ImageStack that wraps a 3D array of data. All slices returned are views so the data can be
    edited.
    """
    def __init__(self, arr):
        self._arr = imstack_standardize_dtype(arr)
        s = self._arr.shape
        super(ImageStackArray, self).__init__([ImageSliceFromArray(self, z) for z in xrange(s[2])], s[1], s[0], self._arr.dtype)
    @ImageStack.cache_size.setter
    def cache_size(self, value): pass # prevent actual caching - all in memory
    @property
    def stack(self): return self._arr # return the underlying data, not a copy
class ImageSliceFromArray(ImageSlice):
    def __init__(self, stack, z):
        super(ImageSliceFromArray, self).__init__(stack, z)
        self._set_props(stack._arr.dtype, stack._arr.shape[:2])
    def _get_props(self): pass
    def _get_data(self): return self._stack._arr[:,:,self._z]

class ImageStackCollection(ImageStack):
    """ImageStack that wraps a collection of ImageSources."""
    def __init__(self, ims):
        ims = [ImageSource.as_image_source(im) for im in ims]
        super(ImageStackCollection, self).__init__([ImageSliceFromCollection(self, z, im) for z,im in enumerate(ims)])
class ImageSliceFromCollection(ImageSlice):
    def __init__(self, stack, z, im):
        super(ImageSliceFromCollection, self).__init__(stack, z)
        self._im = im
    def _get_props(self): self._set_props(self._im.dtype, self._im.shape)
    def _get_data(self): return self._im.data