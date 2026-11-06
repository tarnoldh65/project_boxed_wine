import { useEffect, useRef, useState, type ReactNode } from 'react'

/** A button that opens a small panel of actions. Escape or a click elsewhere closes it. */
export function Menu({
  label,
  trigger,
  children,
}: {
  label: string
  trigger: ReactNode
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    function clickAway(event: MouseEvent) {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
    }
    function escape(event: KeyboardEvent) {
      if (event.key !== 'Escape') return
      setOpen(false)
      button.current?.focus()
    }
    document.addEventListener('mousedown', clickAway)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', clickAway)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  return (
    <div className="menu" ref={root}>
      <button
        ref={button}
        type="button"
        className="ghost"
        aria-label={label}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {trigger}
      </button>
      {open && <div className="menu-panel">{children}</div>}
    </div>
  )
}
