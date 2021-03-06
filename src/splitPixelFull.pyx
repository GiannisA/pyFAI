#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#    Project: Fast Azimuthal integration
#             https://github.com/pyFAI/pyFAI
#
#    Copyright (C) European Synchrotron Radiation Facility, Grenoble, France
#
#    Principal author:       Giannis Ashiotis
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
# 
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
# 
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

"""
Calculates histograms of pos0 (tth) weighted by Intensity

Splitting is done on the pixel's bounding box like fit2D, 
reverse implementation based on a sparse matrix multiplication
"""
__author__ = "Giannis Ashiotis"
__contact__ = "Jerome.kieffer@esrf.fr"
__date__ = "20141020"
__status__ = "stable"
__license__ = "GPLv3+"

import cython
cimport numpy
import numpy
from libc.math cimport fabs, M_PI, floor, sqrt
from libc.stdio cimport printf, fflush, stdout
from cython.view cimport array as cvarray

cdef double pi = M_PI
cdef double piover2 = pi * 0.5
cdef double onef = 1.0


cdef inline double area4(double a0, double a1, double b0, double b1, double c0, double c1, double d0, double d1) nogil:
    """
    Calculate the area of the ABCD quadrilataire  with corners:
    A(a0,a1)
    B(b0,b1)
    C(c0,c1)
    D(d0,d1)
    @return: area, i.e. 1/2 * (AC ^ BD)
    """
    return 0.5 * fabs(((c0 - a0) * (d1 - b1)) - ((c1 - a1) * (d0 - b0)))

cdef struct Function:
    double slope
    double intersect
           
cdef inline double integrate(double A0, double B0, Function AB) nogil:
    """
    integrates the line defined by AB, from A0 to B0
    param A0: first limit
    param B0: second limit
    param AB: struct with the slope and point of intersection of the line
    """    
    if A0 == B0:
        return 0.0
    else:
        return AB.slope * (B0 * B0 - A0 * A0) * 0.5 + AB.intersect * (B0 - A0)
    
@cython.cdivision(True)
cdef inline double getBinNr(double x0, double pos0_min, double dpos) nogil:
    """
    calculate the bin number for any point
    param x0: current position
    param pos0_min: position minimum
    param dpos: bin width
    """
    return (x0 - pos0_min) / dpos

@cython.cdivision(True)
cdef inline double getBin1Nr(double x0, double pos0_min, double delta,double var) nogil:
    """
    calculate the bin number for any point
    param x0: current position
    param pos0_min: position minimum
    param delta: bin width
    """
    if var:
        if x0 >= 0:
            return (x0 - pos0_min) / delta
        else:
            return (x0 + 2*pi - pos0_min) / delta   # temporary fix....
    else:
        return (x0 - pos0_min) / delta
    
cdef struct MyPoint:
    double i
    double j

cdef struct MyPoly:
    int size
    MyPoint[8] data

cdef inline MyPoint ComputeIntersection0(MyPoint S, MyPoint E, double clipEdge) nogil:
    cdef MyPoint intersection
    intersection.i = clipEdge
    intersection.j = (E.j-S.j)*(clipEdge-S.i)/(E.i-S.i) + S.j
    return intersection

cdef inline MyPoint ComputeIntersection1(MyPoint S, MyPoint E, double clipEdge) nogil:
    cdef MyPoint intersection
    intersection.i = (E.i-S.i)*(clipEdge-S.j)/(E.j-S.j) + S.i
    intersection.j = clipEdge
    return intersection
    
cdef inline int point_and_line(double x0, double y0, double x1, double y1, double x, double y) nogil:
    cdef double tmp = (y-y0)*(x1-x0) - (x-x0)*(y1-y0)
    return (tmp > 0) - (tmp < 0)
    
cdef inline int foo(double A, double B, double C, double D) nogil:    # safeguard for pixel crossing from -pi to pi
    return (((A > piover2) and (B > piover2) and (C < -piover2) and (D < -piover2)) or
            ((A < -piover2) and (B < -piover2) and (C > piover2) and (D > piover2)) or
            ((A > piover2) and (B < -piover2) and (C > piover2) and (D < -piover2)) or
            ((A < -piover2) and (B > piover2) and (C < -piover2) and (D > piover2)) or
            ((A > piover2) and (B < -piover2) and (C < -piover2) and (D > piover2)) or
            ((A < -piover2) and (B > piover2) and (C > piover2) and (D < -piover2)))
            
