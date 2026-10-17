#!/usr/bin/env bash
# Copyright 2025-2026 mrveiss
# SPDX-License-Identifier: Apache-2.0
# AutoBot - AI-Powered Automation Platform
# Author: mrveiss
#
# Remove AutoBot from this host (#2706, #18653). Run by `install.sh --uninstall`, or
# directly:   sudo bash /opt/autobot/code_source/scripts/uninstall.sh [--dry-run] [--yes]
#
# Safety rules (#18653):
# - An account is removed only if this host's install record says the installer CREATED
#   it, or it carries the marker AutoBot's own playbooks set on their admin account.
#   Never by name or uid alone: on a reused machine `autobot` can be the owner's account.
# - Packages and apt sources install.sh adds are removed only if the record says it added
#   them. The rest are the components AutoBot's Ansible roles deploy, listed below; the
#   guard repo_tests/uninstall_covers_role_components_18653_test.py fails when a role
#   deploys a unit, sudoers file or apt source this file does not cover.
# - The full plan is printed before anything is touched; without --yes the operator types
#   UNINSTALL to confirm, and a missing terminal refuses rather than proceeding. --dry-run
#   prints the plan and exits.
# - The log lives in /var/tmp, outside every directory this removes: deleting its own log
#   directory mid-run is what used to abort the uninstall halfway (#18653).

set -euo pipefail

# The script deletes the directory it lives in; run from a temporary copy instead.
if [[ "${AUTOBOT_UNINSTALL_COPY:-}" != "1" ]]; then
    copy="$(mktemp /var/tmp/autobot-uninstall.XXXXXX.sh)"
    cp "${BASH_SOURCE[0]}" "${copy}"
    AUTOBOT_UNINSTALL_COPY=1 exec bash "${copy}" "$@"
fi

readonly AUTOBOT_BASE="/opt/autobot"
readonly INSTALL_RECORD="/etc/autobot/install-record"
readonly LOG_FILE="/var/tmp/autobot-uninstall-$(date +%Y%m%d-%H%M%S).log"
# The `comment:` AutoBot's playbooks give the admin account they create
# (enroll-local.yml, setup-backup-admin.yml).
readonly ADMIN_ACCOUNT="autobot_admin"
readonly ADMIN_MARKERS=("Emergency Admin User (fallback access)" "AutoBot Backup Admin (Emergency Access)")

# --- Components AutoBot's Ansible roles deploy (checked by the #18653 guard) ---------------
readonly UNIT_GLOBS=("autobot-*.service" "autobot-*.timer" "autobot-*.socket")
readonly UNITS=(
    slm.target slm-agent.service slm-admin-ui.service node_exporter.service prometheus.service
    loki.service promtail.service redis_exporter.service redisinsight.service ollama.service
    vnc-server.service redis-stack-server.service
)
readonly UNIT_DROPINS=(
    /etc/systemd/system/nginx.service.d/ssl-cert-check.conf
    /etc/systemd/system/logrotate.timer.d/autobot-hourly.conf
)
readonly SUDOERS=(
    90-autobot 90-autobot_admin autobot_admin autobot-nopasswd autobot-python-provision
    autobot-redis-service slm-agent
)
readonly APT_SOURCES=(pgdg grafana redis docker intel-openvino deadsnakes)
readonly PACKAGES=(
    "postgresql-*" "postgresql-client-*" "postgresql-contrib-*" postgresql-common postgresql-client-common
    python3-psycopg2 grafana prometheus "prometheus-node-exporter*" redis-stack-server
)
readonly DATABASES=(slm slm_users autobot_users)
readonly DB_ROLES=(slm_app)
readonly PATHS=(
    "${AUTOBOT_BASE}" /etc/autobot /var/lib/slm /var/lib/slm-agent /var/lib/autobot /var/log/autobot
    /var/lib/postgresql /etc/postgresql /var/lib/grafana /etc/grafana /var/log/grafana
    /var/lib/prometheus /etc/prometheus /opt/prometheus /etc/ssl/slm
    /var/backups/autobot /var/backups/autobot-apt-sources /var/www/slm-admin /var/www/autobot-slm
    /etc/nginx/sites-available/slm /etc/tmpfiles.d/autobot-slm-self-update.conf
    /root/autobot-credentials.txt /root/slm-credentials.txt /tmp/.autobot_detected_ip
    /usr/local/bin/autobot-admin /usr/local/bin/autobot-time-sync /usr/local/bin/autobot-pg-backup.sh
    /usr/local/bin/check-time-sync.sh /var/lib/systemd/timers/stamp-autobot-pg-backup.timer
)
readonly PATH_GLOBS=(
    "/etc/nginx/sites-available/autobot-slm*" "/etc/nginx/sites-enabled/autobot-slm*"
    "/etc/nginx/snippets/autobot-*" "/etc/logrotate.d/autobot-*"
)
# Left on purpose: nginx (install.sh's base package, often serving other sites), the base
# packages install.sh needs (git, curl, openssh-server, ...), and the service accounts the
# roles create (prometheus, node_exporter, ...): they have no install-time record, so this
# cannot tell them from accounts that were already on the machine.

