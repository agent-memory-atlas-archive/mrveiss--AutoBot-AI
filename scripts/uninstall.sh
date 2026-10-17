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
#   them. The rest are the components AutoBot's Ansible roles deploy, listed below -- each
#   either removed or KEPT with its reason. The guard
#   repo_tests/uninstall_covers_role_components_18653_test.py fails when a role deploys a
#   unit, sudoers file, apt source or config file this script neither removes nor keeps.
# - PostgreSQL (clusters, packages, data, the pgdg source) goes only when every database on
#   it is AutoBot's; a cluster holding anything else, or one that cannot be inspected, keeps
#   the whole PostgreSQL stack. AutoBot's own databases are dropped either way.
# - No system-wide `apt-get autoremove`, and the plan shows apt's full purge set,
#   reverse-dependents included, before anything is touched.
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
trap 'rm -f "${BASH_SOURCE[0]}"' EXIT  # the temp copy, on dry run, cancel, refusal or error too

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
    loki.service promtail.service redis_exporter.service redisinsight.service
    vnc-server.service vnc-websockify.service redis-stack-server.service
)
# ollama: commonly installed and used outside AutoBot; its unit and models stay.
readonly KEPT_UNITS=(ollama.service)
readonly UNIT_DROPINS=(
    /etc/systemd/system/nginx.service.d/ssl-cert-check.conf
    /etc/systemd/system/logrotate.timer.d/autobot-hourly.conf
)
readonly SUDOERS=(
    90-autobot 90-autobot_admin autobot_admin autobot-nopasswd autobot-python-provision
    autobot-redis-service slm-agent
)
readonly APT_SOURCES=(grafana redis)
# docker, intel-openvino, deadsnakes: general-purpose sources other software on the machine
# relies on (deadsnakes is removed when install.sh's record says it added it).
readonly KEPT_APT_SOURCES=(docker intel-openvino deadsnakes)
readonly PACKAGES=(python3-psycopg2 grafana prometheus "prometheus-node-exporter*" redis-stack-server)
# Removed only when PostgreSQL holds nothing but AutoBot's databases (postgres_is_autobots).
readonly POSTGRES_PACKAGES=("postgresql-*" "postgresql-client-*" "postgresql-contrib-*" postgresql-common postgresql-client-common)
readonly POSTGRES_PATHS=(/var/lib/postgresql /etc/postgresql)
readonly POSTGRES_APT_SOURCES=(pgdg)
readonly DATABASES=(slm slm_users autobot_users)
readonly DB_ROLES=(slm_app)
readonly PATHS=(
    "${AUTOBOT_BASE}" /etc/autobot /etc/slm-agent /var/lib/slm /var/lib/slm-agent /var/lib/autobot /var/log/autobot
    /var/lib/grafana /etc/grafana /var/log/grafana /opt/node_exporter
    /etc/dnsmasq.d/autobot.conf /etc/logrotate.d/autobot /etc/logrotate.d/redis-stack
    /etc/rsyslog.d/30-autobot.conf /etc/rsyslog.d/50-autobot.conf
    /var/lib/prometheus /etc/prometheus /opt/prometheus /etc/ssl/slm
    /var/backups/autobot /var/backups/autobot-apt-sources /var/www/slm-admin /var/www/autobot-slm
    /etc/nginx/sites-available/slm /etc/tmpfiles.d/autobot-slm-self-update.conf
    /root/autobot-credentials.txt /root/slm-credentials.txt /tmp/.autobot_detected_ip
    /usr/local/bin/autobot-admin /usr/local/bin/autobot-time-sync /usr/local/bin/autobot-pg-backup.sh
    /usr/local/bin/check-time-sync.sh /var/lib/systemd/timers/stamp-autobot-pg-backup.timer
)
readonly PATH_GLOBS=(
    "/etc/nginx/sites-available/autobot-*" "/etc/nginx/sites-enabled/autobot-*"
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

foreign_databases() {  # databases on this host's PostgreSQL that are not AutoBot's
    command -v pg_lsclusters &>/dev/null || return 0
    local own="postgres|template0|template1|$(IFS='|'; echo "${DATABASES[*]}")" ver name port status _
    while read -r ver name port status _; do
        if [[ "${status}" != online* ]]; then
            echo "(cluster ${ver}/${name} is ${status} and cannot be inspected)"
            continue
        fi
        { sudo -u postgres psql -p "${port}" -Atc "SELECT datname FROM pg_database" 2>/dev/null \
            || echo "(cluster ${ver}/${name} did not answer)"; } | grep -vxE "${own}" || true
    done < <(pg_lsclusters --no-header 2>/dev/null || true)
}

# Decided ONCE, before anything changes (main sets POSTGRES_DECISION): after the purge psql is
# gone, and re-deciding then would leave the data directories and the pgdg source behind.
postgres_is_autobots() {
    [[ "${POSTGRES_DECISION}" == "remove" ]]
}

decide_postgres() {  # "remove" when PostgreSQL is installed and holds only AutoBot's databases
    if command -v psql &>/dev/null && [[ -z "$(foreign_databases)" ]]; then echo remove; else echo keep; fi
}

plan_packages() {
    local pkg extra=()
    postgres_is_autobots && extra=("${POSTGRES_PACKAGES[@]}")
    for pkg in "${PACKAGES[@]}" "${extra[@]}" $(record_values package); do
        # dpkg-query exits 1 for a package that is not known here: not installed, not a failure.
        { dpkg-query -W -f='${Package} ${Status}\n' "${pkg}" 2>/dev/null || true; } | awk '/ installed$/{print $1}'
    done
    return 0
}

plan_purge_set() {  # everything apt would remove for plan_packages, reverse-dependents included
    local pkgs
    mapfile -t pkgs < <(plan_packages | sort -u)
    [[ ${#pkgs[@]} -gt 0 ]] || return 0
    { apt-get -s purge "${pkgs[@]}" 2>/dev/null || true; } | awk '/^Purg /{print $2}'
}

plan_apt_sources() {
    local name extra=()
    postgres_is_autobots && extra=("${POSTGRES_APT_SOURCES[@]}")
    for name in "${APT_SOURCES[@]}" "${extra[@]}" $(record_values apt_source); do
        existing "/etc/apt/sources.list.d/${name}*.list" "/etc/apt/sources.list.d/${name}*.sources" \
                 "/etc/apt/sources.list.d/ppa_${name}_*.list" "/etc/apt/sources.list.d/*${name}*.list.distUpgrade"
    done
    existing /usr/share/keyrings/grafana.gpg /usr/share/keyrings/grafana.key
    postgres_is_autobots && existing /usr/share/keyrings/pgdg.asc
    [[ -n "$(record_values apt_source | grep -x nodesource)" ]] && existing /usr/share/keyrings/nodesource.gpg
    return 0
}

plan_paths() {
    existing "${PATHS[@]}" "${PATH_GLOBS[@]}" "${UNIT_DROPINS[@]}"
    postgres_is_autobots && existing "${POSTGRES_PATHS[@]}"
    return 0
}

plan_sudoers() {
    local name path
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
    if command -v psql &>/dev/null && ! postgres_is_autobots; then
        log "  PostgreSQL:   KEPT -- it also holds: $(foreign_databases | tr '\n' ' ')"
    elif command -v psql &>/dev/null; then
        log "  PostgreSQL:   holds only AutoBot's databases -- its clusters, packages and data go too"
    fi
    log "  packages:     $(plan_purge_set | tr '\n' ' ')"
    log "  apt sources:  $(plan_apt_sources | tr '\n' ' ')"
    log "  sudoers:      $(plan_sudoers | tr '\n' ' ')"
    log "  accounts:     $(plan_users | tr '\n' ' ')"
    log "  paths:        $(plan_paths | tr '\n' ' ')"
    [[ -f "${INSTALL_RECORD}" ]] || log "  (no install record at ${INSTALL_RECORD}: no account, recorded package or recorded apt source is removed)"
    log "Kept: nginx, base packages, ${KEPT_UNITS[*]}, apt sources ${KEPT_APT_SOURCES[*]}, role service accounts."
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
        if sudo -u postgres dropdb --if-exists "${db}" >>"${LOG_FILE}" 2>&1; then log "  dropped database ${db}"
        else log "  WARNING: could not drop database ${db}; see ${LOG_FILE}"; fi
    done
    for role in "${DB_ROLES[@]}"; do
        if sudo -u postgres dropuser --if-exists "${role}" >>"${LOG_FILE}" 2>&1; then log "  dropped role ${role}"
        else log "  WARNING: could not drop role ${role}; see ${LOG_FILE}"; fi
    done
    return 0
}

drop_clusters() {  # only when PostgreSQL holds nothing but AutoBot's (now dropped) databases
    postgres_is_autobots || return 0
    local ver name _
    while read -r ver name _; do
        if pg_dropcluster --stop "${ver}" "${name}" >>"${LOG_FILE}" 2>&1; then log "  dropped cluster ${ver}/${name}"
        else log "  WARNING: could not drop cluster ${ver}/${name}; see ${LOG_FILE}"; fi
    done < <(pg_lsclusters --no-header 2>/dev/null || true)
}

purge_packages() {
    local pkgs
    mapfile -t pkgs < <(plan_packages | sort -u)
    [[ ${#pkgs[@]} -gt 0 ]] || return 0
    # A failed purge is reported and the rest of the uninstall still runs: stopping here is
    # how a partial uninstall used to leave credentials and accounts behind (#18653).
    if env DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq "${pkgs[@]}" >>"${LOG_FILE}" 2>&1; then
        log "  purged ${pkgs[*]} (no autoremove: run \`apt autoremove\` yourself to review what else is unused)"
    else
        log "  WARNING: apt-get purge failed for ${pkgs[*]}; see ${LOG_FILE}"
    fi
}

remove_files() {
    local path
    for path in $(plan_apt_sources) $(plan_paths); do
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
    DRY_RUN=false CONFIRM_YES=false POSTGRES_DECISION=keep
    parse_args "$@"
    [[ ${EUID} -eq 0 ]] || { echo "Run as root (sudo)." >&2; exit 1; }
    POSTGRES_DECISION="$(decide_postgres)"
    print_plan
    ${DRY_RUN} && { log "Dry run: nothing was removed. Log: ${LOG_FILE}"; exit 0; }
    confirm
    remove_units
    drop_databases
    drop_clusters
    purge_packages
    remove_sudoers_and_users   # before remove_files: it reads the install record in /etc/autobot
    remove_files
    log "AutoBot has been removed. Kept items are listed above. Log: ${LOG_FILE}"
}

main "$@"
