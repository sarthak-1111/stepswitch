# Scattering glossary

Injected into prompts when a task is classified as scattering (SPEC §6.2): small-angle and wide-
angle X-ray and neutron scattering, light scattering and inelastic scattering.

## Basics

- **Elastic scattering**: no energy exchange between probe and sample; inelastic scattering exchanges energy.
- **Scattering angle 2-theta**: angle between incident and scattered beams.
- **Scattering vector Q**: momentum transfer, |Q| = 4*pi*sin(theta)/lambda; real-space length scale d is about 2*pi/Q.
- **Units of Q**: A^-1 or nm^-1 (1 A^-1 = 10 nm^-1); mixing them is a common bug.
- **Differential cross section dSigma/dOmega**: scattered intensity per unit solid angle and sample volume, in cm^-1 on an absolute scale.
- **Scattering length b**: neutron scattering amplitude of a nucleus; it varies irregularly between isotopes.
- **Scattering length density (SLD)**: sum of scattering lengths per unit volume; contrast is the SLD difference between phases.
- **Contrast variation**: changing the solvent SLD (for example H2O/D2O mixtures) to highlight or match out components.
- **Coherent and incoherent scattering**: coherent scattering carries structural interference; incoherent scattering adds a flat background (large for hydrogen).
- **Thomson scattering**: elastic X-ray scattering by electrons.
- **Compton scattering**: inelastic X-ray scattering that adds a rising background at high Q.

## Small-angle scattering (SAXS, SANS)

- **SAXS and SANS**: small-angle X-ray and neutron scattering, probing structures from about 1 to 100 nm.
- **WAXS**: wide-angle scattering, overlapping with diffraction from crystalline order.
- **Form factor P(Q)**: scattering from a single particle shape; a sphere of radius R has P(Q) = [3*(sin(QR) - QR*cos(QR)) / (QR)^3]^2.
- **Structure factor S(Q)**: interference between particles (concentration effects); not the crystallographic F_hkl.
- **Guinier approximation**: I(Q) = I(0)*exp(-Q^2*Rg^2/3), valid for Q*Rg below about 1.3.
- **Radius of gyration Rg**: root-mean-square distance of scattering density from its centre.
- **Porod law**: I(Q) proportional to Q^-4 at high Q for sharp interfaces; the prefactor gives the specific surface.
- **Porod invariant**: Q* = integral of Q^2*I(Q) dQ, equal to 2*pi^2*phi*(1 - phi)*(contrast)^2 for two phases.
- **Kratky plot**: Q^2*I(Q) versus Q; a peak suggests compact particles, a plateau suggests flexible chains.
- **Pair distance distribution p(r)**: real-space distance distribution obtained by indirect Fourier transform of I(Q).
- **Polydispersity**: distribution of particle sizes; smears form-factor minima.
- **Fractal dimension**: power-law exponent of I(Q) for mass or surface fractals.
- **Debye equation**: I(Q) = sum over atom pairs of f_i*f_j*sin(Q*r_ij)/(Q*r_ij), for isotropic samples.

## Data reduction

- **Azimuthal integration**: reduces a 2-D detector image to 1-D I(Q) using beam centre, sample-detector distance, wavelength and a mask.
- **Mask**: excluded detector pixels (beamstop, gaps, hot pixels).
- **Transmission correction**: divide by the measured sample transmission before subtraction.
- **Background subtraction**: subtract empty cell and solvent scattering, each scaled by its own transmission and thickness.
- **Solid-angle and polarization corrections**: geometric and X-ray polarization corrections per pixel.
- **Detector efficiency (flat field)**: per-pixel sensitivity correction.
- **Absolute calibration**: scaling to cm^-1 with a standard of known cross section (for example water or glassy carbon).
- **Resolution function**: instrumental smearing in Q from wavelength spread, divergence and pixel size.
- **Slit smearing**: smearing from line-collimated cameras; requires desmearing or a smeared model.
- **Multiple scattering**: probe scattered more than once in thick or strongly scattering samples; distorts shapes.
- **Error propagation**: Poisson counting errors must be propagated through every reduction step.

## Light scattering

- **Static light scattering**: time-averaged intensity versus angle; Zimm analysis gives molar mass, Rg and the second virial coefficient.
- **Dynamic light scattering (DLS)**: intensity autocorrelation g2(tau) of fluctuating scattered light.
- **Siegert relation**: g2(tau) = 1 + beta*|g1(tau)|^2, linking intensity and field autocorrelations.
- **Stokes-Einstein relation**: hydrodynamic radius R_h = k_B*T / (6*pi*eta*D).
- **Cumulant analysis**: fit of ln g1 giving the mean decay rate and a polydispersity index.
- **Rayleigh scattering**: scattering by particles much smaller than the wavelength, proportional to lambda^-4.
- **Mie scattering**: exact solution for spheres comparable to the wavelength.

## Inelastic scattering

- **Energy transfer**: hbar*omega = E_i - E_f.
- **Dynamic structure factor S(Q, omega)**: scattering as a function of momentum and energy transfer.
- **Detailed balance**: S(-Q, -omega) = exp(-hbar*omega / (k_B*T)) * S(Q, omega).
- **Intermediate scattering function I(Q, t)**: time Fourier transform of S(Q, omega), measured directly by spin echo.
- **Quasi-elastic scattering**: broadening around zero energy transfer from diffusive motion.
- **Phonon dispersion**: energy of lattice vibrations versus wavevector, measured by inelastic scattering.
- **Common bugs**: theta versus 2-theta in Q, A^-1 versus nm^-1, subtracting a background without correcting each measurement for its own transmission, and fitting smeared data with an unsmeared model.
