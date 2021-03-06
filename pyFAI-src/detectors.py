# !/usr/bin/env python
# -*- coding: utf-8 -*-
#
#    Project: Azimuthal integration
#             https://forge.epn-campus.eu/projects/azimuthal
#
#    File: "$Id$"
#
#    Copyright (C) European Synchrotron Radiation Facility, Grenoble, France
#
#    Principal author:       Jérôme Kieffer (Jerome.Kieffer@ESRF.eu)
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
from __future__ import print_function
__author__ = "Jérôme Kieffer"
__contact__ = "Jerome.Kieffer@ESRF.eu"
__license__ = "GPLv3+"
__copyright__ = "European Synchrotron Radiation Facility, Grenoble, France"
__date__ = "02/02/2014"
__status__ = "stable"
__doc__ = """
Module containing the description of all detectors with a factory to instanciate them
"""

import os
import logging
import threading
import numpy

logger = logging.getLogger("pyFAI.detectors")

from . import spline
from .utils import lazy_property
try:
    from .fastcrc import crc32
except ImportError:
    from zlib import crc32
try:
    import fabio
except ImportError:
    fabio = None
epsilon = 1e-6


class DetectorMeta(type):
    """
    Metaclass used to register all detector classes inheriting from Detector
    """
    # we use __init__ rather than __new__ here because we want
    # to modify attributes of the class *after* they have been
    # created
    def __init__(cls, name, bases, dct):
        cls.registry[name.lower()] = cls
        if hasattr(cls, "aliases"):
            for alias in cls.aliases:
                cls.registry[alias.lower().replace(" ", "_")] = cls
                cls.registry[alias.lower().replace(" ", "")] = cls
        super(DetectorMeta, cls).__init__(name, bases, dct)


class Detector(object):
    """
    Generic class representing a 2D detector
    """
    __metaclass__ = DetectorMeta
    force_pixel = False     # Used to specify pixel size should be defined by the class itself.
    aliases = []            # list of alternative names
    registry = {}           # list of  detectors ...

    @classmethod
    def factory(cls, name, config=None):
        """
        A kind of factory...

        @param name: name of a detector
        @type name: str
        @param config: configuration of the detector
        @type config: dict or JSON representation of it.
    
        @return: an instance of the right detector, set-up if possible
        @rtype: pyFAI.detectors.Detector
        """
        name = name.lower()
        if name in cls.registry:
            mydet = cls.registry[name]()
            if config is not None:
                mydet.set_config(config)
            return mydet
        else:
            msg = ("Detector %s is unknown !, "
                   "please select one from %s" % (name, cls.registry.keys()))
            logger.error(msg)
            raise RuntimeError(msg)

    def __init__(self, pixel1=None, pixel2=None, splineFile=None):
        """
        @param pixel1: size of the pixel in meter along the slow dimension (often Y)
        @type pixel1: float
        @param pixel2: size of the pixel in meter along the fast dimension (often X)
        @type pixel2: float
        @param splineFile: path to file containing the geometric correction.
        @type splineFile: str
        """
        self._pixel1 = None
        self._pixel2 = None
        if pixel1:
            self._pixel1 = float(pixel1)
        if pixel2:
            self._pixel2 = float(pixel2)
        self.max_shape = (None, None)
        self._binning = (1, 1)
        self._mask = False
        self._mask_crc = None
        self._maskfile = None
        self._splineFile = None
        self.spline = None
        self._splineCache = {}  # key=(dx,xpoints,ypoints) value: ndarray
        self._sem = threading.Semaphore()
        if splineFile:
            self.set_splineFile(splineFile)



    def __repr__(self):
        if (self._pixel1 is None) or (self._pixel2 is None):
            return "Undefined detector"
        return "Detector %s\t Spline= %s\t PixelSize= %.3e, %.3e m" % \
            (self.name, self.splineFile, self._pixel1, self._pixel2)

    def set_config(self, config):
        """
        Sets the configuration of the detector. This implies:
        - Orientation: integers
        - Binning
        - ROI

        The configuration is either a python dictionnary or a JSON string or a file containing this JSON configuration

        keys in that dictionnary are :
        "orientation": integers from 0 to 7
        "binning": integer or 2-tuple of integers. If only one integer is provided,
        "offset": coordinate (in pixels) of the start of the detector
        """
        raise NotImplementedError

    def get_splineFile(self):
        return self._splineFile

    def set_splineFile(self, splineFile):
        if splineFile is not None:
            self._splineFile = os.path.abspath(splineFile)
            self.spline = spline.Spline(self._splineFile)
            # NOTA : X is axis 1 and Y is Axis 0
            self._pixel2, self._pixel1 = self.spline.getPixelSize()
            self._splineCache = {}
        else:
            self._splineFile = None
            self.spline = None
    splineFile = property(get_splineFile, set_splineFile)

    def get_binning(self):
        return self._binning

    def set_binning(self, bin_size=(1, 1)):
        """
        Set the "binning" of the detector,

        @param bin_size: binning as integer or tuple of integers.
        @type bin_size: (int, int)
        """
        if "__len__" in dir(bin_size) and len(bin_size) >= 2:
            bin_size = (float(bin_size[0]), float(bin_size[1]))
        else:
            b = float(bin_size)
            bin_size = (b, b)
        if bin_size != self._binning:
            ratioX = bin_size[1] / self._binning[1]
            ratioY = bin_size[0] / self._binning[0]
            if self.spline is not None:
                self.spline.bin((ratioX, ratioY))
                self._pixel2, self._pixel1 = self.spline.getPixelSize()
                self._splineCache = {}
            else:
                self._pixel1 *= ratioY
                self._pixel2 *= ratioX
            self._binning = bin_size

    binning = property(get_binning, set_binning)


    def getPyFAI(self):
        """
        Helper method to serialize the description of a detector using the pyFAI way
        with everything in S.I units.

        @return: representation of the detector easy to serialize
        @rtype: dict
        """
        return {"detector": self.name,
                "pixel1": self._pixel1,
                "pixel2": self._pixel2,
                "splineFile": self._splineFile}

    def getFit2D(self):
        """
        Helper method to serialize the description of a detector using the Fit2d units

        @return: representation of the detector easy to serialize
        @rtype: dict
        """
        return {"pixelX": self._pixel2 * 1e6,
                "pixelY": self._pixel1 * 1e6,
                "splineFile": self._splineFile}

    def setPyFAI(self, **kwarg):
        """
        Twin method of getPyFAI: setup a detector instance according to a description

        @param kwarg: dictionary containing detector, pixel1, pixel2 and splineFile

        """
        if "detector" in kwarg:
            self = detector_factory(kwarg["detector"])
        for kw in kwarg:
            if kw in ["pixel1", "pixel2"]:
                setattr(self, kw, kwarg[kw])
            elif kw == "splineFile":
                self.set_splineFile(kwarg[kw])

    def setFit2D(self, **kwarg):
        """
        Twin method of getFit2D: setup a detector instance according to a description

        @param kwarg: dictionary containing pixel1, pixel2 and splineFile

        """
        for kw, val in kwarg.items():
            if kw == "pixelX":
                self.pixel2 = val * 1e-6
            elif kw == "pixelY":
                self.pixel1 = val * 1e-6
            elif kw == "splineFile":
                self.set_splineFile(kwarg[kw])

    def calc_cartesian_positions(self, d1=None, d2=None):
        """
        Calculate the position of each pixel center in cartesian coordinate
        and in meter of a couple of coordinates.
        The half pixel offset is taken into account here !!!

        @param d1: the Y pixel positions (slow dimension)
        @type d1: ndarray (1D or 2D)
        @param d2: the X pixel positions (fast dimension)
        @type d2: ndarray (1D or 2D)

        @return: position in meter of the center of each pixels.
        @rtype: ndarray

        d1 and d2 must have the same shape, returned array will have
        the same shape.
        """
        if (d1 is None):
            d1 = numpy.outer(numpy.arange(self.max_shape[0]), numpy.ones(self.max_shape[1]))

        if (d2 is None):
            d2 = numpy.outer(numpy.ones(self.max_shape[0]), numpy.arange(self.max_shape[1]))

        if self.spline is None:
            dX = 0.
            dY = 0.
        else:
            if d2.ndim == 1:
                keyX = ("dX", tuple(d1), tuple(d2))
                keyY = ("dY", tuple(d1), tuple(d2))
                if keyX not in self._splineCache:
                    self._splineCache[keyX] = \
                        numpy.array([self.spline.splineFuncX(i2, i1)
                                     for i1, i2 in zip(d1 + 0.5, d2 + 0.5)],
                                    dtype="float64")
                if keyY not in self._splineCache:
                    self._splineCache[keyY] = \
                        numpy.array([self.spline.splineFuncY(i2, i1)
                                     for i1, i2 in zip(d1 + 0.5, d2 + 0.5)],
                                    dtype="float64")
                dX = self._splineCache[keyX]
                dY = self._splineCache[keyY]
            else:
                dX = self.spline.splineFuncX(d2 + 0.5, d1 + 0.5)
                dY = self.spline.splineFuncY(d2 + 0.5, d1 + 0.5)
        p1 = (self._pixel1 * (dY + 0.5 + d1))
        p2 = (self._pixel2 * (dX + 0.5 + d2))
        return p1, p2

    def calc_mask(self):
        """
        Detectors with gaps should overwrite this method with
        something actually calculating the mask!
        """
