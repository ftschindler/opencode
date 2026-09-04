# tools

## sudo

This tool (see [sudo.ts](sudo.ts)) allows opencode to execute commands with [sudo](https://en.wikipedia.org/wiki/Sudo)
on Linux machines. It does so with a popup that let's me inspect the command and the reason
for running it. Without the tool, opencode would execute `sudo pacman ...` and time out due to
the non-interactive shell of the subprocess.
