from planner.agenda import Event, agenda, clashes, end_of

DAY = [
    Event("Lunch", "12:30", 45),
    Event("Stand-up", "9:15", 15),
    Event("Gym", "7:00", 60),
    Event("Review", "10:00", 30),
]


def test_agenda_follows_the_time_of_day():
    assert agenda(DAY) == [
        "7:00-8:00 Gym",
        "9:15-9:30 Stand-up",
        "10:00-10:30 Review",
        "12:30-13:15 Lunch",
    ]


def test_events_at_the_same_time_keep_their_order():
    events = [Event("Call", "9:00", 30), Event("Email", "9:00", 15), Event("Plan", "8:30", 10)]
    assert agenda(events) == ["8:30-8:40 Plan", "9:00-9:30 Call", "9:00-9:15 Email"]


def test_empty_day():
    assert agenda([]) == []


def test_end_of():
    assert end_of(Event("Talk", "9:45", 30)) == "10:15"


def test_clashes():
    assert clashes(Event("A", "9:00", 60), Event("B", "9:30", 15))
    assert not clashes(Event("A", "9:00", 30), Event("B", "9:30", 15))
