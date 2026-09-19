#!/usr/bin/env bash
# Sauvegarde d'une installation AtlasAgri.
#
# Produit **deux** fichiers, et les deux sont nécessaires :
#
#   <horodatage>-roles.sql   les rôles de la grappe, sans mot de passe
#   <horodatage>-atlas.dump  la base, format personnalisé (schéma + données)
#
# Les rôles sont des objets de **grappe**, pas de base : un `pg_dump` seul ne
# les contient pas, et la restauration échoue sur le premier `GRANT` adressé à
# un rôle inexistant. Ce n'est pas un détail de forme : la propriété des tables
# par `atlas_owner` est ce qui rend `FORCE ROW LEVEL SECURITY` opérant, donc une
# restauration qui perd les rôles perd l'isolation entre organisations.
#
# `--no-role-passwords` : une sauvegarde ne transporte pas de secret
# d'authentification. Après restauration, reposez-les avec
# `python -m app.cli set-role-passwords`.
#
# Les messages sont en anglais : seul un opérateur les lit.
set -euo pipefail

DSN="${ATLAS_BACKUP_DSN:-${1:-}}"
OUT_DIR="${ATLAS_BACKUP_DIR:-${2:-./backups}}"

if [[ -z "${DSN}" ]]; then
    echo "usage: ATLAS_BACKUP_DSN=postgresql://user@host/atlas $0 [dsn] [out-dir]" >&2
    echo "The DSN must be an administrative role: pg_dump reads every table," >&2
    echo "and the application role is confined by row level security." >&2
    exit 2
fi

mkdir -p "${OUT_DIR}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ROLES="${OUT_DIR}/${STAMP}-roles.sql"
DUMP="${OUT_DIR}/${STAMP}-atlas.dump"

pg_dumpall --dbname="${DSN}" --roles-only --no-role-passwords > "${ROLES}"
pg_dump --dbname="${DSN}" --format=custom --file="${DUMP}"

# La taille est vérifiée : un `pg_dump` interrompu laisse un fichier valide et
# court, qu'on découvre vide le jour où on en a besoin.
for file in "${ROLES}" "${DUMP}"; do
    size=$(stat -c %s "${file}")
    if [[ "${size}" -lt 1024 ]]; then
        echo "backup aborted: ${file} is only ${size} bytes" >&2
        exit 1
    fi
    printf '  %-48s %s bytes\n' "$(basename "${file}")" "${size}"
done

echo "Backup complete. Restore with scripts/restore.sh ${DUMP}"
