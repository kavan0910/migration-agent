import os
from collections import OrderedDict as OD

@dataclass
class Foo(Base):
    @staticmethod
    def bar(a, b=1, *args, **kw):
        return a

def top(x: int) -> int:
    return x