#        logger.debug("Detector.calc_mask is not implemented for generic detectors")
        return None

    ############################################################################
    # Few properties
    ############################################################################
    def get_mask(self):
        if self._mask is False:
            with self._sem:
                if self._mask is False:
                    self._mask = self.calc_mask()  # gets None in worse cases
                    if self._mask is not None:
                        self._mask_crc = crc32(self._mask)
        return self._mask
    def set_mask(self, mask):
        with self._sem:
            self._mask = mask
            if mask is not None:
                self._mask_crc = crc32(mask)
            else:
                self._mask_crc = None
    mask = property(get_mask, set_mask)
    def set_maskfile(self, maskfile):
        if fabio:
            with self._sem:
                self._mask = numpy.ascontiguousarray(fabio.open(maskfile).data,
                                                     dtype=numpy.int8)
                self._mask_crc = crc32(self._mask)
                self._maskfile = maskfile
        else:
            logger.error("FabIO is not available, unable to load the image to set the mask.")

    def get_maskfile(self):
        return self._maskfile
    maskfile = property(get_maskfile, set_maskfile)

    def get_pixel1(self):
        return self._pixel1
    def set_pixel1(self, value):
        if isinstance(value, float):
            value = value
        elif isinstance(value, (tuple, list)):
            value = float(value[0])
        else:
            value = float(value)
        if self._pixel1:
            err = abs(value - self._pixel1) / self._pixel1
            if self.force_pixel and  (err > epsilon):
                logger.warning("enforcing pixel size 2 for a detector %s" %
                               self.__class__.__name__)
        self._pixel1 = value
    pixel1 = property(get_pixel1, set_pixel1)

    def get_pixel2(self):
        return self._pixel2
    def set_pixel2(self, value):
        if isinstance(value, float):
            value = value
        elif isinstance(value, (tuple, list)):
            value = float(value[0])
        else:
            value = float(value)
        if self._pixel2:
            err = abs(value - self._pixel2) / self._pixel2
            if self.force_pixel and  (err > epsilon):
                logger.warning("enforcing pixel size 2 for a detector %s" %
                               self.__class__.__name__)
        self._pixel2 = value
    pixel2 = property(get_pixel2, set_pixel2)

    def get_name(self):
        """
        Get a meaningful name for detector
        """
        if self.aliases:
            name = self.aliases[0]
        else:
            name = self.__class__.__name__
        return name
    name = property(get_name)

