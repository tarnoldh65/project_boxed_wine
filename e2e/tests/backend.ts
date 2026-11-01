import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

// E2E_TARGET=kind runs against the Helm deployment on the local kind cluster (deploy/kind/up.sh).
const KIND = process.env.E2E_TARGET === 'kind'
const COMPOSE = fileURLToPath(new URL('../../deploy/compose.yaml', import.meta.url))
const EXEC = KIND
  ? ['kubectl', '-n', 'casemgr', 'exec', 'deploy/casemgr-backend', '--']
  : ['docker', 'compose', '-f', COMPOSE, 'exec', '-T', 'backend']

/** The intake mailbox's host name as the backend sees it. */
export const GREENMAIL = KIND ? 'greenmail.casemgr-dev' : 'greenmail'

/** Run a command in a backend container and return its output. */
export function inBackend(...args: string[]): string {
  const [command, ...rest] = EXEC
  return execFileSync(command, [...rest, ...args], { encoding: 'utf8' })
}

export function runJob(name: string): string {
  return inBackend('python', '-m', 'app.jobs', name)
}
