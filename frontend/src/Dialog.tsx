import { useEffect, useRef, type ReactNode } from 'react'

/** A modal dialog, open while mounted. Escape closes it through onClose. */
export function Dialog({
  title,
  onClose,
  children,
}: {
  title: string
  onClose: () => void
  children: ReactNode
}) {
  const dialog = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const element = dialog.current!
    if (!element.open) element.showModal()
    return () => element.close()
  }, [])

  return (
    <dialog ref={dialog} className="dialog" aria-label={title} onClose={onClose}>
      <h3>{title}</h3>
      {children}
    </dialog>
  )
}