class Pilatus(Detector):
    """
    Pilatus detector: generic description containing mask algorithm

    Sub-classed by Pilatus1M, Pilatus2M and Pilatus6M
    """
    MODULE_SIZE = (195, 487)
    MODULE_GAP = (17, 7)
    force_pixel = True

    def __init__(self, pixel1=172e-6, pixel2=172e-6, x_offset_file=None, y_offset_file=None):
        super(Pilatus, self).__init__(pixel1=pixel1, pixel2=pixel2)
        self.x_offset_file = x_offset_file
        self.y_offset_file = y_offset_file
        if self.x_offset_file and self.y_offset_file:
            if fabio:
                self.offset1 = fabio.open(self.y_offset_file).data
                self.offset2 = fabio.open(self.x_offset_file).data
            else:
                logging.error("FabIO is not available: no distortion correction for Pilatus detectors, sorry.")
                self.offset1 = None
                self.offset2 = None
        else:
            self.offset1 = None
            self.offset2 = None

    def __repr__(self):
        txt = "Detector %s\t PixelSize= %.3e, %.3e m" % \
                (self.name, self.pixel1, self.pixel2)
        if self.x_offset_file:
            txt += "\t delta_x= %s" % self.x_offset_file
        if self.y_offset_file:
            txt += "\t delta_y= %s" % self.y_offset_file
        return txt

    def get_splineFile(self):
        if self.x_offset_file and self.y_offset_file:
            return "%s,%s" % (self.x_offset_file, self.y_offset_file)

    def set_splineFile(self, splineFile=None):
        "In this case splinefile is a couple filenames"
        if splineFile is not None:
            try:
                files = splineFile.split(",")
                self.x_offset_file = [os.path.abspath(i) for i in files if "x" in i.lower()][0]
                self.y_offset_file = [os.path.abspath(i) for i in files if "y" in i.lower()][0]
            except Exception as error:
                logger.error("set_splineFile with %s gave error: %s" % (splineFile, error))
                self.x_offset_file = self.y_offset_file = self.offset1 = self.offset2 = None
                return
            if fabio:
                self.offset1 = fabio.open(self.y_offset_file).data
                self.offset2 = fabio.open(self.x_offset_file).data
            else:
                logging.error("FabIO is not available: no distortion correction for Pilatus detectors, sorry.")
                self.offset1 = None
                self.offset2 = None
        else:
            self._splineFile = None
    splineFile = property(get_splineFile, set_splineFile)

    def calc_mask(self):
        """
        Returns a generic mask for Pilatus detectors...
        """
        if (self.max_shape[0] or self.max_shape[1]) is None:
            raise NotImplementedError("Generic Pilatus detector does not know"
                                      "the max size ...")
        mask = numpy.zeros(self.max_shape, dtype=numpy.int8)
        # workinng in dim0 = Y
        for i in range(self.MODULE_SIZE[0], self.max_shape[0],
                       self.MODULE_SIZE[0] + self.MODULE_GAP[0]):
            mask[i: i + self.MODULE_GAP[0], :] = 1
        # workinng in dim1 = X
        for i in range(self.MODULE_SIZE[1], self.max_shape[1],
                       self.MODULE_SIZE[1] + self.MODULE_GAP[1]):
            mask[:, i: i + self.MODULE_GAP[1]] = 1
        return mask

    def calc_cartesian_positions(self, d1=None, d2=None):
        """
        Calculate the position of each pixel center in cartesian coordinate
        and in meter of a couple of coordinates.
        The half pixel offset is taken into account here !!!

        @param d1: the Y pixel positions (slow dimension)
        @type d1: ndarray (1D or 2D)
        @param d2: the X pixel positions (fast dimension)
        @type d2: ndarray (1D or 2D)

        @return: position in meter of the center of each pixels.
        @rtype: ndarray

        d1 and d2 must have the same shape, returned array will have
        the same shape.
        """
        if (d1 is None):
            d1 = numpy.outer(numpy.arange(self.max_shape[0]), numpy.ones(self.max_shape[1]))

        if (d2 is None):
            d2 = numpy.outer(numpy.ones(self.max_shape[0]), numpy.arange(self.max_shape[1]))

        if self.offset1 is None or self.offset2 is None:
            delta1 = delta2 = 0.
        else:
            if d2.ndim == 1:
                d1n = d1.astype(numpy.int32)
                d2n = d2.astype(numpy.int32)
                delta1 = self.offset1[d1n, d2n] / 100.0  # Offsets are in percent of pixel
                delta2 = self.offset2[d1n, d2n] / 100.0
            else:
                if d1.shape == self.offset1.shape:
                    delta1 = self.offset1 / 100.0  # Offsets are in percent of pixel
                    delta2 = self.offset2 / 100.0
                elif d1.shape[0] > self.offset1.shape[0]:  # probably working with corners
                    s0, s1 = self.offset1.shape
                    delta1 = numpy.zeros(d1.shape, dtype=numpy.int32)  # this is the natural type for pilatus CBF
                    delta2 = numpy.zeros(d2.shape, dtype=numpy.int32)
                    delta1[:s0, :s1] = self.offset1
                    delta2[:s0, :s1] = self.offset2
                    mask = numpy.where(delta1[-s0:, :s1] == 0)
                    delta1[-s0:, :s1][mask] = self.offset1[mask]
                    delta2[-s0:, :s1][mask] = self.offset2[mask]
                    mask = numpy.where(delta1[-s0:, -s1:] == 0)
                    delta1[-s0:, -s1:][mask] = self.offset1[mask]
                    delta2[-s0:, -s1:][mask] = self.offset2[mask]
                    mask = numpy.where(delta1[:s0, -s1:] == 0)
                    delta1[:s0, -s1:][mask] = self.offset1[mask]
                    delta2[:s0, -s1:][mask] = self.offset2[mask]
                    delta1 = delta1 / 100.0  # Offsets are in percent of pixel
                    delta2 = delta2 / 100.0  # former arrays were integers
                else:
                    logger.warning("Surprizing situation !!! please investigate: offset has shape %s and input array have %s" % (self.offset1.shape, d1, shape))
                    delta1 = delta2 = 0.
        # For pilatus,
        p1 = (self._pixel1 * (delta1 + 0.5 + d1))
        p2 = (self._pixel2 * (delta2 + 0.5 + d2))
        return p1, p2


class Pilatus100k(Pilatus):
    """
    Pilatus 100k detector
    """
    MAX_SHAPE = 195, 487
    def __init__(self, pixel1=172e-6, pixel2=172e-6):
        super(Pilatus100k, self).__init__(pixel1=pixel1, pixel2=pixel2)
        self.max_shape = self.MAX_SHAPE


class Pilatus200k(Pilatus):
    """
    Pilatus 200k detector
    """
    MAX_SHAPE = (407, 487)
    def __init__(self, pixel1=172e-6, pixel2=172e-6):
        super(Pilatus200k, self).__init__(pixel1=pixel1, pixel2=pixel2)
        self.max_shape = self.MAX_SHAPE


class Pilatus300k(Pilatus):
    """
    Pilatus 300k detector
    """
    MAX_SHAPE = (619, 487)
    def __init__(self, pixel1=172e-6, pixel2=172e-6):
        super(Pilatus300k, self).__init__(pixel1=pixel1, pixel2=pixel2)
        self.max_shape = self.MAX_SHAPE


