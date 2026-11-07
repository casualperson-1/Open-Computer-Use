import math
import random
from typing import List, Tuple

def cubic_bezier_point(p0: Tuple[float, float], p1: Tuple[float, float], 
                       p2: Tuple[float, float], p3: Tuple[float, float], 
                       t: float) -> Tuple[float, float]:
    """Calculate point on cubic bezier curve at parameter t in [0, 1]."""
    u = 1.0 - t
    tt = t * t
    uu = u * u
    uuu = uu * u
    ttt = tt * t

    x = uuu * p0[0] + 3 * uu * t * p1[0] + 3 * u * tt * p2[0] + ttt * p3[0]
    y = uuu * p0[1] + 3 * uu * t * p1[1] + 3 * u * tt * p2[1] + ttt * p3[1]
    return (x, y)

def ease_in_out_sine(t: float) -> float:
    """Smooth acceleration and deceleration using sine easing."""
    return -(math.cos(math.pi * t) - 1.0) / 2.0

def generate_bezier_path(
    start: Tuple[float, float],
    target: Tuple[float, float],
    steps: int = 35,
    deviation_factor: float = 0.25,
    jitter: float = 1.5
) -> List[Tuple[float, float]]:
    """
    Generate human-like trajectory coordinates between start and target points.
    Uses cubic Bezier with perpendicular deviation, ease-in-out time warping,
    and slight micro-jitter.
    """
    x0, y0 = start
    x1, y1 = target

    dx = x1 - x0
    dy = y1 - y0
    dist = math.hypot(dx, dy)

    if dist < 4.0:
        return [target]

    # Calculate perpendicular vector for curve arching
    perp_x = -dy
    perp_y = dx
    norm = math.hypot(perp_x, perp_y)
    if norm > 0:
        perp_x /= norm
        perp_y /= norm

    # Introduce curved trajectory control points
    arch = (random.random() - 0.5) * 2.0 * deviation_factor * dist
    arch2 = (random.random() - 0.5) * 2.0 * deviation_factor * dist

    c1 = (
        x0 + dx * 0.25 + perp_x * arch,
        y0 + dy * 0.25 + perp_y * arch
    )
    c2 = (
        x0 + dx * 0.75 + perp_x * arch2,
        y0 + dy * 0.75 + perp_y * arch2
    )

    path: List[Tuple[float, float]] = []

    for i in range(1, steps + 1):
        t_linear = i / steps
        t_eased = ease_in_out_sine(t_linear)
        bx, by = cubic_bezier_point(start, c1, c2, target, t_eased)

        # Apply micro-jitter (except towards final target)
        if i < steps:
            j_amount = jitter * (1.0 - t_linear * 0.7)
            bx += random.uniform(-j_amount, j_amount)
            by += random.uniform(-j_amount, j_amount)
        else:
            bx, by = x1, y1

        path.append((round(bx, 2), round(by, 2)))

    return path
