# FAQ and feedback

## Recognition or clicks are unreliable

Check the following in order:

1. The game uses a 16:9 resolution of at least 1600×900 (900P).
2. The game uses default brightness, and graphics filters, sharpening, and overlays are disabled.
3. The game window is not minimized, and the computer is neither locked nor asleep.
4. The mouse and manual input are not interfering with a foreground game window.
5. The game frame rate is stable and the game and application use the same privilege level.

## Installation or startup fails

- Use the latest installer and add the installation directory to your security software's trusted list.
- Avoid running from `Program Files` or a path containing Chinese characters.
- When running from source, run `uv sync` again to install dependencies compatible with the current project.
- Run the terminal, IDE, and application as administrator.

### Official launcher requirements and channel startup

CN and GRYPHLINK share one startup flow. The game installation's `Endfield_Data/app.info` identifies the company as `Hypergryph` or `Gryphline`. Each launcher type uses its own registry lookup. Adjacent launchers and cached paths must pass Windows Authenticode, the shared official fingerprint allowlist and the signed product name check for the type being attempted. The game's own type is preferred; if discovery, validation, temporary-parent creation or game-process creation fails, the failed parent is discarded before trying the other official type. Each type is attempted once. After a game process has been created, later failures or initialization timeouts never cause another launch. Fallback changes only the parent type, retaining the game channel, arguments and original company log directory. Installation folder names and publisher names alone do not select or authorize a channel.

The app creates its own suspended official `Games.exe` as the game's immediate parent, without reusing or stopping user-owned launchers or clicking Start Game. Keep at least one of the official launcher types installed and select `Endfield.exe` as the game path. Both direct-launch paths preserve custom arguments and do not inject language or channel arguments.

Both channels watch only fresh complete records in `LocalAppDataLow/<company>/Endfield/sdklogs/OneChannelLC.log`. Once a current-launch `Parent process check success:<PID>` matches the owned temporary process, the app releases that process immediately. It no longer waits for the later SDK initialization callback in `u8sdk_pc.log`. This confirms the parent check, not account login or complete resource loading. A current GRYPHLINK live test confirmed that SDK initialization still succeeds after this early release; other client versions have not all been validated.

If the success record is missing after 120 seconds, the parent remains suspended until game exit. Both channels share log-error handling, fallback monitoring and exit cleanup. On the next OK-EF start, `owned_temporary_launchers/` recovery records are checked against the original owner's liveness and the exact PID, creation time and executable path. A record is reclaimed only when the game and other children have exited. Ambiguous or inaccessible processes are retained with a warning; the app never kills launchers by name alone.

Standard Steam installations launch through Steam's URI; Epic installations containing `.egstore/` launch through Epic's installation-directory URI. Their platform clients manage startup, without creating the temporary parent above or terminating Steam/Epic. Custom OK-EF arguments that cannot be forwarded cause an explicit error; configure them in the platform client. Complete Steam/Epic real-device startup validation remains outstanding.

If the game company identity is missing or unrecognized, or both official launcher types are unavailable, repair the installation or launch manually through the appropriate client. An already running game is still recognized without duplicate startup. Check `logs/ok-script.log` for startup failures.

## Bug reports and feedback

Open a [GitHub Issue](https://github.com/AliceJump/ok-end-field/issues) with a clear screenshot or recording, relevant redacted logs, reproduction steps, actual and expected behaviour, and the application version. Do not upload account details, personal paths, keys, or other sensitive information.