class Pilatus300kw(Pilatus):
    """
    Pilatus 300k-wide detector
    """
    MAX_SHAPE = (195, 1475)
    def __init__(self, pixel1=172e-6, pixel2=172e-6):
        super(Pilatus300kw, self).__init__(pixel1=pixel1, pixel2=pixel2)
        self.max_shape = self.MAX_SHAPE


class Pilatus1M(Pilatus):
    """
    Pilatus 1M detector
    """
    MAX_SHAPE = (1043, 981)
    def __init__(self, pixel1=172e-6, pixel2=172e-6):
        super(Pilatus1M, self).__init__(pixel1=pixel1, pixel2=pixel2)
        self.max_shape = self.MAX_SHAPE


class Pilatus2M(Pilatus):
    """
    Pilatus 2M detector
    """

    MAX_SHAPE = 1679, 1475
    def __init__(self, pixel1=172e-6, pixel2=172e-6):
        super(Pilatus2M, self).__init__(pixel1=pixel1, pixel2=pixel2)
        self.max_shape = self.MAX_SHAPE


class Pilatus6M(Pilatus):
    """
    Pilatus 6M detector
    """
    MAX_SHAPE = (2527, 2463)
    def __init__(self, pixel1=172e-6, pixel2=172e-6):
        super(Pilatus6M, self).__init__(pixel1=pixel1, pixel2=pixel2)
        self.max_shape = self.MAX_SHAPE

class Eiger(Detector):
    """
    Eiger detector: generic description containing mask algorithm
    """
    MODULE_SIZE = (1065, 1030)
    MODULE_GAP = (37, 10)
    force_pixel = True

    def __init__(self, pixel1=75e-6, pixel2=75e-6):
        Detector.__init__(self, pixel1=pixel1, pixel2=pixel2)

    def calc_mask(self):
        """
        Returns a generic mask for Pilatus detectors...
        """
        if (self.max_shape[0] or self.max_shape[1]) is None:
            raise NotImplementedError("Generic Pilatus detector does not know"
                                      "the max size ...")
        mask = numpy.zeros(self.max_shape, dtype=numpy.int8)
        # workinng in dim0 = Y
        for i in range(self.MODULE_SIZE[0], self.max_shape[0],
                       self.MODULE_SIZE[0] + self.MODULE_GAP[0]):
            mask[i: i + self.MODULE_GAP[0], :] = 1
        # workinng in dim1 = X
        for i in range(self.MODULE_SIZE[1], self.max_shape[1],
                       self.MODULE_SIZE[1] + self.MODULE_GAP[1]):
            mask[:, i: i + self.MODULE_GAP[1]] = 1
        return mask

    def calc_cartesian_positions(self, d1=None, d2=None):
        """
        Calculate the position of each pixel center in cartesian coordinate
        and in meter of a couple of coordinates.
        The half pixel offset is taken into account here !!!

        @param d1: the Y pixel positions (slow dimension)
        @type d1: ndarray (1D or 2D)
        @param d2: the X pixel positions (fast dimension)
        @type d2: ndarray (1D or 2D)

        @return: position in meter of the center of each pixels.
        @rtype: ndarray

        d1 and d2 must have the same shape, returned array will have
        the same shape.
        """
        if (d1 is None):
            d1 = numpy.outer(numpy.arange(self.max_shape[0]), numpy.ones(self.max_shape[1]))

        if (d2 is None):
            d2 = numpy.outer(numpy.ones(self.max_shape[0]), numpy.arange(self.max_shape[1]))

        if self.offset1 is None or self.offset2 is None:
            delta1 = delta2 = 0.
        else:
            if d2.ndim == 1:
                d1n = d1.astype(numpy.int32)
                d2n = d2.astype(numpy.int32)
                delta1 = self.offset1[d1n, d2n] / 100.0  # Offsets are in percent of pixel
                delta2 = self.offset2[d1n, d2n] / 100.0
            else:
                if d1.shape == self.offset1.shape:
                    delta1 = self.offset1 / 100.0  # Offsets are in percent of pixel
                    delta2 = self.offset2 / 100.0
                elif d1.shape[0] > self.offset1.shape[0]:  # probably working with corners
                    s0, s1 = self.offset1.shape
                    delta1 = numpy.zeros(d1.shape, dtype=numpy.int32)  # this is the natural type for pilatus CBF
                    delta2 = numpy.zeros(d2.shape, dtype=numpy.int32)
                    delta1[:s0, :s1] = self.offset1
                    delta2[:s0, :s1] = self.offset2
                    mask = numpy.where(delta1[-s0:, :s1] == 0)
                    delta1[-s0:, :s1][mask] = self.offset1[mask]
                    delta2[-s0:, :s1][mask] = self.offset2[mask]
                    mask = numpy.where(delta1[-s0:, -s1:] == 0)
                    delta1[-s0:, -s1:][mask] = self.offset1[mask]
                    delta2[-s0:, -s1:][mask] = self.offset2[mask]
                    mask = numpy.where(delta1[:s0, -s1:] == 0)
                    delta1[:s0, -s1:][mask] = self.offset1[mask]
                    delta2[:s0, -s1:][mask] = self.offset2[mask]
                    delta1 = delta1 / 100.0  # Offsets are in percent of pixel
                    delta2 = delta2 / 100.0  # former arrays were integers
                else:
                    logger.warning("Surprising situation !!! please investigate: offset has shape %s and input array have %s" % (self.offset1.shape, d1, shape))
                    delta1 = delta2 = 0.
        # For pilatus,
        p1 = (self._pixel1 * (delta1 + 0.5 + d1))
        p2 = (self._pixel2 * (delta2 + 0.5 + d2))
        return p1, p2

class Eiger1M(Eiger):
    """
    Eiger 1M detector
    """
    MAX_SHAPE = (1065, 1030)
    def __init__(self, pixel1=75e-6, pixel2=75e-6):
        Eiger.__init__(self, pixel1=pixel1, pixel2=pixel2)
        self.max_shape = self.MAX_SHAPE

class Eiger4M(Eiger):
    """
    Eiger 4M detector
    """
    MAX_SHAPE = (2167, 2070)
    def __init__(self, pixel1=75e-6, pixel2=75e-6):
        Eiger.__init__(self, pixel1=pixel1, pixel2=pixel2)
        self.max_shape = self.MAX_SHAPE