cdef double area_n(MyPoly poly) nogil:
    if   poly.size is 3:
            return 0.5*fabs(poly.data[0].i*poly.data[1].j+poly.data[1].i*poly.data[2].j+poly.data[2].i*poly.data[0].j-
                           poly.data[1].i*poly.data[0].j-poly.data[2].i*poly.data[1].j-poly.data[0].i*poly.data[2].j)
    elif poly.size is 4:
            return 0.5*fabs(poly.data[0].i*poly.data[1].j+poly.data[1].i*poly.data[2].j+poly.data[2].i*poly.data[3].j+poly.data[3].i*poly.data[0].j-
                           poly.data[1].i*poly.data[0].j-poly.data[2].i*poly.data[1].j-poly.data[3].i*poly.data[2].j-poly.data[0].i*poly.data[3].j)
    elif poly.size is 5:
            return 0.5*fabs(poly.data[0].i*poly.data[1].j+poly.data[1].i*poly.data[2].j+poly.data[2].i*poly.data[3].j+poly.data[3].i*poly.data[4].j+poly.data[4].i*poly.data[0].j-
                           poly.data[1].i*poly.data[0].j-poly.data[2].i*poly.data[1].j-poly.data[3].i*poly.data[2].j-poly.data[4].i*poly.data[3].j-poly.data[0].i*poly.data[4].j)
    elif poly.size is 6:
            return 0.5*fabs(poly.data[0].i*poly.data[1].j+poly.data[1].i*poly.data[2].j+poly.data[2].i*poly.data[3].j+poly.data[3].i*poly.data[4].j+poly.data[4].i*poly.data[5].j+poly.data[5].i*poly.data[0].j-
                           poly.data[1].i*poly.data[0].j-poly.data[2].i*poly.data[1].j-poly.data[3].i*poly.data[2].j-poly.data[4].i*poly.data[3].j-poly.data[5].i*poly.data[4].j-poly.data[0].i*poly.data[5].j)
    elif poly.size is 7:
            return 0.5*fabs(poly.data[0].i*poly.data[1].j+poly.data[1].i*poly.data[2].j+poly.data[2].i*poly.data[3].j+poly.data[3].i*poly.data[4].j+poly.data[4].i*poly.data[5].j+poly.data[5].i*poly.data[6].j+poly.data[6].i*poly.data[0].j-
                           poly.data[1].i*poly.data[0].j-poly.data[2].i*poly.data[1].j-poly.data[3].i*poly.data[2].j-poly.data[4].i*poly.data[3].j-poly.data[5].i*poly.data[4].j-poly.data[6].i*poly.data[5].j-poly.data[0].i*poly.data[6].j)
    elif poly.size is 8:
            return 0.5*fabs(poly.data[0].i*poly.data[1].j+poly.data[1].i*poly.data[2].j+poly.data[2].i*poly.data[3].j+poly.data[3].i*poly.data[4].j+poly.data[4].i*poly.data[5].j+poly.data[5].i*poly.data[6].j+poly.data[6].i*poly.data[7].j+poly.data[7].i*poly.data[0].j-
                           poly.data[1].i*poly.data[0].j-poly.data[2].i*poly.data[1].j-poly.data[3].i*poly.data[2].j-poly.data[4].i*poly.data[3].j-poly.data[5].i*poly.data[4].j-poly.data[6].i*poly.data[5].j-poly.data[7].i*poly.data[6].j-poly.data[0].i*poly.data[7].j)


