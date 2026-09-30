# -*- coding: utf-8 -*-
"""操作闸门：同一时刻只允许一项网页操作在途。

- 避免定时器与手动触发重叠执行；
- 模式切换、页面导航、退出时可通过提升“代次”一次性作废在途操作；
- 作废后进行中的结果标记为未知，UI 会如实提示，不假装成功。
"""


class OperationGate:
    __slots__ = ("generation", "pending")

    def __init__(self):
        self.generation = 0
        self.pending = None

    def begin(self, kind):
        if self.pending is not None:
            return None
        self.generation += 1
        self.pending = (self.generation, kind)
        return self.pending

    def finish(self, ticket):
        if ticket is None or self.pending != ticket:
            return False
        self.pending = None
        return True

    def cancel(self):
        self.generation += 1
        self.pending = None

    @property
    def busy(self):
        return self.pending is not None

    @property
    def pending_kind(self):
        return self.pending[1] if self.pending else None
