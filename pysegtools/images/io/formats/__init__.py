"""
Do not edit this file. This dynamically makes it so all modules in this folder can be loaded. To add
a new format, you simply need to add a new .py file.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import os
directory = os.path.dirname(__file__)
__all__ = [os.path.basename(f)[:-3] for f in os.listdir(directory)
           if f[-3:] == ".py" and f[0] != "_" and os.path.isfile(os.path.join(directory, f))]
for mod in __all__:
    __import__(mod, locals(), globals())
del os, directory
try:
    del f #pylint: disable=undefined-loop-variable
except NameError:
    pass
try:
    del mod #pylint: disable=undefined-loop-variable
except NameError:
    pass
