import type { ReactNode } from 'react'
import { severityTone, slaTone, tlpTone, type Tone } from './tones'

/** Status text in a coloured pill. The words carry the meaning; colour only reinforces it. */
export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

export function SeverityBadge({ priority, severity }: { priority?: string; severity: string }) {
  return (
    <Badge tone={severityTone(severity)}>{priority ? `${priority} ${severity}` : severity}</Badge>
  )
}

export function SlaBadge({ status, children }: { status: string; children?: ReactNode }) {
  return (
    <Badge tone={slaTone(status)}>
      {status}
      {children}
    </Badge>
  )
}

export function TlpBadge({ tlp }: { tlp: string }) {
  return <Badge tone={tlpTone(tlp)}>TLP:{tlp.toUpperCase()}</Badge>
}