log() { echo -e "$*" | tee -a "${LOG_FILE}"; }

record_values() {  # every value recorded under key $1
    [[ -f "${INSTALL_RECORD}" ]] || return 0
    sed -n "s/^$1=//p" "${INSTALL_RECORD}"
}

existing() {  # the arguments (globs expanded) that exist on this host
    local item path
    for item in "$@"; do
        # shellcheck disable=SC2086
        for path in ${item}; do [[ -e "${path}" || -L "${path}" ]] && echo "${path}"; done
    done
    return 0
}

plan_units() {
    local unit
    for unit in "${UNITS[@]}"; do
        [[ -e "/etc/systemd/system/${unit}" ]] && echo "${unit}"
    done
    # No matching unit makes systemctl exit 1; that is an empty answer, not a failure.
    { systemctl list-unit-files --no-legend "${UNIT_GLOBS[@]}" 2>/dev/null || true; } | awk '{print $1}'
    return 0
}

plan_packages() {
    local pkg
    for pkg in "${PACKAGES[@]}" $(record_values package); do
        # dpkg-query exits 1 for a package that is not known here: not installed, not a failure.
        { dpkg-query -W -f='${Package} ${Status}\n' "${pkg}" 2>/dev/null || true; } | awk '/ installed$/{print $1}'
    done
    return 0
}

plan_apt_sources() {
    local name
    for name in "${APT_SOURCES[@]}" $(record_values apt_source); do
        existing "/etc/apt/sources.list.d/${name}*.list" "/etc/apt/sources.list.d/${name}*.sources" \
                 "/etc/apt/sources.list.d/ppa_${name}_*.list" "/etc/apt/sources.list.d/*${name}*.list.distUpgrade"
    done
    existing /usr/share/keyrings/pgdg.asc /usr/share/keyrings/grafana.gpg /usr/share/keyrings/grafana.key \
             /usr/share/keyrings/nodesource.gpg
}

plan_sudoers() {
    local name
    for name in "${SUDOERS[@]}"; do existing "/etc/sudoers.d/${name}"; done
    record_values created_sudoers | while read -r path; do existing "${path}"; done
}

plan_users() {
    local user marker gecos
    for user in $(record_values created_user); do id "${user}" &>/dev/null && echo "${user}"; done
    if id "${ADMIN_ACCOUNT}" &>/dev/null; then
        gecos="$(getent passwd "${ADMIN_ACCOUNT}" | cut -d: -f5)"
        for marker in "${ADMIN_MARKERS[@]}"; do [[ "${gecos}" == "${marker}" ]] && echo "${ADMIN_ACCOUNT}"; done
    fi
    return 0
}

print_plan() {
    log "This removes, from $(hostname):"
    log "  units:        $(plan_units | tr '\n' ' ')"
    log "  databases:    ${DATABASES[*]} (role ${DB_ROLES[*]}), if PostgreSQL is present"
    log "  packages:     $(plan_packages | tr '\n' ' ')"
    log "  apt sources:  $(plan_apt_sources | tr '\n' ' ')"
    log "  sudoers:      $(plan_sudoers | tr '\n' ' ')"
    log "  accounts:     $(plan_users | tr '\n' ' ')"
    log "  paths:        $(existing "${PATHS[@]}" "${PATH_GLOBS[@]}" "${UNIT_DROPINS[@]}" | tr '\n' ' ')"
    [[ -f "${INSTALL_RECORD}" ]] || log "  (no install record at ${INSTALL_RECORD}: no account, recorded package or recorded apt source is removed)"
    log "Kept: nginx, base packages, role service accounts (see the header of this script)."
}

