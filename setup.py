#!/usr/bin/env python

import os, sys
from distutils.core import setup, DistutilsSetupError

# We require Python v2.7 or newer
if sys.version_info[:2] < (2,7):
    raise DistutilsSetupError("This requires Python v2.7 or newer")

# If this is True we need to have subprocess32 installed
# Only needed on POSIX systems using Python < 3.2 due to a bug in the built-in subprocess module
need_sp32 = (os.name == 'posix') and (sys.version_info[:2] < (3,2))

# TODO: check if PNG, TIFF, or JPEG images can be read
if os.name == 'posix':
    pass

setup(  name='pysegtools',
        version='0.1',
        description='Python Segmentation Tools',
        long_description=open('README.md').read(),
        author='Jeffrey Bush',
        author_email='j1bush@ncmir.ucsd.edu',
        url='https://cellsegmentation.org/',
        packages=['pysegtools'], # TODO: do all 'packages' need to be listed?
        use_2to3=True, # the code *should* support Python 3 once run through 2to3 but this isn't tested
        zip_safe=False, # I don't think this code would work when running from inside a zip file due to the dynamic-load and dynamic-cython systems
        package_data = { '': ['*.pyx', '*.pyxdep', '*.pxi', '*.pxd', '*.h', '*.txt'], }, # Make sure all Cython files are wrapped up with the code
        install_requires=['numpy>=1.7','scipy>=0.12'] + (['subprocess32>=3.2.6'] if need_sp32 else []),
        extras_require={
              'OPT': ['cython>=0.19','fftw>=0.9.2'],
              'PIL': ['pillow>=2.0'],
              'MATLAB': ['h5py>=2.0'],
              'tasks': ['psutil>=2.0'],
          },
     )