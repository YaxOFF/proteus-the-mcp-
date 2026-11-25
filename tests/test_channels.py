"""Channel router geometry rules: what may cross and what would look connected."""
from proteus_mcp.channels import _crosses_box, _touches


def test_touches():
    assert _touches(((0, 0), (10, 0)), ((5, 0), (20, 0)))       # overlap on one line
    assert _touches(((0, 0), (10, 0)), ((5, 0), (5, 10)))       # T-joint
    assert not _touches(((0, 5), (10, 5)), ((5, 0), (5, 10)))   # plain crossing
    assert not _touches(((0, 0), (10, 0)), ((0, 1), (10, 1)))   # parallel, apart
    assert not _touches(((0, 0), (10, 0)), ((11, 0), (20, 0)))  # collinear, apart


def test_crosses_box():
    box = (10, 10, 20, 20)
    assert _crosses_box(((0, 15), (30, 15)), box)
    assert not _crosses_box(((0, 10), (30, 10)), box)  # along the edge is fine
    assert not _crosses_box(((0, 25), (30, 25)), box)
    assert _crosses_box(((15, 0), (15, 30)), box)
