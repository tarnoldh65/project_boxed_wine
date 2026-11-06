import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { storedTheme } from './theme'
import { ThemeControl } from './ThemeControl'

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
  delete document.documentElement.dataset.theme
})

test('follows the operating system until a theme is chosen', () => {
  render(<ThemeControl />)
  expect(screen.getByRole('radio', { name: 'System' })).toBeChecked()
  expect(document.documentElement.dataset.theme).toBeUndefined()
})

test('applies and remembers a chosen theme', () => {
  render(<ThemeControl />)
  fireEvent.click(screen.getByRole('radio', { name: 'Dark' }))
  expect(document.documentElement.dataset.theme).toBe('dark')
  expect(storedTheme()).toBe('dark')
})

test('returning to System hands control back to the operating system', () => {
  localStorage.setItem('casemgr.theme', 'light')
  document.documentElement.dataset.theme = 'light'
  render(<ThemeControl />)
  expect(screen.getByRole('radio', { name: 'Light' })).toBeChecked()
  fireEvent.click(screen.getByRole('radio', { name: 'System' }))
  expect(document.documentElement.dataset.theme).toBeUndefined()
  expect(storedTheme()).toBe('system')
})

test('ignores a saved value it does not recognize', () => {
  localStorage.setItem('casemgr.theme', 'sepia')
  expect(storedTheme()).toBe('system')
})

test('still works when storage is unavailable', () => {
  const blocked = () => {
    throw new Error('blocked')
  }
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(blocked)
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(blocked)
  render(<ThemeControl />)
  expect(screen.getByRole('radio', { name: 'System' })).toBeChecked()
  fireEvent.click(screen.getByRole('radio', { name: 'Dark' }))
  expect(document.documentElement.dataset.theme).toBe('dark')
  expect(screen.getByRole('radio', { name: 'Dark' })).toBeChecked()
})
