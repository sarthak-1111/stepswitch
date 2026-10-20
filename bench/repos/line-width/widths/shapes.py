"""Full widths at half maximum (FWHM) of spectral line shapes."""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

from widths.constants import FWHM_PER_SIGMA, VOIGT_A, VOIGT_B


def gaussian_fwhm(sigma: ArrayLike) -> NDArray[np.float64]:
    """FWHM of a Gaussian with standard deviation `sigma`."""
    return FWHM_PER_SIGMA * np.asarray(sigma, dtype=float)


def lorentzian_fwhm(gamma: ArrayLike) -> NDArray[np.float64]:
    """FWHM of a Lorentzian whose half width at half maximum is `gamma`."""
    return np.asarray(gamma, dtype=float)


def voigt_fwhm(sigma: ArrayLike, gamma: ArrayLike) -> NDArray[np.float64]:
    """FWHM of a Voigt profile: a Gaussian of standard deviation `sigma` convolved with a
    Lorentzian of half width `gamma` (Olivero and Longbothum's approximation)."""
    f_g = gaussian_fwhm(sigma)
    f_l = lorentzian_fwhm(gamma)
    return VOIGT_A * f_l + np.sqrt(VOIGT_B * f_l**2 + f_g**2)
