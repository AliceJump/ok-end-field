"""选剑演武（Trial of Swordmancy）最高期望策略求解器。

算法与 MaaEnd v2 的 ``agent/go-service/trialofswordmancy/solver`` 完全一致（同一份前端
TS 求解器的移植）：状态 = (剩余演算次数, 剩余放弃次数, 剩余翻倍次数, 是否已翻倍, 手牌各
点数张数)，动作为 抽牌 / 放弃 / 开始演算 / 翻倍，用 DAG 拓扑序 + Bellman 最优 DP 精确
求期望，不做蒙特卡洛或贪心近似。

本模块只负责「算」，不碰游戏窗口：任务侧把读到的战力点（等级条 0~10）和手牌张数喂成
状态，取最优动作后自己点击。命令行入口在 ``scripts/tools/trial_of_swordmancy_solver.py``，
它只是打印决策表，与任务共用这里的一份实现。
"""

from __future__ import annotations

import collections
import dataclasses
import itertools

MAX_REMAIN_CALC = 4
MAX_REMAIN_ABAND = 3
MAX_CARDS = 5
MAX_CARD_POINTS = 5

DRAW = "抽牌"
ABANDON = "放弃"
CALCULATE = "开始演算"
DOUBLE = "翻倍"

#: 等级 4 的奖励表：下标 0..10 = 战力点
DEFAULT_REWARD = [0, 1000, 2000, 4000, 7500, 12000, 20000, 36000, 60000, 100000, 160000]
#: 等级 4 的默认牌库：点数 1..5 的张数
DEFAULT_DECK = [4, 5, 6, 6, 7]
MAX_DOUBLE = 2


@dataclasses.dataclass(frozen=True)
class Config:
    deck: tuple = tuple(DEFAULT_DECK)
    reward: tuple = tuple(DEFAULT_REWARD)
    max_double: int = MAX_DOUBLE
    overflow: str = "twice"  # none / once / twice


def total_score(hand):
    return sum((i + 1) * c for i, c in enumerate(hand))


def power_of(hand):
    return total_score(hand) % 11


def settle_reward(cfg, hand, is_doubled):
    total = total_score(hand)
    if cfg.overflow == "none" and total >= 11:
        return 0
    if cfg.overflow == "once" and total >= 22:
        return 0
    reward = cfg.reward[power_of(hand)]
    return reward * 2 if is_doubled else reward


def hand_combinations(cfg):
    results = []
    for combo in itertools.product(*[range(c + 1) for c in cfg.deck]):
        if sum(combo) <= MAX_CARDS:
            results.append(combo)
    return results


