#!/usr/bin/env bash
# Restauration d'une sauvegarde AtlasAgri dans une base **vide**.
#
# Dans une base vide, et non par-dessus une base existante : `pg_restore` ne
# supprime rien, et restaurer sur des tables peuplées produit des doublons là où
# aucune contrainte ne les interdit. Le script refuse donc une base non vide
# plutôt que d'ajouter `--clean`, qui détruirait les données en place sur une
# faute de frappe.
#
# Après restauration, les rôles n'ont **pas** de mot de passe : la sauvegarde
# n'en transporte pas. Lancez `python -m app.cli set-role-passwords`.
set -euo pipefail

DUMP="${1:-}"
DSN="${ATLAS_RESTORE_DSN:-${2:-}}"
ROLES="${3:-${DUMP%-atlas.dump}-roles.sql}"

if [[ -z "${DUMP}" || -z "${DSN}" ]]; then
    echo "usage: $0 <dump> <admin-dsn> [roles.sql]" >&2
    exit 2
fi
[[ -f "${DUMP}" ]] || { echo "no such dump: ${DUMP}" >&2; exit 1; }

existing=$(psql --dbname="${DSN}" -Atc \
    "SELECT count(*) FROM pg_namespace WHERE nspname IN ('app', 'analytics')")
if [[ "${existing}" != "0" ]]; then
    echo "refusing to restore: the target database already has an app or" >&2
    echo "analytics schema. Restore into an empty database." >&2
    exit 1
fi

if [[ -f "${ROLES}" ]]; then
    # `ON_ERROR_STOP` volontairement absent : les rôles peuvent déjà exister sur
    # la grappe — ce sont des objets partagés — et un « role already exists »
    # n'est pas un échec de restauration.
    psql --dbname="${DSN}" --quiet --file="${ROLES}" 2>&1 | grep -v "already exists" || true
    echo "  roles applied from $(basename "${ROLES}")"
else
    echo "  no roles file: the restore will fail if the roles are absent" >&2
fi

# La propriété est **restaurée**, pas neutralisée : `--no-owner` rendrait les
# tables au rôle qui restaure, et `FORCE ROW LEVEL SECURITY` cesserait de
# s'appliquer au propriétaire. C'est-à-dire que l'isolation entre organisations
# survivrait au sinistre sous une forme décorative.
pg_restore --dbname="${DSN}" --exit-on-error "${DUMP}"

echo "  restored $(basename "${DUMP}")"
psql --dbname="${DSN}" -Atc \
    "SELECT 'tenants=' || count(*) FROM app.tenants" 
echo "Set the role passwords before starting the application:"
echo "  ATLAS_APP_ROLE_PASSWORD=… python -m app.cli set-role-passwords"
