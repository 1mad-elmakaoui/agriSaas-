/**
 * The single operational surface: parcelles, sites, and the selected shipment.
 *
 * MapLibre with a plain raster basemap. PMTiles is the intended vector source
 * and it is **not** wired: no tile archive is hosted in this environment, and
 * pointing at a public style would put a network dependency behind a screen that
 * must work in a field office. That gap is in the honesty ledger rather than
 * hidden behind a spinner.
 *
 * The coupling the specification asks for runs one way: the recommendation
 * drives the map. Selecting a parcelle in the list moves the map; the map never
 * silently changes what the list considers selected.
 */
import { useEffect, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { Field, Recommendation } from '@/lib/types'

/** Semantic colours, resolved here because MapLibre needs literals. */
const MARKER_COLOUR: Record<Recommendation | 'unknown' | 'blocked', string> = {
  IRRIGATE: '#c2570f',
  MONITOR: '#b8860b',
  NO_IRRIGATION: '#2f7d4f',
  POSTPONE_RAIN: '#1b5fa8',
  unknown: '#868e96',
  // A parcelle that cannot be computed stays on the map in grey with its
  // reason, rather than disappearing.
  blocked: '#ced4da',
}

const LEGEND: { colour: string; label: string }[] = [
  { colour: MARKER_COLOUR.IRRIGATE, label: 'Irrigation recommandée' },
  { colour: MARKER_COLOUR.MONITOR, label: 'À surveiller' },
  { colour: MARKER_COLOUR.NO_IRRIGATION, label: 'Pas d’irrigation nécessaire' },
  { colour: MARKER_COLOUR.POSTPONE_RAIN, label: 'À reporter (pluie prévue)' },
  { colour: MARKER_COLOUR.blocked, label: 'Recommandation impossible' },
]

const BASEMAP: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© OpenStreetMap',
    },
  },
  layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
}

export function OperationalMap({
  fields,
  recommendations,
  selectedCode,
  onSelect,
  height = 420,
}: {
  fields: Field[]
  recommendations?: Record<string, Recommendation>
  selectedCode?: string | null
  onSelect?: (code: string) => void
  height?: number
}) {
  const container = useRef<HTMLDivElement | null>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const markers = useRef<maplibregl.Marker[]>([])
  // A basemap that silently fails to load leaves a grey rectangle, which reads
  // as « the map is broken » or, worse, as « there is nothing there ». The
  // parcelle markers still work without tiles, so the honest state is: markers
  // shown, background missing, and say which.
  const [renderFailed, setRenderFailed] = useState(false)
  const [tilesFailed, setTilesFailed] = useState(false)

  useEffect(() => {
    if (!container.current || map.current) return
  // Un navigateur sans WebGL — machine de bureau verrouillée, accélération
  // désactivée, pilote ancien — fait échouer le constructeur de MapLibre. Sans
  // garde, l'exception remonte et emporte **toute** la page : l'exploitant perd
  // la recommandation en même temps que la carte, alors que seule la carte est
  // en cause. La décision doit survivre à l'absence de fond.
  try {
      map.current = new maplibregl.Map({
        container: container.current,
        style: BASEMAP,
        center: [-9.2, 30.42],
        zoom: 8,
        attributionControl: { compact: true },
      })
    } catch {
      setRenderFailed(true)
      return
    }
    map.current.addControl(new maplibregl.NavigationControl({ showCompass: false }))
    map.current.on('error', (event) => {
      // Fired per failed tile request; one notice is enough.
      if (String(event.error?.message ?? '').length > 0) setTilesFailed(true)
    })
    return () => {
      map.current?.remove()
      map.current = null
    }
  }, [])

  useEffect(() => {
    const instance = map.current
    if (!instance) return

    markers.current.forEach((marker) => marker.remove())
    markers.current = []

    fields.forEach((field) => {
      const status = field.blocked_reason_fr
        ? 'blocked'
        : (recommendations?.[field.code] ?? 'unknown')
      const element = document.createElement('button')
      element.type = 'button'
      element.setAttribute('aria-label', `${field.code} — ${field.name_fr}`)
      element.style.cssText = [
        `background:${MARKER_COLOUR[status]}`,
        'width:16px',
        'height:16px',
        'border-radius:9999px',
        'border:2px solid white',
        'box-shadow:0 1px 3px rgb(0 0 0 / .35)',
        'cursor:pointer',
        'padding:0',
      ].join(';')
      if (selectedCode === field.code) {
        element.style.width = '24px'
        element.style.height = '24px'
        element.style.borderWidth = '3px'
      }
      element.addEventListener('click', () => onSelect?.(field.code))

      // `textContent`, never markup: a parcelle name is typed by a farmer, and
      // this popup is outside React's escaping.
      const popupBody = document.createElement('div')
      const title = document.createElement('strong')
      title.textContent = `${field.code} — ${field.name_fr}`
      const detail = document.createElement('div')
      detail.textContent =
        field.blocked_reason_fr ?? `${field.crop_name_fr ?? 'Culture inconnue'}`
      popupBody.append(title, detail)

      const marker = new maplibregl.Marker({ element })
        .setLngLat([field.longitude, field.latitude])
        .setPopup(new maplibregl.Popup({ offset: 14 }).setDOMContent(popupBody))
        .addTo(instance)
      markers.current.push(marker)
    })
  }, [fields, recommendations, selectedCode, onSelect])

  useEffect(() => {
    const instance = map.current
    if (!instance || !selectedCode) return
    const field = fields.find((f) => f.code === selectedCode)
    if (field) instance.flyTo({ center: [field.longitude, field.latitude], zoom: 12 })
  }, [selectedCode, fields])

  return (
    <div>
      <div
        ref={container}
        style={{ height }}
        className="w-full overflow-hidden rounded-lg border border-ink-200"
        role="region"
        aria-label="Carte opérationnelle"
      />
      {(renderFailed || tilesFailed) && (
        <p className="mt-2 rounded-md border border-dashed border-ink-300 bg-ink-50 px-3 py-2 text-xs text-ink-600">
          {renderFailed
            ? 'La carte n’a pas pu s’afficher sur ce poste. Les parcelles restent listées avec leur recommandation.'
            : 'Le fond de carte n’a pas pu être chargé : le réseau de cette installation ne joint pas le serveur de tuiles. Les parcelles ci-dessus sont à leur position réelle ; seul le fond manque.'}
        </p>
      )}
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {LEGEND.map((entry) => (
          <li key={entry.label} className="flex items-center gap-1.5 text-xs text-ink-600">
            <span
              className="h-2.5 w-2.5 rounded-full border border-white shadow-sm"
              style={{ background: entry.colour }}
              aria-hidden="true"
            />
            {entry.label}
          </li>
        ))}
      </ul>
    </div>
  )
}
