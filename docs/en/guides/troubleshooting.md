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

### CN game requires the official launcher or times out

Recent CN SDK versions check the game's immediate parent process. In this **experimental alternative to PR #558**, the app locates and verifies the official Hypergryph `Games.exe` and creates its **own suspended temporary instance** as the game's parent. It does not stop or reuse existing user launcher processes, and does not click "Start Game". Keep the official launcher installed and the game executable selected as the game path.

The app does not inject CN language or channel arguments into `Endfield.exe`; existing custom launch arguments, including DX11 options, are preserved unchanged. This launch method targets the official CN launcher; international and other channel versions have not been validated. An already running game is left running.

After creating the game, the app reads only fresh complete lines from `OneChannelLC.log` and `u8sdk_pc.log` under `LocalAppDataLow/Hypergryph/Endfield/sdklogs`. It terminates **only its own temporary `Games.exe`** after observing both `Parent process check success:<temporary PID>` and `OnU8V2InitCallback status :0`. It does not use a fixed delay to claim success. If logs are missing or invalid after the 120-second observation window, it keeps the temporary process suspended while the game runs and monitors for game exit instead of forcing cleanup on timeout. Once the game exits, it discards its own parent. Errors before game creation or a confirmed game exit before monitoring starts also discard only the owned temporary process. If the primary monitor cannot start while the game is running, a fallback watcher takes ownership. If thread creation also fails, the launch call may wait synchronously for verified startup or game exit rather than abandon the process handle. If game liveness cannot be monitored, manual cleanup may still be necessary. On the next OK-EF start (or game launch attempt), the app also checks persisted records of suspended parents it created earlier. It reclaims one only when the original OK-EF owner has exited, the exact process identity (PID, creation time and executable path) still matches, and no recorded game or child process is running. It never terminates a launcher by name alone; ambiguous or inaccessible records are preserved with a warning. Records are stored under the app's configuration folder in `owned_temporary_launchers/`. For startup failures check `logs/ok-script.log` and ensure the official launcher installation is intact.

## Bug reports and feedback

Open a [GitHub Issue](https://github.com/AliceJump/ok-end-field/issues) with a clear screenshot or recording, relevant redacted logs, reproduction steps, actual and expected behaviour, and the application version. Do not upload account details, personal paths, keys, or other sensitive information.
