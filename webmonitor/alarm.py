# -*- coding: utf-8 -*-
"""报警判定规则（纯逻辑，可独立单元测试）。

规则一：目标值模式 —— 区域内出现目标数值的行数 >= 相同行数阈值。
规则二：默认模式 —— 区域内任意连续 N 行数值完全相同。

两种规则都使用“增量确认”：已消除报警的组合/数量会被记录，
表格新增行导致重复命中时不会再重复报警。
"""


def same_runs(digits, count):
    """返回所有长度为 count 的连续相同行组合 {(起始下标, 组合元组)}。"""
    runs = set()
    if count <= 0:
        return runs
    for start in range(len(digits) - count + 1):
        group = tuple(digits[start:start + count])
        if group and all(item == group[0] for item in group):
            runs.add((start, group))
    return runs


def build_rule(target_value, same_count):
    return {
        "target_value": (target_value or "").strip(),
        "same_count": max(1, int(same_count or 1)),
    }


class AlarmTracker:
    """维护每个区域的报警状态与“已确认”基线。"""

    def __init__(self):
        self.alarming = {}
        self.ack_counts = {}
        self.ack_runs = {}
        self.latest = {}

    # ---------- 查询 ----------
    def active_boxes(self):
        return [idx for idx, flag in sorted(self.alarming.items()) if flag]

    def is_alarming(self, box_idx):
        return bool(self.alarming.get(box_idx, False))

    def latest_digits(self, box_idx):
        return list(self.latest.get(box_idx, []))

    # ---------- 判定 ----------
    def evaluate(self, box_idx, digits, rule):
        """返回 True 表示本轮检测出现了新的、尚未确认的报警。"""
        digits = list(digits or [])
        self.latest[box_idx] = digits

        rule = build_rule(rule.get("target_value"), rule.get("same_count", 1))
        if rule["target_value"]:
            current = digits.count(rule["target_value"])
            confirmed = self.ack_counts.get(box_idx, 0)
            if current >= rule["same_count"] and current > confirmed:
                self.alarming[box_idx] = True
                return True
            return False

        confirmed_runs = self.ack_runs.get(box_idx, set())
        if same_runs(digits, rule["same_count"]) - confirmed_runs:
            self.alarming[box_idx] = True
            return True
        return False

    # ---------- 消除 ----------
    def acknowledge(self, box_idx, rule=None):
        """把当前识别结果记为“已确认”，后续相同内容不再报警。"""
        digits = self.latest.get(box_idx, [])
        rule = build_rule(rule.get("target_value") if rule else "",
                          rule.get("same_count", 1) if rule else 1)
        if rule["target_value"]:
            self.ack_counts[box_idx] = digits.count(rule["target_value"])
        else:
            self.ack_runs.setdefault(box_idx, set()).update(
                same_runs(digits, rule["same_count"]))
        self.alarming[box_idx] = False

    def acknowledge_all(self, box_count, rule=None):
        for box_idx in range(1, box_count + 1):
            self.acknowledge(box_idx, rule)

    def forget_box(self, box_idx):
        for store in (self.alarming, self.ack_counts, self.ack_runs, self.latest):
            store.pop(box_idx, None)

    def reset(self):
        self.alarming.clear()
        self.ack_counts.clear()
        self.ack_runs.clear()
        self.latest.clear()
