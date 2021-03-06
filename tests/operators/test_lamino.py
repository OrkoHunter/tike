#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest

import numpy as np
from tike.operators import Lamino

from .util import random_complex, OperatorTests

__author__ = "Daniel Ching, Viktor Nikitin"
__copyright__ = "Copyright (c) 2020, UChicago Argonne, LLC."
__docformat__ = 'restructuredtext en'


class TestLamino(unittest.TestCase, OperatorTests):
    """Test the Laminography operator."""

    def setUp(self, n=16, ntheta=8, tilt=np.pi / 3, eps=1e-6):
        self.operator = Lamino(
            n=n,
            theta=np.linspace(0, 2 * np.pi, ntheta),
            tilt=tilt,
            eps=eps,
        )
        self.operator.__enter__()
        self.xp = self.operator.xp
        np.random.seed(0)
        self.m = self.xp.asarray(random_complex(n, n, n), dtype='complex64')
        self.m_name = 'u'
        self.d = self.xp.asarray(random_complex(ntheta, n, n),
                                 dtype='complex64')
        self.d_name = 'data'
        self.kwargs = {}
        print(self.operator)

    @unittest.skip('FIXME: This operator is not scaled.')
    def test_scaled(self):
        pass


if __name__ == '__main__':
    unittest.main()
