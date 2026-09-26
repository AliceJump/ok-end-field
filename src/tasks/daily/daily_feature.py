"""日常子任务包装器：把独立任务接进日常任务清单，日常任务不继承业务 mixin。

骨架移植自 ok-ap 的 ``DailyFeature``（PR#30），注入逻辑适配 ok-end-field 的
账号覆盖机制，差异点：

- ok-ap 注入 ``_account_override_provider`` 供子任务经 ``_sub_task_cfg`` 显式取参；
  本仓库的账号覆盖走 ``AccountOverrideMixin._bind_account_aware_config_get``
  对 ``config.get`` 的自动补丁，读取闸门是 ``self.running``，因此这里注入
  ``running=True`` + ``set_current_account(...)``（绑定宿主账号上下文），
  业务 mixin 方法体无需任何改动；
- 帝江号共享状态 ``_daily_boat_state_confirmed`` 由 DailyTaskRunner 维护在
  宿主上（shared_state_task_keys），子任务拆成独立实例后由本包装器在执行
  前后做 host↔impl 同步，保证「整理/收菜共享帝江号状态」语义不变。

机制：
- 执行时从 executor 已注册的一次性任务实例里找到子任务实例——框架已替它把
  ``configs/<类名>.json`` 加载成内存配置，参数读取直接用那份（真正的
  「沿用子任务的配置」），无需再读文件；
- 执行前把宿主的账号上下文注入子任务实例，使「多账户独立配置」对子任务
  参数生效；执行后恢复原状（try/finally）；
- 子任务未注册时记日志并把该项记为失败（返回 False），不中断日常其余任务。

用法（DailyTask）::

    self.mail_feature = DailyFeature(self, MailTask, switch_key="⭐收邮件")
    # build_task_plan 里：
    return [self.mail_feature.plan_item(), ...]
"""

from __future__ import annotations

# 帝江号共享状态属性名：与 LiaisonMixin / DailyTaskRunner 的约定保持一致
_BOAT_STATE_ATTR = "_daily_boat_state_confirmed"


class DailyFeature:
    """把一个独立任务包装成 ``DailyTaskRunner`` 任务清单的子任务项。"""

    def __init__(self, host, task_class, switch_key: str, run_method: str = "run", predicate=None):
        """
        Args:
            host: 日常宿主任务实例（DailyTask），提供 executor 与账号上下文。
            task_class: 子任务类（如 MailTask），须已在 ``onetime_tasks`` 注册。
            switch_key: 日常配置里控制本子任务的开关键名。
            run_method: 执行时调用的子任务方法名，默认 ``run``。
            predicate: 可选开关谓词。提供时替代 runner 默认的
                ``config.get(switch_key)`` 判定（例如多开关 OR 语义、
                以子任务自身配置的非空列表值作为开关）。
        """
        self.host = host
        self.task_class = task_class
        self.switch_key = switch_key
        self.run_method = run_method
        self.predicate = predicate

    def plan_item(self):
        """返回任务清单元素 ``(任务名, 执行函数)`` 或 ``(任务名, 执行函数, 谓词)``。

        任务名即宿主配置里的开关键名。
        """
        if self.predicate is not None:
            return (self.switch_key, self.run, self.predicate)
        return (self.switch_key, self.run)

    def _resolve_impl(self):
        """从 executor 已注册的一次性任务实例里找子任务实例，找不到返回 None。"""
        for task in getattr(self.host.executor, "onetime_tasks", []) or []:
            if isinstance(task, self.task_class):
                return task
        return None

    def impl_config(self, key, default=None):
        """读取已注册子任务实例的配置值；实例缺失时返回 default。

        供任务清单的开关谓词使用：参数迁到子任务卡片后，宿主侧的
        开关判定（如多选列表非空即启用、多开关 OR）改读子任务配置。
        """
        impl = self._resolve_impl()
        if impl is None:
            return default
        return impl.config.get(key, default)

    def run(self):
        """在子任务实例上执行业务流程，前后注入/恢复宿主的账号上下文。"""
        impl = self._resolve_impl()
        if impl is None:
            self.host.log_info(
                self.host.tr("未找到 {task_class} 的已注册实例，{switch_key} 记为失败").format(
                    task_class=self.task_class.__name__,
                    switch_key=self.host.tr(self.switch_key),
                ),
                notify=True,
            )
            return False

        host_boat_state = getattr(self.host, _BOAT_STATE_ATTR, False)
        # 注入前备份子任务原值，结束后恢复，避免污染独立运行/GUI 场景
        backup = (
            getattr(impl, "running", False),
            getattr(impl, "current_account_id", ""),
            getattr(impl, "current_user", ""),
        )
        # running=True 是账号覆盖读取的闸门（AccountOverrideMixin）；
        # set_current_account 绑定账号上下文并给子任务的 config.get 打覆盖补丁
        impl.running = True
        impl.set_current_account(
            getattr(self.host, "current_user", "") or "",
            getattr(self.host, "current_account_id", "") or "",
        )
        # 帝江号共享状态：执行前从宿主带入（runner 已按 shared_state_task_keys 处理过宿主侧）
        setattr(impl, _BOAT_STATE_ATTR, host_boat_state)
        try:
            return getattr(impl, self.run_method)()
        finally:
            # 子任务执行期间可能置位/重置共享状态，带回宿主供 runner 后续任务判定
            setattr(self.host, _BOAT_STATE_ATTR, getattr(impl, _BOAT_STATE_ATTR, host_boat_state))
            (
                impl.running,
                impl.current_account_id,
                impl.current_user,
            ) = backup