class Eiger9M(Eiger):
    """
    Eiger 9M detector
    """
    MAX_SHAPE = (3269, 3110)
    def __init__(self, pixel1=75e-6, pixel2=75e-6):
        Eiger.__init__(self, pixel1=pixel1, pixel2=pixel2)
        self.max_shape = self.MAX_SHAPE

class Eiger16M(Eiger):
    """
    Eiger 16M detector
    """
    MAX_SHAPE = (4371, 4150)
    def __init__(self, pixel1=75e-6, pixel2=75e-6):
        Eiger.__init__(self, pixel1=pixel1, pixel2=pixel2)
        self.max_shape = self.MAX_SHAPE


class Fairchild(Detector):
    """
    Fairchild Condor 486:90 detector
    """
    force_pixel = True
    aliases = ["Fairchild Condor 486:90"]
    MAX_SHAPE = (4096, 4096)
    def __init__(self, pixel1=15e-6, pixel2=15e-6):
        Detector.__init__(self, pixel1=pixel1, pixel2=pixel2)
        self.max_shape = self.MAX_SHAPE


class Titan(Detector):
    """
    Titan CCD detector from Agilent. Mask not handled
    """
    force_pixel = True
    MAX_SHAPE = (2048, 2048)
    aliases = ["Titan 2k x 2k"]
    def __init__(self, pixel1=60e-6, pixel2=60e-6):
        Detector.__init__(self, pixel1=pixel1, pixel2=pixel2)
        self.max_shape = self.MAX_SHAPE


class Dexela2923(Detector):
    """
    Dexela CMOS family detector
    """
    force_pixel = True
    aliases = ["Dexela 2923"]
    MAX_SHAPE = (3888, 3072)
    def __init__(self, pixel1=75e-6, pixel2=75e-6):
        super(Dexela2923, self).__init__(pixel1=pixel1, pixel2=pixel2)
        self.max_shape = self.MAX_SHAPE


class FReLoN(Detector):
    """
    FReLoN detector:
    The spline is mandatory to correct for geometric distortion of the taper

    TODO: create automatically a mask that removes pixels out of the "valid reagion"
    """
    def __init__(self, splineFile=None):
        super(FReLoN, self).__init__(splineFile=splineFile)
        if splineFile:
            self.max_shape = (int(self.spline.ymax - self.spline.ymin),
                              int(self.spline.xmax - self.spline.xmin))
        else:
            self.max_shape = (2048, 2048)

    def calc_mask(self):
        """
        Returns a generic mask for Frelon detectors...
        All pixels which (center) turns to be out of the valid region are by default discarded
        """

        d1 = numpy.outer(numpy.arange(self.max_shape[0]), numpy.ones(self.max_shape[1])) + 0.5
        d2 = numpy.outer(numpy.ones(self.max_shape[0]), numpy.arange(self.max_shape[1])) + 0.5
        dX = self.spline.splineFuncX(d2, d1)
        dY = self.spline.splineFuncY(d2, d1)
        p1 = dY + d1
        p2 = dX + d2
        below_min = numpy.logical_or((p2 < self.spline.xmin), (p1 < self.spline.ymin))
        above_max = numpy.logical_or((p2 > self.spline.xmax), (p1 > self.spline.ymax))
        mask = numpy.logical_or(below_min, above_max)
        return mask


class Basler(Detector):
    """
    Basler camera are simple CCD camara over GigaE

    """
    force_pixel = True
    aliases = ["aca1300"]
    MAX_SHAPE = (966, 1296)
    def __init__(self, pixel=3.75e-6):
        super(Basler, self).__init__(pixel1=pixel, pixel2=pixel)
        self.max_shape = self.MAX_SHAPE

class Mar345(Detector):

    """
    Mar345 Imaging plate detector

    """
    force_pixel = False
    MAX_SHAPE = (3450, 3450)
    aliases = ["MAR 345", "Mar3450"]
    def __init__(self, pixel1=100e-6, pixel2=100e-6):
        Detector.__init__(self, pixel1, pixel2)
        self.max_shape = (int(self.MAX_SHAPE[0] * 100e-6 / self.pixel1),
                          int(self.MAX_SHAPE[1] * 100e-6 / self.pixel2))