class Solver:
    def __init__(self, cfg):
        self.cfg = cfg
        self.states = []
        self.index = {}
        self.value = None

    @staticmethod
    def _key(state):
        if state is None:
            return "END"
        remain_calc, remain_aband, remain_double, is_doubled, hand = state
        return ",".join(str(x) for x in (remain_calc, remain_aband, remain_double, int(is_doubled), *hand))

    def allowed_actions(self, state):
        if state is None:
            return []
        _, _, remain_double, is_doubled, hand = state
        total = sum(hand)
        if total == MAX_CARDS:
            return [CALCULATE, ABANDON]
        actions = [DRAW, ABANDON, CALCULATE]
        if total == 2 and not is_doubled and remain_double > 0:
            actions.append(DOUBLE)
        return actions

    def transitions(self, state, action):
        remain_calc, remain_aband, remain_double, is_doubled, hand = state
        if action == DRAW:
            remain = [self.cfg.deck[i] - hand[i] for i in range(5)]
            total_remain = sum(remain)
            out = []
            for i in range(5):
                if remain[i] > 0:
                    new_hand = list(hand)
                    new_hand[i] += 1
                    out.append(
                        (
                            (remain_calc, remain_aband, remain_double, is_doubled, tuple(new_hand)),
                            remain[i] / total_remain,
                        )
                    )
            return out
        if action == ABANDON:
            if remain_calc == MAX_REMAIN_CALC:
                nxt = (remain_calc - 1, remain_aband, remain_double, False, (0, 0, 0, 0, 0))
            elif remain_aband > 0:
                nxt = (remain_calc, remain_aband - 1, remain_double, False, (0, 0, 0, 0, 0))
            else:
                nxt = (remain_calc - 1, remain_aband, remain_double, False, (0, 0, 0, 0, 0))
            return [(nxt if nxt[0] > 0 else None, 1.0)]
        if action == CALCULATE:
            next_double = remain_double - 1 if is_doubled else remain_double
            nxt = (remain_calc - 1, remain_aband, next_double, False, (0, 0, 0, 0, 0))
            return [(nxt if nxt[0] > 0 else None, 1.0)]
        # DOUBLE
        return [((remain_calc, remain_aband, remain_double, True, hand), 1.0)]

    def keep_state(self, state):
        remain_calc, remain_aband, remain_double, is_doubled, hand = state
        if not (1 <= remain_calc <= MAX_REMAIN_CALC):
            return False
        if not (0 <= remain_aband <= MAX_REMAIN_ABAND):
            return False
        min_double = max(0, remain_calc - MAX_REMAIN_CALC + self.cfg.max_double)
        if not (min_double <= remain_double <= self.cfg.max_double):
            return False
        if sum(hand) <= 1 and is_doubled:
            return False
        return not (is_doubled and remain_double == 0)

    def build(self):
        self.states = [None]
        self.index = {"END": 0}
        combos = hand_combinations(self.cfg)
        for remain_calc in range(1, MAX_REMAIN_CALC + 1):
            for remain_aband in range(0, MAX_REMAIN_ABAND + 1):
                for remain_double in range(0, self.cfg.max_double + 1):
                    for is_doubled in (False, True):
                        for hand in combos:
                            state = (remain_calc, remain_aband, remain_double, is_doubled, hand)
                            if not self.keep_state(state):
                                continue
                            key = self._key(state)
                            if key not in self.index:
                                self.index[key] = len(self.states)
                                self.states.append(state)

    def solve(self):
        if self.value is not None:
            return self.value
        self.build()
        total = len(self.states)
        reverse = [[] for _ in range(total)]
        for u, state in enumerate(self.states):
            if state is None:
                continue
            for action in self.allowed_actions(state):
                for target, _prob in self.transitions(state, action):
                    reverse[self.index[self._key(target)]].append(u)
        dist = [-1] * total
        dist[0] = 0
        # 反向 BFS 求到吸收态的最长步数，作为 DAG 拓扑序的依据（FIFO 顺序与 list.pop(0) 一致）
        queue = collections.deque([0])
        while queue:
            v = queue.popleft()
            for u in reverse[v]:
                if dist[u] < dist[v] + 1:
                    dist[u] = dist[v] + 1
                    queue.append(u)
        order = sorted(range(total), key=lambda i: dist[i])
        value = [0.0] * total
        for idx in order:
            state = self.states[idx]
            if state is None:
                continue
            best = float("-inf")
            for action in self.allowed_actions(state):
                immediate = settle_reward(self.cfg, state[4], state[3]) if action == CALCULATE else 0
                future = sum(p * value[self.index[self._key(t)]] for t, p in self.transitions(state, action))
                best = max(best, immediate + future)
            value[idx] = best
        self.value = value
        return value

    def contains(self, state):
        """状态是否在求解器的状态空间内；不可达状态给不出可靠决策。"""
        if state is None or state[0] <= 0:
            return False
        return self._key(state) in self.index

    def decide(self, state):
        """返回当前局面所有合法动作的 (动作, 期望总奖励)，按期望从高到低排序。

        局面不可达（手牌超出牌库、剩余次数组合非法、剩余演算为 0 等）时返回空列表，
        调用方需要自行回退到别的策略。
        """
        self.solve()
        if not self.contains(state):
            return []
        outcomes = []
        for action in self.allowed_actions(state):
            immediate = settle_reward(self.cfg, state[4], state[3]) if action == CALCULATE else 0
            future = sum(p * self.value[self.index[self._key(t)]] for t, p in self.transitions(state, action))
            outcomes.append((action, immediate + future))
        outcomes.sort(key=lambda item: item[1], reverse=True)
        return outcomes


_SOLVERS: dict = {}


def get_solver(cfg=None):
    """取（并按配置缓存的）求解器实例。

    默认设定有 1.6 万个状态，求解一次约 1 秒；任务在一趟里只构建一次，之后每步都是查表。
    """
    cfg = cfg or Config()
    solver = _SOLVERS.get(cfg)
    if solver is None:
        solver = Solver(cfg)
        solver.solve()
        _SOLVERS[cfg] = solver
    return solver


def representative_hand(count, power, deck=None):
    """按「手牌张数 + 战力点」估一组手牌（各点数张数），用于识别缺失时兜底。

    按点数从低到高优先取小牌，且每点数不超过牌库张数；找不到时返回 None。
    """
    if count == 0:
        return (0, 0, 0, 0, 0) if power == 0 else None
    deck = DEFAULT_DECK if deck is None else deck
    for combo in itertools.product(*[range(min(cap, count) + 1) for cap in deck]):
        if sum(combo) != count:
            continue
        if total_score(combo) % 11 == power:
            return combo
    return None


