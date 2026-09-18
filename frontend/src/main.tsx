import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { App } from './App'
import { LocaleProvider } from './i18n'
import './index.css'

const container = document.getElementById('root')
if (container === null) throw new Error('Racine introuvable.')

createRoot(container).render(
  <StrictMode>
    <BrowserRouter>
      <LocaleProvider>
        <App />
      </LocaleProvider>
    </BrowserRouter>
  </StrictMode>,
)
