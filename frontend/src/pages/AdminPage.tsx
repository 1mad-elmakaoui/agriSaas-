/**
 * Administration — l'organisation, ses membres, son journal, sa conformité.
 *
 * Quatre panneaux, dont trois correspondent à des droits opposables (loi 09-08)
 * plutôt qu'à des fonctions de confort : consulter le journal, exporter,
 * supprimer.
 *
 * Trois partis pris que l'écran porte :
 *
 * **Un refus vient du serveur.** Une personne qui n'est pas administratrice voit
 * le message du serveur, pas un panneau vide ni une section masquée : un écran
 * qui cache ses commandes laisse croire qu'elles n'existent pas.
 *
 * **Ce qui n'est pas couvert est affiché avec ce qui l'est.** La page de
 * conformité rend des déclarations *et* des limites, et les deux listes sont
 * montrées côte à côte. Une page qui n'afficherait que les cases cochées ne
 * servirait à personne, et surtout pas à celui qui doit répondre devant la CNDP.
 *
 * **La suppression demande de recopier l'identifiant.** Une case à cocher se
 * coche par réflexe.
 */
import { useState } from 'react'
import { RequestFailed, api, storeToken } from '@/lib/api'
import { useResource } from '@/lib/useResource'
import type { AuditEntry, Member, UserRole } from '@/lib/types'
import { dateTime } from '@/lib/format'
import { PageHeader } from '@/components/Shell'
import { Button, Disclosure, ErrorNotice, Loading, Panel } from '@/components/primitives'

const ROLES: { value: UserRole; label: string }[] = [
  { value: 'ADMIN', label: 'Administrateur' },
  { value: 'SUPPLY_CHAIN_MANAGER', label: "Responsable chaîne d'approvisionnement" },
  { value: 'OPERATIONS_MANAGER', label: "Responsable d'exploitation" },
  { value: 'AGRONOME', label: 'Agronome' },
  { value: 'ANALYST', label: 'Analyste' },
  { value: 'EXECUTIVE', label: 'Direction' },
]

function failure(cause: unknown): { message: string; remedy: string | null } {
  if (cause instanceof RequestFailed) {
    return { message: cause.message, remedy: cause.remedyFr }
  }
  return {
    message: 'Le serveur est momentanément injoignable.',
    remedy: 'Réessayez dans quelques instants.',
  }
}

function MemberRow({
  member,
  busy,
  onRole,
  onDelete,
}: {
  member: Member
  busy: boolean
  onRole: (role: UserRole) => void
  onDelete: () => void
}) {
  return (
    <tr className="border-t border-ink-100">
      <td className="py-2 pr-3">
        <p className="text-sm text-ink-900">{member.full_name}</p>
        <p className="text-xs text-ink-500">{member.email}</p>
      </td>
      <td className="py-2 pr-3">
        <label className="sr-only" htmlFor={`role-${member.id}`}>
          Rôle de {member.full_name}
        </label>
        <select
          id={`role-${member.id}`}
          value={member.role}
          disabled={busy}
          onChange={(event) => onRole(event.target.value as UserRole)}
          className="focusable rounded border border-ink-300 bg-white px-2 py-1 text-sm text-ink-800"
        >
          {ROLES.map((role) => (
            <option key={role.value} value={role.value}>
              {role.label}
            </option>
          ))}
        </select>
      </td>
      <td className="py-2 pr-3 text-xs text-ink-500">{dateTime(member.created_at)}</td>
      <td className="py-2 text-right">
        <Button onClick={onDelete} disabled={busy}>
          Supprimer
        </Button>
      </td>
    </tr>
  )
}

