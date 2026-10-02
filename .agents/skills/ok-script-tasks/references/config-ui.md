# Conditional task configuration UI

Read this when adding `sub_configs` or numeric bounds to `self.config_type[key]`. The shared schema resolver is `ok/core/config_schema.py`; the Qt card is `ok/ui/qt/tasks/ConfigCard.py`.

## Conditional children

`sub_configs` maps a **parent value** to visible child keys:

```python
self.config_type["浮层信息"] = {
    "sub_configs": {True: ["浮层文字透明度", "浮层背景透明度", "浮层字号"]},
}
```

A value without a mapping hides the children. Multiple selected values union their child lists, and children can themselves be parents. The parent must be a change-emitting switch, dropdown, or multi-selection widget. Examples: `src/core/BattleConfig.py` and `src/tasks/onetime/DeliveryTask.py`.

## Numeric bounds and type inference

- `min` and `max` bound an `int` default's Qt `SpinBox`. A `float` default uses `DoubleSpinBox`, which ignores these Qt bounds. The headless/web schema still emits `minimum` and `maximum` for either type.
- An explicit `config_type[key]["type"]` wins. Otherwise the widget follows the **default value's type**: bool switch, int `SpinBox`, float `DoubleSpinBox`, or list editor. Choose the default accordingly.
- Config keys and help text are user-visible; sync their gettext entries with `$ok-script-i18n`.
