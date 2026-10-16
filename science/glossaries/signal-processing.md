# Signal processing glossary

Injected into prompts when a task is classified as signal processing (SPEC §6.2). Function names
refer to numpy and scipy, the libraries most research code uses.

## Sampling

- **Sampling rate f_s**: samples per second; the sampling interval is 1/f_s.
- **Nyquist frequency**: f_s/2, the highest frequency a sampled signal represents without aliasing.
- **Aliasing**: content above Nyquist folding onto lower frequencies; prevent it with an anti-aliasing low-pass filter before sampling or decimating.
- **Sampling theorem**: a band-limited signal is fully determined by samples taken faster than twice its highest frequency.
- **Quantization**: rounding samples to discrete levels; an ideal N-bit converter gives a signal-to-quantization-noise ratio of about 6.02*N + 1.76 dB for a full-scale sine.
- **Decimation**: low-pass filtering followed by downsampling (scipy.signal.decimate).
- **Resampling**: changing the sampling rate, for example by polyphase filtering (scipy.signal.resample_poly).
- **Interpolation**: estimating values between samples (linear, cubic spline, sinc); it adds no new information.

## Fourier analysis

- **DFT**: X_k = sum over n of x_n*exp(-2*pi*i*k*n/N); the FFT computes it in O(N log N).
- **numpy.fft normalization**: by default the forward transform is unscaled and the inverse divides by N (norm="backward").
- **rfft**: transform of real input returning N//2 + 1 non-negative frequency bins; rfftfreq gives their frequencies.
- **fftfreq and fftshift**: bin frequencies in FFT order, and reordering so that zero frequency is centred.
- **Frequency resolution**: bin spacing f_s/N; set by the record length, not by zero padding.
- **Zero padding**: appending zeros to interpolate the spectrum more finely; it does not separate closer lines.
- **Parseval's theorem**: sum of |x_n|^2 equals (1/N) * sum of |X_k|^2 for the unnormalized DFT.
- **Spectral leakage**: energy spreading into neighbouring bins when a tone does not complete whole cycles in the record.
- **Window functions**: Hann, Hamming, Blackman, flat-top and Kaiser tapers that trade main-lobe width for side-lobe level.
- **Coherent gain and ENBW**: window corrections for amplitude (divide by the window's mean) and for noise power (equivalent noise bandwidth).
- **Gibbs phenomenon**: ringing of about 9% overshoot near discontinuities in truncated Fourier series.
- **Phase unwrapping**: removing 2*pi jumps from phase (numpy.unwrap).

## Spectra of signals

- **Power spectral density (PSD)**: power per unit frequency; units are signal^2/Hz.
- **Periodogram**: squared magnitude of a single (windowed) DFT, a noisy PSD estimate.
- **Welch's method**: averaging periodograms of overlapping windowed segments to lower the variance (scipy.signal.welch).
- **One-sided spectrum**: non-negative frequencies only; double every bin except DC and Nyquist to keep total power.
- **Spectrogram and STFT**: spectra of successive windows; longer windows improve frequency resolution and worsen time resolution.
- **Decibel**: 10*log10 of a power ratio, or 20*log10 of an amplitude ratio.
- **Signal-to-noise ratio (SNR)**: signal power over noise power, often in dB.

## Filters

- **Impulse response**: output for a unit impulse; it fully describes a linear time-invariant system.
- **Transfer function and frequency response**: H(z) of a filter, and its value on the unit circle (gain and phase versus frequency).
- **FIR filter**: finite impulse response; always stable, and exactly linear phase when the taps are symmetric.
- **IIR filter**: infinite impulse response (recursive); fewer coefficients for a sharp response, but phase is non-linear.
- **Butterworth, Chebyshev, Bessel, elliptic**: IIR families: maximally flat, rippled with a steeper edge, near-linear phase, steepest edge.
- **Cutoff normalization**: scipy.signal designs take cutoffs as a fraction of Nyquist unless fs= is passed.
- **Second-order sections (SOS)**: cascaded biquads (output="sos") that keep high-order IIR filters numerically stable.
- **Zero-phase filtering**: filtfilt or sosfiltfilt run the filter forward and backward: no phase shift, squared magnitude response.
- **Group delay**: the delay each frequency component experiences, -d(phase)/d(omega).
- **Moving average**: boxcar FIR smoother; poor frequency selectivity.
- **Median filter**: non-linear filter that removes spikes while keeping edges (scipy.signal.medfilt).
- **Savitzky-Golay filter**: local polynomial fit for smoothing and derivatives (scipy.signal.savgol_filter).
- **Edge effects**: filters and convolutions are unreliable near the ends; choose padding (reflect, constant, edge) on purpose.
- **Detrending**: removing a mean or linear trend before spectral analysis (scipy.signal.detrend).

## Convolution and correlation

- **Convolution theorem**: convolution in time is multiplication in frequency.
- **Linear versus circular convolution**: FFT products give circular convolution; pad both signals to N + M - 1 for linear convolution.
- **Cross-correlation**: similarity versus lag; its peak estimates a time delay (scipy.signal.correlate with method="fft").
- **Autocorrelation**: correlation of a signal with itself; its Fourier transform is the PSD (Wiener-Khinchin theorem).
- **Matched filter**: correlating with a known template to detect it in noise with maximal SNR.
- **Deconvolution**: undoing a known blur; ill-posed with noise, so it needs regularization (Wiener filtering).

## Time-frequency and envelopes

- **Analytic signal**: x + i*H(x) from the Hilbert transform; its magnitude is the envelope (scipy.signal.hilbert).
- **Instantaneous frequency**: derivative of the unwrapped phase of the analytic signal, divided by 2*pi.
- **Wavelet transform**: decomposition into scaled, shifted wavelets; thresholding the coefficients denoises signals.
- **Lock-in detection**: demodulating with a reference at a known frequency, then low-pass filtering, to measure a weak signal.
- **Kalman filter**: recursive estimator of a linear system's state from noisy measurements.

## Peaks

- **Peak detection**: local maxima filtered by height, prominence, width and distance (scipy.signal.find_peaks).
- **Prominence**: height of a peak above its higher base; each base is the lowest point between the peak and higher signal (or the record end) on that side.
- **Sub-sample peak position**: refined by the vertex of a parabola through three samples, a Gaussian (log-parabola) fit, or a centroid.
- **Common bugs**: forgetting fs in filter design, ignoring filter start-up transients, using filtfilt on records shorter than its padding, and comparing amplitudes from different windows without gain correction.
