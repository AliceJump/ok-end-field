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

### Endfield standalone startup with official launcher fallback

CN and GRYPHLINK identify their game channel using `Endfield_Data/app.info` (`Hypergryph` or `Gryphline`), never by guessing from installation folder names. Startup is attempted in this order:

1. **Game-bundled signed helper (experimental, first choice):** inspect `PlatformProcess.exe`, then `UnityCrashHandler64.exe`, next to `Endfield.exe`. Require a valid Windows Authenticode signature and a certificate fingerprint in the existing official allowlist. **Copy** the trusted program to a private, per-launch UUID directory as `Games.exe`, verify the copied image again, and create it suspended as the game's immediate parent. This does not modify game files or require an installed official launcher.
2. **Existing official launcher fallback:** when no valid bundled helper is available, preparation fails, or a failure happens **before** a game process is created, try the current game's official `Games.exe` type, then the other known official type. Existing launcher discovery (adjacent paths, registry, cache) still verifies Authenticode, the official fingerprint and the launcher product name. This fallback needs at least one installed official launcher, but never clicks Start Game.

Both sources reuse the existing process creation, arguments and cleanup behaviour. **Once a game process has been created, later errors, missing log signals or SDK initialization failures never trigger a second game launch.** Already-running games are left alone. Steam and Epic remain routed through their own platform URIs without either temporary-parent strategy; unsupported custom arguments still fail explicitly.

Both game channels watch only fresh complete lines in `LocalAppDataLow/<company>/Endfield/sdklogs/OneChannelLC.log`. An owned temporary parent is released early only after `Parent process check success:<PID>` matches that launch's actual parent PID. This indicates the parent check succeeded, not that login or loading completed. If the signal is missing at 120 seconds, the parent is kept until the game exits; responsiveness or a fixed grace period is not treated as proof of SDK acceptance.

The existing `owned_temporary_launchers/` journal and crash recovery still check PID, creation time, complete image path and live descendants. Copies of bundled helpers reside at `endfield_parent_images/<UUID>/Games.exe`; they are removed only once their owned parent has terminated. Failed deletion retains the journal so it can be retried, and no unrelated launcher or foreign files are removed.

**Testing status:** the game-bundled route has not been live-validated on CN or GRYPHLINK and may not pass the parent check in every game version. Testers should report the selected image, `Parent process check success`, login and automation results, and redacted logs. Inspired by [Starward's standalone launcher commit](https://github.com/NagiYume/Starward/commit/79c310a0e89640d8d387c98e15861ecb6a765d37); its forced UAC elevation and window-responsive-plus-30-second cleanup are intentionally not copied.

If channel identity cannot be determined, or neither the bundled helper nor installed official launchers can be used, repair the installation or launch through its client manually. Check `logs/ok-script.log` for errors.

## Bug reports and feedback

Open a [GitHub Issue](https://github.com/AliceJump/ok-end-field/issues) with a clear screenshot or recording, relevant redacted logs, reproduction steps, actual and expected behaviour, and the application version. Do not upload account details, personal paths, keys, or other sensitive information.