@cython.cdivision(True)
@cython.boundscheck(False)
@cython.wraparound(False)
def fullSplit1D(numpy.ndarray pos not None,
                numpy.ndarray weights not None,
                size_t bins=100,
                pos0Range=None,
                pos1Range=None,
                dummy=None,
                delta_dummy=None,
                mask=None,
                dark=None,
                flat=None,
                solidangle=None,
                polarization=None
              ):
    """
    Calculates histogram of pos weighted by weights

    Splitting is done on the pixel's bounding box like fit2D.
    No compromise for speed has been made here.


    @param pos: 3D or 4D array with the coordinates of each pixel point
    @param weights: array with intensities
    @param bins: number of output bins
    @param pos0Range: minimum and maximum  of the 2th range
    @param pos1Range: minimum and maximum  of the chi range
    @param dummy: value for bins without pixels
    @param delta_dummy: precision of dummy value
    @param mask: array (of int8) with masked pixels with 1 (0=not masked)
    @param dark: array (of float64) with dark noise to be subtracted (or None)
    @param flat: array (of float64) with flat image
    @param polarization: array (of float64) with polarization correction
    @param solidangle: array (of float64) with flat image
    @return 2theta, I, weighted histogram, unweighted histogram
    """
    cdef size_t  size = weights.size
    if pos.ndim > 3: #create a view
        pos = pos.reshape((-1,4,2))
    assert pos.shape[0] == size
    assert pos.shape[1] == 4
    assert pos.shape[2] == 2
    assert pos.ndim == 3
    assert bins > 1
    cdef:
        numpy.ndarray[numpy.float64_t, ndim = 3] cpos = numpy.ascontiguousarray(pos,dtype=numpy.float64)
        numpy.ndarray[numpy.float64_t, ndim = 1] cdata = numpy.ascontiguousarray(weights.ravel(), dtype=numpy.float64)
        numpy.ndarray[numpy.float64_t, ndim = 1] outData = numpy.zeros(bins, dtype=numpy.float64)
        numpy.ndarray[numpy.float64_t, ndim = 1] outCount = numpy.zeros(bins, dtype=numpy.float64)
        numpy.ndarray[numpy.float64_t, ndim = 1] outMerge = numpy.zeros(bins, dtype=numpy.float64)
        numpy.int8_t[:] cmask
        double[:] cflat, cdark, cpolarization, csolidangle
        double cdummy=0, cddummy=0, data=0
        double pos0_min=0, pos0_max=0, pos0_maxin=0, pos1_min=0, pos1_max=0, pos1_maxin=0
        double areaPixel=0, dpos=0, fbin0_min=0, fbin0_max=0#, fbin1_min, fbin1_max 
        double A0=0, B0=0, C0=0, D0=0, A1=0, B1=0, C1=0, D1=0
        double A_lim=0, B_lim=0, C_lim=0, D_lim=0
        double oneOverArea=0, partialArea=0, tmp=0
        double max0, min0, min1, max1
        Function AB, BC, CD, DA
        double epsilon=1e-10
        int lut_size = 0
        bint check_pos1=False, check_mask=False, do_dummy=False, do_dark=False, do_flat=False, do_polarization=False, do_solidangle=False
        int i=0, idx=0, bin=0, bin0, bin0_max=0, bin0_min=0, pixel_bins=0, cur_bin

    if pos0Range is not None and len(pos0Range) > 1:
        pos0_min = min(pos0Range)
        pos0_maxin = max(pos0Range)
    else:
        pos0_min = pos[:, :, 0].min()
        pos0_maxin = pos[:, :, 0].max()
    pos0_max = pos0_maxin * (1 + numpy.finfo(numpy.float32).eps)
    if pos1Range is not None and len(pos1Range) > 1:
        pos1_min = min(pos1Range)
        pos1_maxin = max(pos1Range)
        do_pos1 = True
    else:
        pos1_min = pos[:, :, 1].min()
        pos1_maxin = pos[:, :, 1].max()
    pos1_max = pos1_maxin * (1 + numpy.finfo(numpy.float32).eps)
    dpos = (pos0_max - pos0_min) / (< double > (bins))

    outPos = numpy.linspace(pos0_min + 0.5 * dpos, pos0_maxin - 0.5 * dpos, bins)

    if (dummy is not None) and (delta_dummy is not None):
        check_dummy = True
        cdummy = < double > dummy
        cddummy = < double > delta_dummy
    elif (dummy is not None):
        check_dummy = True
        cdummy = < double > dummy
        cddummy = 0.0
    else:
        check_dummy = False
        cdummy = 0.0
        cddummy = 0.0

    if mask is not None:
        check_mask = True
        assert mask.size == size
        cmask = numpy.ascontiguousarray(mask.ravel(), dtype=numpy.int8)
    if dark is not None:
        do_dark = True
        assert dark.size == size
        cdark = numpy.ascontiguousarray(dark.ravel(), dtype=numpy.float64)
    if flat is not None:
        do_flat = True
        assert flat.size == size
        cflat = numpy.ascontiguousarray(flat.ravel(), dtype=numpy.float64)
    if polarization is not None:
        do_polarization = True
        assert polarization.size == size
        cpolarization = numpy.ascontiguousarray(polarization.ravel(), dtype=numpy.float64)
    if solidangle is not None:
        do_solidangle = True
        assert solidangle.size == size
        csolidangle = numpy.ascontiguousarray(solidangle.ravel(), dtype=numpy.float64)

        
    with nogil:
        for idx in range(size):

            if (check_mask) and (cmask[idx]):
                continue

            data = cdata[idx]
            if check_dummy and ((cddummy == 0.0 and data == cdummy) or (cddummy != 0.0 and fabs(data - cdummy) <= cddummy)):
                continue

            A0 = getBinNr(< double > cpos[idx, 0, 0], pos0_min, dpos)
            A1 = < double > cpos[idx, 0, 1]
            B0 = getBinNr(< double > cpos[idx, 1, 0], pos0_min, dpos)
            B1 = < double > cpos[idx, 1, 1]
            C0 = getBinNr(< double > cpos[idx, 2, 0], pos0_min, dpos)
            C1 = < double > cpos[idx, 2, 1]
            D0 = getBinNr(< double > cpos[idx, 3, 0], pos0_min, dpos)
            D1 = < double > cpos[idx, 3, 1]

            min0 = min(A0, B0, C0, D0)
            max0 = max(A0, B0, C0, D0)
            if (max0 < 0) or (min0 >= bins):
                continue
            if check_pos1:
                min1 = min(A1, B1, C1, D1)
                max1 = max(A1, B1, C1, D1)
                if (max1 < pos1_min) or (min1 > pos1_maxin):
                    continue

            if do_dark:
                data -= cdark[idx]
            if do_flat:
                data /= cflat[idx]
            if do_polarization:
                data /= cpolarization[idx]
            if do_solidangle:
                data /= csolidangle[idx]

            bin0_min = < int > floor(min0)
            bin0_max = < int > floor(max0)

            if bin0_min == bin0_max:
                # All pixel is within a single bin
                outCount[bin0_min] += 1
                outData[bin0_min] += data
                lut_size += 1

            else:
                A0 -= bin0_min
                B0 -= bin0_min
                C0 -= bin0_min
                D0 -= bin0_min
                
                AB.slope = (B1 - A1) / (B0 - A0)
                AB.intersect = A1 - AB.slope * A0
                BC.slope = (C1 - B1) / (C0 - B0)
                BC.intersect = B1 - BC.slope * B0
                CD.slope = (D1 - C1) / (D0 - C0)
                CD.intersect = C1 - CD.slope * C0
                DA.slope = (A1 - D1) / (A0 - D0)
                DA.intersect = D1 - DA.slope * D0
                areaPixel = area4(A0, A1, B0, B1, C0, C1, D0, D1)
                oneOverPixelArea = 1.0 / areaPixel
                partialArea2 = 0.0
                for bin in range(bin0_min, bin0_max + 1):
                    bin0 = bin - bin0_min
                    A_lim = (A0<=bin0)*(A0<=(bin0+1))*bin0 + (A0>bin0)*(A0<=(bin0+1))*A0 + (A0>bin0)*(A0>(bin0+1))*(bin0+1)
                    B_lim = (B0<=bin0)*(B0<=(bin0+1))*bin0 + (B0>bin0)*(B0<=(bin0+1))*B0 + (B0>bin0)*(B0>(bin0+1))*(bin0+1)
                    C_lim = (C0<=bin0)*(C0<=(bin0+1))*bin0 + (C0>bin0)*(C0<=(bin0+1))*C0 + (C0>bin0)*(C0>(bin0+1))*(bin0+1)
                    D_lim = (D0<=bin0)*(D0<=(bin0+1))*bin0 + (D0>bin0)*(D0<=(bin0+1))*D0 + (D0>bin0)*(D0>(bin0+1))*(bin0+1)
                    partialArea = integrate(A_lim, B_lim, AB)
                    partialArea += integrate(B_lim, C_lim, BC)
                    partialArea += integrate(C_lim, D_lim, CD)
                    partialArea += integrate(D_lim, A_lim, DA)
                    tmp = fabs(partialArea) * oneOverPixelArea
                    partialArea2 += partialArea
                    outCount[bin] += tmp
                    outData[bin] += data * tmp
                    lut_size += 1

        for i in range(bins):
            if outCount[i] > epsilon:
                outMerge[i] = outData[i] / outCount[i]
            else:
                outMerge[i] = cdummy