@dataclasses.dataclass
class DrawState:
    """任务侧维护的跨轮局面，与求解器的状态一一对应。

    手牌张数、剩余次数全部由代码自己数，不依赖界面识别：任务只从等级条读战力点。
    """

    remain_calc: int
    remain_aband: int
    remain_double: int
    is_doubled: bool = False
    hand: tuple = (0, 0, 0, 0, 0)

    @classmethod
    def from_config(cls, remain_calc=3, remain_aband=3, remain_double=2):
        """按配置的剩余次数开局，并把越界值夹回状态空间允许的范围。"""
        return cls(
            remain_calc=min(MAX_REMAIN_CALC, max(1, int(remain_calc))),
            remain_aband=min(MAX_REMAIN_ABAND, max(0, int(remain_aband))),
            remain_double=min(MAX_DOUBLE, max(0, int(remain_double))),
        )

    def solver_state(self):
        """展开成求解器需要的状态元组。"""
        return (self.remain_calc, self.remain_aband, self.remain_double, self.is_doubled, self.hand)

    def card_count(self):
        return sum(self.hand)

    def has_remain_calc(self):
        """是否还有演算次数可用（0 表示这趟已经打完）。"""
        return self.remain_calc > 0

    def add_card(self, points):
        """按点数记一张牌；点数不在 1..5 时返回 False，由调用方兜底。"""
        if not 1 <= points <= MAX_CARD_POINTS:
            return False
        hand = list(self.hand)
        hand[points - 1] += 1
        self.hand = tuple(hand)
        return True

    def replace_hand(self, hand):
        """整体替换手牌（战力点跳变、推算不出牌面时按张数与战力点估一组）。"""
        self.hand = tuple(hand)

    def mark_doubled(self):
        self.is_doubled = True

    def finish_round(self, action):
        """按 MaaEnd 的转移规则结算「开始演算 / 放弃」，推进到下一轮开局。"""
        if action == CALCULATE:
            # 开始演算消耗一次演算次数；本局翻倍过的再消耗一次翻倍次数。
            self.remain_calc = max(0, self.remain_calc - 1)
            if self.is_doubled:
                self.remain_double = max(0, self.remain_double - 1)
        elif action == ABANDON:
            # 跨日残局的额外一局（剩余演算 = 4）放弃也扣演算次数；
            # 常规状态先扣放弃次数，放弃次数为 0 时再扣演算次数。
            if self.remain_calc == MAX_REMAIN_CALC or self.remain_aband <= 0:
                self.remain_calc = max(0, self.remain_calc - 1)
            else:
                self.remain_aband -= 1
        else:
            raise ValueError(f"未知的回合结束动作: {action}")
        self.is_doubled = False
        self.hand = (0, 0, 0, 0, 0)


def expected_action(solver, draw_state):
    """按最高期望原则选下一步动作，返回 (动作, 期望总奖励)。

    局面不可达时返回 (None, 0.0)，由调用方回退到原策略。并列时沿用求解器的稳定顺序
    （抽牌 > 放弃 > 开始演算），不采用 MaaEnd pickDecision 里「抽牌与放弃相差 <1 时改选
    放弃」的展示规则——本策略按纯期望取最优。
    """
    outcomes = solver.decide(draw_state.solver_state())
    if not outcomes:
        return None, 0.0
    action, expected = outcomes[0]
    return action, expected


def hand_text(hand):
    """把各点数张数拼成可读文本，如「1×2、4×1」；空手牌返回「空」。"""
    parts = [f"{index + 1}×{count}" for index, count in enumerate(hand) if count]
    return "、".join(parts) if parts else "空"


def format_state(draw_state, power):
    """把当前局面拼成一行可读文本，供决策日志与不可达告警共用。"""
    doubled = "（已翻倍）" if draw_state.is_doubled else ""
    return (
        f"手牌 {draw_state.card_count()} 张（{hand_text(draw_state.hand)}），战力点 {power}，"
        f"剩余演算 {draw_state.remain_calc} / 放弃 {draw_state.remain_aband} / 翻倍 {draw_state.remain_double}"
        f"{doubled}"
    )


def format_decision(draw_state, power, action, expected):
    """一行决策日志：张数 / 战力点 / 求解器给出的最优动作 / 期望值。"""
    return f"最高期望原则：{format_state(draw_state, power)}，最优动作 {action}，期望奖励 {expected:.0f}"


def strategy_table(cfg=None, remain_calc=MAX_REMAIN_CALC, remain_aband=MAX_REMAIN_ABAND, solver=None):
    """按「手牌张数 × 战力点」列出本回合的最优动作，供命令行与测试核对。

    返回 [(张数, 战力点, 动作, 期望值), ...]；组合不存在的行会被跳过。
    """
    cfg = cfg or Config()
    solver = solver or get_solver(cfg)
    rows = []
    for count in range(0, MAX_CARDS + 1):
        for power in range(0, 11):
            hand = representative_hand(count, power, cfg.deck)
            if hand is None:
                continue
            state = (remain_calc, remain_aband, cfg.max_double, False, hand)
            outcomes = solver.decide(state)
            if not outcomes:
                continue
            action, expected = outcomes[0]
            rows.append((count, power, action, expected))
    return rows
