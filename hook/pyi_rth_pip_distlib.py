"""Teach pip's vendored distlib how to read resources under PyInstaller."""

from pip._vendor import distlib
from pip._vendor.distlib import resources


# PyInstaller's frozen module loader is not one of distlib's built-in finder
# types. All distlib launcher executables are extracted as normal data files,
# so the standard filesystem finder is the correct implementation here.
resources.register_finder(distlib.__loader__, resources.ResourceFinder)
