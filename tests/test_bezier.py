from shared.bezier import generate_bezier_path, ease_in_out_sine, cubic_bezier_point

def test_ease_in_out():
    assert ease_in_out_sine(0.0) == 0.0
    assert abs(ease_in_out_sine(0.5) - 0.5) < 1e-6
    assert abs(ease_in_out_sine(1.0) - 1.0) < 1e-6

def test_cubic_bezier_endpoints():
    p0 = (10.0, 20.0)
    p1 = (30.0, 40.0)
    p2 = (50.0, 60.0)
    p3 = (70.0, 80.0)
    start = cubic_bezier_point(p0, p1, p2, p3, 0.0)
    end = cubic_bezier_point(p0, p1, p2, p3, 1.0)
    assert start == p0
    assert end == p3

def test_generate_bezier_path_reach_target():
    start = (100.0, 100.0)
    target = (500.0, 400.0)
    path = generate_bezier_path(start, target, steps=25)
    assert len(path) == 25
    # The last point must match target exactly
    assert path[-1] == target
