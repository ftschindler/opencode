import { tool } from "@opencode-ai/plugin"
import { Database } from "bun:sqlite"

function getSessionTitle(sessionID: string): string {
  try {
    const dataDir = process.env.XDG_DATA_HOME || `${process.env.HOME}/.local/share`
    const db = new Database(`${dataDir}/opencode/opencode.db`, { readonly: true })
    const row = db.query("SELECT title FROM session WHERE id = ?")
      .get(sessionID) as { title: string } | null
    db.close()
    return row?.title || sessionID
  } catch {
    return sessionID
  }
}

function buildSudoDisplay(
  sessionID: string, command: string, cwd: string
): string {
  const title = getSessionTitle(sessionID)
  return (
    `opencode -s ${sessionID} [${title}]`
    + ` in ${cwd} wants to execute \`${command}\``
  )
}

export default tool({
  description:
    "Execute commands with sudo -k -A"
    + " (uses GUI password prompt via askpass helper)",
  args: {
    command: tool.schema
      .string()
      .describe("The command to execute with sudo"),
    description: tool.schema
      .string()
      .optional()
      .describe("Description of what the command does"),
    timeout: tool.schema
      .number()
      .optional()
      .describe("Optional timeout in milliseconds"),
    workdir: tool.schema
      .string()
      .optional()
      .describe("The working directory to run the command in"),
  },
  async execute(args, context) {
    const workdir = args.workdir || context.directory
    const timeout = args.timeout || 120000

    const proc = Bun.spawn(
      ['bash', '-c', `sudo -k -A ${args.command}`],
      {
        cwd: workdir,
        env: {
          ...process.env,
          SUDO_ASKPASS:
            `${process.env.HOME}/.local/bin/opencode-askpass`,
          OPENCODE_SUDO_DISPLAY:
            buildSudoDisplay(
              context.sessionID, args.command, workdir
            ),
        },
        stdout: 'pipe',
        stderr: 'pipe',
      }
    )

    const timeoutId = setTimeout(() => proc.kill(), timeout)

    try {
      const exitCode = await proc.exited
      clearTimeout(timeoutId)

      const stdout =
        await Bun.readableStreamToText(proc.stdout)
      const stderr =
        await Bun.readableStreamToText(proc.stderr)

      let output = ''
      if (stdout) output += stdout
      if (stderr) output += stderr
      if (exitCode !== 0) {
        output += `\n[Exit code: ${exitCode}]`
      }

      return output || '[No output]'
    } catch (error) {
      clearTimeout(timeoutId)
      throw error
    }
  },
})
