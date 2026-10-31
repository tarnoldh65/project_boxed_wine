import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import App from './App'

test('renders the shell layout', () => {
  render(<App />)
  expect(screen.getByText('Case Manager')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Cases' })).toBeInTheDocument()
})
