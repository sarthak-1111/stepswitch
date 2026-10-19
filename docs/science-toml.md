# science.toml reference

`science.toml` sits at the repository root and configures the science-track gates (SPEC §6.2):
golden fixtures with per-quantity tolerances, protected zones, and what goes into the
reproducibility record. The science track runs its gates in this order and stops at the first
failure: `format`, `lint`, `protected`, `impacted_tests`, `golden`, `full_tests` (override with
`[tracks.science].gates` in `stepswitch.toml`).

The file is TOML (read as UTF-8; a byte-order mark is tolerated). Every table is strict.
Unknown keys, wrong types and out-of-range values stop the golden gate with an error that names
the key, for example:

    science.toml: golden[0].quantities[0].rtoll: Extra inputs are not permitted

Paths in the file are relative to the repository, written with `/`. Absolute paths and `..` are
rejected.

## Example

```toml
[science]
python = ".venv-analysis/bin/python"   # optional: the interpreter that runs the cases
min_peak_height = 0.05                 # optional: default for peaks mode

[[golden]]
case = "peaks"                         # tests/golden/peaks/
command = ["{python}", "-m", "toy.spectra", "{case}/input.json", "{output}/spectrum.json"]
timeout = 120                          # optional, seconds

[[golden.quantities]]
name = "spectrum"
file = "spectrum.json"                 # written by the command into {output}
key = "y"
mode = "elementwise"
rtol = 1e-6

[[golden.quantities]]
name = "peak-positions"
file = "spectrum.json"
key = "y"
x_key = "x"
mode = "peaks"
atol = 0.01                            # in units of x

[[protected]]
file = "toy/constants.py"
names = ["SPEED_OF_LIGHT", "PLANCK"]

[[protected]]
file = "data/calibration.csv"          # no names: the whole file is protected

[reproducibility]
seeds = [0]
```

## `[science]`

| key | type | default | meaning |
|---|---|---|---|
| `python` | string | none | Interpreter for the science track's gates and golden cases. A path relative to the repository, an absolute path, or a program name on `PATH`. |
| `min_peak_height` | float in [0, 1] | `0.05` | Default peak threshold for `peaks` mode, as a fraction of the maximum. |

The project interpreter is chosen in this order. The first setting found wins:

1. `[tracks.science].python` in `stepswitch.toml` or `.stepswitch/local.toml`.
2. `[science].python` in `stepswitch.toml` or `.stepswitch/local.toml`.
3. `[science].python` here.
4. The repository's `.venv` (`Scripts/python.exe` on Windows, `bin/python` elsewhere).
5. The interpreter running Stepswitch.

Tools such as ruff, mypy, pytest and jupytext are looked up in that interpreter's environment.

## `[[golden]]`: golden cases

Each case runs its command once, then compares every declared quantity of the output with an
expected copy kept in the repository.

| key | type | default | meaning |
|---|---|---|---|
| `case` | string | required | Name of the case directory `tests/golden/<case>/`, which must exist. |
| `command` | list of strings | required | The argv of the reference run (no shell). |
| `timeout` | float > 0 | the gate timeout (900 s unless the caller sets one) | Seconds before the command and everything it started are killed. |
| `quantities` | array of tables | empty | The compared outputs (below). A case without quantities only checks the exit code. |

### Command placeholders

Placeholders are replaced inside each argument:

| placeholder | value |
|---|---|
| `{python}` | the project interpreter (see `[science]`) |
| `{case}` | absolute path of `tests/golden/<case>/` |
| `{output}` | a fresh, empty temporary directory for this run, deleted afterwards |
| `{repo}` | absolute path of the repository |

The command runs with the repository as its working directory, no standard input, and the
repository root first on `PYTHONPATH`. Before the cases run, cached bytecode older than its
source file's last-modified second is deleted. Otherwise a same-size edit made within the same
second could run stale code.

A case fails when its command exits with a nonzero status or times out. It is an error when the command
cannot start. Either way the output tail goes into the report.

### `[[golden.quantities]]`