#    print(lut_size)
    return outPos, outMerge, outData, outCount



@cython.cdivision(True)
@cython.boundscheck(False)
@cython.wraparound(False)
def fullSplit2D(numpy.ndarray pos not None,
                numpy.ndarray weights not None,
                bins not None,
                pos0Range=None,
                pos1Range=None,
                dummy=None,
                delta_dummy=None,
                mask=None,
                dark=None,
                flat=None,
                solidangle=None,
                polarization=None):
    """
    Calculate 2D histogram of pos weighted by weights

    Splitting is done on the pixel's bounding box like fit2D


    @param pos: 3D array with pos0; Corner A,B,C,D; tth or chi
    @param weights: array with intensities
    @param bins: number of output bins int or 2-tuple of int
    @param pos0Range: minimum and maximum  of the 2th range
    @param pos1Range: minimum and maximum  of the chi range
    @param dummy: value for bins without pixels
    @param delta_dummy: precision of dummy value
    @param mask: array (of int8) with masked pixels with 1 (0=not masked)
    @param dark: array (of float64) with dark noise to be subtracted (or None)
    @param flat: array (of float64) with flat-field image
    @param polarization: array (of float64) with polarization correction
    @param solidangle: array (of float64)with solid angle corrections
    @return  I, edges0, edges1, weighted histogram(2D), unweighted histogram (2D)
    """
    cdef int all_bins0 = 0, all_bins1 = 0, size = weights.size
    if pos.ndim > 3:  # create a view
        pos = pos.reshape((-1, 4, 2))

    assert pos.shape[0] == size
    assert pos.shape[1] == 4  # 4 corners
    assert pos.shape[2] == 2  # tth and chi
    assert pos.ndim == 3
    try:
        all_bins0, all_bins1 = tuple(bins)
    except:
        all_bins0 = all_bins1 = < int > bins
    if all_bins0 <= 0:
        all_bins0 = 1
    if all_bins1 <= 0:
        all_bins1 = 1

    cdef numpy.ndarray[numpy.float64_t, ndim = 3] cpos = numpy.ascontiguousarray(pos,dtype=numpy.float64)
    cdef numpy.ndarray[numpy.float64_t, ndim = 1] cdata = numpy.ascontiguousarray(weights.ravel(), dtype=numpy.float64)
    cdef numpy.ndarray[numpy.float64_t, ndim = 2] outData = numpy.zeros((all_bins0,all_bins1), dtype=numpy.float64)
    cdef numpy.ndarray[numpy.float64_t, ndim = 2] outCount = numpy.zeros((all_bins0,all_bins1), dtype=numpy.float64)
    cdef numpy.ndarray[numpy.float64_t, ndim = 2] outMerge = numpy.zeros((all_bins0,all_bins1), dtype=numpy.float64)
    cdef numpy.ndarray[numpy.float64_t, ndim = 1] edges0 = numpy.zeros(all_bins0, dtype=numpy.float64)
    cdef numpy.ndarray[numpy.float64_t, ndim = 1] edges1 = numpy.zeros(all_bins1, dtype=numpy.float64)
    cdef numpy.int8_t[:] cmask
    cdef double[:] cflat, cdark, cpolarization, csolidangle    
    cdef double pos0_min=0, pos0_max=0, pos0_maxin=0, pos1_min=0, pos1_max=0, pos1_maxin=0
    cdef bint check_mask = False, do_dummy = False, do_dark = False, do_flat = False, do_polarization = False, do_solidangle = False
    cdef double cdummy = 0, cddummy = 0, data = 0

    cdef double max0, min0, min1, max1
    cdef double areaPixel=0, delta0=0, delta1=0, areaPixel2=0
    cdef double A0=0, B0=0, C0=0, D0=0, A1=0, B1=0, C1=0, D1=0
    cdef double A_lim=0, B_lim=0, C_lim=0, D_lim=0
    cdef double oneOverArea=0, partialArea=0, tmp_f=0, var=0
    cdef Function AB, BC, CD, DA
    cdef MyPoint A, B, C, D, S, E
    cdef MyPoly list1, list2
    cdef int bins0, bins1, i=0, j=0, idx=0, bin=0, bin0=0, bin1=0, bin0_max=0, bin0_min=0, bin1_min=0, bin1_max=0, k=0
    cdef int all_bins=all_bins0*all_bins1, pixel_bins=0, tmp_i, index
    cdef double epsilon = 1e-10
    #cdef int range1=0, range2=0

    if pos0Range is not None and len(pos0Range) > 1:
        pos0_min = min(pos0Range)
        pos0_maxin = max(pos0Range)
    else:
        pos0_min = pos[:, :, 0].min()
        pos0_maxin = pos[:, :, 0].max()
    pos0_max = pos0_maxin * (1 + numpy.finfo(numpy.float64).eps)
    if pos1Range is not None and len(pos1Range) > 1:
        pos1_min = min(pos1Range)
        pos1_maxin = max(pos1Range)
    else:
        pos1_min = pos[:, :, 1].min()
        pos1_maxin = pos[:, :, 1].max()
    pos1_max = pos1_maxin * (1 + numpy.finfo(numpy.float64).eps)

    delta0 = (pos0_max - pos0_min) / (< double > (all_bins0))
    delta1 = (pos1_max - pos1_min) / (< double > (all_bins1))
    edges0 = numpy.linspace(pos0_min + 0.5 * delta0, pos0_maxin - 0.5 * delta0, all_bins0)
    edges1 = numpy.linspace(pos1_min + 0.5 * delta1, pos1_maxin - 0.5 * delta1, all_bins1)

    cdef numpy.ndarray[numpy.int8_t, ndim = 2] is_inside = numpy.zeros((< int > (1.5*sqrt(size)/all_bins0) ,< int > (1.5*sqrt(size)/all_bins1)), dtype=numpy.int8)

    if (dummy is not None) and (delta_dummy is not None):
        check_dummy = True
        cdummy = < double > dummy
        cddummy = < double > delta_dummy
    elif (dummy is not None):
        check_dummy = True
        cdummy = < double > dummy
        cddummy = 0.0
    else:
        check_dummy = False
        cdummy = 0.0
        cddummy = 0.0

    if mask is not None:
        check_mask = True
        assert mask.size == size
        cmask = numpy.ascontiguousarray(mask.ravel(), dtype=numpy.int8)
    if dark is not None:
        do_dark = True
        assert dark.size == size
        cdark = numpy.ascontiguousarray(dark.ravel(), dtype=numpy.float64)
    if flat is not None:
        do_flat = True
        assert flat.size == size
        cflat = numpy.ascontiguousarray(flat.ravel(), dtype=numpy.float64)
    if polarization is not None:
        do_polarization = True
        assert polarization.size == size
        cpolarization = numpy.ascontiguousarray(polarization.ravel(), dtype=numpy.float64)
    if solidangle is not None:
        do_solidangle = True
        assert solidangle.size == size
        csolidangle = numpy.ascontiguousarray(solidangle.ravel(), dtype=numpy.float64)
        
    with nogil:
        for idx in range(size):
            #printf("%d\n",idx)
            #fflush(stdout)
            data = cdata[idx]
            if check_dummy and ((cddummy == 0.0 and data == cdummy) or (cddummy != 0.0 and fabs(data - cdummy) <= cddummy)):
                continue

            if (check_mask) and (cmask[idx]):
                continue

            A0 = getBinNr(< double > cpos[idx, 0, 0], pos0_min, delta0)
            B0 = getBinNr(< double > cpos[idx, 1, 0], pos0_min, delta0)
            C0 = getBinNr(< double > cpos[idx, 2, 0], pos0_min, delta0)
            D0 = getBinNr(< double > cpos[idx, 3, 0], pos0_min, delta0)

            var = foo(cpos[idx, 0, 1], cpos[idx, 1, 1], cpos[idx, 2, 1], cpos[idx, 3, 1])
            A1 = getBin1Nr(< double > cpos[idx, 0, 1], pos1_min, delta1, var)
            B1 = getBin1Nr(< double > cpos[idx, 1, 1], pos1_min, delta1, var)
            C1 = getBin1Nr(< double > cpos[idx, 2, 1], pos1_min, delta1, var)
            D1 = getBin1Nr(< double > cpos[idx, 3, 1], pos1_min, delta1, var)


            #if idx is 257027 or idx is 257026:
                #printf("%f %f \n", cpos[idx, 0, 0], cpos[idx, 0, 1])
                #printf("%f %f \n", cpos[idx, 1, 0], cpos[idx, 1, 1])
                #printf("%f %f \n", cpos[idx, 2, 0], cpos[idx, 2, 1])
                #printf("%f %f \n", cpos[idx, 3, 0], cpos[idx, 3, 1])
                #printf(" \n")
                #printf("%f %f \n", A0, A1)
                #printf("%f %f \n", B0, B1)
                #printf("%f %f \n", C0, C1)
                #printf("%f %f \n", D0, D1)
                #fflush(stdout)

            min0 = min(A0, B0, C0, D0)
            max0 = max(A0, B0, C0, D0)
            min1 = min(A1, B1, C1, D1)
            max1 = max(A1, B1, C1, D1)

            if (max0<0) or (min0 >= all_bins0) or (max1<0): # or (min1 >= all_bins1 + 2 ):
                printf("DB out of bound %f %f %f %f\n",min0, max0, min1, max1)  # for DB
                continue
            
            if do_dark:
                data -= cdark[idx]
            if do_flat:
                data /= cflat[idx]
            if do_polarization:
                data /= cpolarization[idx]
            if do_solidangle:
                data /= csolidangle[idx]


            bin0_min = < int > floor(min0)
            bin0_max = < int > floor(max0)
            bin1_min = < int > floor(min1)
            bin1_max = < int > floor(max1)

            #printf("  0 %d  %d \n",bin0_min,bin0_max)
            #fflush(stdout)
            if bin0_min == bin0_max:
                if bin1_min == bin1_max:
                    #Whole pixel is within a single bin
                    index = bin0_min*all_bins1 + bin1_min
                    if index > all_bins:   # for DB
                        printf("DB 0 index = %d > %d!! \n",index,all_bins)
                        fflush(stdout)
                    outCount[bin0_min,bin1_min] += 1.0
                    outData[bin0_min,bin1_min] += data
                else:
                    #printf("  1 %d  %d \n",bin1_min,bin1_max)
                    #fflush(stdout)
                    # transpose of 1D code
                    #A0 -= bin0_min
                    A1 -= bin1_min
                    #B0 -= bin0_min
                    B1 -= bin1_min
                    #C0 -= bin0_min
                    C1 -= bin1_min
                    #D0 -= bin0_min
                    D1 -= bin1_min

                    AB.slope=(B0-A0)/(B1-A1)
                    AB.intersect= A0 - AB.slope*A1
                    BC.slope=(C0-B0)/(C1-B1)
                    BC.intersect= B0 - BC.slope*B1
                    CD.slope=(D0-C0)/(D1-C1)
                    CD.intersect= C0 - CD.slope*C1
                    DA.slope=(A0-D0)/(A1-D1)
                    DA.intersect= D0 - DA.slope*D1

                    areaPixel = area4(A0, A1, B0, B1, C0, C1, D0, D1)
                    oneOverPixelArea = 1.0 / areaPixel

                    #for bin in range(bin0_min, bin0_max+1):
                    for bin1 in range(bin1_max+1 - bin1_min):
                        #bin1 = bin - bin1_min
                        A_lim = (A1<=bin1)*(A1<=(bin1+1))*bin1 + (A1>bin1)*(A1<=(bin1+1))*A1 + (A1>bin1)*(A1>(bin1+1))*(bin1+1)
                        B_lim = (B1<=bin1)*(B1<=(bin1+1))*bin1 + (B1>bin1)*(B1<=(bin1+1))*B1 + (B1>bin1)*(B1>(bin1+1))*(bin1+1)
                        C_lim = (C1<=bin1)*(C1<=(bin1+1))*bin1 + (C1>bin1)*(C1<=(bin1+1))*C1 + (C1>bin1)*(C1>(bin1+1))*(bin1+1)
                        D_lim = (D1<=bin1)*(D1<=(bin1+1))*bin1 + (D1>bin1)*(D1<=(bin1+1))*D1 + (D1>bin1)*(D1>(bin1+1))*(bin1+1)

                        partialArea  = integrate(A_lim, B_lim, AB)
                        partialArea += integrate(B_lim, C_lim, BC)
                        partialArea += integrate(C_lim, D_lim, CD)
                        partialArea += integrate(D_lim, A_lim, DA)

                        index = bin0_min*all_bins1 + bin1_min + bin1
                        if index > all_bins:  # for DB
                            printf("DB 1 index = %d > %d!! \n",index,all_bins)
                            fflush(stdout)
                        partialArea = fabs(partialArea) * oneOverPixelArea
                        outCount[bin0_min,bin1_min+bin1] += partialArea
                        outData[bin0_min,bin1_min+bin1]  += partialArea * data

            elif bin1_min == bin1_max:
                #printf("  1 %d  %d \n",bin1_min,bin1_max)
                #fflush(stdout)
                # 1D code
                A0 -= bin0_min
                #A1 -= bin1_min
                B0 -= bin0_min
                #B1 -= bin1_min
                C0 -= bin0_min
                #C1 -= bin1_min
                D0 -= bin0_min
                #D1 -= bin1_min

                AB.slope=(B1-A1)/(B0-A0)
                AB.intersect= A1 - AB.slope*A0
                BC.slope=(C1-B1)/(C0-B0)
                BC.intersect= B1 - BC.slope*B0
                CD.slope=(D1-C1)/(D0-C0)
                CD.intersect= C1 - CD.slope*C0
                DA.slope=(A1-D1)/(A0-D0)
                DA.intersect= D1 - DA.slope*D0

                areaPixel = area4(A0, A1, B0, B1, C0, C1, D0, D1)
                oneOverPixelArea = 1.0 / areaPixel

                #for bin in range(bin0_min, bin0_max+1):
                for bin0 in range(bin0_max+1 - bin0_min):
                    #bin0 = bin - bin0_min
                    A_lim = (A0<=bin0)*(A0<=(bin0+1))*bin0 + (A0>bin0)*(A0<=(bin0+1))*A0 + (A0>bin0)*(A0>(bin0+1))*(bin0+1)
                    B_lim = (B0<=bin0)*(B0<=(bin0+1))*bin0 + (B0>bin0)*(B0<=(bin0+1))*B0 + (B0>bin0)*(B0>(bin0+1))*(bin0+1)
                    C_lim = (C0<=bin0)*(C0<=(bin0+1))*bin0 + (C0>bin0)*(C0<=(bin0+1))*C0 + (C0>bin0)*(C0>(bin0+1))*(bin0+1)
                    D_lim = (D0<=bin0)*(D0<=(bin0+1))*bin0 + (D0>bin0)*(D0<=(bin0+1))*D0 + (D0>bin0)*(D0>(bin0+1))*(bin0+1)

                    partialArea  = integrate(A_lim, B_lim, AB)
                    partialArea += integrate(B_lim, C_lim, BC)
                    partialArea += integrate(C_lim, D_lim, CD)
                    partialArea += integrate(D_lim, A_lim, DA)

                    index = (bin0_min+bin0)*all_bins1 + bin1_min
                    if index > all_bins:   # for DB
                        printf("DB 2 index = %d > %d!! \n",index,all_bins)
                        fflush(stdout)
                    partialArea = fabs(partialArea) * oneOverPixelArea
                    outCount[bin0_min+bin0,bin1_min] += partialArea
                    outData[bin0_min+bin0,bin1_min]  += partialArea * data
            else:
                #printf("  1 %d  %d \n",bin1_min,bin1_max)
                #fflush(stdout)

                bins0 = bin0_max - bin0_min + 1
                bins1 = bin1_max - bin1_min + 1

                A0 -= bin0_min
                A1 -= bin1_min
                B0 -= bin0_min
                B1 -= bin1_min
                C0 -= bin0_min
                C1 -= bin1_min
                D0 -= bin0_min
                D1 -= bin1_min

                areaPixel = area4(A0, A1, B0, B1, C0, C1, D0, D1)
                oneOverPixelArea = 1.0 / areaPixel

                #perimeter skipped - not inside for sure
                for i in range(1,bins0):
                    for j in range(1,bins1):
                        tmp_i  = point_and_line(A0,A1,B0,B1,i,j)
                        tmp_i += point_and_line(B0,B1,C0,C1,i,j)
                        tmp_i += point_and_line(C0,C1,D0,D1,i,j)
                        tmp_i += point_and_line(D0,D1,A0,A1,i,j)
                        is_inside[i,j] = (< int > fabs(tmp_i)) / < int > 4

                for i in range(bins0):
                    for j in range(bins1):
                        tmp_i  = is_inside[i,j]
                        tmp_i += is_inside[i,j+1]
                        tmp_i += is_inside[i+1,j]
                        tmp_i += is_inside[i+1,j+1]
                        if tmp_i is 4:
                            index = (i+bin0_min)*all_bins1 + j+bin1_min
                            if index > all_bins:    # for DB
                                printf("DB 3 index = %d > %d!! \n",index,all_bins)
                                fflush(stdout)
                            outCount[bin0_min+i,bin1_min+j] += oneOverPixelArea
                            outData[bin0_min+i,bin1_min+j]  += oneOverPixelArea * data
                        elif tmp_i is 1 or tmp_i is 2 or tmp_i is 3:
                            A.i = A0
                            A.j = A1
                            B.i = B0
                            B.j = B1
                            C.i = C0
                            C.j = C1
                            D.i = D0
                            D.j = D1

                            list1.data[0] = A
                            list1.data[1] = B
                            list1.data[2] = C
                            list1.data[3] = D
                            list1.size = 4
                            list2.size = 0

                            S = list1.data[list1.size-1] # last element
                            for tmp_i in range(list1.size):
                                E = list1.data[tmp_i]
                                if E.i > i:  # is_inside(E, clipEdge):   -- i is the x coord of current bin
                                    if S.i <= i:  # not is_inside(S, clipEdge):
                                        list2.data[list2.size] = ComputeIntersection0(S,E,i)
                                        list2.size += 1
                                    list2.data[list2.size] = E
                                    list2.size += 1
                                elif S.i > i:  # is_inside(S, clipEdge):
                                    list2.data[list2.size] = ComputeIntersection0(S,E,i)
                                    list2.size += 1
                                S = E;
                            #y=b+1
                            list1.size = 0
                            S = list2.data[list2.size-1]
                            for tmp_i in range(list2.size):
                                E = list2.data[tmp_i]
                                if E.j < j+1:  # is_inside(E, clipEdge):   -- j is the y coord of current bin
                                    if S.j >= j+1:  # not is_inside(S, clipEdge):
                                        list1.data[list1.size] = ComputeIntersection1(S,E,j+1)
                                        list1.size += 1
                                    list1.data[list1.size] = E
                                    list1.size += 1
                                elif S.j < j+1:  # is_inside(S, clipEdge):
                                    list1.data[list1.size] = ComputeIntersection1(S,E,j+1)
                                    list1.size += 1
                                S = E;
                            #x=a+1
                            list2.size = 0
                            S = list1.data[list1.size-1]
                            for tmp_i in range(list1.size):
                                E = list1.data[tmp_i]
                                if E.i < i+1:  # is_inside(E, clipEdge):
                                    if S.i >= i+1:  # not is_inside(S, clipEdge):
                                        list2.data[list2.size] = ComputeIntersection0(S,E,i+1)
                                        list2.size += 1
                                    list2.data[list2.size] = E
                                    list2.size += 1
                                elif S.i < i+1:  # is_inside(S, clipEdge):
                                    list2.data[list2.size] = ComputeIntersection0(S,E,i+1)
                                    list2.size += 1
                                S = E;
                            #y=b
                            list1.size = 0
                            S = list2.data[list2.size-1]
                            for tmp_i in range(list2.size):
                                E = list2.data[tmp_i]
                                if E.j > j:  # is_inside(E, clipEdge):
                                    if S.j <= j:  # not is_inside(S, clipEdge):
                                        list1.data[list1.size] = ComputeIntersection1(S,E,j)
                                        list1.size += 1
                                    list1.data[list1.size] = E
                                    list1.size += 1
                                elif S.j > j:  # is_inside(S, clipEdge):
                                    list1.data[list1.size] = ComputeIntersection1(S,E,j)
                                    list1.size += 1
                                S = E;

                            partialArea = area_n(list1)
                            index = (i+bin0_min)*all_bins1 + j+bin1_min
                            if index > all_bins:   # for DB
                                printf("DB 3.1 index = %d > %d!! \n",index,all_bins)
                                fflush(stdout)
                            partialArea = fabs(partialArea) * oneOverPixelArea
                            outCount[bin0_min+i,bin1_min+j] += partialArea
                            outData[bin0_min+i,bin1_min+j]  += partialArea * data
        for i in range(all_bins0):
            for j in range(all_bins1):
                if outCount[i,j] > epsilon:
                    outMerge[i,j] = outData[i,j]/outCount[i,j]
                else:
                    outMerge[i,j] = cdummy
                    
            
                    
    return outMerge, edges0, edges1, outData, outCount