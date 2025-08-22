import datetime
from routing.src.assign import haversine


# Small unit tests focused on time window logic (local) and haversine
def test_time_window_checks_local():
    st = datetime.time(9, 0, 0)
    et = datetime.time(10, 0, 0)
    depart = datetime.time(9, 30, 0)
    assert st <= depart <= et


def test_time_window_cross_midnight_local():
    st = datetime.time(23, 0, 0)
    et = datetime.time(3, 0, 0)
    inside1 = datetime.time(23, 30, 0)
    inside2 = datetime.time(2, 0, 0)
    outside = datetime.time(12, 0, 0)
    assert (inside1 >= st) or (inside1 <= et)
    assert (inside2 >= st) or (inside2 <= et)
    assert not ((outside >= st) or (outside <= et))


def test_haversine_symmetry():
    d1 = haversine(4.0, -74.0, 4.1, -74.1)
    d2 = haversine(4.1, -74.1, 4.0, -74.0)
    assert abs(d1 - d2) < 1e-6
