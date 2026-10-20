"""Constants of line-shape widths: exact values or published coefficients."""

import math

# FWHM of a Gaussian in units of its standard deviation: 2 sqrt(2 ln 2).
FWHM_PER_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))

# Olivero and Longbothum (1977): Voigt FWHM ~ VOIGT_A fL + sqrt(VOIGT_B fL^2 + fG^2), where fL
# and fG are the full widths of the Lorentzian and Gaussian parts.
VOIGT_A = 0.5346
VOIGT_B = 0.2166
