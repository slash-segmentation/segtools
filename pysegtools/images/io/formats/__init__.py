"""
Do not edit this file. This dynamically makes it so all modules in this folder can be loaded. To add
a new format, you simply need to add a new .py file.
"""
import os
d = os.path.dirname(__file__)
__all__ = [os.path.basename(f)[:-3] for f in os.listdir(d)
           if f[-3:] == ".py" and f[:2] != "__" and os.path.isfile(os.path.join(d, f))]
for mod in __all__: __import__(mod, locals(), globals())
del os, d, f, mod
