import math

import numpy as np
from widths.shapes import gaussian_fwhm, lorentzian_fwhm, voigt_fwhm


def test_gaussian_fwhm() -> None:
    assert np.isclose(gaussian_fwhm(1.0), 2.0 * math.sqrt(2.0 * math.log(2.0)))
    assert np.allclose(gaussian_fwhm([0.0, 2.0]), [0.0, 4.0 * math.sqrt(2.0 * math.log(2.0))])


def test_lorentzian_fwhm_is_twice_the_half_width() -> None:
    assert np.allclose(lorentzian_fwhm([0.5, 2.0]), [1.0, 4.0])


def test_voigt_reduces_to_its_parts() -> None:
    assert np.isclose(voigt_fwhm(1.0, 0.0), gaussian_fwhm(1.0))
    # The approximation is within 0.02% of the exact width.
    assert np.isclose(voigt_fwhm(0.0, 1.0), 2.0, rtol=2e-4)


def test_voigt_is_wider_than_either_part() -> None:
    sigma, gamma = np.array([1.0, 3.0]), np.array([2.0, 0.5])
    width = voigt_fwhm(sigma, gamma)
    assert np.all(width > gaussian_fwhm(sigma)) and np.all(width > lorentzian_fwhm(gamma))
    assert np.all(width < gaussian_fwhm(sigma) + lorentzian_fwhm(gamma))