confirm() {
    ${CONFIRM_YES} && return 0
    local fd answer=""
    # Read the terminal, not stdin, and REFUSE without one: proceeding unconfirmed would be
    # a destructive wipe nobody approved (#17929's fail-closed rule for this prompt).
    if ! { exec {fd}</dev/tty; } 2>/dev/null || ! IFS= read -r -p "Type 'UNINSTALL' to confirm: " answer <&"${fd}"; then
        log "No terminal available to confirm an uninstall. Re-run with --yes if you intend to uninstall non-interactively."
        exit 1
    fi
    exec {fd}<&-
    [[ "${answer}" == "UNINSTALL" ]] || { log "Uninstall cancelled."; exit 0; }
}

remove_units() {
    local unit
    for unit in $(plan_units); do
        systemctl disable --now "${unit}" >>"${LOG_FILE}" 2>&1 || true  # a unit that is not running is not an error
        rm -f "/etc/systemd/system/${unit}" && log "  removed unit ${unit}"
    done
    rm -rf /etc/systemd/system/slm.target.wants
    find /etc/systemd/system -xtype l -name 'autobot-*' -delete
    systemctl daemon-reload
}

drop_databases() {
    command -v psql &>/dev/null || { log "  PostgreSQL not installed -- no databases to drop"; return 0; }
    systemctl start postgresql >>"${LOG_FILE}" 2>&1 || true  # already gone with its unit is fine
    local db role
    for db in "${DATABASES[@]}"; do
        sudo -u postgres dropdb --if-exists "${db}" >>"${LOG_FILE}" 2>&1 && log "  dropped database ${db}"
    done
    for role in "${DB_ROLES[@]}"; do
        sudo -u postgres dropuser --if-exists "${role}" >>"${LOG_FILE}" 2>&1 && log "  dropped role ${role}"
    done
    return 0
}

purge_packages() {
    local pkgs
    mapfile -t pkgs < <(plan_packages | sort -u)
    [[ ${#pkgs[@]} -gt 0 ]] || return 0
    command -v pg_lsclusters &>/dev/null && pg_lsclusters --no-header 2>/dev/null \
        | while read -r ver name _; do pg_dropcluster --stop "${ver}" "${name}" >>"${LOG_FILE}" 2>&1; done
    # A failed purge is reported and the rest of the uninstall still runs: stopping here is
    # how a partial uninstall used to leave credentials and accounts behind (#18653).
    if env DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq "${pkgs[@]}" >>"${LOG_FILE}" 2>&1; then
        log "  purged ${pkgs[*]}"
        env DEBIAN_FRONTEND=noninteractive apt-get autoremove -y -qq >>"${LOG_FILE}" 2>&1 \
            || log "  WARNING: apt-get autoremove failed; see ${LOG_FILE}"
    else
        log "  WARNING: apt-get purge failed for ${pkgs[*]}; see ${LOG_FILE}"
    fi
}

remove_files() {
    local path
    for path in $(plan_apt_sources) $(existing "${PATHS[@]}" "${PATH_GLOBS[@]}" "${UNIT_DROPINS[@]}"); do
        rm -rf "${path}" && log "  removed ${path}"
    done
}

remove_sudoers_and_users() {
    local path user users
    users="$(plan_users)"   # read before /etc/autobot (and its record) is gone
    for path in $(plan_sudoers); do rm -f "${path}" && log "  removed ${path}"; done
    visudo -c >>"${LOG_FILE}" 2>&1 || log "  WARNING: visudo reports a problem; check /etc/sudoers.d"
    for user in ${users}; do
        userdel -r "${user}" >>"${LOG_FILE}" 2>&1 && log "  removed account ${user}" \
            || log "  WARNING: could not remove account ${user} (running processes?)"
    done
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run) DRY_RUN=true ;;
            --yes|-y)  CONFIRM_YES=true ;;
            *) echo "Unknown option: $1 (use --dry-run, --yes)" >&2; exit 2 ;;
        esac
        shift
    done
}

main() {
    DRY_RUN=false CONFIRM_YES=false
    parse_args "$@"
    [[ ${EUID} -eq 0 ]] || { echo "Run as root (sudo)." >&2; exit 1; }
    print_plan
    ${DRY_RUN} && { log "Dry run: nothing was removed. Log: ${LOG_FILE}"; exit 0; }
    confirm
    remove_units
    drop_databases
    purge_packages
    remove_sudoers_and_users   # before remove_files: it reads the install record in /etc/autobot
    remove_files
    rm -f "${BASH_SOURCE[0]}"
    log "AutoBot has been removed. nginx and base packages were kept. Log: ${LOG_FILE}"
}

main "$@"
