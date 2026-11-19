#!/bin/bash
# App-owned isolated mount test.
set -euo pipefail

# Run only in an isolated Linux test container with CAP_SYS_ADMIN. Synthetic
# files only; no HA data, credentials, source backups or database process.
base=$(mktemp -d)
source_dir="${base}/storage"
alias_dir="${base}/alias"
target="${base}/vendor"
mkdir -p "${source_dir}/unifi/data/db" "${source_dir}/uos" "${alias_dir}" "${target}"
printf 'unifi.db.name=ace\n' > "${source_dir}/unifi/data/system.properties"
printf 'original\n' > "${source_dir}/unifi/data/db/sentinel"
printf 'persisted\n' > "${source_dir}/uos/sentinel"
mount --bind "${source_dir}" "${target}"
mount --bind "${target}" "${alias_dir}"
/usr/local/bin/unifi-os-legacy-layout "${alias_dir}/unifi/data" "${target}" "${alias_dir}"
mountpoint -q "${target}"
[[ ! -L "${target}" ]]
cmp "${target}/system.properties" "${source_dir}/unifi/data/system.properties"
[[ "$(cat "${target}/db/sentinel")" == original ]]
[[ "$(cat "${alias_dir}/uos/sentinel")" == persisted ]]
[[ ! -e "${target}/unifi" ]]
[[ "$(stat -c '%d:%i' "${target}")" == "$(stat -c '%d:%i' "${source_dir}/unifi/data")" ]]
before=$(findmnt -n -o ID --mountpoint "${target}")
/usr/local/bin/unifi-os-legacy-layout "${alias_dir}/unifi/data" "${target}" "${alias_dir}"
[[ "$(findmnt -n -o ID --mountpoint "${target}")" == "${before}" ]]
echo "Actual bind mount, preserved storage root and repeat-call checks passed."
