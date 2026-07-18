from types import MethodType

from src.tasks.account.account_scope_store import get_account_task_overrides


class AccountOverrideMixin:
    """按账号上下文读取任务配置覆盖。"""

    def _bind_account_aware_config_get(self):
        cfg = getattr(self, "config", None)
        if cfg is None or getattr(cfg, "_account_get_patched", False):
            return

        raw_get = cfg.get

        def _patched_get(config_obj, key, default=None):
            return self._config_get_with_account_override(key, default, raw_get)

        cfg._raw_get = raw_get
        cfg.get = MethodType(_patched_get, cfg)
        cfg._account_get_patched = True

    def _raw_cfg_get(self, key, default=None):
        cfg = getattr(self, "config", None)
        if cfg is None:
            return default

        raw_get = getattr(cfg, "_raw_get", None)
        if callable(raw_get):
            return raw_get(key, default)

        return dict.get(cfg, key, default)

    def _is_account_override_enabled(self):
        cfg = getattr(self, "config", None)
        if cfg is None:
            return False

        # 保持兼容：有开关时尊重开关；无开关时默认允许账号覆盖。
        if "多账户独立配置" in cfg:
            return bool(self._raw_cfg_get("多账户独立配置", False))
        return True

    @staticmethod
    def _coerce_override_value(base_value, override_value):
        if base_value is None or override_value is None:
            return override_value

        if isinstance(base_value, bool):
            if isinstance(override_value, bool):
                return override_value
            if isinstance(override_value, str):
                value = override_value.strip().lower()
                if value in {"true", "1", "yes", "on", "是", "开启"}:
                    return True
                if value in {"false", "0", "no", "off", "否", "关闭"}:
                    return False
            return base_value

        if isinstance(base_value, int) and not isinstance(base_value, bool):
            if isinstance(override_value, int):
                return override_value
            if isinstance(override_value, str):
                try:
                    return int(override_value.strip())
                except ValueError:
                    return base_value
            return base_value

        if isinstance(base_value, float):
            if isinstance(override_value, (int, float)):
                return float(override_value)
            if isinstance(override_value, str):
                try:
                    return float(override_value.strip())
                except ValueError:
                    return base_value
            return base_value

        if isinstance(base_value, list):
            if isinstance(override_value, list):
                return override_value
            return base_value

        if isinstance(base_value, str):
            return str(override_value)

        if isinstance(override_value, type(base_value)):
            return override_value

        return base_value

    def _config_get_with_account_override(self, key, default, raw_get):
        base_value = raw_get(key, default)

        if not self._is_account_override_enabled():
            return base_value

        account_id = (self.current_account_id or "").strip()
        account_name = (self.current_user or "").strip()
        if not account_id and not account_name:
            return base_value

        task_name = self.__class__.__name__
        account_overrides = get_account_task_overrides(account_id or account_name, task_name, account_name=account_name)
        if key not in account_overrides:
            return base_value

        return self._coerce_override_value(base_value, account_overrides.get(key))

    def cfg_get(self, key, default=None):
        return self._config_get_with_account_override(key, default, self._raw_cfg_get)
