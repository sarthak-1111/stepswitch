from calc import add, mul, sub


def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0


def test_sub():
    assert sub(5, 3) == 2


def test_mul():
    assert mul(4, 3) == 12
