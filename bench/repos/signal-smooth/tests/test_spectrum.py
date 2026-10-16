import numpy as np
from smoothing.spectrum import smooth, window_samples

X = np.arange(100.0, 1105.0, 5.0)


def test_window_samples_is_an_odd_number_of_grid_steps():
    assert window_samples(X, 30.0) == 7
    assert window_samples(X, 25.0) == 5
    assert window_samples(X, 1.0) == 1


def test_smoothing_keeps_a_symmetric_peak_in_place():
    y = np.exp(-0.5 * ((X - 600.0) / 20.0) ** 2)
    assert X[np.argmax(smooth(X, y, 30.0))] == 600.0


def test_two_peaks_stay_where_they_are():
    y = np.exp(-0.5 * ((X - 400.0) / 15.0) ** 2) + 0.5 * np.exp(-0.5 * ((X - 800.0) / 15.0) ** 2)
    smoothed = smooth(X, y, 40.0)
    assert X[np.argmax(smoothed[X < 600.0])] == 400.0
    assert X[X >= 600.0][np.argmax(smoothed[X >= 600.0])] == 800.0
