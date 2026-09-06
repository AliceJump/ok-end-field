# Config Export and Import

Back: [Documentation home](index.md) / [README](https://github.com/AliceJump/ok-end-field/blob/master/README.md)

Use this feature to back up, restore, or migrate the application configuration between devices. Configuration lives in the `configs` folder inside the application directory and covers task switches and parameters, account configuration with account-specific task overrides, shared battle configurations, and window/device selections.

## 1. Where to find it

Open the Capture page of the application. The button row of the Debug card contains two buttons:

- Export Config
- Import Config

## 2. Export Config

1. Click **Export Config**.
2. The application packs the `configs` folder into a zip file saved to the system Downloads folder, named like `ok-ef-config-20260907_010515.zip`.
3. When finished, a "Config Exported" notification appears and the file manager opens with the zip selected.

The exported zip does not include logs or screenshots. The `configs/backup` and `configs/global_config_migration_backup` folders are excluded as well.

## 3. Import Config

Two equivalent entry points are supported:

- Click **Import Config** and pick a zip file in the file dialog (it starts in the Downloads folder).
- Drag a zip file anywhere onto the application main window. When several zips are dropped at once, only the first one is imported.

The zip must contain at least one `.json` config file. Three layouts are recognized:

1. A zip produced by **Export Config** (entries under a `configs/` folder).
2. Config files placed at the zip root.
3. Config files wrapped in a single folder inside the zip (the wrapper folder is stripped on import; entries land directly in `configs`).

Import flow:

1. After you confirm the dialog, running tasks are paused.
2. The current configuration is backed up to `configs/backup/import_backup_<timestamp>/`, then the `configs` folder is overwritten with the imported content (the two backup folders above are preserved).
3. When finished, an "Import Completed" notification appears and a dialog asks whether to restart now.

## 4. Taking effect and rolling back

- Import only changes the config files on disk. The running application keeps using the in-memory configuration until you click **Restart Now** or restart the app manually.
- To roll back, close the application first, copy the files from `configs/backup/import_backup_<timestamp>/` back into `configs`, or import the zip you exported before.
- Export and import between identical or close application versions. Large version gaps may reset individual config items to their defaults.

## 5. FAQ

1. The import button reports "No config files found in the zip archive": the zip contains no `.json` file, or the selected file is not a config package.
2. Dragging a zip does nothing: make sure the dragged file is a `.zip`; input widgets with their own drop handling (such as code editors) do not trigger the import.
3. The export button reports "Export Failed": check that the `configs` folder is not locked by another program (for example an open config file) and retry.
