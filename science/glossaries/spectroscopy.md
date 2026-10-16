# Spectroscopy glossary

Injected into prompts when a task is classified as spectroscopy (SPEC §6.2). Covers optical,
vibrational, X-ray and resonance techniques at the level needed to read and change analysis code.

## Axes and units

- **Spectrum**: intensity as a function of energy, frequency, wavelength or wavenumber.
- **Wavenumber**: 1/lambda, usually in cm^-1; wavenumber[cm^-1] = 1e7 / lambda[nm].
- **Photon energy**: E = h*nu = h*c/lambda; lambda[nm] is about 1239.84 / E[eV] and 1 eV is about 8065.54 cm^-1.
- **Raman shift**: laser wavenumber minus scattered wavenumber, in cm^-1; Stokes lines have positive shift, anti-Stokes negative.
- **Spectral axis direction**: some instruments store wavenumber axes in decreasing order; sort or keep x with y.
- **Non-uniform axes**: wavelength-uniform data is not uniform in wavenumber or energy; integrate over the real axis.
- **Jacobian conversion**: converting a spectral density between wavelength and energy axes multiplies by |dlambda/dE| (proportional to 1/E^2).

## Intensity quantities

- **Transmittance**: T = I / I0.
- **Absorbance**: A = -log10(T) = log10(I0 / I).
- **Beer-Lambert law**: A = epsilon*l*c (molar absorptivity, path length, concentration); linear only at moderate absorbance.
- **Kubelka-Munk function**: F(R) = (1 - R)^2 / (2R), used for diffuse reflectance.
- **Counts and count rate**: raw detector counts versus counts per second; normalize by integration time before comparing.
- **Dark spectrum**: signal recorded with no light, subtracted to remove detector offset and dark current.
- **Saturation**: detector counts at the maximum value; saturated peaks have flattened tops and wrong areas.
- **Quantum yield**: photons emitted per photon absorbed (fluorescence).

## Peaks

- **Peak position**: centre of a band; refined by fitting or by interpolating around the maximum.
- **Peak height, area**: maximum above baseline, and integrated intensity (the area is more robust to broadening).
- **FWHM**: full width at half maximum.
- **Gaussian line shape**: from inhomogeneous or instrumental broadening; FWHM = 2.3548*sigma.
- **Lorentzian line shape**: from lifetime (homogeneous) broadening; FWHM in cm^-1 equals 1/(2*pi*c*tau).
- **Voigt profile**: convolution of Gaussian and Lorentzian; pseudo-Voigt approximates it as eta*L + (1 - eta)*G.
- **Peak fitting (deconvolution)**: modelling overlapping bands as a sum of line shapes plus baseline.
- **Overtone and combination bands**: weaker bands near multiples or sums of fundamental frequencies (IR, NIR).
- **Fermi resonance**: mixing of a fundamental with an overtone of similar energy, shifting both bands.
- **Depolarization ratio**: ratio of perpendicular to parallel polarized Raman intensity; tells symmetric from non-symmetric modes.

## Preprocessing

- **Baseline (background)**: slowly varying signal under the peaks (fluorescence in Raman, scattering, offsets).
- **Polynomial baseline**: low-order polynomial fitted to peak-free regions, often iteratively.
- **Asymmetric least squares (AsLS)**: penalized smoother with smoothness lambda and asymmetry p that keeps the baseline below the peaks.
- **SNIP and rolling-ball baselines**: clipping-based baseline estimates driven by a window width.
- **Cosmic-ray spikes**: narrow one- or two-pixel spikes in CCD spectra; remove by median filtering or by comparing repeated frames.
- **Savitzky-Golay filter**: local polynomial least-squares smoothing or differentiation that preserves peak shape better than a moving average.
- **Derivative spectra**: first or second derivatives that sharpen overlapping bands and remove constant or linear baselines.
- **Normalization**: scaling to unit area, unit maximum, unit L2 norm (vector normalization) or a reference band.
- **SNV (standard normal variate)**: subtract each spectrum's mean and divide by its standard deviation.
- **MSC (multiplicative scatter correction)**: fit each spectrum as offset + scale * reference (often the mean spectrum), then subtract the offset and divide by the scale.
- **Interpolation onto a common axis**: required before subtracting or averaging spectra from different instruments.
- **Instrument response correction**: dividing by the measured system response so relative intensities are comparable.

## Calibration

- **Wavelength calibration**: mapping detector pixels to wavelength with reference lines from a lamp of known emission.
- **Raman shift calibration**: checked with standards such as the crystalline silicon band near 520.7 cm^-1.
- **IR calibration standard**: the polystyrene band near 1601 cm^-1 checks the wavenumber axis.
- **Laser wavelength drift**: moves every scattered band by the same absolute wavenumber, so shifts computed from the nominal laser line are offset.
- **Spectral resolution**: the smallest separable line spacing, set by slit width and grating (dispersive) or maximum path difference (FTIR).

## Techniques

- **FTIR**: infrared spectra from the Fourier transform of an interferogram; uses apodization and zero filling.
- **ATR**: attenuated total reflectance; penetration depth grows with wavelength, so band ratios differ from transmission spectra.
- **UV-Vis**: electronic absorption in the ultraviolet and visible range.
- **Fluorescence**: emission after absorption; the Stokes shift is the gap between absorption and emission maxima.
- **NIR**: near-infrared overtone and combination bands, usually analysed with chemometrics.
- **XPS**: photoelectron spectroscopy; binding energy E_B = h*nu - E_kin - phi (work function), often charge-referenced to C 1s.
- **XAS**: X-ray absorption; XANES near the edge and EXAFS oscillations above it, with k = sqrt(2*m_e*(E - E0))/hbar.
- **NMR chemical shift**: delta = (nu - nu_ref) / nu_ref * 1e6 ppm; J-coupling splits lines into multiplets.
- **Mass spectrum**: intensity versus mass-to-charge ratio m/z (not optical, but analysed with the same peak tools).

## Chemometrics

- **PCA**: principal component analysis; finds orthogonal directions of maximal variance for exploration.
- **PLS regression**: partial least squares, predicting concentrations or properties from spectra.
- **Spectral matching**: comparing a spectrum with library spectra, for example by cosine similarity (spectral contrast angle).
- **Cross-validation**: estimating prediction error on data not used for fitting; leakage between folds inflates scores.
- **Common bugs**: decreasing axes, unit mix-ups (nm versus cm^-1 versus eV), integrating over sample index instead of the axis, and normalizing before baseline removal.
