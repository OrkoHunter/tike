#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest

import numpy as np

from .util import random_complex, inner_complex
from tike.operators import Propagation

__author__ = "Daniel Ching"
__copyright__ = "Copyright (c) 2020, UChicago Argonne, LLC."
__docformat__ = 'restructuredtext en'


class TestPropagation(unittest.TestCase):
    """Test the Propagation operator."""

    def setUp(self):
        """Load a dataset for reconstruction."""
        self.nwaves = 13
        self.probe_shape = 127
        self.detector_shape = self.probe_shape * 3

    def test_adjoint(self):
        """Check that the adjoint operator is correct."""
        np.random.seed(0)
        nearplane = random_complex(self.nwaves, self.probe_shape,
                                   self.probe_shape)
        farplane = random_complex(self.nwaves, self.detector_shape,
                                  self.detector_shape)

        nearplane = nearplane.astype('complex64')
        farplane = farplane.astype('complex64')

        with Propagation(
                nwaves=self.nwaves,
                detector_shape=self.detector_shape,
                probe_shape=self.probe_shape,
        ) as op:
            f = op.fwd(
                nearplane=nearplane,
                farplane=farplane,
            )
            n = op.adj(
                nearplane=nearplane,
                farplane=farplane,
            )
            a = inner_complex(nearplane, n)
            b = inner_complex(f, farplane)
            print()
            print('<ψ , F*Ψ> = {:.6f}{:+.6f}j'.format(a.real.item(),
                                                      a.imag.item()))
            print('<Fψ,   Ψ> = {:.6f}{:+.6f}j'.format(b.real.item(),
                                                      b.imag.item()))
            # Test whether Adjoint fixed probe operator is correct
            np.testing.assert_allclose(a.real, b.real, rtol=1e-5)
            np.testing.assert_allclose(a.imag, b.imag, rtol=1e-5)


if __name__ == '__main__':
    unittest.main()