function JournalRow({ entry }: { entry: AuditEntry }) {
  return (
    <tr className="border-t border-ink-100 align-top">
      <td className="py-1.5 pr-3 text-xs tabular-nums text-ink-500">
        {dateTime(entry.occurred_at)}
      </td>
      <td className="py-1.5 pr-3 text-xs text-ink-700">
        {/* « — » plutôt qu'un nom inventé : un acte sans auteur identifié est
            une connexion refusée ou un compte supprimé, pas « le système ». */}
        {entry.actor_email ?? '—'}
      </td>
      <td className="py-1.5 pr-3 text-xs font-medium text-ink-800">{entry.action}</td>
      <td className="py-1.5 pr-3 text-xs text-ink-600">
        {entry.resource_type}
        {entry.resource_id && <span className="text-ink-400"> · {entry.resource_id}</span>}
      </td>
      <td className="py-1.5 text-xs text-ink-600">{entry.outcome}</td>
    </tr>
  )
}

function AddMember({
  busy,
  onAdd,
}: {
  busy: boolean
  onAdd: (draft: {
    email: string
    full_name: string
    role: UserRole
    password: string
  }) => void
}) {
  const [email, setEmail] = useState('')
  const [fullName, setFullName] = useState('')
  const [role, setRole] = useState<UserRole>('AGRONOME')
  const [password, setPassword] = useState('')

  return (
    <form
      className="grid gap-3 sm:grid-cols-2"
      onSubmit={(event) => {
        event.preventDefault()
        onAdd({ email, full_name: fullName, role, password })
        setEmail('')
        setFullName('')
        setPassword('')
      }}
    >
      <div>
        <label htmlFor="member-email" className="block text-xs text-ink-600">
          Adresse électronique
        </label>
        <input
          id="member-email"
          type="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          className="focusable mt-1 w-full rounded border border-ink-300 px-2.5 py-1.5 text-sm"
        />
      </div>
      <div>
        <label htmlFor="member-name" className="block text-xs text-ink-600">
          Nom complet
        </label>
        <input
          id="member-name"
          required
          value={fullName}
          onChange={(event) => setFullName(event.target.value)}
          className="focusable mt-1 w-full rounded border border-ink-300 px-2.5 py-1.5 text-sm"
        />
      </div>
      <div>
        <label htmlFor="member-role" className="block text-xs text-ink-600">
          Rôle
        </label>
        <select
          id="member-role"
          value={role}
          onChange={(event) => setRole(event.target.value as UserRole)}
          className="focusable mt-1 w-full rounded border border-ink-300 bg-white px-2.5 py-1.5 text-sm"
        >
          {ROLES.map((entry) => (
            <option key={entry.value} value={entry.value}>
              {entry.label}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor="member-password" className="block text-xs text-ink-600">
          Mot de passe provisoire
        </label>
        <input
          id="member-password"
          type="password"
          required
          minLength={12}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          className="focusable mt-1 w-full rounded border border-ink-300 px-2.5 py-1.5 text-sm"
        />
      </div>
      <div className="sm:col-span-2">
        {/* Dit ici, parce que c'est ici qu'on croirait le contraire : la
            plateforme n'a aucun acheminement de courrier, et annoncer
            « invitation envoyée » sans rien envoyer serait la pire option. */}
        <p className="mb-2 text-xs text-ink-500">
          Aucun courriel n'est envoyé : transmettez ce mot de passe à la personne par
          un autre canal, et demandez-lui de le changer.
        </p>
        <Button type="submit" disabled={busy}>
          Ajouter le membre
        </Button>
      </div>
    </form>
  )
}

export function AdminPage() {
  const organisation = useResource(() => api.organisation())
  const journal = useResource(() => api.auditJournal(50))
  const compliance = useResource(() => api.compliance())

  const [error, setError] = useState<{ message: string; remedy: string | null } | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirmation, setConfirmation] = useState('')

  async function act(run: () => Promise<string | null>) {
    setError(null)
    setNotice(null)
    setBusy(true)
    try {
      const message = await run()
      if (message) setNotice(message)
      organisation.reload()
      journal.reload()
    } catch (cause) {
      setError(failure(cause))
    } finally {
      setBusy(false)
    }
  }

  function download() {
    void act(async () => {
      const payload = await api.exportData()
      // Fabriqué dans le navigateur à partir de la réponse : le point d'entrée
      // exige un jeton, qu'un lien direct ne porterait pas.
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: 'application/json',
      })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `atlasagri-export-${new Date().toISOString().slice(0, 10)}.json`
      link.click()
      URL.revokeObjectURL(url)
      return 'Export téléchargé.'
    })
  }

  return (
    <>
      <PageHeader
        title="Administration"
        subtitle="Membres, journal des accès, conformité et suppression des données."
      />

      {error && <ErrorNotice message={error.message} remedy={error.remedy} />}
      {notice && (
        <p className="mb-4 rounded-md border border-ink-200 bg-ink-50 px-4 py-2 text-sm text-ink-700">
          {notice}
        </p>
      )}

      <div className="space-y-5">
        <Panel title="Organisation">
          {organisation.loading && <Loading label="Chargement de l'organisation…" />}
          {organisation.error && (
            <ErrorNotice
              message={organisation.error.message}
              remedy={organisation.error.remedy}
            />
          )}
          {organisation.data && (
            <>
              <dl className="mb-4 grid gap-3 text-sm sm:grid-cols-3">
                <div>
                  <dt className="text-xs text-ink-500">Nom</dt>
                  <dd className="text-ink-900">{organisation.data.name}</dd>
                </div>
                <div>
                  <dt className="text-xs text-ink-500">Identifiant</dt>
                  <dd className="font-mono text-xs text-ink-700">{organisation.data.slug}</dd>
                </div>
                <div>
                  <dt className="text-xs text-ink-500">Plan</dt>
                  <dd className="text-ink-900">{organisation.data.plan_code}</dd>
                </div>
              </dl>
              <table className="w-full">
                <thead>
                  <tr className="text-left text-xs font-medium text-ink-500">
                    <th className="pb-1.5 pr-3">Membre</th>
                    <th className="pb-1.5 pr-3">Rôle</th>
                    <th className="pb-1.5 pr-3">Depuis</th>
                    <th className="pb-1.5" />
                  </tr>
                </thead>
                <tbody>
                  {organisation.data.members.map((member) => (
                    <MemberRow
                      key={member.id}
                      member={member}
                      busy={busy}
                      onRole={(role) =>
                        void act(async () => {
                          await api.setMemberRole(member.id, role)
                          return `Rôle de ${member.full_name} mis à jour.`
                        })
                      }
                      onDelete={() =>
                        void act(async () => {
                          const result = await api.deleteMember(member.id)
                          return result.message_fr
                        })
                      }
                    />
                  ))}
                </tbody>
              </table>

              <div className="mt-4 border-t border-ink-100 pt-4">
                <AddMember
                  busy={busy}
                  onAdd={(draft) =>
                    void act(async () => {
                      await api.addMember(draft)
                      return `${draft.full_name} a été ajouté à l'organisation.`
                    })
                  }
                />
              </div>
            </>
          )}
        </Panel>

        <Panel
          title="Journal des accès"
          subtitle="Qui a vu quoi, qui a approuvé quoi, qui a changé quel plan. Consulter ce journal y est inscrit."
        >
          {journal.loading && <Loading label="Chargement du journal…" />}
          {journal.error && (
            <ErrorNotice message={journal.error.message} remedy={journal.error.remedy} />
          )}
          {journal.data && journal.data.length === 0 && (
            <p className="text-sm text-ink-500">Aucun évènement enregistré.</p>
          )}
          {journal.data && journal.data.length > 0 && (
            <div className="max-h-96 overflow-y-auto">
              <table className="w-full">
                <thead>
                  <tr className="text-left text-xs font-medium text-ink-500">
                    <th className="pb-1.5 pr-3">Quand</th>
                    <th className="pb-1.5 pr-3">Qui</th>
                    <th className="pb-1.5 pr-3">Action</th>
                    <th className="pb-1.5 pr-3">Ressource</th>
                    <th className="pb-1.5">Issue</th>
                  </tr>
                </thead>
                <tbody>
                  {journal.data.map((entry, index) => (
                    <JournalRow key={`${entry.occurred_at}-${index}`} entry={entry} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel
          title="Conformité — loi 09-08 et CNDP"
          subtitle="Ce que cette installation déclare, et ce qu'elle ne couvre pas."
        >
          {compliance.loading && <Loading label="Chargement…" />}
          {compliance.error && (
            <ErrorNotice
              message={compliance.error.message}
              remedy={compliance.error.remedy}
            />
          )}
          {compliance.data && (
            <>
              <dl className="grid gap-3 text-sm sm:grid-cols-2">
                <div>
                  <dt className="text-xs text-ink-500">Résidence des données</dt>
                  {/* « Non déclarée » plutôt que « Maroc » : le code ne peut pas
                      savoir où tourne sa base, et l'inventer serait une
                      affirmation de conformité que personne n'a vérifiée. */}
                  <dd className="text-ink-900">
                    {compliance.data.residency_country ?? 'Non déclarée'}
                    {compliance.data.residency_provider && (
                      <span className="text-ink-500"> · {compliance.data.residency_provider}</span>
                    )}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-ink-500">Déclaration CNDP</dt>
                  <dd className="text-ink-900">
                    {compliance.data.cndp_declaration_number ?? 'Aucune'}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-ink-500">Conservation du journal</dt>
                  <dd className="text-ink-900">
                    {compliance.data.audit_retention_days} jours
                    <span className="text-ink-500">
                      {compliance.data.retention_enforced
                        ? ' · appliquée'
                        : ' · annoncée, non appliquée'}
                    </span>
                  </dd>
                </div>
              </dl>

              <ul className="mt-4 space-y-1.5 text-sm text-ink-700">
                {compliance.data.statements_fr.map((statement) => (
                  <li key={statement}>· {statement}</li>
                ))}
              </ul>

              <Disclosure summary="Ce qui n'est pas couvert">
                <ul className="space-y-1.5 text-sm text-ink-700">
                  {compliance.data.limitations_fr.map((limitation) => (
                    <li key={limitation}>· {limitation}</li>
                  ))}
                </ul>
              </Disclosure>
            </>
          )}
        </Panel>

        <Panel
          title="Vos données"
          subtitle="Obtenir une copie, ou tout faire effacer."
        >
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={download} disabled={busy} variant="primary">
              Exporter toutes les données
            </Button>
            <p className="text-xs text-ink-500">
              Copie JSON de toutes vos tables. Les mots de passe en sont retirés.
            </p>
          </div>

          <Disclosure summary="Supprimer définitivement l'organisation">
            <p className="text-sm text-ink-700">
              Toutes les parcelles, mesures, expéditions, recommandations, comptes et
              lignes de journal seront effacés. Cette action est irréversible et la
              plateforme ne conserve aucune sauvegarde.
            </p>
            <label className="mt-3 block text-sm text-ink-700" htmlFor="confirmation">
              Recopiez l'identifiant de l'organisation
              {organisation.data && (
                <span className="font-mono text-xs text-ink-900"> ({organisation.data.slug})</span>
              )}{' '}
              pour confirmer :
            </label>
            <div className="mt-1.5 flex flex-wrap gap-2">
              <input
                id="confirmation"
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
                className="focusable w-64 rounded border border-ink-300 px-2.5 py-1.5 text-sm"
              />
              <Button
                disabled={busy || confirmation.trim().length === 0}
                onClick={() =>
                  void act(async () => {
                    const receipt = await api.deleteOrganisation(confirmation.trim())
                    // Plus rien à afficher : la session ne désigne plus rien.
                    storeToken(null)
                    window.location.assign('/')
                    return receipt.message_fr
                  })
                }
              >
                Supprimer définitivement
              </Button>
            </div>
          </Disclosure>
        </Panel>
      </div>
    </>
  )
}
