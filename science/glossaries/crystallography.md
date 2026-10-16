# Crystallography glossary

Injected into prompts when a task is classified as crystallography (SPEC §6.2). Angles are in
degrees unless stated; trigonometric functions in code usually expect radians.

## Lattices and symmetry

- **Unit cell**: the smallest repeating parallelepiped of a crystal, described by its lattice parameters.
- **Lattice parameters**: edge lengths a, b, c and interaxial angles alpha, beta, gamma of the unit cell.
- **Crystal system**: one of seven classes (triclinic, monoclinic, orthorhombic, tetragonal, trigonal, hexagonal, cubic) set by the cell's symmetry constraints.
- **Bravais lattice**: one of the 14 three-dimensional lattice types (crystal system plus centring P, I, F, A/B/C or R).
- **Point group**: one of the 32 crystallographic groups of symmetry operations that leave a point fixed.
- **Space group**: one of the 230 groups of a crystal's full symmetry (point operations plus translations), numbered 1-230.
- **Hermann-Mauguin symbol**: standard notation for point and space groups, for example Fm-3m or P2_1/c.
- **Asymmetric unit**: the smallest part of the cell from which the space-group operations generate the whole cell.
- **Fractional coordinates**: atom positions as fractions of the lattice vectors; atoms inside the cell lie in [0, 1).
- **Occupancy**: the fraction of a crystallographic site filled by a given atom type.
- **Wyckoff position**: a set of symmetry-equivalent sites with a given site symmetry and multiplicity.

## Planes and reciprocal space

- **Miller indices (hkl)**: integer triple labelling a family of lattice planes (reciprocal intercepts, cleared of fractions).
- **d-spacing**: distance between adjacent (hkl) planes; for a cubic cell d = a / sqrt(h^2 + k^2 + l^2).
- **Reciprocal lattice**: lattice of vectors G_hkl with |G_hkl| = 1/d (crystallographic convention) or 2*pi/d (physics convention).
- **Scattering vector Q**: |Q| = 4*pi*sin(theta)/lambda = 2*pi/d; keep track of the 2*pi convention when converting.
- **Bragg's law**: n*lambda = 2*d*sin(theta), where theta is half the scattering angle 2-theta.
- **2-theta**: the angle between incident and diffracted beams; peak positions in 2-theta only make sense with the wavelength.
- **Laue condition**: diffraction occurs when the scattering vector equals a reciprocal-lattice vector (equivalent to Bragg's law).
- **Ewald sphere**: sphere of radius 1/lambda in reciprocal space; reflections occur where it meets reciprocal-lattice points.

## Intensities

- **Structure factor F_hkl**: sum over atoms of f_j * exp(2*pi*i*(h*x_j + k*y_j + l*z_j)) with displacement factors; intensity is proportional to |F_hkl|^2.
- **Atomic form factor f**: X-ray scattering amplitude of an atom, decreasing with sin(theta)/lambda; neutrons use a constant scattering length b instead.
- **Systematic absences**: reflections with zero intensity caused by centring, screw axes or glide planes (FCC allows only h, k, l all even or all odd; BCC allows only h + k + l even).
- **Multiplicity**: number of symmetry-equivalent reflections that overlap in one powder peak.
- **Lorentz-polarization factor**: angle-dependent intensity correction for the diffraction geometry and X-ray polarization.
- **Atomic displacement parameter**: B (or U = B / (8*pi^2)) describing thermal motion; it scales intensities by exp(-2*B*sin^2(theta)/lambda^2).
- **Absorption correction**: correction for intensity lost in the sample; depends on geometry, thickness and the linear absorption coefficient mu.

## Powder diffraction

- **Powder diffraction (XRD)**: diffraction from many randomly oriented crystallites, measured as intensity versus 2-theta, d or Q.
- **Cu K-alpha**: common laboratory X-ray line; lambda(K-alpha1) = 1.5406 A and lambda(K-alpha2) = 1.5444 A, so K-alpha2 adds a satellite at slightly higher 2-theta.
- **Mo K-alpha1**: shorter-wavelength laboratory line, lambda = 0.7093 A.
- **Bragg-Brentano geometry**: reflection geometry with equal incidence and exit angles (theta-2theta or theta-theta scans).
- **Debye-Scherrer rings**: powder diffraction cones on an area detector; azimuthal integration turns them into a 1-D pattern.
- **Peak profile**: shape of a reflection; Gaussian, Lorentzian, Voigt (their convolution), pseudo-Voigt (eta*L + (1 - eta)*G) or Pearson VII.
- **FWHM**: full width at half maximum; for a Gaussian, FWHM = 2*sqrt(2*ln 2)*sigma, about 2.3548*sigma.
- **Caglioti function**: FWHM^2 = U*tan^2(theta) + V*tan(theta) + W, the usual model of instrumental broadening versus angle.
- **Scherrer equation**: crystallite size tau = K*lambda / (beta*cos(theta)), with beta the sample broadening in radians and K about 0.9.
- **Williamson-Hall analysis**: separates size and strain broadening using beta*cos(theta) = K*lambda/tau + 4*epsilon*sin(theta).
- **Zero shift**: constant offset of the 2-theta axis.
- **Specimen displacement**: sample height error in Bragg-Brentano geometry; shifts peaks by about -2*s*cos(theta)/R radians in 2-theta.
- **Preferred orientation (texture)**: non-random crystallite orientation that distorts relative intensities (often modelled with March-Dollase).
- **Background**: smooth non-Bragg intensity (amorphous scattering, air scatter, fluorescence), often fitted with Chebyshev polynomials.
- **Indexing**: assigning hkl to observed peaks and finding the unit cell.
- **Phase identification**: matching observed peak positions and intensities against reference patterns.

## Refinement

- **Rietveld refinement**: least-squares fit of a full calculated powder pattern (structure, profile, background, instrument) to the measured one.
- **Le Bail and Pawley fits**: whole-pattern fits with free peak intensities and no structural model, used to refine the cell and profile.
- **R_wp**: weighted-profile agreement factor of a whole-pattern fit; R_exp is its statistically expected value.
- **Goodness of fit**: chi^2 = (R_wp / R_exp)^2; values near 1 mean the fit is as good as the counting statistics allow.
- **R_Bragg**: agreement factor computed on integrated reflection intensities.
- **Constraints and restraints**: exact relations between parameters versus penalty terms that pull parameters toward target values.
- **Esd (standard uncertainty)**: estimated standard deviation of a refined parameter, written as 3.1416(2).

## Total scattering

- **Total scattering**: Bragg and diffuse scattering measured together, used for local structure.
- **S(Q)**: total scattering structure function, normalized so that S(Q) tends to 1 at high Q.
- **Pair distribution function G(r)**: G(r) = (2/pi) * integral of Q*(S(Q) - 1)*sin(Q*r) dQ, the distribution of interatomic distances.
- **Q_max**: upper limit of the PDF transform; truncation causes termination ripples with period about 2*pi/Q_max.
- **Q_damp, Q_broad**: instrument parameters that damp and broaden PDF peaks with r.

## Files and units

- **CIF**: Crystallographic Information File, the standard text format for crystal structures.
- **Angstrom (A)**: 1 A = 0.1 nm = 1e-10 m; Q in A^-1 is ten times smaller than in nm^-1.
- **Common bugs**: degrees versus radians, theta versus 2-theta, the 2*pi convention in Q, and A versus nm.
