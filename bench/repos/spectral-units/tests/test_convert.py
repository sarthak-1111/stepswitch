import numpy as np
import pytest
from numpy.testing import assert_allclose
from spectro.convert import (
    energy_to_wavelength,
    raman_wavelength,
    wavelength_to_energy,
    wavelength_to_wavenumber,
    wavenumber_to_wavelength,
)

PAIRS = [(1000.0, 10000.0), (12500.0, 800.0), (20000.0, 500.0)]  # (cm^-1, nm)


@pytest.mark.parametrize(("wavenumber", "wavelength"), PAIRS)
def test_wavenumber_to_wavelength(wavenumber, wavelength):
    assert wavenumber_to_wavelength(wavenumber) == pytest.approx(wavelength, rel=1e-12)


@pytest.mark.parametrize(("wavenumber", "wavelength"), PAIRS)
def test_wavelength_to_wavenumber(wavenumber, wavelength):
    assert wavelength_to_wavenumber(wavelength) == pytest.approx(wavenumber, rel=1e-12)


def test_wavenumbers_survive_a_round_trip():
    wavenumbers = np.linspace(400.0, 4000.0, 37)
    back = wavelength_to_wavenumber(wavenumber_to_wavelength(wavenumbers))
    assert_allclose(back, wavenumbers, rtol=1e-12)


def test_arrays_keep_their_shape():
    wavelengths = wavenumber_to_wavelength(np.full((2, 3), 2000.0))
    assert_allclose(wavelengths, np.full((2, 3), 5000.0), rtol=1e-12)


def test_photon_energy_and_wavelength():
    # A photon of 1 eV has a wavelength of h c / (1 eV) = 1239.84198... nm.
    assert energy_to_wavelength(1.0) == pytest.approx(1239.841984, rel=1e-9)
    assert wavelength_to_energy(1239.841984) == pytest.approx(1.0, rel=1e-9)


def test_raman_wavelength():
    # A 532 nm laser line lies at 1e7 / 532 cm^-1; the Raman line sits 1000 cm^-1 below it.
    assert raman_wavelength(1000.0, 532.0) == pytest.approx(561.892691, rel=1e-9)
    assert_allclose(raman_wavelength([0.0, 1000.0], 532.0), [532.0, 561.892691], rtol=1e-9)


@pytest.mark.parametrize("values", [0.0, -5.0, [1000.0, 0.0]])
def test_values_must_be_positive(values):
    with pytest.raises(ValueError):
        wavenumber_to_wavelength(values)
