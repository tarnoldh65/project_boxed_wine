interface SaveState {
  isPending: boolean
  isSuccess: boolean
  error: Error | null
}

export function SaveBar({ save }: { save: SaveState }) {
  return (
    <div className="save-bar">
      <button type="submit" disabled={save.isPending}>
        Save
      </button>
      {save.isSuccess && <span role="status">Saved</span>}
      {save.error && <p role="alert">{save.error.message}</p>}
    </div>
  )
}
