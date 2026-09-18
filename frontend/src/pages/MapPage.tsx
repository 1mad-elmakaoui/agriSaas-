/**
 * Carte — the operational surface on its own, at full height.
 *
 * Same component as the parcelles page: one map, not two implementations that
 * would drift apart in colour and behaviour.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/lib/api'
import { useResource } from '@/lib/useResource'
import { PageHeader } from '@/components/Shell'
import { OperationalMap } from '@/components/OperationalMap'
import { ErrorNotice, Loading, NotDelivered, Panel } from '@/components/primitives'

export function MapPage() {
  const { data, loading, error } = useResource(() => api.fields())
  const [selected, setSelected] = useState<string | null>(null)
  const navigate = useNavigate()

  if (loading) return <Loading label="Chargement de la carte…" />
  if (error) return <ErrorNotice message={error.message} remedy={error.remedy} />

  return (
    <>
      <PageHeader
        title="Carte"
        subtitle="Parcelles de l’organisation. Cliquez une parcelle pour ouvrir sa recommandation."
      />
      <OperationalMap
        fields={data ?? []}
        selectedCode={selected}
        onSelect={(code) => {
          setSelected(code)
          navigate(`/parcelles/${code}`)
        }}
        height={560}
      />
      <div className="mt-5">
        <Panel title="Couches non disponibles">
          <NotDelivered>
            Itinéraires, tronçons exposés et zones de risque ne sont pas affichés :
            le moteur de risque logistique n’est pas livré. Le fond de carte est un
            fond raster public ; les tuiles vectorielles PMTiles prévues par la
            spécification ne sont pas hébergées dans cette installation.
          </NotDelivered>
        </Panel>
      </div>
    </>
  )
}