| key | type | default | meaning |
|---|---|---|---|
| `name` | string | required | Label in reports and plot file names. |
| `file` | string | required | Output file, relative to `{output}`. It may also start with `{output}/`, `{case}/` or `{repo}/`. |
| `key` | string | `name` | Entry inside the file (see Data files). |
| `x_key` | string | none | Entry holding the x axis, read from the same file on each side. Used by `integral` and `peaks`; without it the sample index is the axis. |
| `mode` | string | `"elementwise"` | `elementwise`, `integral`, `peaks` or `overlap` (below). |
| `rtol` | float >= 0 | `1e-7` | Relative tolerance (`elementwise`, `integral`). |
| `atol` | float >= 0 | `0.0` | Absolute tolerance (`elementwise`, `integral`), or the largest peak shift in x units (`peaks`). |
| `min_overlap` | float in [-1, 1] | `0.999` | Smallest accepted cosine similarity (`overlap`). |
| `min_peak_height` | float in [0, 1] | `[science].min_peak_height` | Peak threshold for this quantity (`peaks`). |
| `expected` | string | `expected/<file>` | The expected copy, relative to `tests/golden/<case>/`. For a `file` with a placeholder prefix the default is `expected/<file name>`. |

The defaults `rtol = 1e-7`, `atol = 0` match `numpy.testing.assert_allclose`.

### Data files

| suffix | how `key` is read |
|---|---|
| `.json` | A dotted path into an object (`fit.y`, and `peaks.0` indexes a list); a top-level key spelled exactly like the whole path wins. A file holding a bare list is the data itself, and `key` is ignored. `null` reads as NaN. |
| `.npz` | The array named `key`. Read with `allow_pickle=False`. |
| `.npy` | The single array in the file (`key` ignored). |
| `.csv` | The column whose header is `key`. Leading lines starting with `#` are skipped, and empty cells read as NaN. |

An `x_key` needs a named entry, so a bare JSON list or a `.npy` file cannot hold an x axis.
Problems reading the expected copy make the comparison an error (a setup problem). Problems
reading the output make it fail.

## Comparison modes

`e` is the expected array and `a` the actual one. Every comparison also reports the maximum
absolute deviation `max |a - e|` and the maximum relative deviation `max |a - e| / |e|` (over
`e != 0`) when the shapes match. Equal values, including NaN against NaN, count as no deviation.

- **elementwise**: flagged if the shapes differ, or if any element has
  `|a - e| > atol + rtol * |e|`. It is also flagged if NaN appears on one side only, or if an
  infinity is not matched by the same infinity. The measured value is
  `max |a - e| / (atol + rtol * |e|)`, in units of the tolerance.
- **integral**: `I(y)` is the trapezoid rule (`numpy.trapezoid`) over that side's x axis, or
  over the sample index without `x_key`. Flagged if `|I(a) - I(e)| > atol + rtol * |I(e)|`.
  The data must be one-dimensional.
- **peaks**: a peak is a sample with `y[i] > y[i-1]`, `y[i] >= y[i+1]` and
  `y[i] >= min_peak_height * max(y)`; the first and last samples do not qualify. Each peak is
  refined by the vertex of the parabola through its three samples (left `l`, middle `m`, right
  `r`): the offset `0.5 * (l - r) / (l - 2m + r)` (0 when the denominator is 0) is multiplied by
  the local grid step `x[i+1] - x[i]` (by 1 without `x_key`). The positions on each side are
  sorted and paired in order. Flagged if the peak counts differ or any `|shift| > atol`. The data
  must be one-dimensional and real.
- **overlap**: cosine similarity `a.e / (|a| |e|)` over all elements. It is 1 when both sides
  are all 0 and 0 when only one side is. Flagged if the similarity is below `min_overlap`. The
  shapes must match and the data must be real. Use it for spectra whose shape matters more than
  their scale.

`rtol` and `atol` do not apply to `overlap`, and `rtol` does not apply to `peaks`.

## Numerical diff report

The golden gate reports a Markdown table with one row per quantity: case, quantity, mode, max
abs deviation, max rel deviation, the measured value against its tolerance, and the result. When
the run has an artifacts directory, the gate also writes:

