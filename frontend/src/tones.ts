export type Tone = 'critical' | 'high' | 'medium' | 'low' | 'ok' | 'neutral'

// Tenants can rename severities; a name not listed here shows as neutral.
const SEVERITY_TONES: Record<string, Tone> = {
  critical: 'critical',
  high: 'high',
  medium: 'medium',
  low: 'low',
}
const SLA_TONES: Record<string, Tone> = {
  breached: 'critical',
  late: 'high',
  warning: 'medium',
  met: 'ok',
}
const TLP_TONES: Record<string, Tone> = {
  red: 'critical',
  'amber+strict': 'high',
  amber: 'medium',
  green: 'ok',
}

export function severityTone(severity: string): Tone {
  return SEVERITY_TONES[severity] ?? 'neutral'
}

export function slaTone(status: string): Tone {
  return SLA_TONES[status] ?? 'neutral'
}

export function tlpTone(tlp: string): Tone {
  return TLP_TONES[tlp] ?? 'neutral'
}
