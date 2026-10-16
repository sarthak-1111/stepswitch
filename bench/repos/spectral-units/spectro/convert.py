"""Conversions between wavenumber (cm^-1), wavelength (nm) and photon energy (eV)."""

import numpy as np
from numpy.typing import ArrayLike, NDArray

from spectro.constants import ELEMENTARY_CHARGE, NM_PER_CM, NM_PER_M, PLANCK, SPEED_OF_LIGHT


def _positive(values: ArrayLike, what: str) -> NDArray[np.float64]:
    array = np.asarray(values, dtype=float)
    if np.any(array <= 0):
        raise ValueError(f"{what} must be positive")
    return array


def wavenumber_to_wavelength(wavenumber: ArrayLike) -> NDArray[np.float64]:
    """The wavelength in nm of light with the given wavenumber in cm^-1."""
    return NM_PER_M / _positive(wavenumber, "wavenumbers")


def wavelength_to_wavenumber(wavelength: ArrayLike) -> NDArray[np.float64]:
    """The wavenumber in cm^-1 of light with the given wavelength in nm."""
    return NM_PER_CM / _positive(wavelength, "wavelengths")


def wavelength_to_energy(wavelength: ArrayLike) -> NDArray[np.float64]:
    """The energy in eV of a photon with the given wavelength in nm."""
    metres = _positive(wavelength, "wavelengths") / NM_PER_M
    return PLANCK * SPEED_OF_LIGHT / (metres * ELEMENTARY_CHARGE)


def energy_to_wavelength(energy: ArrayLike) -> NDArray[np.float64]:
    """The wavelength in nm of a photon with the given energy in eV."""
    joules = _positive(energy, "energies") * ELEMENTARY_CHARGE
    return PLANCK * SPEED_OF_LIGHT / joules * NM_PER_M


def raman_wavelength(shift: ArrayLike, laser: float) -> NDArray[np.float64]:
    """The wavelength in nm of the Stokes Raman light `shift` cm^-1 below a laser line.

    `laser` is the wavelength of the laser in nm.
    """
    return wavenumber_to_wavelength(wavelength_to_wavenumber(laser) - np.asarray(shift))