- `golden/golden-report.md`: the table and the reproducibility record.
- `golden/<case>--<quantity>.png`: expected and actual overlaid, plus their difference when both
  share one grid. Only for one-dimensional real data. Curves longer than 4000 samples are
  reduced to a min/max envelope, so narrow peaks stay visible.

## `[[protected]]`: protected zones

Values that a task may not change unless it is explicitly allowed to: physical constants,
calibration values, fitting defaults.

| key | type | default | meaning |
|---|---|---|---|
| `file` | string | required | The file holding the values. |
| `names` | list of strings | empty | The protected names. Empty means the whole file. |

How values are compared between the base revision (`git cat-file`) and the working tree:

- **`.py` files**: module-level bindings of each name.
  - A single plain assignment of a literal is compared by value, so `6.626_070_15e-34` equals
    `6.62607015e-34` and `1` equals `1.0`.
  - A single assignment of any other expression is compared by its syntax tree, so comments and
    formatting do not matter.
  - Several bindings, or any other kind of binding, are compared as a whole, so adding a later
    rebinding is a change. Other kinds include `+=`, `for` and `with` targets, imports
    (including `import *`), `def`, `class`, `del`, `:=` and `global` assignments inside
    functions.
  - Dynamic rebinding through `exec`, `globals()` or `setattr` on the module is not seen.
- **`.json` and `.toml` files**: the parsed value at each dotted key. List indices are allowed,
  as in `fit.steps.1`.
- **Whole-file zones** (no `names`, any file type): the file's git blob id, as git would store
  it.
- Named values in any other file type are an error. Leave out `names` to protect the whole file.

A name that appears or disappears counts as a change. The zones are the union of the base
revision's `science.toml` and the working tree's, so removing a zone in the same change does not
unprotect it. An invalid working-tree `science.toml` is noted, and the base zones still apply.

The gate needs a base revision: without one it is skipped. It fails if any change is not
allowed, and its report lists each change with its value before and after. To allow a change,
run the task with `ssw run --allow-protected <entry>` (repeatable). An entry matches:

- `NAME`: that name in any zone;
- `path/to/file.py:NAME`: that name in that file;
- `path/to/file` or `path/to/file:*`: a whole-file zone.

## `[reproducibility]`

| key | type | default | meaning |
|---|---|---|---|
| `seeds` | list of integers | empty | Seeds your code uses, recorded with every run. |
| any other key | any | none | Recorded as-is under `settings`. |

Every golden run attaches a reproducibility record with these keys:

| key | content |
|---|---|
| `python`, `implementation` | version and implementation of the project interpreter |
| `platform`, `machine` | operating system string and CPU architecture |
| `git_commit`, `git_dirty` | `HEAD` and whether the working tree has uncommitted changes (null outside git) |
| `lockfiles` | sha256 of each lockfile at the repository root: `uv.lock`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, `pylock.toml`, `pylock.*.toml`, `requirements*.txt` (CRLF read as LF, so the hash does not depend on the checkout's line endings) |
| `seeds` | from `[reproducibility]` |
| `packages` | `count` and `sha256` of the installed `name==version` set, plus `versions` of numpy, scipy and pandas when present |
| `settings` | extra `[reproducibility]` keys, when present |
| `interpreter_error` | why the interpreter could not be inspected, when it could not |

## Notebooks

When the repository has a `jupytext.toml` (for example `formats = "ipynb,py:percent"`), agents
edit the paired script, not the notebook. Before the golden cases run, the gate pushes each
changed script that has an `.ipynb` beside it into the notebook with
`jupytext --to ipynb --update <script>.py`. This keeps the notebook's stored outputs. `--sync`
is not used, because it picks the source by file timestamp. jupytext runs from the project
environment, or from `PATH`. If it is missing, the gate notes that and continues. If jupytext
fails, the golden gate fails.

## Invariant tests

`science/templates/test_invariants_template.py` is a property-test template built on Hypothesis.
It covers four invariants: normalization conserved, physical bounds, monotonicity and symmetry.
Copy it into your tests, replace the placeholder functions with imports of your own code, and
the `impacted_tests` and `full_tests` gates run it with the rest of the suite.