#        self.mode = 1

    def calc_mask(self):
        c = [i // 2 for i in self.max_shape]
        x, y = numpy.ogrid[:self.max_shape[0], :self.max_shape[1]]
        mask = ((x + 0.5 - c[0]) ** 2 + (y + 0.5 - c[1]) ** 2) > (c[0]) ** 2
        return mask


class Xpad_flat(Detector):
    """
    Xpad detector: generic description for
    ImXPad detector with 8x7modules
    """
    MODULE_SIZE = (120, 80)
    MODULE_GAP = (3 + 3.57 * 1000 / 130, 3)  # in pixels
    force_pixel = True
    MAX_SHAPE = (960, 560)
    def __init__(self, pixel1=130e-6, pixel2=130e-6):
        super(Xpad_flat, self).__init__(pixel1=pixel1, pixel2=pixel2)
        self.max_shape = self.MAX_SHAPE

    def __repr__(self):
        return "Detector %s\t PixelSize= %.3e, %.3e m" % \
                (self.name, self.pixel1, self.pixel2)

    def calc_mask(self):
        """
        Returns a generic mask for Xpad detectors...
        discards the first line and raw form all modules:
        those are 2.5x bigger and often mis - behaving
        """
        if (self.max_shape[0] or self.max_shape[1]) is None:
            raise NotImplementedError("Generic Xpad detector does not"
                                      " know the max size ...")
        mask = numpy.zeros(self.max_shape, dtype=numpy.int8)
        # workinng in dim0 = Y
        for i in range(0, self.max_shape[0], self.MODULE_SIZE[0]):
            mask[i, :] = 1
            mask[i + self.MODULE_SIZE[0] - 1, :] = 1
        # workinng in dim1 = X
        for i in range(0, self.max_shape[1], self.MODULE_SIZE[1]):
            mask[:, i ] = 1
            mask[:, i + self.MODULE_SIZE[1] - 1] = 1
        return mask

    def calc_cartesian_positions(self, d1=None, d2=None):
        """
        Calculate the position of each pixel center in cartesian coordinate
        and in meter of a couple of coordinates.
        The half pixel offset is taken into account here !!!

        @param d1: the Y pixel positions (slow dimension)
        @type d1: ndarray (1D or 2D)
        @param d2: the X pixel positions (fast dimension)
        @type d2: ndarray (1D or 2D)

        @return: position in meter of the center of each pixels.
        @rtype: ndarray

        d1 and d2 must have the same shape, returned array will have
        the same shape.

        """
        if (d1 is None):
            c1 = numpy.arange(self.max_shape[0])
            for i in range(self.max_shape[0] // self.MODULE_SIZE[0]):
                c1[i * self.MODULE_SIZE[0],
                   (i + 1) * self.MODULE_SIZE[0]] += i * self.MODULE_GAP[0]
        else:
            c1 = d1 + (d1.astype(numpy.int64) // self.MODULE_SIZE[0])\
                * self.MODULE_GAP[0]
        if (d2 is None):
            c2 = numpy.arange(self.max_shape[1])
            for i in range(self.max_shape[1] // self.MODULE_SIZE[1]):
                c2[i * self.MODULE_SIZE[1],
                   (i + 1) * self.MODULE_SIZE[1]] += i * self.MODULE_GAP[1]
        else:
            c2 = d2 + (d2.astype(numpy.int64) // self.MODULE_SIZE[1])\
                * self.MODULE_GAP[1]

        p1 = self.pixel1 * (0.5 + c1)
        p2 = self.pixel2 * (0.5 + c2)
        return p1, p2


def _pixels_compute_center(pixels_size):
    """
    given a list of pixel size, this method return the center of each
    pixels. This method is generic.

    @param pixels_size: the size of the pixels.
    @type length: ndarray

    @return: the center-coordinates of each pixels 0..length
    @rtype: ndarray
    """
    center = pixels_size.cumsum()
    tmp = center.copy()
    center[1:] += tmp[:-1]
    center /= 2.

    return center


def _pixels_extract_coordinates(coordinates, pixels):
    """
    given a list of pixel coordinates, return the correspondig
    pixels coordinates extracted from the coodinates array.

    @param coodinates: the pixels coordinates
    @type coordinates: ndarray 1D (pixels -> coordinates)
    @param pixels: the list of pixels to extract.
    @type pixels: ndarray 1D(calibration) or 2D(integration)

    @return: the pixels coordinates
    @rtype: ndarray
    """
    return coordinates[pixels] if (pixels is not None) else coordinates


class ImXPadS140(Detector):
    """
    ImXPad detector: ImXPad s140 detector with 2x7modules
    """
    MODULE_SIZE = (120, 80)  # number of pixels per module (y, x)
    MAX_SHAPE = (240, 560)  # max size of the detector
    PIXEL_SIZE = (130e-6, 130e-6)
    force_pixel = True
    aliases = ["Imxpad S140"]

    class __metaclass__(DetectorMeta):

        @lazy_property
        def COORDINATES(cls):
            """
            cache used to store the coordinates of the y, x, detector
            pixels. These array are compute only once for all
            instances.
            """
            return tuple(_pixels_compute_center(cls._pixels_size(n, m, p))
                         for n, m, p in zip(cls.MAX_SHAPE,
                                            cls.MODULE_SIZE,
                                            cls.PIXEL_SIZE))

    @staticmethod
    def _pixels_size(length, module_size, pixel_size):
        """
        given the length (in pixel) of the detector, the size of a
        module (in pixels) and the pixel_size (in meter). this method
        return the length of each pixels 0..length.

        @param length: the number of pixel to compute
        @type length: int
        @param module_size: the number of pixel of one module
        @type module_size: int
        @param pixel_size: the size of one pixels (meter per pixel)
        @type length: float

        @return: the coordinates of each pixels 0..length
        @rtype: ndarray
        """
        size = numpy.ones(length)
        n = length // module_size
        for i in range(1, n):
            size[i * module_size - 1] = 2.5
            size[i * module_size] = 2.5
        return pixel_size * size

    def __init__(self, pixel1=130e-6, pixel2=130e-6):
        super(ImXPadS140, self).__init__(pixel1=pixel1, pixel2=pixel2)

        self.max_shape = self.MAX_SHAPE

    def __repr__(self):
        return "Detector %s\t PixelSize= %.3e, %.3e m" % \
            (self.name, self.pixel1, self.pixel2)


    def calc_cartesian_positions(self, d1=None, d2=None):
        """
        Calculate the position of each pixel center in cartesian coordinate
        and in meter of a couple of coordinates.
        The half pixel offset is taken into account here !!!

        @param d1: the Y pixel positions (slow dimension)
        @type d1: ndarray (1D or 2D)
        @param d2: the X pixel positions (fast dimension)
        @type d2: ndarray (1D or 2D)

        @return: position in meter of the center of each pixels.
        @rtype: ndarray

        d1 and d2 must have the same shape, returned array will have
        the same shape.

        """
        return tuple(_pixels_extract_coordinates(coordinates, pixels)
                     for coordinates, pixels in zip(ImXPadS140.COORDINATES,
                                                    (d1, d2)))


class Perkin(Detector):
    """
    Perkin detector

    """
    aliases = ["Perkin detector"]
    force_pixel = True
    MAX_SHAPE = (2048, 2048)
    def __init__(self, pixel=200e-6):
        super(Perkin, self).__init__(pixel1=pixel, pixel2=pixel)


class Rayonix(Detector):
    force_pixel = True
    BINNED_PIXEL_SIZE = {}

    def __init__(self, pixel1=None, pixel2=None):
        Detector.__init__(self, pixel1=pixel1, pixel2=pixel2)

    def get_binning(self):
        return self._binning

    def set_binning(self, bin_size=(1, 1)):
        """
        Set the "binning" of the detector,

        @param bin_size: set the binning of the detector
        @type bin_size: int or (int, int)
        """
        if "__len__" in dir(bin_size) and len(bin_size) >= 2:
            bin_size = int(round(float(bin_size[0]))), int(round(float(bin_size[1])))
        else:
            b = int(round(float(bin_size)))
            bin_size = (b, b)
        if bin_size != self._binning:
            if (bin_size[0] in self.BINNED_PIXEL_SIZE) and (bin_size[1] in self.BINNED_PIXEL_SIZE):
                self._pixel1 = self.BINNED_PIXEL_SIZE[bin_size[0]]
                self._pixel2 = self.BINNED_PIXEL_SIZE[bin_size[1]]
            else:
                logger.warning("Binning factor (%sx%s) is not an official value for Rayonix detectors" % (bin_size[0], bin_size[1]))
                self._pixel1 = self.BINNED_PIXEL_SIZE[1] / float(bin_size[0])
                self._pixel2 = self.BINNED_PIXEL_SIZE[1] / float(bin_size[1])
            self._binning = bin_size
            self.max_shape = (self.MAX_SHAPE[0] // bin_size[0],
                              self.MAX_SHAPE[1] // bin_size[1])
    binning = property(get_binning, set_binning)


class Rayonix133(Rayonix):
    """
    Rayonix 133 2D CCD detector detector also known as mar133

    Personnal communication from M. Blum

    What should be the default binning factor for those cameras ?

    Circular detector
    """
    force_pixel = True
    BINNED_PIXEL_SIZE = {1: 32e-6,
                         2: 64e-6,
                         4: 128e-6,
                         8: 256e-6,
                         }
    MAX_SHAPE = (4096 , 4096)
    aliases = ["MAR133"]

    def __init__(self):
        Rayonix.__init__(self, pixel1=64e-6, pixel2=64e-6)
        self.max_shape = (2048, 2048)
        self._binning = (2, 2)

    def calc_mask(self):
        """Circular mask"""
        c = [i // 2 for i in self.max_shape]
        x, y = numpy.ogrid[:self.max_shape[0], :self.max_shape[1]]
        mask = ((x + 0.5 - c[0]) ** 2 + (y + 0.5 - c[1]) ** 2) > (c[0]) ** 2
        return mask


class RayonixSx165(Rayonix):
    """
    Rayonix sx165 2d Detector also known as MAR165.

    Circular detector
    """
    BINNED_PIXEL_SIZE = {1: 39.5e-6,
                         2: 79e-6,
                         3: 118.616e-6,  # image shape is then 1364 not 1365 !
                         4: 158e-6,
                         8: 316e-6,
                         }
    MAX_SHAPE = (4096 , 4096)
    aliases = ["MAR165", "Rayonix Sx165"]
    force_pixel = True

    def __init__(self):
        Rayonix.__init__(self, pixel1=39.5e-6, pixel2=39.5e-6)
        self.max_shape = self.MAX_SHAPE
        self._binning = (1, 1)

    def calc_mask(self):
        """Circular mask"""
        c = [i // 2 for i in self.max_shape]
        x, y = numpy.ogrid[:self.max_shape[0], :self.max_shape[1]]
        mask = ((x + 0.5 - c[0]) ** 2 + (y + 0.5 - c[1]) ** 2) > (c[0]) ** 2
        return mask


class RayonixSx200(Rayonix):
    """
    Rayonix sx200 2d CCD Detector.

    Pixel size are personnal communication from M. Blum.
    """
    BINNED_PIXEL_SIZE = {1: 48e-6,
                         2: 96e-6,
                         3: 144e-6,
                         4: 192e-6,
                         8: 384e-6,
                         }
    MAX_SHAPE = (4096 , 4096)
    aliases = ["Rayonix sx200"]

    def __init__(self):
        Rayonix.__init__(self, pixel1=48e-6, pixel2=48e-6)
        self.max_shape = self.MAX_SHAPE
        self._binning = (1, 1)


class RayonixLx170(Rayonix):
    """
    Rayonix lx170 2d CCD Detector (2x1 CCDs).

    Nota: this is the same for lx170hs
    """
    BINNED_PIXEL_SIZE = {1:  44.2708e-6,
                         2:  88.5417e-6,
                         3: 132.8125e-6,
                         4: 177.0833e-6,
                         5: 221.3542e-6,
                         6: 265.625e-6,
                         8: 354.1667e-6,
                         10:442.7083e-6
                         }
    MAX_SHAPE = (1920, 3840)
    force_pixel = True
    aliases = ["Rayonix lx170"]

    def __init__(self):
        Rayonix.__init__(self, pixel1=44.2708e-6, pixel2=44.2708e-6)
        self.max_shape = self.MAX_SHAPE
        self._binning = (1, 1)


class RayonixMx170(Rayonix):
    """
    Rayonix mx170 2d CCD Detector (2x2 CCDs).

    Nota: this is the same for mx170hs
    """
    BINNED_PIXEL_SIZE = {1:  44.2708e-6,
                         2:  88.5417e-6,
                         3: 132.8125e-6,
                         4: 177.0833e-6,
                         5: 221.3542e-6,
                         6: 265.625e-6,
                         8: 354.1667e-6,
                         10:442.7083e-6
                         }
    MAX_SHAPE = (3840, 3840)
    aliases = ["Rayonix mx170"]

    def __init__(self):
        Rayonix.__init__(self, pixel1=44.2708e-6, pixel2=44.2708e-6)
        self.max_shape = self.MAX_SHAPE
        self._binning = (1, 1)


class RayonixLx255(Rayonix):
    """
    Rayonix lx255 2d Detector (3x1 CCDs)

    Nota: this detector is also called lx255hs
    """
    BINNED_PIXEL_SIZE = {1:  44.2708e-6,
                         2:  88.5417e-6,
                         3: 132.8125e-6,
                         4: 177.0833e-6,
                         5: 221.3542e-6,
                         6: 265.625e-6,
                         8: 354.1667e-6,
                         10:442.7083e-6
                         }
    MAX_SHAPE = (1920 , 5760)
    aliases = [ "Rayonix lx225"]

    def __init__(self):
        Rayonix.__init__(self, pixel1=44.2708e-6, pixel2=44.2708e-6)
        self.max_shape = self.MAX_SHAPE
        self._binning = (1, 1)


class RayonixMx225(Rayonix):
    """
    Rayonix mx225 2D CCD detector detector

    Nota: this is the same definition for mx225he
    Personnal communication from M. Blum
    """
    force_pixel = True
    BINNED_PIXEL_SIZE = {1:  36.621e-6,
                         2:  73.242e-6,
                         3: 109.971e-6,
                         4: 146.484e-6,
                         8: 292.969e-6
                         }
    MAX_SHAPE = (6144, 6144)
    aliases = ["Rayonix mx225"]

    def __init__(self):
        Rayonix.__init__(self, pixel1=73.242e-6, pixel2=73.242e-6)
        self.max_shape = (3072, 3072)
        self._binning = (2, 2)


class RayonixMx225hs(Rayonix):
    """
    Rayonix mx225hs 2D CCD detector detector

    Pixel size from a personnal communication from M. Blum
    """
    force_pixel = True
    BINNED_PIXEL_SIZE = {1:  39.0625e-6,
                         2:  78.125e-6,
                         3: 117.1875e-6,
                         4: 156.25e-6,
                         5: 195.3125e-6,
                         6: 234.3750e-6,
                         8: 312.5e-6,
                         10:390.625e-6,
                         }
    MAX_SHAPE = (5760 , 5760)
    aliases = ["Rayonix mx225hs"]
    def __init__(self):
        Rayonix.__init__(self, pixel1=78.125e-6, pixel2=78.125e-6)
        self.max_shape = (2880, 2880)
        self._binning = (2, 2)


class RayonixMx300(Rayonix):
    """
    Rayonix mx300 2D detector (4x4 CCDs)

    Pixel size from a personnal communication from M. Blum
    """
    force_pixel = True
    BINNED_PIXEL_SIZE = {1:  36.621e-6,
                         2:  73.242e-6,
                         3: 109.971e-6,
                         4: 146.484e-6,
                         8: 292.969e-6
                         }
    MAX_SHAPE = (8192, 8192)
    aliases = ["Rayonix mx300"]

    def __init__(self):
        Rayonix.__init__(self, pixel1=73.242e-6, pixel2=73.242e-6)
        self.max_shape = (4096, 4096)
        self._binning = (2, 2)


class RayonixMx300hs(Rayonix):
    """
    Rayonix mx300hs 2D detector (4x4 CCDs)

    Pixel size from a personnal communication from M. Blum
    """
    force_pixel = True
    BINNED_PIXEL_SIZE = {1:   39.0625e-6,
                         2:   78.125e-6,
                         3:  117.1875e-6,
                         4:  156.25e-6,
                         5:  195.3125e-6,
                         6:  234.3750e-6,
                         8:  312.5e-6,
                         10: 390.625e-6
                         }
    MAX_SHAPE = (7680, 7680)
    aliases = ["Rayonix mx300hs"]

    def __init__(self):
        Rayonix.__init__(self, pixel1=78.125e-6, pixel2=78.125e-6)
        self.max_shape = (3840, 3840)
        self._binning = (2, 2)


class RayonixMx340hs(Rayonix):
    """
    Rayonix mx340hs 2D detector (4x4 CCDs)

    Pixel size from a personnal communication from M. Blum
    """
    force_pixel = True
    BINNED_PIXEL_SIZE = {1:   44.2708e-6,
                         2:   88.5417e-6,
                         3:  132.8125e-6,
                         4:  177.0833e-6,
                         5:  221.3542e-6,
                         6:  265.625e-6,
                         8:  354.1667e-6,
                         10: 442.7083e-6
                         }
    MAX_SHAPE = (7680 , 7680)
    aliases = ["Rayonix mx340hs"]

    def __init__(self):
        Rayonix.__init__(self, pixel1=88.5417e-6, pixel2=88.5417e-6)
        self.max_shape = (3840, 3840)
        self._binning = (2, 2)

class RayonixSx30hs(Rayonix):
    """
    Rayonix sx30hs 2D CCD camera (1 CCD chip)

    Pixel size from a personnal communication from M. Blum
    """
    BINNED_PIXEL_SIZE = {1:  15.625e-6,
                         2:  31.25e-6,
                         3:  46.875e-6,
                         4:  62.5e-6,
                         5:  78.125e-6,
                         6:  93.75e-6,
                         8: 125.0e-6,
                         10:156.25e-6
                         }
    MAX_SHAPE = (1920 , 1920)
    aliases = ["Rayonix Sx30hs"]

    def __init__(self):
        Rayonix.__init__(self, pixel1=15.625e-6, pixel2=15.625e-6)
        self.max_shape = self.MAX_SHAPE
        self._binning = (1, 1)


class RayonixSx85hs(Rayonix):
    """
    Rayonix sx85hs 2D CCD camera (1 CCD chip)

    Pixel size from a personnal communication from M. Blum
    """
    BINNED_PIXEL_SIZE = {1:   44.2708e-6,
                         2:   88.5417e-6,
                         3:   132.8125e-6,
                         4:   177.0833e-6,
                         5:   221.3542e-6,
                         6:   265.625e-6,
                         8:   354.1667e-6,
                         10:  442.7083e-6
                         }
    MAX_SHAPE = (1920 , 1920)
    aliases = ["Rayonix Sx85hs"]
    def __init__(self):
        Rayonix.__init__(self, pixel1=44.2708e-6, pixel2=44.2708e-6)
        self.max_shape = self.MAX_SHAPE
        self._binning = (1, 1)

class RayonixMx425hs(Rayonix):
    """
    Rayonix mx425hs 2D CCD camera (5x5 CCD chip)

    Pixel size from a personnal communication from M. Blum
    """
    BINNED_PIXEL_SIZE = {1:   44.2708e-6,
                         2:   88.5417e-6,
                         3:   132.8125e-6,
                         4:   177.0833e-6,
                         5:   221.3542e-6,
                         6:   265.625e-6,
                         8:   354.1667e-6,
                         10:  442.7083e-6
                         }
    MAX_SHAPE = (9600 , 9600)
    aliases = ["Rayonix mx425hs"]
    def __init__(self):
        Rayonix.__init__(self, pixel1=44.2708e-6, pixel2=44.2708e-6)
        self.max_shape = self.MAX_SHAPE
        self._binning = (1, 1)


class RayonixMx325(Rayonix):
    """
    Rayonix mx325 and mx325he 2D detector (4x4 CCD chips)

    Pixel size from a personnal communication from M. Blum
    """
    BINNED_PIXEL_SIZE = {1:  39.673e-6,
                         2:  79.346e-6,
                         3: 119.135e-6,
                         4: 158.691e-6,
                         8: 317.383e-6
                         }
    MAX_SHAPE = (8192 , 8192)
    aliases = ["Rayonix mx325"]
    def __init__(self):
        Rayonix.__init__(self, pixel1=79.346e-6, pixel2=79.346e-6)
        self.max_shape = (4096, 4096)
        self._binning = (2, 2)





ALL_DETECTORS = Detector.registry
detector_factory = Detector.factory


