"""Seeded tasks for the disagreement claim (C-013), each solved by two scripted candidates.

Every task is a small project. Two fake workers from different model families, "alpha" and
"beta", solve it the same way a model would: write the implementation, write a test of it, run
the tests and finish, so both candidates pass the gates.

* In an ambiguous task the instruction allows two readings and each candidate takes one; its
  own test pins that reading down, so it fails on the other candidate's code.
* In an unambiguous task the candidates write different code with the same behaviour, and each
  test passes on both.
* The science tasks use the golden case of `science_fixture` with a 5% tolerance. The
  candidates scale the spectrum by different amounts: both stay within the tolerance of the
  expected output, but in the ambiguous task they are further apart from each other than the
  tolerance allows.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from stepswitch.compat.ir import Request, Usage
from stepswitch.workers.base import Worker, WorkerSpec
from stepswitch.workers.catalog import Catalog
from stepswitch.workers.fake import Action, ScriptedModel
from tests.claims import science_fixture

FAMILIES = ("alpha", "beta")
TURN_USAGE = Usage(input_tokens=200, output_tokens=20)
GOLDEN_RTOL = 0.05


@dataclass(frozen=True)
class SeededTask:
    name: str
    ambiguous: bool
    instruction: str
    # Files of the project before the task, and the files each candidate writes.
    base: dict[str, str]
    writes: dict[str, dict[str, str]]
    track: str = "general"
    # Science tasks: the value each candidate gives SCALE in toy/spectra.py.
    scale: dict[str, float] = field(default_factory=dict)

    def make(self, dest: Path) -> Path:
        if self.track == "science":
            repo = science_fixture.make_project(dest)
            science_fixture.write_science_toml(repo, [QUANTITY])
            science_fixture.git(repo, "commit", "-q", "-am", "golden tolerance")
            return repo
        repo = dest / "project"
        for rel, text in self.base.items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        science_fixture.git(repo, "init", "-q")
        science_fixture.git(repo, "add", "-A")
        science_fixture.git(repo, "commit", "-q", "-m", "base")
        return repo

    def files_for(self, family: str, repo: Path) -> dict[str, str]:
        if self.track == "science":
            source = (repo / "toy" / "spectra.py").read_text(encoding="utf-8")
            return {
                "toy/spectra.py": source.replace("SCALE = 0.0", f"SCALE = {self.scale[family]!r}")
            }
        return self.writes[family]

    def catalog(self, repo: Path) -> Catalog:
        return Catalog(
            [candidate(family, self.files_for(family, repo), repo) for family in FAMILIES]
        )


QUANTITY = {
    "name": "spectrum",
    "file": "spectrum.json",
    "key": "y",
    "x_key": "x",
    "mode": "elementwise",
    "rtol": GOLDEN_RTOL,
    "atol": 1e-9,
}


def candidate(family: str, files: dict[str, str], repo: Path) -> Worker:
    """A worker of `family` that writes `files`, runs the tests when there are any, finishes."""
    actions = [
        Action(
            tool="edit",
            arguments={"path": rel, "text": text}
            | ({} if (repo / rel).exists() else {"create": True}),
        )
        for rel, text in files.items()
    ]
    if any(rel.startswith("tests/") for rel in files):
        actions.append(Action(tool="test", arguments={}))
    actions.append(Action(tool="finish", arguments={"summary": f"Done ({family})."}))
    model = ScriptedModel(script(actions), usage=TURN_USAGE, model=family)
    spec = WorkerSpec(
        id=f"fake/{family}",
        endpoint=family,
        harness="fake",
        model=family,
        tier="t2",
        family=family,
        local=True,
        tool_protocol="native",
    )
    return Worker(spec, model)


def script(actions: list[Action]) -> Callable[[Request, int], Action]:
    def answer(request: Request, _index: int) -> Action:
        if not request.tools:
            return Action(text="APPROVE")
        done = sum(1 for message in request.messages if message.role == "tool")
        return actions[min(done, len(actions) - 1)]

    return answer


def _module(doc: str, body: str) -> str:
    return f'"""{doc}"""\n\n{body}'


def _general(
    name: str,
    ambiguous: bool,
    instruction: str,
    module: str,
    doc: str,
    base_body: str,
    base_test: str,
    alpha: tuple[str, str],
    beta: tuple[str, str],
) -> SeededTask:
    """A general-track task: `module` holds the code, each candidate adds a body and a test."""
    stem = module.removesuffix(".py")
    base = {module: _module(doc, base_body), f"tests/test_{stem}.py": base_test}
    writes = {
        family: {
            module: _module(doc, base_body + "\n\n" + body),
            f"tests/test_{stem}_{family}.py": test,
        }
        for family, (body, test) in zip(FAMILIES, (alpha, beta), strict=True)
    }
    return SeededTask(name, ambiguous, instruction, base, writes)


SEEDED: tuple[SeededTask, ...] = (
    _general(
        "round-price",
        True,
        "Add round_price(value) to pricing.py: round a price to a whole number.",
        "pricing.py",
        "Prices.",
        'CURRENCY = "EUR"\n',
        'import pricing\n\n\ndef test_currency():\n    assert pricing.CURRENCY == "EUR"\n',
        (
            "def round_price(value):\n    return int(value + 0.5)\n",
            "from pricing import round_price\n\n\ndef test_halves_round_up():\n"
            "    assert round_price(2.5) == 3\n    assert round_price(2.4) == 2\n",
        ),
        (
            "def round_price(value):\n    return round(value)\n",
            "from pricing import round_price\n\n\ndef test_halves_round_to_even():\n"
            "    assert round_price(2.5) == 2\n    assert round_price(3.5) == 4\n",
        ),
    ),
    _general(
        "title-slug",
        True,
        "Add slug(title) to text.py: make a URL slug from a title.",
        "text.py",
        "Text helpers.",
        'SEPARATORS = " \\t"\n',
        'import text\n\n\ndef test_separators():\n    assert " " in text.SEPARATORS\n',
        (
            'def slug(title):\n    return "-".join(title.lower().split())\n',
            "from text import slug\n\n\ndef test_hyphens():\n"
            '    assert slug("Hello World") == "hello-world"\n',
        ),
        (
            'def slug(title):\n    return "_".join(title.lower().split())\n',
            "from text import slug\n\n\ndef test_underscores():\n"
            '    assert slug("Hello World") == "hello_world"\n',
        ),
    ),
    _general(
        "split-name",
        True,
        "Add split_name(full) to names.py: split a full name into first and last name.",
        "names.py",
        "Name handling.",
        'GREETING = "Hello"\n',
        'import names\n\n\ndef test_greeting():\n    assert names.GREETING == "Hello"\n',
        (
            'def split_name(full):\n    first, _, last = full.partition(" ")\n'
            "    return first, last\n",
            "from names import split_name\n\n\ndef test_first_word_is_first_name():\n"
            '    assert split_name("Ada King Lovelace") == ("Ada", "King Lovelace")\n',
        ),
        (
            'def split_name(full):\n    first, _, last = full.rpartition(" ")\n'
            "    return first, last\n",
            "from names import split_name\n\n\ndef test_last_word_is_last_name():\n"
            '    assert split_name("Ada King Lovelace") == ("Ada King", "Lovelace")\n',
        ),
    ),
    _general(
        "add-numbers",
        False,
        "Add add(a, b) to calc.py: return the sum of a and b.",
        "calc.py",
        "Arithmetic.",
        "ZERO = 0\n",
        "import calc\n\n\ndef test_zero():\n    assert calc.ZERO == 0\n",
        (
            "def add(a, b):\n    return a + b\n",
            "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        ),
        (
            "def add(a, b):\n    return sum((a, b))\n",
            "from calc import add\n\n\ndef test_add_signs():\n"
            "    assert add(-1, 1) == 0\n    assert add(2, 2) == 4\n",
        ),
    ),
    _general(
        "clamp-range",
        False,
        "Add clamp(x, low, high) to bounds.py: limit x to the closed range from low to high.",
        "bounds.py",
        "Range helpers.",
        "UNIT = (0.0, 1.0)\n",
        "import bounds\n\n\ndef test_unit():\n    assert bounds.UNIT == (0.0, 1.0)\n",
        (
            "def clamp(x, low, high):\n    return max(low, min(x, high))\n",
            "from bounds import clamp\n\n\ndef test_clamp():\n"
            "    assert clamp(5, 0, 3) == 3\n    assert clamp(-1, 0, 3) == 0\n",
        ),
        (
            "def clamp(x, low, high):\n    if x < low:\n        return low\n"
            "    if x > high:\n        return high\n    return x\n",
            "from bounds import clamp\n\n\ndef test_clamp_inside_and_above():\n"
            "    assert clamp(2, 0, 3) == 2\n    assert clamp(9, 0, 3) == 3\n",
        ),
    ),
    _general(
        "even-numbers",
        False,
        "Add is_even(n) to parity.py: return True when the integer n is even.",
        "parity.py",
        "Parity checks.",
        "BASE = 2\n",
        "import parity\n\n\ndef test_base():\n    assert parity.BASE == 2\n",
        (
            "def is_even(n):\n    return n % 2 == 0\n",
            "from parity import is_even\n\n\ndef test_even():\n"
            "    assert is_even(4)\n    assert not is_even(7)\n",
        ),
        (
            "def is_even(n):\n    return not n & 1\n",
            "from parity import is_even\n\n\ndef test_even_zero_and_negative():\n"
            "    assert is_even(0)\n    assert not is_even(-3)\n",
        ),
    ),
    SeededTask(
        "spectrum-scale",
        True,
        "Calibrate the spectrum amplitude: set SCALE in toy/spectra.py.",
        {},
        {},
        track="science",
        scale={"alpha": 0.04, "beta": -0.04},
    ),
    SeededTask(
        "spectrum-trim",
        False,
        "Trim the spectrum amplitude slightly: set SCALE in toy/spectra.py.",
        {},
        {},
        track="science",
        scale={"alpha": 0.002, "beta": -0.002},
    ),
)
