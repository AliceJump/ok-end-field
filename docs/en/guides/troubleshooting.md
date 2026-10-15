# FAQ and feedback

## Recognition or clicks are unreliable

Check the following in order:

1. The game uses a 16:9 resolution of at least 1920×1080 (1080P).
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

Recent CN SDK versions check the game's immediate parent process. The app verifies the official Hypergryph launcher's digital signature and creates the game with its running `Games.exe` as the parent. If necessary, it opens the launcher using its installation registration. No click on "Start Game" is required. Keep the official launcher installed and keep the game executable selected as the game path.

Default arguments are `-launcher_lang=zh-cn -launcher_sub_channel=1`. Explicit values and other arguments, including DX11 options, are preserved. This launch method targets the official CN launcher; international and other channel versions have not been validated. An already running game is left running.

The app waits up to 120 seconds for the game window during SDK initialization. On failure, check `logs/ok-script.log` for signature, process access, or Windows process creation errors. If the launcher cannot be located, open the official launcher manually and retry.

## Bug reports and feedback

Open a [GitHub Issue](https://github.com/AliceJump/ok-end-field/issues) with a clear screenshot or recording, relevant redacted logs, reproduction steps, actual and expected behaviour, and the application version. Do not upload account details, personal paths, keys, or other sensitive information.
