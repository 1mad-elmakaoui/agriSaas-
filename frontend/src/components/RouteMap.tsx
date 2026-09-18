/**
 * La carte d'un réacheminement : plan actuel, option recommandée, tronçons exposés.
 *
 * Le couplage que la spécification demande va dans un seul sens : **la
 * recommandation pilote la carte**. Sélectionner une option dans le tableau
 * trace son corridor et recadre ; la carte ne modifie jamais en silence ce que
 * le tableau considère comme sélectionné.
 *
 * Le tracé relie les villes en ligne droite — c'est ce que le graphe de
 * référence contient. Suffisant pour reconnaître un corridor, insuffisant pour
 * du guidage, et l'interface ne doit pas laisser croire l'inverse.
 */
import { useEffect, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { ShipmentRisk, TransportAlternative } from '@/lib/types'

const CURRENT_COLOUR = '#868e96'
const RECOMMENDED_COLOUR = '#1b5fa8'
const EXPOSED_COLOUR = '#b3261e'

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

function lineFeature(
  coordinates: number[][],
  properties: Record<string, string>,
): GeoJSON.Feature<GeoJSON.LineString> {
  return { type: 'Feature', properties, geometry: { type: 'LineString', coordinates } }
}

export function RouteMap({
  risk,
  selected,
  height = 380,
}: {
  risk: ShipmentRisk
  selected: TransportAlternative | null
  height?: number
}) {
  const container = useRef<HTMLDivElement | null>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const [ready, setReady] = useState(false)
  // Deux échecs distincts, deux messages distincts. Une carte qui trace les
  // itinéraires sans fond n'est pas une carte absente : dire « elle n'a pas pu
  // s'afficher » alors que les corridors sont visibles décrédibilise l'écran.
  const [renderFailed, setRenderFailed] = useState(false)
  const [tilesFailed, setTilesFailed] = useState(false)

  useEffect(() => {
    if (!container.current || map.current) return
  // Un navigateur sans WebGL — machine de bureau verrouillée, accélération
  // désactivée, pilote ancien — fait échouer le constructeur de MapLibre. Sans
  // garde, l'exception remonte et emporte **toute** la page : l'exploitant perd
  // la recommandation en même temps que la carte, alors que seule la carte est
  // en cause. La décision doit survivre à l'absence de fond.
  let instance: maplibregl.Map
    try {
      instance = new maplibregl.Map({
        container: container.current,
        style: BASEMAP,
        center: [-8.5, 31.6],
        zoom: 6,
        attributionControl: { compact: true },
      })
    } catch {
      setRenderFailed(true)
      return
    }
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }))
    instance.on('load', () => setReady(true))
    instance.on('error', (event) => {
      if (String(event.error?.message ?? '').length > 0) setTilesFailed(true)
    })
    map.current = instance
    return () => {
      instance.remove()
      map.current = null
      setReady(false)
    }
  }, [])

  useEffect(() => {
    const instance = map.current
    if (!instance || !ready) return

    const current = risk.alternatives.find((a) => a.is_current_plan)
    const highlighted =
      selected ?? risk.alternatives.find((a) => a.is_recommended) ?? current ?? null

    const features: GeoJSON.Feature<GeoJSON.LineString>[] = []
    if (current?.path_lonlat.length) {
      features.push(lineFeature(current.path_lonlat, { role: 'current' }))
    }
    if (highlighted && highlighted.id !== current?.id && highlighted.path_lonlat.length) {
      features.push(lineFeature(highlighted.path_lonlat, { role: 'highlighted' }))
    }
    // Les tronçons exposés se superposent au tracé : c'est *où* le camion est
    // pris, pas seulement que l'itinéraire est risqué.
    for (const segment of risk.exposed_segments) {
      features.push(
        lineFeature(
          [
            [segment.longitudes[0] ?? 0, segment.latitudes[0] ?? 0],
            [segment.longitudes[1] ?? 0, segment.latitudes[1] ?? 0],
          ],
          { role: 'exposed' },
        ),
      )
    }

    const collection: GeoJSON.FeatureCollection<GeoJSON.LineString> = {
      type: 'FeatureCollection',
      features,
    }
    const source = instance.getSource('routes') as maplibregl.GeoJSONSource | undefined
    if (source) {
      source.setData(collection)
    } else {
      instance.addSource('routes', { type: 'geojson', data: collection })
      instance.addLayer({
        id: 'routes-line',
        type: 'line',
        source: 'routes',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': [
            'match',
            ['get', 'role'],
            'exposed', EXPOSED_COLOUR,
            'highlighted', RECOMMENDED_COLOUR,
            CURRENT_COLOUR,
          ],
          'line-width': ['match', ['get', 'role'], 'exposed', 7, 'highlighted', 5, 3],
          'line-opacity': ['match', ['get', 'role'], 'current', 0.55, 0.95],
        },
      })
    }

    const points = features.flatMap((feature) => feature.geometry.coordinates)
    if (points.length >= 2) {
      const bounds = points.reduce(
        (acc, point) => acc.extend(point as [number, number]),
        new maplibregl.LngLatBounds(
          points[0] as [number, number],
          points[0] as [number, number],
        ),
      )
      instance.fitBounds(bounds, { padding: 56, duration: 600, maxZoom: 9 })
    }
  }, [risk, selected, ready])

  return (
    <div>
      <div
        ref={container}
        style={{ height }}
        className="w-full overflow-hidden rounded-lg border border-ink-200"
        role="region"
        aria-label="Carte de l’itinéraire"
      />
      {(renderFailed || tilesFailed) && (
        <p className="mt-2 rounded-md border border-dashed border-ink-300 bg-ink-50 px-3 py-2 text-xs text-ink-600">
          {renderFailed
            ? 'La carte n’a pas pu s’afficher sur ce poste. L’analyse, les options et leurs écarts restent complets ci-dessous : seule la représentation géographique manque.'
            : 'Le fond de carte n’a pas pu être chargé : le réseau de cette installation ne joint pas le serveur de tuiles. Les itinéraires ci-dessus sont à leur position réelle ; seul le fond manque.'}
        </p>
      )}
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-600">
        <LegendEntry colour={CURRENT_COLOUR} label="Itinéraire actuel" />
        <LegendEntry colour={RECOMMENDED_COLOUR} label="Itinéraire recommandé" />
        <LegendEntry colour={EXPOSED_COLOUR} label="Tronçon exposé" />
      </ul>
      <p className="mt-1.5 text-xs text-ink-500">
        Tracé schématique reliant les villes du réseau de référence : suffisant pour
        reconnaître un corridor, insuffisant pour du guidage.
      </p>
    </div>
  )
}

function LegendEntry({ colour, label }: { colour: string; label: string }) {
  return (
    <li className="flex items-center gap-1.5">
      <span
        className="h-1 w-5 rounded-full"
        style={{ background: colour }}
        aria-hidden="true"
      />
      {label}
    </li>
  )
}
